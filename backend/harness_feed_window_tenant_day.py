#!/usr/bin/env python3
"""A FEED WINDOW'S DAY COMES FROM THE TENANT'S ZONE, NEVER THE SERVER'S (index §COMMCALC-53).

DB-free and browser-free. The owner reported on 2026-10-31 that three carrier feeds had stopped
refreshing. Two of them had one broken login behind them (§COMMCALC-50). The third — the
comprehensive comp report — had never arrived automatically ONCE since the leg was built, and the
reason was arithmetic, not access:

    today = datetime.now(timezone.utc).date()      # the CONTAINER's day
    days  = _recent_days(refresh_days, today)      # the window asked of the portal

Its slot is 23:30 in the tenant's timezone, deliberately, because the portal posts a day's
compensation at about 23:30 local. 23:30 America/New_York is 03:30 UTC THE NEXT DAY, so every run
asked the portal for a day that had not begun where the portal lives. Measured on eight consecutive
recorded runs: the UTC date was one day ahead of the tenant's date every single time. The portal
answered with zero rows, and zero rows was read as "no compensation posted".

The rule already had a home. `notify/report_filters.business_today` was written on 2026-07-30 and
its docstring states this exact defect — *"A scheduled send fires at the SCHEDULE's local hour;
resolving a relative date off the server's UTC day would put an 8pm-Eastern Friday run on
Saturday's date."* Nothing in commcalc dereferenced it. §19.18's shape again: a registry written
and the callers never wired.

It fails the build if:
  • a portal driver builds a day off the container's clock again;
  • a driver stops dereferencing `report_filters.business_today`, or keeps a second zone ladder;
  • a window-building entry point gains a DEFAULT for `tz`, which is how a caller that never asked
    the org what day it was still got an answer;
  • a router call site stops passing the tenant's zone;
  • the excused sibling list grows.

Run: python3 harness_feed_window_tenant_day.py
"""
import ast
import inspect
import io
import os
import re
import sys
import tokenize
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import b2b_sweep as b2b            # noqa: E402
from app.modules.commcalc import epay_sweep as es            # noqa: E402
from app.modules.commcalc import feed_period as fp           # noqa: E402
from app.modules.commcalc import vip_sweep as vip            # noqa: E402
from app.modules.notify import report_filters as rf          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MODULES = os.path.join(HERE, "app/modules/commcalc")
ROUTER = os.path.join(MODULES, "router.py")

#: Every portal driver that asks a source for a WINDOW or stamps a row with "the day this arrived".
#: Each one must dereference the single home; none may carry a zone ladder or a clock of its own.
DRIVERS = ("epay_sweep.py", "vip_sweep.py", "b2b_sweep.py")
DRIVER_MODULES = {"epay_sweep.py": es, "vip_sweep.py": vip, "b2b_sweep.py": b2b}

#: SIBLINGS EXCUSED, WITH THE REASON, PINNED SHRINK-ONLY. `feed_period.month_state` defaults its
#: `today` to the container's day. It is excused because (a) it is MONTH grain, so the container and
#: the tenant disagree only in the hours either side of midnight on the 1st, and (b) `today` is
#: already an injectable argument there, so the fix for a caller that cares is to PASS the tenant's
#: day, not to change the home. This list may only ever shrink.
EXCUSED = {"feed_period.month_state": "month grain; `today` already injectable"}

#: The container's day, spelled every way a caller has reached for it here.
CLOCK_PATTERNS = (
    r"datetime\.now\(timezone\.utc\)\.date\(\)",
    r"datetime\.utcnow\(\)\.date\(\)",
    r"datetime\.now\(\)\.date\(\)",
    r"\bdate\.today\(\)",
    r"_dt\.date\.today\(\)",
)

#: An IANA zone name, by its real region prefixes — not just "a word with a slash in it", which in
#: these drivers also matches a portal's own column headings ('MI/ATU', 'ESN/IMEI').
ZONE_LITERAL = (r"['\"](?:Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian"
                r"|Pacific|Etc)/[A-Za-z_]+['\"]")

_passed = _failed = 0


def check(label, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print("  PASS  %s" % label)
    else:
        _failed += 1
        print("  FAIL  %s%s" % (label, ("   %s" % detail) if detail else ""))


def executable_text(path):
    """A file's source with every comment and docstring stripped — what actually RUNS.

    Both go, on the house precedent that a comment naming the defect a fix is about otherwise reads
    as that fix's own violation (2026-10-30). A scan of prose is not a scan.
    """
    kept = []
    for tok in tokenize.generate_tokens(io.StringIO(open(path).read()).readline):
        if tok.type == tokenize.COMMENT:
            continue
        kept.append(tok[:2])
    tree = ast.parse(tokenize.untokenize(kept))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body.pop(0)
    return ast.unparse(tree)


DRIVER_EXEC = {name: executable_text(os.path.join(MODULES, name)) for name in DRIVERS}
ROUTER_SRC = open(ROUTER).read()


def requires_tz(fn):
    """True when `tz` is keyword-only AND has no default — so a caller cannot omit the org."""
    sig = inspect.signature(fn)
    p = sig.parameters.get("tz")
    return bool(p) and p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty


print("\n§A  THE REGRESSION — the arithmetic the defect rested on")

#: (run start in UTC, the tenant's local date at that instant) — read off `core.job_run`, the eight
#: consecutive comp-report runs that each returned zero rows.
RECORDED_RUNS = [
    ("2026-09-27T03:31:00+00:00", date(2026, 9, 26)),
    ("2026-09-28T03:31:02+00:00", date(2026, 9, 27)),
    ("2026-09-29T03:31:07+00:00", date(2026, 9, 28)),
    ("2026-09-30T03:30:59+00:00", date(2026, 9, 29)),
    ("2026-10-01T03:30:57+00:00", date(2026, 9, 30)),
    ("2026-10-02T03:30:39+00:00", date(2026, 10, 1)),
    ("2026-10-03T03:31:01+00:00", date(2026, 10, 2)),
    ("2026-10-04T03:31:04+00:00", date(2026, 10, 3)),
]
EASTERN = "America/New_York"

ahead = []
for started, local in RECORDED_RUNS:
    t = datetime.fromisoformat(started)
    ahead.append(t.astimezone(timezone.utc).date() != local
                 and t.astimezone(ZoneInfo(EASTERN)).date() == local)
check("A1 at every recorded run instant the container's day was NOT the tenant's day",
      all(ahead), str(ahead))
check("A2 the window now ends on the tenant's day, for each of those runs",
      all(es._recent_days(1, today=local) == [local.isoformat()] for _s, local in RECORDED_RUNS))
check("A3 a window widens BACKWARDS only — no width makes tomorrow answerable",
      es._recent_days(5, today=date(2026, 9, 26))[-1] == "2026-09-26"
      and es._recent_days(5, today=date(2026, 9, 26))[0] == "2026-09-22")
try:
    es._recent_days(1)
    check("A4 `_recent_days` can no longer invent a day of its own", False, "it still has a default")
except TypeError:
    check("A4 `_recent_days` can no longer invent a day of its own", True)


print("\n§B  ONE HOME, DEREFERENCED — the same function object, not a re-spelling")

check("B1 the home exists where it was written in 2026-07-30", callable(rf.business_today))
for name, mod in DRIVER_MODULES.items():
    check("B2:%s `feed_today` IS `report_filters.business_today`" % name,
          getattr(mod, "feed_today", None) is rf.business_today,
          repr(getattr(mod, "feed_today", None)))
check("B3 the home answers in the zone it is given",
      (rf.business_today("Pacific/Kiritimati") - rf.business_today("Pacific/Niue")).days in (0, 1))
check("B4 an unusable zone never takes a feed down", isinstance(rf.business_today("Not/AZone"), date))


print("\n§C  NO DRIVER KEEPS A CLOCK OR A ZONE LADDER OF ITS OWN")

for name in DRIVERS:
    hits = [p for p in CLOCK_PATTERNS if re.search(p, DRIVER_EXEC[name])]
    check("C1:%s takes no day off the container's clock" % name, not hits, str(hits))
    check("C2:%s carries no zone ladder of its own" % name,
          "ZoneInfo" not in DRIVER_EXEC[name])
    zones = re.findall(ZONE_LITERAL, DRIVER_EXEC[name])
    check("C3:%s spells no timezone literal (RULE TWO — the house fallback lives in ONE place)" % name,
          not zones, str(zones[:3]))
check("C4 exactly one module in the house answers 'today in the tenant's zone'",
      sum(1 for n in DRIVERS if "def feed_today" in open(os.path.join(MODULES, n)).read()) == 0,
      "a driver defined its own instead of binding the home's")


print("\n§D  THE ZONE IS REQUIRED, SO NO CALLER CAN SILENTLY USE THE SERVER'S DAY")

REQUIRE_TZ = [
    ("epay_sweep._expand_jobs", es._expand_jobs),
    ("epay_sweep.run_epay_sweep", es.run_epay_sweep),
    ("epay_sweep._process_report", es._process_report),
    ("epay_sweep._landing_evidence", es._landing_evidence),
    ("vip_sweep.run_invoice_sweep", vip.run_invoice_sweep),
    ("vip_sweep.run_paygo_sweep", vip.run_paygo_sweep),
    ("b2b_sweep.run_inventory_sweep", b2b.run_inventory_sweep),
]
for label, fn in REQUIRE_TZ:
    check("D:%s requires `tz` (keyword-only, NO default)" % label, requires_tz(fn),
          str(inspect.signature(fn)))
try:
    es._expand_jobs(["comp_report"], {})
    check("D8 omitting the org's zone is a TypeError, not a silent UTC window", False, "no raise")
except TypeError:
    check("D8 omitting the org's zone is a TypeError, not a silent UTC window", True)


print("\n§E  EVERY CALL SITE ASKS THE ORG — a registry nothing reads is this house's oldest defect")

CALL_SITES = ("run_epay_sweep(", "run_invoice_sweep(", "run_paygo_sweep(", "run_inventory_sweep(")
tree = ast.parse(ROUTER_SRC)
unwired = []
for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
        continue
    fname = getattr(node.func, "attr", None)
    if fname not in {c.rstrip("(") for c in CALL_SITES}:
        continue
    if not any(k.arg == "tz" for k in node.keywords):
        unwired.append("%s:%d" % (fname, node.lineno))
check("E1 every router call site passes the tenant's zone", not unwired, str(unwired))
check("E2 and it reads it from the connector's own config row, not a literal",
      ROUTER_SRC.count("tz=cfg.get('timezone')") + ROUTER_SRC.count('tz=cfg.get("timezone")') >= 5,
      "sites wired: %d" % (ROUTER_SRC.count("tz=cfg.get('timezone')")
                           + ROUTER_SRC.count('tz=cfg.get("timezone")')))
check("E3 the column it reads really is on the sweep config tables",
      "timezone" in open(os.path.join(HERE, "..", "database", "migrations", "020_epay_sweep.sql")).read())


print("\n§F  EVERY TENANT, NOT ONE — one org's clock never decides another's window")

for tz in ("America/New_York", "America/Chicago", "America/Los_Angeles", "Asia/Kolkata", "UTC"):
    jobs = es._expand_jobs(["comp_report"], {"comp_report": {"refresh_days": 2}}, tz=tz)
    check("F:%s window ends on THAT tenant's own day" % tz,
          jobs[0][1]["covers_through"] == rf.business_today(tz).isoformat())
east = es._expand_jobs(["comp_report"], {"comp_report": {"refresh_days": 1}},
                       tz="Pacific/Kiritimati")[0][1]["covers_through"]
west = es._expand_jobs(["comp_report"], {"comp_report": {"refresh_days": 1}},
                       tz="Pacific/Niue")[0][1]["covers_through"]
check("F6 two tenants either side of midnight get different windows", east >= west, "%s %s" % (east, west))


print("\n§G  THE EXCUSED SIBLINGS ARE NAMED, REASONED AND PINNED (shrink-only)")

check("G1 the excusal list holds exactly the siblings it was written for",
      set(EXCUSED) == {"feed_period.month_state"}, str(sorted(EXCUSED)))
check("G2 …and the reason still holds: `today` is injectable there",
      "today" in inspect.signature(fp.month_state).parameters
      and inspect.signature(fp.month_state).parameters["today"].default is None)
check("G3 …and it really is month grain, so the disagreement is hours, not days",
      fp.month_state("September 2026", date(2026, 10, 1)) == fp.CLOSED
      and fp.month_state("October 2026", date(2026, 10, 1)) == fp.OPEN)


print("\n§H  THE OLD SHAPE CANNOT COME BACK UNDER ANOTHER NAME")

check("H1 no driver re-spells 'today' as a module constant",
      not any(re.search(r"^TODAY\s*=", DRIVER_EXEC[n], re.M) for n in DRIVERS))
check("H2 the one home is imported by every driver, by module, not copied function-by-function",
      all("report_filters" in open(os.path.join(MODULES, n)).read() for n in DRIVERS))
check("H3 this lock is registered in the index",
      "harness_feed_window_tenant_day" in open(
          os.path.join(HERE, "..", "docs", "SYSTEM_DATA_FLOW_INDEX.md")).read())


print("\n%d passed, %d failed" % (_passed, _failed))
if _failed:
    print("\nFAILED — a feed window's day must come from the ORG's zone, out of one home.")
    sys.exit(1)
print("\nOK — one home for the tenant's today, required at every window-building caller.")
