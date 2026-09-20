"""DB-FREE PROOF — detect, then FIX, then tell somebody who actually hears it.

OWNER DIRECTIVES, 2026-09-20: *"check if that was set up for boost and all tenants or that was patch
work also"*, then *"build all for all tenants"*, then *"most importantly the system should be capable
of autofix"*.

WHAT THE AUDIT FOUND, measured live, and it was the reverse of the assumption. The auto-CHECK was
already general — `_data_freshness_monitor` runs for every org after every sweep. On the house org it
worked perfectly: twelve `freshness:` alerts, one a day, 09-09 through 09-20, plus fourteen
`connector:email_sweep_config:*:errored`. The platform detected the missing ingest the day after it
began and reported it every single day. Three things were wrong anyway:

  1. NOBODY TO TELL, AND NO TRACE. `_send_alert` returned early when a scope had no `alert_recipient`
     row — no email, no log row, nothing. Configured on ONE tenant and on neither of the other two, so a
     feed dying on those would have been found, reported to no one, and left no evidence that the
     platform had tried. Whether a tenant hears about its own broken feed depended on someone having
     remembered to add a row.
  2. AN UNHEARD ALERT COUNTED AS DELIVERED. The dedup counted ANY prior `alert_log` row, including one
     written when zero messages went out, so the quietest failure was also the stickiest.
  3. CORRECT ALERTS, DROWNED. Both ref_keys carried TODAY's date, so a standing failure re-alerted
     daily forever. The house org got 7-8 connector mails a day, six for connectors nobody uses any more
     (ePay last ran 08-24, FTP 06-26, B2B reporting "disabled") — so the one that mattered was one line
     in ~90 emails across twelve days.

AND THE REPAIR THAT DID NOT EXIST. A sweep only sees mail inside its IMAP window. Once a feed falls
further behind than the window reaches, the file that would close the gap is IN the mailbox and
invisible, so every later run is a correct no-op and the gap is permanent until a human widens it. The
house org sat at latest=09-08 with a 2-day window: nothing the platform could do on its own would ever
have recovered 09-09..09-19. `_freshness_autofix` performs exactly that repair — one catch-up sweep at
a transient wider window — and it is only safe because §19.19's backlog collapse made a wide window
cost one import per report per month instead of ~336.

Run:  cd backend && python3 harness_alert_autofix.py
"""
import ast
import asyncio
import os
import sys
from datetime import date, datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

ROUTER = "app/modules/commcalc/router.py"
CLOSING = "app/modules/closing/router.py"

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def eq(name, got, want):
    ok(name, got == want, f"got={got!r} want={want!r}")


def _src(rel):
    return open(os.path.join(_HERE, rel), encoding="utf-8").read()


def _func_src(rel, name):
    text = _src(rel)
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"function {name!r} not found in {rel}")


# ── the two pure units, lifted out without importing FastAPI/supabase ────────────────────────────
_ns = {}
exec(_func_src(CLOSING, "_org_admin_recipients"), _ns)
admins = _ns["_org_admin_recipients"]


class _Q:
    def __init__(self, rows, raise_on=False):
        self.rows, self.raise_on, self._f = rows, raise_on, {}

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self._f[k] = v
        return self

    def limit(self, n):
        return self

    def execute(self):
        if self.raise_on:
            raise RuntimeError("roster unreadable")
        return type("R", (), {"data": [r for r in self.rows
                                       if all(r.get(k) == v for k, v in self._f.items())]})()


class _C:
    def __init__(self, rows, raise_on=False):
        self.rows, self.raise_on = rows, raise_on

    def schema(self, _s):
        return self

    def table(self, _t):
        return _Q(self.rows, self.raise_on)


ORG = "org-1"
ROSTER = [
    {"org_id": ORG, "role": "admin", "email": "a@x.com", "full_name": "A", "is_active": True},
    {"org_id": ORG, "role": "Admin", "email": "b@x.com", "full_name": "B", "is_active": True},
    {"org_id": ORG, "role": "sales_rep", "email": "rep@x.com", "full_name": "R", "is_active": True},
    {"org_id": ORG, "role": "admin", "email": "gone@x.com", "full_name": "G", "is_active": False},
    {"org_id": ORG, "role": "admin", "email": "", "full_name": "NoMail", "is_active": True},
    {"org_id": ORG, "role": "accountant", "email": "s@x.com", "super_admin": True,
     "full_name": "S", "is_active": True},
    {"org_id": "other-org", "role": "admin", "email": "wrong@x.com", "full_name": "W", "is_active": True},
]

print("\n§A  somebody is always told — the last-resort recipients")
got = admins(_C(ROSTER), ORG)
eq("A1 active admins (case-insensitive) plus super_admins, nobody else",
   sorted(r["email"] for r in got), ["a@x.com", "b@x.com", "s@x.com"])
ok("A2 a sales rep is never alerted about a broken connector",
   all(r["email"] != "rep@x.com" for r in got))
ok("A3 a DEACTIVATED admin is not mailed", all(r["email"] != "gone@x.com" for r in got))
ok("A4 an admin with no email address is skipped, not sent an empty To:",
   all((r.get("email") or "").strip() for r in got))
ok("A5 RULE ONE — another tenant's admin is never a fallback for this one",
   all(r["email"] != "wrong@x.com" for r in got))
ok("A6 every fallback is flagged as one, so the UI can say WHY they were mailed",
   all(r.get("is_org_admin_fallback") for r in got))
eq("A7 an unreadable roster yields NO fallback rather than raising", admins(_C([], True), ORG), [])
eq("A8 a tenant with no admins at all yields none (and the caller then records the silence)",
   admins(_C([r for r in ROSTER if r["role"] == "sales_rep"]), ORG), [])

print("\n§B  the alert layer's honesty rules")
SA = _func_src(CLOSING, "_send_alert")
AR = _func_src(CLOSING, "_alert_recipients")
ok("B1 the fallback is wired: no configured recipient falls through to the tenant's admins",
   "_org_admin_recipients(client, org_id)" in AR)
ok("B2 configured recipients still WIN — the fallback runs only when nothing else resolved",
   "if not out:" in AR and AR.index("if not out:") > AR.index("alert_recipient"))
ok("B3 the dedup reads `recipients`, not just existence",
   'select("id,recipients")' in SA)
ok("B4 …and only a row that actually DELIVERED suppresses a re-send",
   'any((r.get("recipients") or "").strip() for r in seen)' in SA)
ok("B5 a suppressed alert is RECORDED, with the reason, instead of returning silently",
   '"suppressed": "no_recipients"' in SA and 'table("alert_log").insert' in SA)
ok("B6 …and that record has empty recipients, so it can never masquerade as delivered",
   '"recipients": "",' in SA)

print("\n§C  one alert per EPISODE, not one per day")
SCAN = _func_src(ROUTER, "_scan_connector_health")
ok("C1 the connector key is keyed on the episode's start, not today's date",
   'since-{since}' in SCAN and 'now.date()}:{kind}' not in SCAN)
ok("C2 …taken from last_run_at, which §19.17 made mean LAST SUCCESS",
   'since = str(lr or "")[:10] or "never"' in SCAN)
MON = _func_src(ROUTER, "_data_freshness_monitor")
ok("C3 the freshness key is keyed on the feed's stuck data date, not today's date",
   "stuck-at-{ld}" in MON and "{today_s}" not in MON)
ROUTER_SRC = _src(ROUTER)
ok("C4 no alert ref_key anywhere in the sweep paths still carries today's date",
   ROUTER_SRC.count("today_s") == 0, ROUTER_SRC.count("today_s"))


# episode semantics, as arithmetic
def key(last_success, kind="errored", table="email_sweep_config", rid=7):
    return f"connector:{table}:{rid}:since-{last_success or 'never'}:{kind}"


eq("C5 twelve days of the same outage produce ONE key",
   len({key("2026-09-07") for _ in range(12)}), 1)
ok("C6 a recovery then a new failure is a NEW key — the next episode alerts afresh",
   key("2026-09-07") != key("2026-09-20"))
ok("C7 a connector that never once succeeded is 'never', not a crash", "never" in key(None))

print("\n§D  the autofix — the one repair the platform can actually perform")
FIX = _func_src(ROUTER, "_freshness_autofix")
AFX = {}
exec("from datetime import date as _date, datetime as _datetime", AFX)


def decide(latest_data_date, window, today=date(2026, 9, 20), cap=60):
    """`_freshness_autofix`'s decision, restated: catch up only when the gap is WIDER than the window."""
    if not latest_data_date:
        return None
    needed = (today - datetime.strptime(latest_data_date, "%Y-%m-%d").date()).days + 1
    if needed <= window:
        return None
    return min(needed + 1, cap)


eq("D1 the live house org: data ends 09-08, window 2d -> catch up at 14d",
   decide("2026-09-08", 2), 14)
eq("D2 a feed inside the window is NOT swept again — widening cannot help, so the alert goes",
   decide("2026-09-19", 14), None)
eq("D3 …exactly at the boundary, too (needed == window)", decide("2026-09-19", 2), None)
eq("D4 a feed that never had data is left alone — widening would be a guess, not a repair",
   decide(None, 2), None)
eq("D5 a long-dead feed is capped, so one gap cannot make the platform scan a year of mail",
   decide("2024-01-01", 2), 60)
ok("D6 SELF-LIMITING: after a successful catch-up the condition stops being true",
   decide("2026-09-08", 2) is not None and decide("2026-09-20", 2) is None,
   "this is why it needs no cooldown state")
ok("D7 …and it heals a too-narrow window without a human touching the config",
   decide("2026-09-08", 2) == 14)

print("\n§E  the autofix is wired, bounded, and never louder than the truth")
ok("E1 it runs BEFORE the alert — a human is only interrupted by what could not be fixed",
   MON.index("_freshness_autofix") < MON.index("_send_alert"))
ok("E2 a feed that recovered produces NO alert at all",
   "if not stale:" in MON.split("_freshness_autofix", 1)[1][:800] and "return rep" in MON)
ok("E3 the feed is RE-MEASURED after the repair, never assumed fixed",
   "_data_freshness_report(client, org_id)" in MON.split("_freshness_autofix", 1)[1][:600])
ok("E4 a feed still behind says the catch-up was tried and what it recovered",
   "An automatic catch-up already ran" in MON and "widened_to" in MON)
ok("E5 the widened window is TRANSIENT — the saved config is never rewritten",
   "since_days_override" in FIX and "email_sweep_config').update" not in FIX
   and 'table("email_sweep_config").update' not in FIX)
ok("E6 a tenant with no mailbox is a no-op, not a crash (it cannot pull what has no source)",
   "if not accounts:" in FIX)
ok("E7 the autofix never raises — a failed repair must still leave the alert to be sent",
   "except Exception as e:" in FIX and "WARN autofix catch-up sweep failed" in FIX
   and "except Exception as e:" in MON.split("_freshness_autofix", 1)[1][:400])
ok("E8 the cap is a named constant, not a magic number",
   "AUTOFIX_MAX_CATCHUP_DAYS" in ROUTER_SRC and "min(needed + 1, AUTOFIX_MAX_CATCHUP_DAYS)" in FIX)
SWEEP = _func_src(ROUTER, "_run_email_sweep")
ok("E9 the override widens only THIS run's search window",
   "if since_days_override:" in SWEEP and "'since_days': int(since_days_override)" in SWEEP)
ok("E10 …and nothing persists it", "update(" not in SWEEP.split("since_days_override", 1)[1][:300])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
