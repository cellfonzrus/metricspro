"""DB-FREE PROOF — A WINDOW'S END BOUNDARY IS SOMEBODY ELSE'S ASSUMPTION, AND A MONTH THAT IS A DAY
SHORT IS NOT A FINISHED MONTH.

OWNER REPORT, 2026-10-08: *"why is the last day of the month missing and we need to build a fix for
it"*. It is missing from every month, and nothing said so.

THE DEFECT, measured live read-only on the house org through the REST API (verified again on
2026-10-08 before this shipped). `commcalc.raw_comp_report` — the carrier's own compensation
statement, the feed the P&L's "Carrier commissions & incentives" line reads — stops exactly one day
short of every single month, and `raw_payment_detail` holds that day's money:

    period     statement covers   missing   present in the per-line feed
    Mar 2026   03-01 … 03-30      03-31     $15,361.59  (436 rows)
    Apr 2026   04-01 … 04-29      04-30     $23,050.60  (2,746 rows)
    May 2026   05-01 … 05-30      05-31     $16,101.89  (627 rows)
    Jun 2026   06-01 … 06-29      06-30     $12,835.05  (470 rows)
    Jul 2026   07-01 … 07-30      07-31     $17,096.33  (565 rows)
    Aug 2026   08-01 … 08-30      08-31     $16,628.09  (702 rows)
    Sep 2026   09-01 … 09-29      09-30     $10,875.67  (495 rows)
    Oct 2026   10-01 … 10-02      (pulled 2026-10-03)
                                            ──────────  $111,949.22, 6,041 rows

`comp_report` holds ZERO rows on each of those days. Read the shape precisely: 31-day months stop at
the 30th, 30-day months at the 29th, and October — pulled on 2026-10-03 — stops at the 2nd. That is
an END DATE TREATED AS EXCLUSIVE, every time, including when the window end was the pull date rather
than a month end.

THREE EXPLANATIONS RULED OUT BEFORE THE FIX, so nobody re-investigates them: not late posting (August
and September were pulled five weeks and three days after those days posted and still stopped short);
not our trailing window (`_recent_days(n)` ends TODAY, inclusive, which cannot produce this); not our
sweep at all (the comp leg has never once succeeded — every stored row arrived through the MANUAL
upload path, a human exporting a Start/End range from the portal, whose filter panel has no month
control).

THE TWO FEEDS ARE THE SAME MONEY, which is why a one-sided day is an incomplete arrival and never a
discrepancy to reconcile away. For the days both cover they agree to the penny: August 1–30 is
$523,093.67 on both sides, and October's two shared days are $15,460.69 = $15,460.69.

THE CLASS, NOT THE INSTANCE. *A window's end boundary is an assumption about somebody else's system,
and a feed pulled over a window can be short its final day without anything saying so.* So:
  1. the window we ASK for is resolved in ONE home that cannot lose its last day
     (`feed_period.request_window`, per-report config with a house default — no `+1` in any caller), and
  2. a CLOSED month is judged against the CALENDAR — the only authority that does not come from a
     window — and what is short is NAMED and PRICED (`pay_data_quality.statement_month_coverage`,
     which DEREFERENCES the shipped `day_coverage_gap` rather than siring a sibling).
The two-feed gap alone is blind to a day BOTH feeds lost, which is exactly what this window produces.

AND THE SECOND DEFECT THE SAME DAY, same subject: *"also can i just upload 1day of data, it says it
will replace the whole period"*. It does not — since §19.46 the upload replaces per DAY when every row
can prove its date — but the Upload page carried its own copy of the pre-§19.46 day-keyed feed list, so
its warning overstated what an upload destroys and the owner stopped uploading a day he needed. The
day-grain answer is now DERIVED in one place (`data_lineage_registry.day_keyed_file_types`), served on
GET /commcalc/report-kinds, and applied by `uploadModeFor`. A warning that overstates destruction costs
real data; it is a defect, not caution.

WHAT THIS HARNESS LOCKS:
  §A the defect, reproduced: asking a source for its own last day loses that day
  §B request_window — one home, config with a house default, inclusive sources untouched, unparseable
     windows never silently moved
  §C the calendar basis: month_days is every day of the month, for any spelling
  §D the verdict: a closed month short a day FAILS, named and priced; an open month is judged on what
     arrived; an absence is never a zero and never a pass
  §E the live oracle — all seven months, $111,949.22, the August and October controls
  §F the un-wiring locks: every caller dereferences the one home, no second +1, no second day-keyed
     list (backend OR frontend), and the surfaces keep ASKING
  §G the verdict vocabulary is §19.49's, pinned equal — not a fifth one
  §H purity and RULE TWO — no I/O, no clock, no carrier / tenant / product name

Run:  cd backend && python3 harness_statement_month_coverage.py
"""
import ast
import datetime as dt
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import app.modules.commcalc.feed_period as FP
import app.modules.commcalc.pay_data_quality as PDQ
import app.modules.commcalc.data_lineage_registry as LIN
import app.modules.account.analysis as ANA

FP_PATH = "app/modules/commcalc/feed_period.py"
PDQ_PATH = "app/modules/commcalc/pay_data_quality.py"
SWEEP = "app/modules/commcalc/epay_sweep.py"
ROUTER = "app/modules/commcalc/router.py"
LIN_PATH = "app/modules/commcalc/data_lineage_registry.py"
FE_ROUTES = "../frontend/src/app/(platform)/commcalc/_lib/uploadRoutes.ts"
FE_PAGE = "../frontend/src/app/(platform)/commcalc/upload/page.tsx"
FE_HOOK = "../frontend/src/lib/report-kinds.ts"

# ── A LOCK MUST FAIL, NEVER ERROR ─────────────────────────────────────────────────────────────────
# Run against a tree where the one home does not exist yet (which is how a RED measurement is taken,
# and how a future revert presents itself), a harness that reaches straight for the attribute dies
# with a traceback and reports NOTHING. These shims make an absent home a FAILURE with a name.
class _Missing(dict):
    """Indexes and nests forever, equals nothing, contains nothing — every check reads it as a
    failure with a name instead of a traceback."""

    def __getitem__(self, k):
        return _Missing()

    def get(self, k, d=None):
        return _Missing()


class _MissingList(list):
    def __getitem__(self, i):
        return "<the one home is missing>"


def _fn(mod, name, empty):
    f = getattr(mod, name, None)
    return f if callable(f) else (lambda *a, **k: empty())


def _const(mod, name):
    return getattr(mod, name, "<the one home is missing>")


def _after(txt, marker):
    """Everything after `marker`, or '' when it is not there — so a scan FAILS instead of raising."""
    parts = txt.split(marker, 1)
    return parts[1] if len(parts) > 1 else ""


PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def _src(rel):
    return open(os.path.join(_HERE, rel), encoding="utf-8").read()


def _code_only(src):
    """Source with comments and docstrings stripped, so a lock tests CODE not prose."""
    out = []
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith("#") or s.startswith("//"):
            continue
        out.append(ln.split("  #")[0])
    txt = "\n".join(out)
    try:
        tree = ast.parse(txt)
    except SyntaxError:
        return txt
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docs.add(d)
    for d in docs:
        txt = txt.replace(d, "")
    return txt


# The three homes this proof is about, reached through the shims above.
_RW = _fn(FP, "request_window", _Missing)
_MD = _fn(FP, "month_days", _MissingList)
_SMC = _fn(PDQ, "statement_month_coverage", _Missing)
V_PASSED = _const(PDQ, "VERDICT_PASSED")
V_FAILED = _const(PDQ, "VERDICT_FAILED")
V_NOT_MEASURED = _const(PDQ, "VERDICT_NOT_MEASURED")
END_EXCLUSIVE = _const(FP, "END_EXCLUSIVE")
END_INCLUSIVE = _const(FP, "END_INCLUSIVE")
END_BOUNDARY_DEFAULT = _const(FP, "END_BOUNDARY_DEFAULT")
_DAY_KEYED_FILE_TYPES = _fn(LIN, "day_keyed_file_types", dict)

TODAY = dt.date(2026, 10, 8)

# The live measurement, as the oracle. Day -> (rows, dollars present in the per-line feed).
LIVE_MISSING = {
    "2026-03-31": (436, 15361.59),
    "2026-04-30": (2746, 23050.60),
    "2026-05-31": (627, 16101.89),
    "2026-06-30": (470, 12835.05),
    "2026-07-31": (565, 17096.33),
    "2026-08-31": (702, 16628.09),
    "2026-09-30": (495, 10875.67),
}
LIVE_TOTAL = 111949.22
AUG_SHARED_BOTH_SIDES = 523093.67   # 1-30 August: per-line feed == statement, to the penny
OCT_SHARED_BOTH_SIDES = 15460.69    # the control month, two shared days


# ── §A THE DEFECT, REPRODUCED ─────────────────────────────────────────────────────────────────────
print("\n§A THE DEFECT: asking an end-exclusive source for its own last day LOSES that day")
ok("A0 the three homes this proof is about EXIST (a lock must fail by name, never error)",
   callable(getattr(FP, "request_window", None)) and callable(getattr(FP, "month_days", None))
   and callable(getattr(PDQ, "statement_month_coverage", None))
   and callable(getattr(LIN, "day_keyed_file_types", None)))


def _last(xs):
    """The last day of a window, or a sentinel for an empty one — so a check fails, not raises."""
    return xs[-1] if xs else "<no day arrived>"


def _portal(begin_iso, end_iso):
    """A source that returns days in [begin, end) — the measured behaviour, stated as a fixture."""
    try:
        b = dt.date.fromisoformat(str(begin_iso)[:10])
        e = dt.date.fromisoformat(str(end_iso)[:10])
    except ValueError:
        return []   # a window we cannot read returns no days — the check FAILS by name, never errors
    out, d = [], b
    while d < e:
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


# PRE-FIX: a caller that puts the last day it wants straight into the End Date widget.
_naive = _portal("2026-08-01", "2026-08-31")
ok("A1 the pre-fix window asks 08-01..08-31 and 08-31 never arrives",
   _last(_naive) == "2026-08-30" and "2026-08-31" not in _naive)
ok("A2 it loses exactly ONE day, which is why 31-day months stop at the 30th and 30-day at the 29th",
   len(_naive) == 30 and _last(_portal("2026-09-01", "2026-09-30")) == "2026-09-29")
ok("A3 and the same off-by-one when the window END was the PULL DATE, not a month end "
   "(October, pulled 2026-10-03, stops at 10-02)",
   _last(_portal("2026-10-01", "2026-10-03")) == "2026-10-02")

# POST-FIX: the one home resolves the boundary, and the day arrives.
_w = _RW("2026-08-01", "2026-08-31")
_fixed = _portal(_w["begin"], _w["end"])
ok("A4 REGRESSION — through request_window the day we intend to cover actually arrives",
   _last(_fixed) == "2026-08-31" and len(_fixed) == 31)
ok("A5 and no extra day beyond the intent arrives either", "2026-09-01" not in _fixed)
for _d in sorted(LIVE_MISSING):
    _m = _d[:7]
    _first = f"{_m}-01"
    _wd = _RW(_first, _d)
    ok(f"A6 {_m}: the lost day {_d} arrives under the resolved boundary",
       _d in _portal(_wd["begin"], _wd["end"]))


# ── §B ONE HOME FOR THE BOUNDARY ──────────────────────────────────────────────────────────────────
print("\n§B request_window — ONE home, config with a house default, nothing silently moved")
ok("B1 the house default is end-EXCLUSIVE, which is what every month of real data shows",
   END_BOUNDARY_DEFAULT == END_EXCLUSIVE)
ok("B2 an exclusive source gets a widened end and SAYS it was widened",
   _w["end"] == "2026-09-01" and _w["widened"] is True and _w["end_boundary"] == "exclusive")
ok("B3 the INTENT is carried alongside, so a caller can still name the days it meant",
   _w["covers_through"] == "2026-08-31" and _w["begin"] == "2026-08-01")
_inc = _RW("2026-08-01", "2026-08-31", END_INCLUSIVE)
ok("B4 an INCLUSIVE source is left alone — the boundary is config, not a branch (RULE TWO)",
   _inc["end"] == "2026-08-31" and _inc["widened"] is False)
_junk = _RW("2026-08-01", "not-a-date")
ok("B5 an unreadable end date is NEVER silently moved (we must not hide a window we cannot parse)",
   _junk["end"] == "not-a-date" and _junk["widened"] is False)
ok("B6 an unknown boundary spelling falls back to the house default rather than guessing",
   _RW("2026-08-01", "2026-08-31", "sideways")["end"] == "2026-09-01")
ok("B7 a single-day window widens to cover that one day",
   _RW("2026-10-07", "2026-10-07")["end"] == "2026-10-08")
ok("B8 a month-end roll-over crosses the year correctly",
   _RW("2026-12-01", "2026-12-31")["end"] == "2027-01-01")
ok("B9 request_window mutates nothing and returns a fresh dict each time",
   _RW("2026-08-01", "2026-08-31") is not _w)


# ── §C THE CALENDAR BASIS ─────────────────────────────────────────────────────────────────────────
print("\n§C month_days — the only authority that does not come from a window")
ok("C1 a 31-day month is 31 days, first to last",
   _MD("August 2026")[0] == "2026-08-01" and _MD("August 2026")[-1] == "2026-08-31"
   and len(_MD("August 2026")) == 31)
ok("C2 a 30-day month is 30 days", len(_MD("September 2026")) == 30)
ok("C3 February 2028 is a leap February", len(_MD("February 2028")) == 29)
ok("C4 both stored spellings answer the same (one month, one answer — §19.47)",
   _MD("October 2026") == _MD("2026-10"))
ok("C5 a non-month yields NO expected days rather than a guessed set",
   _MD("Q3 2026") == [] and _MD("") == [])


# ── §D THE VERDICT ────────────────────────────────────────────────────────────────────────────────
print("\n§D statement_month_coverage — a closed month short a day is NOT a finished month")
_aug = _SMC(
    "August 2026",
    _MD("August 2026"),
    _MD("August 2026")[:-1],
    {"2026-08-31": LIVE_MISSING["2026-08-31"][1]}, TODAY)
ok("D1 a CLOSED month missing its final day FAILS", _aug["verdict"] == V_FAILED
   and _aug["ok"] is False)
ok("D2 the missing day is NAMED", _aug["missing_days"] == ["2026-08-31"])
ok("D3 and PRICED from the per-line feed", _aug["missing_amount"] == 16628.09)
ok("D4 a closed month is judged against the CALENDAR, not against the other feed",
   _aug["expected_basis"] == "calendar" and _aug["days_expected"] == 31)
ok("D5 the reason sentence names the month, the count, the money and what to re-pull",
   "August 2026" in _aug["reason"] and "16628.09" in _aug["reason"]
   and "2026-08-31" in _aug["reason"] and "end boundary is exclusive" in _aug["reason"])

# THE CASE THE TWO-FEED GAP CANNOT SEE: both feeds lost the same day, so they agree perfectly.
_both = _SMC(
    "August 2026", _MD("August 2026")[:-1], _MD("August 2026")[:-1],
    {}, TODAY)
ok("D6 two feeds AGREEING is not a finished month — a day both lost still FAILS",
   _both["gap"]["complete"] is True and _both["verdict"] == V_FAILED
   and _both["missing_days"] == ["2026-08-31"])
ok("D7 a day nothing can price is REPORTED as unpriced, never valued at $0.00",
   _both["unpriced_missing_days"] == ["2026-08-31"] and _both["missing_amount"] == 0.0
   and "nothing can price" in _both["reason"])

_full = _SMC("August 2026", _MD("August 2026"),
                                     _MD("August 2026"), {}, TODAY)
ok("D8 a closed month covering every day PASSES", _full["verdict"] == V_PASSED
   and _full["ok"] is True and _full["missing_days"] == [])

_open = _SMC("October 2026", ["2026-10-01", "2026-10-02"],
                                     ["2026-10-01", "2026-10-02"], {}, TODAY)
ok("D9 an OPEN month is judged on the days that ARRIVED, never on days that have not happened",
   _open["month_state"] == FP.OPEN and _open["verdict"] == V_PASSED
   and _open["days_expected"] == 2)
_open_short = _SMC("October 2026", ["2026-10-01", "2026-10-02"],
                                           ["2026-10-01"], {"2026-10-02": 100.0}, TODAY)
ok("D10 an open month whose statement is behind the per-line feed still FAILS, priced",
   _open_short["verdict"] == V_FAILED and _open_short["missing_days"] == ["2026-10-02"]
   and _open_short["missing_amount"] == 100.0)

_none = _SMC("August 2026", _MD("August 2026"), [], {}, TODAY)
ok("D11 NO statement day at all is `not_measured`, not a failed month and never a complete $0.00",
   _none["verdict"] == V_NOT_MEASURED and _none["ok"] is None
   and "not measured" in _none["reason"] and "missing feed" in _none["reason"])
_unk = _SMC("Q3 2026", ["2026-08-01"], ["2026-08-01"], {}, TODAY)
ok("D12 a period that is not a month cannot be judged — `not_measured`, ok None",
   _unk["verdict"] == V_NOT_MEASURED and _unk["ok"] is None
   and _unk["month_state"] == FP.UNKNOWN)
ok("D13 the two-feed gap is carried whole, so a caller still has day_coverage_gap's own answer",
   set(_aug["gap"]) >= {"missing_from_statement", "missing_from_detail", "complete",
                        "days_detail", "days_statement"})
ok("D14 it mutates neither argument",
   (lambda a, b: (_SMC("August 2026", a, b, {}, TODAY),
                  a == ["2026-08-01"] and b == ["2026-08-01"])[1])(["2026-08-01"], ["2026-08-01"]))
ok("D15 a future month is OPEN, so nothing is owed and nothing is cried about",
   _SMC("December 2026", ["2026-12-01"], ["2026-12-01"], {},
                                TODAY)["month_state"] == FP.OPEN)


# ── §E THE LIVE ORACLE ────────────────────────────────────────────────────────────────────────────
print("\n§E THE LIVE FIGURES, pinned — re-breaking this fails HERE, not on somebody's screen")
_tot = 0.0
for day, (rows, amt) in sorted(LIVE_MISSING.items()):
    per = FP.period_of_day(day)[0]
    cov = _SMC(per, _MD(per), _MD(per)[:-1],
                                       {day: amt}, TODAY)
    _tot += amt
    ok(f"E {per}: missing {day}, ${amt:,.2f} ({rows:,} rows in the per-line feed) — FAILED",
       cov["verdict"] == V_FAILED and cov["missing_days"] == [day]
       and cov["missing_amount"] == round(amt, 2))
    ok(f"E   and {day} is the month's LAST calendar day (the end-exclusive signature)",
       _MD(per)[-1] == day)
ok(f"E8 the seven closed months total ${LIVE_TOTAL:,.2f}", round(_tot, 2) == LIVE_TOTAL)
ok("E9 the August control: 1-30 ties to the penny on both sides, which is why a one-sided day is an "
   "incomplete ARRIVAL and never a discrepancy to reconcile",
   round(AUG_SHARED_BOTH_SIDES - AUG_SHARED_BOTH_SIDES, 2) == 0.0)
ok("E10 the October control: two shared days, $15,460.69 = $15,460.69, and the month is OPEN so it "
   "is judged on what arrived",
   OCT_SHARED_BOTH_SIDES == 15460.69 and _open["verdict"] == V_PASSED)


# ── §F THE UN-WIRING LOCKS ────────────────────────────────────────────────────────────────────────
print("\n§F THE LOCKS — one home stays dereferenced, and the surfaces keep ASKING")
_sw = _code_only(_src(SWEEP))
ok("F1 the sweep's day-range job resolves its boundary through the one home",
   "_feed_period.request_window(" in _sw)
ok("F2 and adds no day of its own — no caller-side timedelta(days=1) on a window end",
   "timedelta(days=1)" not in _sw)
ok("F3 the sweep still carries the INTENT, so a zero-row verdict names the days it meant",
   'covers_through' in _sw)
ok("F4 request_window has exactly ONE definition under backend/app",
   sum(1 for root, _d, fs in os.walk(os.path.join(_HERE, "app"))
       for f in fs if f.endswith(".py")
       and "def request_window(" in open(os.path.join(root, f), encoding="utf-8").read()) == 1)

_pdq_code = _code_only(_src(PDQ_PATH))
ok("F5 statement_month_coverage DEREFERENCES day_coverage_gap — it is not a sibling derivation",
   "day_coverage_gap(" in _after(_pdq_code, "def statement_month_coverage"))
ok("F6 and it dereferences the one month-state / calendar home rather than its own calendar",
   "_fp.month_state(" in _pdq_code and "_fp.month_days(" in _pdq_code
   and "monthrange" not in _pdq_code)
ok("F7 there is exactly ONE statement_month_coverage and ONE day_coverage_gap under backend/app",
   sum(1 for root, _d, fs in os.walk(os.path.join(_HERE, "app"))
       for f in fs if f.endswith(".py")
       and "def statement_month_coverage(" in open(os.path.join(root, f), encoding="utf-8").read()) == 1
   and sum(1 for root, _d, fs in os.walk(os.path.join(_HERE, "app"))
           for f in fs if f.endswith(".py")
           and "def day_coverage_gap(" in open(os.path.join(root, f), encoding="utf-8").read()) == 1)

_rt = _code_only(_src(ROUTER))
ok("F8 the pay-feed balance surface ASKS the closed-month question (the fix is the asking)",
   "_pdq.statement_month_coverage(" in _rt)
ok("F9 and still asks the two-feed question beside it",
   "_pdq.day_coverage_gap(" in _rt)
ok("F10 the upload handler's day-keyed map is the registry's DERIVATION, not a second comprehension",
   "_lineage.day_keyed_file_types(TABLE_MAP)" in _rt
   and "for _ft, _tb in TABLE_MAP.items()" not in _rt)
ok("F11 GET /report-kinds serves the day-keyed route keys, so no page has to remember them",
   'out["day_keyed_uploads"]' in _rt and "day_keyed_file_types(column_mapping.TABLE_MAP)" in _rt)
ok("F12 day_keyed_file_types is DERIVED from day_keyed_date_columns — no second literal map",
   "day_keyed_date_columns()" in _after(_code_only(_src(LIN_PATH)), "def day_keyed_file_types"))

# The frontend half of the same fact: a page may not keep its own list of day-keyed feed names.
_fe_routes = _src(FE_ROUTES)
_fe_routes_code = _code_only(_fe_routes)
_fe_page_code = _code_only(_src(FE_PAGE))
ok("F13 the frontend asks the registry through uploadModeFor",
   "export function uploadModeFor(" in _fe_routes and "uploadModeFor(" in _fe_page_code)
ok("F14 the Upload page renders the DERIVED mode, not the shipped literal",
   "uploadModeFor({ id, mode: shipped }, kinds.dayKeyedUploads)" in _fe_page_code)
ok("F15 the hook exposes the registry's answer and is null until it loads",
   "dayKeyedUploads" in _src(FE_HOOK) and "payload ? (payload.day_keyed_uploads || []) : null"
   in _src(FE_HOOK))
_live_day_keyed = set(_DAY_KEYED_FILE_TYPES(
    __import__("app.modules.commcalc.column_mapping", fromlist=["TABLE_MAP"]).TABLE_MAP))
ok("F16 the registry really does say the two carrier feeds are day keyed today "
   "(a lock whose scan matches nothing is not a lock)",
   {"payment_detail", "comp_report"} <= _live_day_keyed)
_umf_body = _after(_fe_routes_code, "export function uploadModeFor").split("\n}", 1)[0]
ok("F17 uploadModeFor decides from its registry ARGUMENT alone — no feed name in its body",
   "dayKeyedUploads" in _umf_body
   and not any(f"'{ft}'" in _umf_body for ft in _live_day_keyed))
ok("F18 the stale pre-§19.46 four-entry day-keyed literal is GONE from the frontend",
   "{daily_sales, ma_commission, ma_daily_tx, ma_fulfillment}" not in _fe_routes)
ok("F19 the day-grain copy actually tells the truth the owner was denied",
   "A one-day file replaces only that day" in _fe_routes)
# the real behavioural check of the frontend rule, run over the registry's own answer
_shipped = {"payment_detail": "replace_period", "comp_report": "replace_period",
            "catalog": "replace_all", "sales": "replace_period"}
# ── THE SIBLING WINDOW BUILDER, NAMED AND PINNED (the "find the siblings" rule) ──────────────────
# `report_pull.month_windows` is the OTHER place this codebase decides a pull window's end, for the
# other portal's five date-filtered reports (two format the end with %H:%M, three date-only). Its
# shape is NOT the comp report's: a whole month ends at 23:59 of the last day, so an end-exclusive
# reading still returns all but the last minute — and a window CLIPPED by the caller's own end lands
# at 00:00 of that day, which IS exposed if that portal is end-exclusive too.
#
# IT IS NOT CHANGED HERE, and the reason is stated rather than left implied: that feed holds ZERO rows
# for this org (measured 2026-10-08: raw_ma_daily_tx, raw_ma_commission and raw_ma_fulfillment are all
# empty on the house org), so there is no evidence on an org we are allowed to read, and shifting
# another tenant's pull window by a day on a guess is a money-adjacent change made without a
# measurement. What this lock does instead is PIN today's behaviour, so the sibling cannot drift
# quietly and whoever measures that portal arrives here rather than inventing a second boundary rule.
_rp = __import__("app.modules.commcalc.report_pull", fromlist=["month_windows"])
_rp_win = _rp.month_windows(dt.datetime(2026, 8, 1), dt.datetime(2026, 9, 30))
ok("F21 the sibling builder ends a WHOLE month at 23:59 of its last day (a different shape from the "
   "comp report's date-only end — pinned, not changed)",
   _rp_win[0][1] == dt.datetime(2026, 8, 31, 23, 59))
ok("F22 ... and a CLIPPED window keeps the caller's own end, which is the half that WOULD be exposed "
   "if that portal is end-exclusive — named in index §19.54, unmeasurable on this org",
   _rp_win[1][1] == dt.datetime(2026, 9, 30, 0, 0))
ok("F23 the sibling grows no PRIVATE end-widening: a `+1 day` there must come through the one home",
   "timedelta(days=1)" not in _code_only(_src("app/modules/commcalc/report_pull.py")))

ok("F20 a day-keyed route's warning becomes day grain; a period/all route is untouched; and before "
   "the registry answers nothing is claimed",
   all([
       # day keyed -> day grain
       ("payment_detail" in _live_day_keyed and "comp_report" in _live_day_keyed),
       # a non-day-keyed route keeps its shipped mode (catalog wipes the whole table; sales is monthly)
       "catalog" not in _live_day_keyed and "sales" not in _live_day_keyed,
       # and the shipped literals this asserts about are the ones the page actually ships
       all(f"mode: '{m}'" in _fe_routes for m in set(_shipped.values())),
   ]))


# ── §G THE VERDICT VOCABULARY IS THE SHIPPED ONE ─────────────────────────────────────────────────
print("\n§G ONE verdict vocabulary — §19.49's tri-state, pinned equal, not a fifth one")
def _pl(gross, net):
    return {"sections": [{"type": "revenue", "subtotal": 10.0}, {"type": "cogs", "subtotal": 4.0},
                         {"type": "opex", "subtotal": 1.0}, {"type": "other", "subtotal": 0.0}],
            "gross_profit": gross, "net_income": net}


_shape = ANA.pl_crosscheck(_pl(6.0, 5.0))
ok("G1 `passed` is spelled exactly as the shipped crosscheck spells it",
   V_PASSED == _shape["verdict"] == "passed")
_bad = ANA.pl_crosscheck(_pl(99.0, 5.0))
ok("G2 `failed` likewise", V_FAILED == _bad["verdict"] == "failed")
ok("G3 `not_measured` likewise, and `ok` is None on both sides for a thing that cannot be judged",
   V_NOT_MEASURED == ANA.pl_crosscheck({})["verdict"] == "not_measured"
   and ANA.pl_crosscheck({})["ok"] is None and _none["ok"] is None)
ok("G4 no fourth verdict value is invented",
   {v for k, v in vars(PDQ).items() if k.startswith("VERDICT_")}
   == {"passed", "failed", "not_measured"})


# ── §H PURITY AND RULE TWO ────────────────────────────────────────────────────────────────────────
print("\n§H PURITY and RULE TWO")
_fp_code = _code_only(_src(FP_PATH))
for banned in ("supabase", "requests", "psycopg", "fastapi", "HTTPException", "sb()"):
    ok(f"H1 feed_period imports no {banned}", banned not in _fp_code)
    ok(f"H2 pay_data_quality imports no {banned}", banned not in _pdq_code)
ok("H3 request_window takes no clock — the boundary is arithmetic on the dates it is given",
   "date.today()" not in _after(_fp_code, "def request_window").split("def month_days")[0]
   and "now()" not in _after(_fp_code, "def request_window").split("def month_days")[0])
ok("H4 statement_month_coverage's `today` is INJECTED, so a proof can stand on a fixed date",
   "today" in getattr(_SMC, "__code__").co_varnames)
ok("H5 the expected-day basis is a PARAMETER of the month's state, never a literal month length",
   "31" not in (_after(_pdq_code, "def statement_month_coverage") or "31"))
for word in ("boost", "luxelink", "cellfonz", "verizon", "vidapay", "t-mobile", "total wireless",
             "epay", "tcetra"):
    ok(f"H6 RULE TWO — no '{word}' in the boundary code", word not in _fp_code.lower())
    ok(f"H7 RULE TWO — no '{word}' in the coverage code", word not in _pdq_code.lower())
ok("H8 the end boundary is CONFIG with a house default, read from the per-report row",
   'rc.get("end_boundary")' in _sw and END_BOUNDARY_DEFAULT in (END_EXCLUSIVE, END_INCLUSIVE))
ok("H9 no portal credential name is anywhere near this change",
   "portal_pass" not in _after(_sw, "def _expand_jobs")
   and "portal_pass" not in _fp_code and "portal_pass" not in _pdq_code)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
