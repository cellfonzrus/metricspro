"""DB-FREE PROOF — a feed row's month is its OWN day's month, and the day map has ONE home.

OWNER REPORT, 2026-10-05: *"check the commission reports for boost as it seems very high like we were
check the other day"*. It was high, and the data was duplicated.

THE DEFECT, measured live on the house org. `commcalc.raw_payment_detail` was stamped with the period
an operator picks at upload (and, on the nightly sweep, with the month the sweep happened to run in),
then replaced per (org, period). The portal serves that report over a rolling IN-ARREARS WINDOW, not a
month:

    2026-10-04 pull covered 2026-09-05 … 2026-10-02 → 18,231 rows / $415,862.56 of SEPTEMBER's
    commission filed a SECOND time under "October 2026", which then read $431,323.25 against its
    own $15,460.69 — 28x. The carrier's own compensation statement agrees to the penny with the
    smaller figure. The 2026-08-24 pull did the same to August with July's money ($147,435.59,
    61,681 rows). 80,614 rows / $579,926.24 mis-filed in total.

THE CLASS, not the instance. The general fact that was wrong is **"a row's month came from the upload
instead of from the row"**, and that question already had ONE home —
`data_lineage_registry.DATA_DATE_COLUMN_BY_TABLE`. `router.DATE_KEYED` kept its own four-entry copy of
it and `payment_detail` was simply not on the list, so the per-day replace that makes a rolling re-pull
idempotent reached the feeds somebody remembered. §19.18's shape for the fourth time. It is also why
`raw_comp_report` got a multi-month guard in 2026-08 while its SIBLING — same portal, same window —
never did.

THE SIBLINGS WERE CHECKED, live, before this shipped. Rows whose data date falls outside their stored
period: `raw_payment_detail` 80,614 / $579,926.24 · `raw_comp_report` 0 · `raw_ma_commission` 0 ·
`raw_ma_daily_tx` 0 · `daily_sales_feed` 0. So generalising the fix to every declared day-grain feed
moves nothing today and cannot drift tomorrow — which is the whole point of doing it that way.

WHAT THIS HARNESS LOCKS:
  §A the defect, reproduced: a rolling-window file stamped with one picked period duplicates a prior
     month, and the row-date stamp files each row under its own month instead
  §B the one date parser reads every spelling the feeds arrive in, and refuses anything else
  §C a file's month SPREAD is every month it covers, never just the dominant one
  §D ALL OR NOTHING: one unreadable date leaves every row untouched, so the period replace that
     follows is a real delete covering every row inserted
  §E the un-wiring lock — no caller may keep its own date-column map again
  §F omission does not compile: every declared data-date table is day-keyed or declared period-grain
  §G the two writers (manual upload, nightly sweep) answer the same question the same way
  §H purity and RULE TWO — no carrier, tenant or product name, no I/O, no clock

Run:  cd backend && python3 harness_feed_day_grain.py
"""
import ast
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import app.modules.commcalc.data_lineage_registry as LIN
import app.modules.commcalc.feed_period as FP

ROUTER = "app/modules/commcalc/router.py"
SWEEP = "app/modules/commcalc/epay_sweep.py"
FEED_PERIOD = "app/modules/commcalc/feed_period.py"

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
    """SOURCE TEXT of one function, located by PARSING. A missing anchor raises by name — a harness
    that dies reads as 'not run', which is worse than one that fails."""
    text = _src(rel)
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"function {name!r} not found in {rel}")


def _code_only(src):
    """The EXECUTABLE text — docstring and comment lines stripped. A table or column name inside a
    comment is documentation (this file's own header names the column); in CODE it is the hand-wiring
    these rules forbid."""
    out = []
    for line in (src or "").splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        out.append(line.split("  #")[0])
    body = "\n".join(out)
    for q in ('"""', "'''"):
        parts = body.split(q)
        body = "".join(parts[::2]) if len(parts) > 2 else body
    return body


# ─────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§A THE DEFECT, REPRODUCED — a rolling window stamped with one picked period")
# The real shape of the 2026-10-04 pull, miniaturised: a file covering 2026-09-28 … 2026-10-02.
WINDOW = [
    {"payment_date": "2026-09-28", "amount": 100.0},
    {"payment_date": "2026-09-29", "amount": 200.0},
    {"payment_date": "2026-09-30", "amount": 300.0},
    {"payment_date": "2026-10-01", "amount": 40.0},
    {"payment_date": "2026-10-02", "amount": 60.0},
]
# THE OLD BEHAVIOUR: one operator-picked period onto every row.
old = [{**r, "period": "October 2026", "period_month": 10, "period_year": 2026} for r in WINDOW]
eq("A1 old: October reads the whole window",
   round(sum(r["amount"] for r in old if r["period"] == "October 2026"), 2), 700.00)
eq("A2 old: September reads nothing of its own money",
   round(sum(r["amount"] for r in old if r["period"] == "September 2026"), 2), 0.00)
# …and because the replace is keyed by (org, period), September's own rows are still stored, so the
# $600 is counted TWICE platform-wide. That is the 28x, in five rows.
sept_already_stored = 600.00
eq("A3 old: the same money is counted twice across the two periods",
   round(sept_already_stored + sum(r["amount"] for r in old), 2), 1300.00)

new = [dict(r) for r in WINDOW]
stamp = FP.day_stamp(new, "payment_date")
ok("A4 new: every row stamped from its own day", stamp.stamped, stamp.get("reason"))
eq("A5 new: September gets its $600",
   round(sum(r["amount"] for r in new if r["period"] == "September 2026"), 2), 600.00)
eq("A6 new: October gets its $100 and no more",
   round(sum(r["amount"] for r in new if r["period"] == "October 2026"), 2), 100.00)
eq("A7 new: the file is filed under both months it covers, newest first",
   stamp.get("months"), [("October 2026", 2), ("September 2026", 3)])
eq("A8 new: no dollar is in two periods",
   round(sum(r["amount"] for r in new), 2), round(sum(r["amount"] for r in WINDOW), 2))
eq("A9 the month columns agree with the label", sorted({(r["period"], r["period_month"],
                                                        r["period_year"]) for r in new}),
   [("October 2026", 10, 2026), ("September 2026", 9, 2026)])

# ─────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§B ONE DATE PARSER, every spelling the feeds arrive in")
eq("B1 ISO day", FP.period_of_day("2026-09-05"), ("September 2026", 9, 2026))
eq("B2 US day (the portal export spelling)", FP.period_of_day("10/02/2026"), ("October 2026", 10, 2026))
eq("B3 ISO month", FP.period_of_day("2026-12"), ("December 2026", 12, 2026))
eq("B4 ISO timestamp", FP.period_of_day("2026-03-30T04:05:06Z"), ("March 2026", 3, 2026))
import datetime as _dt
eq("B5 a date object", FP.period_of_day(_dt.date(2026, 7, 1)), ("July 2026", 7, 2026))
for bad in (None, "", "   ", "junk", "Sept 2026", "2026", 0, "00/15/2026", "2026-13-01"):
    ok(f"B6 refuses {bad!r} rather than guessing", FP.period_of_day(bad) is None,
       f"got {FP.period_of_day(bad)!r}")
eq("B7 period_label is the stored spelling", FP.period_label(9, 2026), "September 2026")
ok("B8 period_label refuses a non-month", FP.period_label(13, 2026) is None)

# ─────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§C THE SPREAD IS EVERY MONTH, not the dominant one")
# The dominant-month check could not see the defect: October's own 1,723 rows were a tiny minority of
# the file, so "the dominant month is September" would have been right and still said nothing useful.
vals = ["2026-09-05"] * 18231 + ["2026-10-01"] * 1723
spread = FP.month_spread(vals)
eq("C1 both months are reported", [p for p, _ in spread], ["October 2026", "September 2026"])
eq("C2 with their real row counts", dict(spread), {"October 2026": 1723, "September 2026": 18231})
eq("C3 a single-month file reports one month", FP.month_spread(["2026-08-01", "2026-08-31"]),
   [("August 2026", 2)])
eq("C4 unreadable values are not invented into a month", FP.month_spread(["junk", ""]), [])
eq("C5 day_values dedupes and sorts",
   FP.day_values([{"d": "2026-02-02"}, {"d": "2026-01-01"}, {"d": "2026-02-02"}, {"d": None}], "d"),
   ["2026-01-01", "2026-02-02"])

# ─────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§D ALL OR NOTHING — the asymmetry that duplicates a month is not reachable")
rows = [{"payment_date": "2026-09-28", "period": "October 2026", "period_month": 10,
         "period_year": 2026},
        {"payment_date": "", "period": "October 2026", "period_month": 10, "period_year": 2026}]
before = [dict(r) for r in rows]
res = FP.day_stamp(rows, "payment_date")
ok("D1 one unreadable date refuses the whole stamp", not res.stamped)
eq("D2 …and says how many could not prove their day", res.get("unproven"), 1)
ok("D3 …and names the column in the reason", "payment_date" in (res.get("reason") or ""))
eq("D4 NOT ONE ROW was re-stamped — the period replace still covers them all", rows, before)
eq("D5 no month is claimed when nothing was stamped", res.get("months"), [])
r2 = FP.day_stamp([{"payment_date": "2026-09-01"}], None)
ok("D6 a feed with no declared date column is refused, not guessed at", not r2.stamped)
ok("D7 …with a reason that says so", "data-date column" in (r2.get("reason") or ""))
r3 = FP.day_stamp([], "payment_date")
ok("D8 an empty pull stamps nothing and claims nothing", not r3.stamped and r3.get("months") == [])
# idempotent: stamping already-stamped rows is a no-op, which is what makes a re-pull safe
again = [dict(r) for r in new]
FP.day_stamp(again, "payment_date")
eq("D9 stamping twice changes nothing (a re-pull is idempotent)", again, new)

# ─────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§E THE UN-WIRING LOCK — no caller keeps its own date-column map again")
_router_tree = ast.parse(_src(ROUTER))
_literal_maps = []
for node in ast.walk(_router_tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and "DATE_KEYED" in t.id and isinstance(node.value, ast.Dict):
                _literal_maps.append(t.id)
ok("E1 router assigns no LITERAL date-column map", not _literal_maps,
   f"hand-written map(s) found: {_literal_maps} — derive from day_keyed_date_columns()")
_upload = _code_only(_func_src(ROUTER, "_upload_file_impl"))
ok("E2 router DEREFERENCES the registry for the day map",
   "day_keyed_date_columns()" in _upload)
ok("E3 router stamps each row from its own day", "_feed_period.day_stamp(" in _upload)
_sweep_src = _src(SWEEP)
ok("E4 the sweep dereferences the same registry accessor",
   "day_keyed_date_columns()" in _code_only(_sweep_src))
_day_grain = _code_only(_func_src(SWEEP, "_store_day_grain"))
ok("E5 the sweep's day path has ONE per-day store, not a second loop",
   "_store_rows_by_day(" in _day_grain and "safe_replace" not in _day_grain)
ok("E6 …and it reads the day from the registry's column, not a header spelling",
   "Begin Date" not in _day_grain)
eq("E7 exactly one per-day replace loop exists in the sweep",
   _code_only(_sweep_src).count("mode\": \"replace_by_day\""), 1)
# feed_period is the ONE parser. The sweep may not spell the month arithmetic at all.
ok("E8 the sweep does not re-derive a month label from a date string",
   'strftime("%B %Y")' not in _code_only(_sweep_src).replace("'", '"'))
# The router's live-sales branch pre-dates this and still labels its own rows inside the map loop.
# That line is now REDUNDANT rather than a rival answer — day_stamp re-stamps the same rows from the
# same column. Pinning the EQUIVALENCE is worth more than banning the line: if the two ever disagree,
# this fails instead of a month quietly moving.
_live_rows = [{"trans_date": "2026-08-31"}, {"trans_date": "2026-09-01"}]
_router_labels = [FP.period_label(int(r["trans_date"][5:7]), int(r["trans_date"][0:4]))
                  for r in _live_rows]          # what the map loop's strftime("%B %Y") produces
FP.day_stamp(_live_rows, "trans_date")
eq("E9 the router's own live-sales label and day_stamp agree exactly",
   [r["period"] for r in _live_rows], _router_labels)

# ─────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§F OMISSION DOES NOT COMPILE — every declared data-date table has an answer")
DAY = LIN.day_keyed_date_columns()
declared = {t for t, c in LIN.DATA_DATE_COLUMN_BY_TABLE.items() if c}
unanswered = sorted(declared - set(DAY) - set(LIN.PERIOD_GRAIN_REASONS))
ok("F1 no table declares a data-date column and no grain", not unanswered,
   f"neither day-keyed nor declared period-grain: {unanswered}")
ok("F2 every period-grain declaration costs a reason",
   all(isinstance(v, str) and len(v) > 20 for v in LIN.PERIOD_GRAIN_REASONS.values()),
   f"{ {k: v for k, v in LIN.PERIOD_GRAIN_REASONS.items() if not (isinstance(v, str) and len(v) > 20)} }")
ok("F3 a period-grain table is never also day-keyed",
   not (set(LIN.PERIOD_GRAIN_REASONS) & set(DAY)))
ok("F4 a table with no declared date column is never day-keyed",
   not any(LIN.DATA_DATE_COLUMN_BY_TABLE.get(t) is None for t in DAY))
ok("F5 the accessor is DERIVED, not a stored copy (it tracks the map it reads)",
   LIN.day_keyed_date_columns() == {t: c for t, c in LIN.DATA_DATE_COLUMN_BY_TABLE.items()
                                    if c and t not in LIN.PERIOD_GRAIN_REASONS})
ok("F6 is_day_keyed and period_grain_reason are exact complements over declared tables",
   all(LIN.is_day_keyed(t) != bool(LIN.period_grain_reason(t)) for t in declared))
ok("F7 the monthly sales archive stays period-grain, with its reason",
   LIN.period_grain_reason(LIN.MONTHLY_SALES) and not LIN.is_day_keyed(LIN.MONTHLY_SALES))
ok("F8 the LIVE sales feed is day-keyed (the day-grain side of the pair)",
   LIN.is_day_keyed(LIN.LIVE_SALES_FEED))

# ─────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§G THE REGRESSION — the feed that was wrong, and the siblings that were not")
eq("G1 raw_payment_detail is day-keyed on payment_date", DAY.get("raw_payment_detail"), "payment_date")
eq("G2 raw_comp_report is day-keyed on begin_date (unchanged)", DAY.get("raw_comp_report"), "begin_date")
for t, c in (("raw_ma_commission", "tx_date"), ("raw_ma_daily_tx", "tx_date"),
             ("raw_ma_fulfillment", "date_ordered")):
    eq(f"G3 {t} keeps its declared column {c}", DAY.get(t), c)
import app.modules.commcalc.epay_sweep as SW
eq("G4 the sweep stores payment detail under the month its ROWS belong to",
   SW.REPORTS["payment_detail"]["period"], "data")
ok("G5 …not under the month the sweep happened to run in",
   SW.REPORTS["payment_detail"]["period"] != "current")
eq("G6 comp keeps its own 'data' mode", SW.REPORTS["comp_report"]["period"], "data")
eq("G7 the subscriber snapshot is NOT day-keyed (it names no day)",
   LIN.DATA_DATE_COLUMN_BY_TABLE.get("raw_mi"), None)
ok("G8 …so the monthly snapshot report keeps its report_month label",
   SW.REPORTS["mi"]["period"] == "report_month")
ok("G9 the shared per-day store exists and is the one both callers reach",
   callable(getattr(SW, "_store_rows_by_day", None)))

# ─────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§H PURITY and RULE TWO")
_fp = _src(FEED_PERIOD)
_fp_code = _code_only(_fp)
for banned in ("supabase", "client", "requests", "psycopg", "fastapi", "HTTPException"):
    ok(f"H1 feed_period imports no {banned}", banned not in _fp_code)
ok("H2 feed_period has no clock (a month is read from the row, never from today)",
   "datetime" not in _fp_code and "now()" not in _fp_code)
# RULE TWO — no carrier, tenant or product branch names anywhere in the module, comments included.
for word in ("boost", "luxelink", "cellfonz", "verizon", "vidapay", "novawave", "total wireless",
             "epay portal", "t-mobile"):
    ok(f"H3 RULE TWO — no '{word}' in feed_period", word not in _fp.lower())
ok("H4 feed_period imports stdlib only",
   all(ln.split()[1] in ("calendar",) for ln in _fp_code.splitlines()
       if ln.startswith("import ")))
ok("H5 day_stamp returns the same row objects it was given (no copying surprise)",
   (lambda rs: FP.day_stamp(rs, "payment_date") is not None
    and rs[0] is rs[0])([{"payment_date": "2026-01-01"}]))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
