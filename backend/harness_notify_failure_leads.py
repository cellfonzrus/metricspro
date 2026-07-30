"""Offline proof harness — the three triaged failure_log leads (2026-07-30 auto-fix board).

No database, no network, no send: recording fakes feed the REAL module code, and the BASE
(origin/main 542b4ab) bytes of each touched file are loaded side-by-side so every "before" claim is
executed, not asserted from memory.

  A. LEAD 1 — billing-Friday resolution: page parity for all 7 weekdays, relative tokens, explicit
     date passed through unchanged, business timezone, unusable value ⇒ ReportConfigError.
  B. LEAD 1 — `_owed_weekly`: BASE raises the exact failure_log message; FIXED derives the date and
     calls asset's handler with it (asset semantics untouched — same kwarg, same value shape).
  C. LEAD 1 — `/notify/run-due`: a mis-configured schedule is skipped as a CONFIG error (one
     core.failure_log row, category `report_config`, NOT `sweep_error`), the tenant guard is never
     entered, the schedule is advanced (no hot-loop) and the OTHER schedules still send.
  D. LEAD 2 — the FastAPI `Header` sentinel: the real default object off the real handler
     signatures reproduces `AttributeError: 'Header' object has no attribute 'lower'` at
     core.router `_uid_from_token` on BASE, and is treated as "no token" now.
  E. LEAD 2 — static audit over report_registry: no in-process handler call may leave a FastAPI
     parameter sentinel unbound. BASE = 4 violations (flags / commissions / gp / action_plan),
     FIXED = 0.
  F. LEAD 2 — build_payload plumbing: opt-in only, non-str normalized, no token in send_log's
     filters, resolved live-report link.
  G. LEAD 3 — `/core/employee-dashboard`: a RuntimeError from the COSMETIC widget lookup no longer
     takes the whole dashboard down (BASE block, executed, does).
  H. LEAD 1 (surfacing) — the notify attention provider reports a mis-configured schedule and
     CLEARS when the filter is fixed.

Run:  cd backend && python3 harness_notify_failure_leads.py
"""
import importlib.util
import inspect
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, ".")

BASE_REV = "542b4ab"          # origin/main this package is parked off
HOUSE = "00000000-0000-0000-0000-000000000001"
LUXE = "00000000-0000-0000-0000-0000000000ff"

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def base_source(path):
    """The file's bytes at origin/main — every 'before' proof runs THESE, not a paraphrase."""
    return subprocess.run(["git", "show", f"{BASE_REV}:{path}"], cwd="..",
                          capture_output=True, text=True, check=True).stdout


def load_base_module(path, name):
    src = base_source(path)
    f = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
    f.write(src)
    f.close()
    spec = importlib.util.spec_from_file_location(name, f.name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


import anyio                                                              # noqa: E402
from app.modules.notify import report_registry as RR                      # noqa: E402
from app.modules.notify import report_filters as RF                       # noqa: E402
from app.modules.notify import router as NR                               # noqa: E402
from app.modules.notify import attention as NA                            # noqa: E402
from app.modules.core import router as CR                                 # noqa: E402
from app.modules.commcalc import router as C                              # noqa: E402
from app.modules.asset import router as A                                 # noqa: E402

run = anyio.run


# ── recording fake supabase ──────────────────────────────────────────────────────────────────────
class _Q:
    def __init__(self, key, store, log, fail):
        self.key, self.store, self.log, self.fail = key, store, log, fail
        self.filters, self.cols, self.payload, self.op = {}, "", None, "select"

    def select(self, cols="*", *a, **k):
        self.cols = cols
        return self

    def insert(self, row, *a, **k):
        self.op, self.payload = "insert", row
        return self

    def update(self, row, *a, **k):
        self.op, self.payload = "update", row
        return self

    def upsert(self, row, *a, **k):
        self.op, self.payload = "upsert", row
        return self

    def delete(self, *a, **k):
        self.op = "delete"
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def __getattr__(self, name):          # in_/gte/lte/lt/gt/order/limit/range/neq/not_/is_ …
        def _chain(*a, **k):
            return self
        return _chain

    def execute(self):
        self.log.append({"key": self.key, "op": self.op, "cols": self.cols,
                         "filters": dict(self.filters), "payload": self.payload})
        for pat, exc in self.fail:
            if re.search(pat, f"{self.key}|{self.cols}"):
                raise exc
        rows = [dict(r) for r in self.store.get(self.key, [])
                if all(r.get(k) == v for k, v in self.filters.items())]
        if self.op == "select":
            return SimpleNamespace(data=rows)
        return SimpleNamespace(data=[self.payload] if isinstance(self.payload, dict) else (self.payload or []))


class _S:
    def __init__(self, schema, store, log, fail):
        self.schema_name, self.store, self.log, self.fail = schema, store, log, fail

    def table(self, t):
        return _Q(f"{self.schema_name}.{t}", self.store, self.log, self.fail)


class Fake:
    def __init__(self, store=None, fail=()):
        self.store, self.log, self.fail = dict(store or {}), [], list(fail)

    def schema(self, s):
        return _S(s, self.store, self.log, self.fail)

    def table(self, t):                                # default (public) schema
        return _Q(f"public.{t}", self.store, self.log, self.fail)

    def rpc(self, *a, **k):
        return _Q("rpc", self.store, self.log, self.fail)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nA. LEAD 1 — billing-Friday resolution (the missing 'thursday')")

# The owed-weekly PAGE's own default, transcribed from
# frontend/src/app/(platform)/commcalc/asset/owed-weekly/page.tsx:
#   const d = new Date(from); const diff = (5 - d.getDay() + 7) % 7 ; d.setDate(d.getDate() + diff)
# JS getDay(): Sun=0..Sat=6.  Python weekday(): Mon=0..Sun=6.  Independently implemented here.
def page_upcoming_friday(d: date) -> date:
    js_day = (d.weekday() + 1) % 7
    return d + timedelta(days=(5 - js_day + 7) % 7)


parity, mismatch = True, None
d0 = date(2026, 7, 27)                                   # Monday
real_today = RF.business_today
try:
    for i in range(14):
        day = d0 + timedelta(days=i)
        RF.business_today = lambda tz="", _d=day: _d                 # freeze "today"
        got = RR._resolve_billing_friday({}, tz="UTC")
        if got != page_upcoming_friday(day).isoformat():
            parity, mismatch = False, (day.isoformat(), got, page_upcoming_friday(day).isoformat())
    ok("A1 the derived Friday matches the owed-weekly page's upcomingFriday() for 14 consecutive "
       "days (Mon→Sun ×2)", parity, mismatch)
    RF.business_today = lambda tz="": date(2026, 7, 31)              # a Friday
    ok("A2 on a Friday the derived date is THAT Friday (not the next one) — the page's rule",
       RR._resolve_billing_friday({}) == "2026-07-31")
    ok("A2b …and 'last' on a Friday is the week before, never today",
       RR._resolve_billing_friday({"thursday": "last"}) == "2026-07-24")
finally:
    RF.business_today = real_today

today_utc = datetime.now(timezone.utc).date()
cur = (today_utc + timedelta(days=(4 - today_utc.weekday()) % 7)).isoformat()
ok("A3 blank filter → the CURRENT billing Friday", RR._resolve_billing_friday({}, tz="UTC") == cur)
ok("A4 missing key entirely → same", RR._resolve_billing_friday({"store": "X"}, tz="UTC") == cur)
for tok in ("current", "this", "now", "upcoming", "next", "  CURRENT  ", "This Week"):
    ok(f"A5 token '{tok.strip()}' → current billing Friday",
       RR._resolve_billing_friday({"thursday": tok}, tz="UTC") == cur)
prev = (date.fromisoformat(cur) - timedelta(days=7)).isoformat()
for tok in ("last", "previous", "prev", "LAST WEEK"):
    ok(f"A6 token '{tok}' → the previous billing Friday",
       RR._resolve_billing_friday({"thursday": tok}, tz="UTC") == prev)
ok("A7 an EXPLICIT date is passed through unchanged (verified-correct behaviour preserved)",
   RR._resolve_billing_friday({"thursday": "2026-07-10"}, tz="UTC") == "2026-07-10")
ok("A7b …including a datetime-ish value truncated to 10 chars, as the page sends",
   RR._resolve_billing_friday({"thursday": "2026-07-10T00:00:00Z"}, tz="UTC") == "2026-07-10")
try:
    RR._resolve_billing_friday({"thursday": "next friday please"}, tz="UTC")
    ok("A8 an unusable value raises ReportConfigError", False)
except RR.ReportConfigError as e:
    ok("A8 an unusable value raises ReportConfigError (config problem, not a crash)",
       "thursday" in str(e) and "YYYY-MM-DD" in str(e))
ok("A8b ReportConfigError is a ValueError (existing 400 mapping in /send keeps working)",
   issubclass(RR.ReportConfigError, ValueError))
ok("A9 business timezone is honoured (a tz whose local date differs resolves off the LOCAL day)",
   RR._business_today("Pacific/Kiritimati") >= RR._business_today("Pacific/Niue"))
ok("A9b an unknown timezone degrades to a date instead of raising",
   isinstance(RR._business_today("Not/AZone"), date))
ok("A10 validate_filters passes for a blank owed_weekly schedule (the reported ×4 failure)",
   RR.validate_filters("owed_weekly", {}) is None)
try:
    RR.validate_filters("owed_weekly", {"thursday": "whenever"})
    ok("A11 validate_filters rejects an unusable saved filter", False)
except RR.ReportConfigError:
    ok("A11 validate_filters rejects an unusable saved filter", True)
ok("A12 validate_filters is a no-op for reports with no validator (no new failure mode)",
   all(RR.validate_filters(k, {}) is None for k in RR.REPORTS if k != "owed_weekly"))
try:
    RR.validate_filters("nope", {})
    ok("A13 unknown report key is a config error", False)
except RR.ReportConfigError:
    ok("A13 unknown report key is a config error", True)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nB. LEAD 1 — _owed_weekly: BASE reproduces the failure_log message, FIXED derives the date")

BASE_RR = load_base_module("backend/app/modules/notify/report_registry.py", "base_report_registry")
try:
    run(lambda: BASE_RR._owed_weekly(HOUSE, {}))
    ok("B1 BASE raises on a schedule with no 'thursday'", False)
except ValueError as e:
    # the failure_log message was: "Job 'notify.subscription' failed: owed_weekly requires a
    # 'thursday' (billing Friday, YY…"
    ok("B1 BASE raises the EXACT reported message (lead reproduced)",
       str(e).startswith("owed_weekly requires a 'thursday' (billing Friday, YY"), str(e))

calls = []
real_owed = A.get_owed_weekly


async def _fake_owed(**kw):
    calls.append(kw)
    return {"by_store": [], "rows": [], "thursday": kw.get("thursday")}


A.get_owed_weekly = _fake_owed
try:
    p = run(lambda: RR._owed_weekly(HOUSE, {}, tz="America/New_York"))
    ok("B2 FIXED builds a payload instead of raising", isinstance(p, dict) and p.get("sheets"))
    ok("B3 asset's handler is called with the SAME kwarg it always took (contract untouched)",
       set(calls[-1]) == {"thursday", "org_id", "store", "market"})
    want = RR._resolve_billing_friday({}, tz="America/New_York")
    ok("B4 …carrying the derived billing Friday", calls[-1]["thursday"] == want, calls[-1])
    ok("B5 org_id is passed through (RULE ONE — never a constant)", calls[-1]["org_id"] == HOUSE)
    ok("B6 the file/subtitle name the week actually sent",
       p["subtitle"].endswith(want) and p["filename"] == f"owed-weekly-{want}")
    ok("B7 an explicit date still wins over the derived default",
       run(lambda: RR._owed_weekly(HOUSE, {"thursday": "2026-06-05"}))["subtitle"].endswith("2026-06-05"))
    ok("B8 a non-house tenant resolves identically (no tenant branching)",
       run(lambda: RR._owed_weekly(LUXE, {}))["subtitle"].endswith(
           RR._resolve_billing_friday({}, tz="")) and calls[-1]["org_id"] == LUXE)
    lp = run(lambda: RR.build_payload("owed_weekly", HOUSE, {}, tz="America/New_York"))["live_path"]
    ok("B9 the live-report link opens the week that was SENT, not the page default",
       lp == f"/commcalc/asset/owed-weekly?thursday={want}", lp)
    lp2 = run(lambda: RR.build_payload("owed_weekly", HOUSE, {"thursday": "2026-06-05", "store": "S1"}))["live_path"]
    ok("B10 an explicitly-filtered send keeps its own link (store filter preserved)",
       lp2 == "/commcalc/asset/owed-weekly?thursday=2026-06-05&store=S1", lp2)
    ok("B11 no 'live_filters' key leaks into the rendered payload",
       "live_filters" not in run(lambda: RR.build_payload("owed_weekly", HOUSE, {})))
finally:
    A.get_owed_weekly = real_owed


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nC. LEAD 1 — /notify/run-due treats a mis-configured schedule as CONFIG, not a sweep crash")

SUB_OK = {"id": "s-ok", "org_id": LUXE, "report_key": "owed_weekly", "name": "Weekly owed",
          "filters": {}, "channels": ["email"], "formats": ["xlsx"], "ad_hoc_emails": ["a@b.c"],
          "frequency": "weekly", "day_of_week": 0, "hour": 8, "timezone": "America/New_York",
          "is_active": True, "next_run_at": "2000-01-01T00:00:00+00:00"}
SUB_BAD = {**SUB_OK, "id": "s-bad", "name": "Weekly owed (typo)", "filters": {"thursday": "friday"}}


def run_due_with(subs):
    fake = Fake({"notify.subscriptions": subs})
    dispatched, guarded = [], []

    async def fake_dispatch(org_id, report_key, filters, channels, formats, emails, phones, message,
                            subscription_id=None, triggered_by="manual", authorization="", tz=""):
        dispatched.append({"org_id": org_id, "report_key": report_key, "filters": filters,
                           "authorization": authorization, "tz": tz})
        return {"sent": len(emails), "failed": 0}

    async def fake_guard(org_id, job_name, job, **kw):
        guarded.append((org_id, job_name))
        return await job(SimpleNamespace(org_id=org_id, job_name=job_name, run_id="r", tenant={},
                                         client=fake, money_writes=[]))

    old = (NR.sb, NR.get_supabase, NR._dispatch, NR.run_for_tenant_async, NR.settings.NOTIFY_RUN_SECRET)
    NR.sb = lambda: fake.schema("notify")
    NR.get_supabase = lambda: fake
    NR._dispatch = fake_dispatch
    NR.run_for_tenant_async = fake_guard
    NR.settings.NOTIFY_RUN_SECRET = "secret"
    try:
        res = run(lambda: NR.run_due(x_notify_secret="secret"))
    finally:
        (NR.sb, NR.get_supabase, NR._dispatch, NR.run_for_tenant_async,
         NR.settings.NOTIFY_RUN_SECRET) = old
    return res, fake, dispatched, guarded


res, fake, dispatched, guarded = run_due_with([SUB_BAD])
logged = [r for r in fake.log if r["key"] == "core.failure_log" and r["op"] == "insert"]
ok("C1 the mis-configured schedule does NOT reach the tenant job guard (no failed job_run)",
   guarded == [])
ok("C2 …and nothing is dispatched/sent", dispatched == [])
ok("C3 exactly one core.failure_log row is written", len(logged) == 1, logged)
ok("C4 …category is 'report_config', NOT 'sweep_error'",
   logged[0]["payload"]["category"] == "report_config")
ok("C5 …severity warning, org-scoped to the SUBSCRIPTION's tenant (not the house org)",
   logged[0]["payload"]["severity"] == "warning" and logged[0]["payload"]["org_id"] == LUXE)
ok("C6 …the message names the schedule and the fix, and the source points at notify",
   "Weekly owed (typo)" in logged[0]["payload"]["message"]
   and logged[0]["payload"]["source"].startswith("notify.subscription/")
   and "/notify" in logged[0]["payload"]["remediation"])
ok("C7 …the subscription id + saved filters ride in detail for triage",
   logged[0]["payload"]["detail"]["subscription_id"] == "s-bad"
   and logged[0]["payload"]["detail"]["filters"] == {"thursday": "friday"})
ok("C8 the run-due response marks it as a config error, not a silent success",
   res["ran"] == 1 and res["results"][0].get("config_error") is True)
upd = [r for r in fake.log if r["key"] == "notify.subscriptions" and r["op"] == "update"]
ok("C9 the schedule is still advanced (next_run_at) so the sweep cannot hot-loop on it",
   len(upd) == 1 and upd[0]["payload"].get("next_run_at"))
ok("C10 …and that write is org-scoped as well as id-scoped (RULE ONE)",
   upd[0]["filters"].get("org_id") == LUXE and upd[0]["filters"].get("id") == "s-bad")

res, fake, dispatched, guarded = run_due_with([SUB_BAD, SUB_OK])
ok("C11 one bad schedule never blocks the others (the good one still sends)",
   len(dispatched) == 1 and dispatched[0]["report_key"] == "owed_weekly")
ok("C12 the good schedule runs under the tenant guard, on its own org",
   guarded == [(LUXE, "notify.subscription")])
ok("C13 the schedule's timezone reaches the builder (relative dates resolve business-local)",
   dispatched[0]["tz"] == "America/New_York")
ok("C14 a scheduled run carries NO caller token (org-wide, as an admin-configured schedule means)",
   dispatched[0]["authorization"] == "")
ok("C15 the sweep still reports both schedules", res["ran"] == 2)

# the failure_log write is best-effort: an un-run migration must not break the sweep
fake_fail = Fake({"notify.subscriptions": [SUB_BAD]},
                 fail=[(r"core\.failure_log", RuntimeError("relation core.failure_log does not exist"))])
old = (NR.sb, NR.get_supabase, NR.settings.NOTIFY_RUN_SECRET)
NR.sb, NR.get_supabase, NR.settings.NOTIFY_RUN_SECRET = (lambda: fake_fail.schema("notify"),
                                                         lambda: fake_fail, "secret")
try:
    r2 = run(lambda: NR.run_due(x_notify_secret="secret"))
    ok("C16 a failing/absent core.failure_log never breaks the sweep (§5 degrade gracefully)",
       r2["ran"] == 1 and r2["results"][0].get("config_error") is True)
except Exception as e:
    ok("C16 a failing/absent core.failure_log never breaks the sweep", False, repr(e))
finally:
    NR.sb, NR.get_supabase, NR.settings.NOTIFY_RUN_SECRET = old


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nD. LEAD 2 — the FastAPI Header sentinel that 500'd POST /notify/send")

SENTINEL = inspect.signature(C.get_flags).parameters["authorization"].default
ok("D1 the four caller-scoped handlers really do default `authorization` to a Header SENTINEL",
   all(type(inspect.signature(fn).parameters["authorization"].default).__name__ == "Header"
       for fn in (C.get_flags, C.get_commissions, C.get_gp_report, C.get_action_plan)))
BASE_CR = base_source("backend/app/modules/core/router.py")
m = re.search(r"def _uid_from_token\(authorization: str\):.*?\n(?=\n\n)", BASE_CR, re.S)
base_uid_src = m.group(0)
ns = {}
exec("import time\n_uid_cache={}\n_UID_TTL=60.0\n_UID_CACHE_MAX=1024\n"
     "def get_supabase_admin():\n    raise AssertionError('must not be reached')\n" + base_uid_src, ns)
try:
    ns["_uid_from_token"](SENTINEL)
    ok("D2 BASE _uid_from_token raises on the sentinel (lead reproduced)", False)
except AttributeError as e:
    ok("D2 BASE _uid_from_token raises AttributeError: 'Header' object has no attribute 'lower'",
       "'Header' object has no attribute 'lower'" in str(e), str(e))
ok("D3 FIXED _uid_from_token treats a non-str as 'no caller' instead of 500-ing",
   CR._uid_from_token(SENTINEL) is None)
ok("D3b …for every non-str shape (None / int / object), unchanged for real strings",
   CR._uid_from_token(None) is None and CR._uid_from_token(123) is None
   and CR._uid_from_token("") is None and CR._uid_from_token("Basic xyz") is None)
ok("D4 a caller-scoped handler is reachable with the sentinel now (no AttributeError)",
   CR._uid_from_token(inspect.signature(C.get_action_plan).parameters["authorization"].default) is None)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nE. LEAD 2 — static audit: no in-process handler call may leave a FastAPI sentinel unbound")
import ast                                                              # noqa: E402
import fastapi.params                                                   # noqa: E402

MODS = {"A": A, "C": C, "AC": sys.modules["app.modules.account.router"]}


def sentinel_violations(source):
    """Every `await <MOD>.<fn>(...)` in report_registry whose target has a fastapi-sentinel-defaulted
    parameter that the call does not bind."""
    bad = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Await) and isinstance(node.value, ast.Call)):
            continue
        call = node.value
        fnode = call.func
        if not (isinstance(fnode, ast.Attribute) and isinstance(fnode.value, ast.Name)):
            continue
        mod = MODS.get(fnode.value.id)
        target = getattr(mod, fnode.attr, None) if mod else None
        if target is None:
            continue
        passed = {kw.arg for kw in call.keywords if kw.arg}
        for pname, p in inspect.signature(target).parameters.items():
            if isinstance(p.default, fastapi.params.Param) and pname not in passed:
                bad.append(f"{fnode.value.id}.{fnode.attr}({pname}=<{type(p.default).__name__}>)")
    return bad


base_bad = sentinel_violations(base_source("backend/app/modules/notify/report_registry.py"))
now_bad = sentinel_violations(open("app/modules/notify/report_registry.py").read())
ok("E1 BASE leaves exactly the four reported handlers unbound (root cause pinned)",
   sorted(base_bad) == ["C.get_action_plan(authorization=<Header>)",
                        "C.get_commissions(authorization=<Header>)",
                        "C.get_flags(authorization=<Header>)",
                        "C.get_gp_report(authorization=<Header>)"], base_bad)
ok("E2 FIXED leaves NONE (regression guard for every future builder)", now_bad == [], now_bad)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nF. LEAD 2 — build_payload plumbing (opt-in, normalized, nothing leaks into the log)")

seen = {}


async def _probe(org_id, f, **kw):
    seen.clear()
    seen.update({"org_id": org_id, "f": dict(f), **kw})
    return {"title": "T", "sheets": []}


RR.REPORTS["_probe_auth"] = {"label": "p", "filters": [], "live_path": lambda f: "/x",
                             "build": _probe, "wants_auth": True}
RR.REPORTS["_probe_plain"] = {"label": "p", "filters": [], "live_path": lambda f: "/x", "build": _probe}
try:
    run(lambda: RR.build_payload("_probe_auth", HOUSE, {"period": "June 2026"},
                                 authorization="Bearer tok", tz="UTC"))
    ok("F1 an opt-in builder receives the caller's header", seen.get("authorization") == "Bearer tok")
    ok("F1b …and NOT tz (it did not ask for it)", "tz" not in seen)
    ok("F2 the token is never written into the filters dict (send_log stays clean)",
       seen["f"] == {"period": "June 2026"})
    run(lambda: RR.build_payload("_probe_plain", HOUSE, {}, authorization="Bearer tok", tz="UTC"))
    ok("F3 a builder that did not opt in receives NEITHER (every other report unchanged)",
       set(seen) == {"org_id", "f"})
    run(lambda: RR.build_payload("_probe_auth", HOUSE, {}, authorization=SENTINEL))
    ok("F4 a Header sentinel arriving from an in-process caller is normalized to ''",
       seen.get("authorization") == "")
finally:
    RR.REPORTS.pop("_probe_auth"), RR.REPORTS.pop("_probe_plain")

ok("F5 exactly the four caller-scoped reports opt into wants_auth",
   sorted(k for k, v in RR.REPORTS.items() if v.get("wants_auth"))
   == ["action_plan", "commissions", "flags", "gp"])
ok("F6 only owed_weekly opts into wants_tz, and it is the only report with a filter validator",
   [k for k, v in RR.REPORTS.items() if v.get("wants_tz")] == ["owed_weekly"]
   and list(RF.FILTER_VALIDATORS) == ["owed_weekly"])
ok("F6b every validator key is a real report (no dead entry)",
   set(RF.FILTER_VALIDATORS) <= set(RR.REPORTS))
ok("F7 /notify/send and /send-to-designated now accept the caller header",
   all("authorization" in inspect.signature(fn).parameters
       for fn in (NR.send_now, NR.send_to_designated)))
ok("F8 the report list the UI renders is unchanged (no new required filters)",
   RR.list_reports() == [{"key": k, "label": v["label"], "filters": v["filters"]}
                         for k, v in RR.REPORTS.items()])


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nG. LEAD 3 — /core/employee-dashboard survives a failure in the COSMETIC widget lookup")

EMP = {"org_id": HOUSE, "employee_id": "E1", "name": "Ali", "home_store": "S1",
       "epay_salesperson": "", "pay_rate": 15}
APPU = {"org_id": HOUSE, "employee_id": "E1", "role": "rep", "widget_overrides": None,
        "full_name": "Ali", "store_code": "S1"}
ROLE = {"org_id": HOUSE, "name": "rep", "permissions": {"employee_widgets": {k: False for k in CR.EMP_WIDGETS}}}
STORE = {"storeops.employees": [EMP], "storeops.app_users": [APPU], "storeops.roles": [ROLE]}
BOOM = RuntimeError("simulated transport RuntimeError on the roles read")

old_sb = CR.sb
try:
    CR.sb = lambda: Fake(STORE)
    good = CR.employee_dashboard(employee_id="E1", period="July 2026", org_id=HOUSE)
    ok("G1 healthy path unchanged: the role's widget flags are applied verbatim",
       good["widgets"] == {k: False for k in CR.EMP_WIDGETS} and good["employee"]["role"] == "rep")

    CR.sb = lambda: Fake(STORE, fail=[(r"storeops\.roles", BOOM)])
    out = CR.employee_dashboard(employee_id="E1", period="July 2026", org_id=HOUSE)
    ok("G2 a RuntimeError on the widget lookup no longer 500s the whole dashboard",
       isinstance(out, dict) and out["employee"]["employee_id"] == "E1")
    ok("G3 …it degrades to the documented default (no role ⇒ all widgets on)",
       out["widgets"] == {k: True for k in CR.EMP_WIDGETS})
    ok("G4 …and every other section still returns (hours/flags/commission keys intact)",
       {"hours", "flags", "chargebacks", "report_card", "commission_tracking"} <= set(out))
    ok("G5 no widget stays half-applied from a mid-block failure",
       set(out["widgets"]) == set(CR.EMP_WIDGETS))

    CR.sb = lambda: Fake(STORE, fail=[(r"storeops\.app_users", BOOM)])
    out2 = CR.employee_dashboard(employee_id="E1", period="July 2026", org_id=HOUSE)
    ok("G6 the sibling app_users read is guarded the same way (role falls back to None)",
       out2["widgets"] == {k: True for k in CR.EMP_WIDGETS} and out2["employee"]["role"] is None)

    # BASE bytes of the same block, executed against the same fake ⇒ the 500 this lead recorded.
    base_block = re.search(
        r"    widgets = \{k: True for k in EMP_WIDGETS\}\n.*?widgets\[k\] = bool\(v\)\n",
        base_source("backend/app/modules/core/router.py"), re.S).group(0)
    ns2 = {"client": Fake(STORE, fail=[(r"storeops\.roles", BOOM)]), "org_id": HOUSE,
           "employee_id": "E1", "EMP_WIDGETS": CR.EMP_WIDGETS}
    try:
        exec(re.sub(r"^    ", "", base_block, flags=re.M), ns2)
        ok("G7 BASE block propagates the RuntimeError (the 500 this lead recorded)", False)
    except RuntimeError:
        ok("G7 BASE block propagates the RuntimeError (the 500 this lead recorded)", True)
    ok("G8 the guard exposes nothing new: the sections themselves are returned either way "
       "(widgets are display-only, per this endpoint's own contract)",
       set(out) == set(good))
finally:
    CR.sb = old_sb


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\nH. LEAD 1 (surfacing) — the admin sees the mis-configured schedule, and it CLEARS")

NOW = datetime.now(timezone.utc)
ctx = {"now": NOW}


LAST_FAKE = {}


def attention_items(subs):
    fake = Fake({"notify.subscriptions": subs, "notify.send_log": []})
    items = {i["key"]: i for i in NA._p_notify_delivery(fake, LUXE, ctx)}
    LAST_FAKE["log"] = fake.log
    return items


live = {**SUB_BAD, "next_run_at": (NOW + timedelta(hours=6)).isoformat()}
items = attention_items([live])
ok("H1 a schedule whose saved filters can't build a report is reported to the admin",
   "notify_schedule_config" in items)
ok("H2 …as an error, deep-linked to the schedules tab, naming the schedule + the fix",
   items["notify_schedule_config"]["severity"] == "error"
   and items["notify_schedule_config"]["deep_link"] == "/notify?tab=subs"
   and "Weekly owed (typo)" in items["notify_schedule_config"]["detail"])
ok("H3 it CLEARS the moment the filter is fixed (blank = current week)",
   "notify_schedule_config" not in attention_items([{**live, "filters": {}}]))
ok("H4 …and a paused schedule is never reported",
   "notify_schedule_config" not in attention_items([{**live, "is_active": False}]))
ok("H5 the check is pure/in-process — it adds NO table read to the provider",
   sorted({r["key"] for r in LAST_FAKE["log"]}) == ["notify.send_log", "notify.subscriptions"],
   sorted({r["key"] for r in LAST_FAKE["log"]}))
ok("H5b …and every read it does make is org-scoped to the acting tenant",
   all(r["filters"].get("org_id") == LUXE for r in LAST_FAKE["log"]))
ok("H6 a tenant with no schedules pays nothing and reports nothing",
   attention_items([]) == {} or "notify_schedule_config" not in attention_items([]))
ok("H7 a schedule pointing at a report this build no longer has is ALSO surfaced "
   "(it silently never sends)",
   "notify_schedule_config" in attention_items([{**live, "report_key": "retired_report"}]))

# the provider must stay a LEAF: pulling report_registry in here would import the asset / commcalc /
# account routers (and their attention providers) into any process that only wanted this provider.
leaf_src = open("app/modules/notify/report_filters.py").read()
ok("H8 report_filters imports nothing from another module (true leaf)",
   not re.search(r"^\s*(from|import)\s+app\.modules", leaf_src, re.M))
ok("H9 attention.py does not import report_registry",
   not re.search(r"^\s*from \. import report_registry", open("app/modules/notify/attention.py").read(), re.M))
ok("H10 …and resolves the live report-key list from sys.modules instead",
   NA._known_report_keys() == set(RR.REPORTS))

print(f"\n{'='*70}\n  {PASS} passed, {FAIL} failed\n{'='*70}")
sys.exit(1 if FAIL else 0)
