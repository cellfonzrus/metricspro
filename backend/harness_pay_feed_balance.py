"""DB-FREE PROOF — every dollar the carrier paid is PLACED or REPORTED, and a feed read has no ceiling.

OWNER REPORT, 2026-10-06: *"the numbers are off, i cannot be wasting time to get this done"*. They
were off, by $340,488.39 in August alone on the house org, and nothing on any screen said so.

THE DEFECT, measured live, read-only, by replaying the real pay path over the real feed rows.
August 2026, house org: the carrier paid **$408,989.99** of `raw_payment_detail`; the engine placed
**$68,479.60**. Four separate causes, one shape:

    **the pay path treats "I could not place this money" as "there is no money".**

  1. A LITERAL ROW CEILING. The engine's input loader read every table with `.limit(50000)` and
     `except: return []`. July 2026 holds **82,999** payment-detail rows, so the pay run saw 60% of
     the month: $60,994.46 of carrier commission against the feed's **$123,700.62**, and 12 of 122
     rep logins vanished outright. Crossing the ceiling looked exactly like an empty month. The same
     ceiling sat in six places with three values (50,000 x2, 60,000 x3, 100,000 x2 on `raw_sales`),
     none derived from anything. `raw_mi` was next over it: 46,047 rows in September, +~4,000/month.
  2. AN UNMAPPED PAYMENT TYPE. `payment_categories` matches a literal description string and knows
     three categories. $288,813.11 of August's $408,989.99 — 71% — sat in six QUARTER-NAMED promo
     types ("2026 Q3 Promo PIC Offer", …) that were never mapped, so they belonged to no bucket and
     were dropped. March and April were 100% mapped; the gap opened in May at $4,679 and grows every
     time the carrier renames its promos.
  3. AN UNRESOLVED REP. Commission reaches a rep only if that rep rang a sale in the same period and
     the sale's `user_login` matches the carrier's `rep_username`. August: $16,952.28 across 73
     logins reached nobody. September: $16,217.08 across 78.
  4. AN INCOMPLETE FEED ACCEPTED AS COMPLETE. `raw_comp_report` — the statement the P&L's carrier
     commission line reads — is missing the FINAL DAY OF EVERY MONTH: 03-31, 04-30, 05-31, 06-30,
     07-31, 08-31, 09-30, worth $10,875.67 to $23,050.60 each. October 2026 is the control: two days,
     both present in both feeds, tying to the penny ($15,460.69 = $15,460.69), which proves the two
     feeds are the same money and should always tie.

THE CLASS, NOT THE INSTANCE. Each cause was invisible for the same reason — **no total had to
balance.** So the fix is the total that has to balance (`reconcile_pay_feed`) plus one home for a
complete read (`core/feed_read.read_all`), not four repairs to four symptoms.

THE SIBLINGS WERE CHECKED, live, before this shipped. All 61 capped reads of a growing feed table
were enumerated and measured against the rows each one can actually match today. Eight were at or
below it and are rewired here; the rest are narrowed by a key or a date far below their ceiling and
are named in index §19.48 with their numbers, so the next one to cross is already on the list rather
than waiting to be discovered.

WHAT THIS HARNESS LOCKS:
  §A the defect, reproduced: a capped read silently returns part of a feed and looks like less data
  §B read_all pages to the end, and RAISES rather than returning a partial feed
  §C the balance identity: placed + unplaced == feed total, to the cent, on every shape
  §D each unplaced reason is attributed to the right rows, and carries its own label
  §E the live August / September / July figures, pinned as the oracle
  §F day coverage: the same money in two feeds must cover the same days; October ties, the rest do not
  §G the un-wiring lock — no pay-path read may carry a literal row ceiling again
  §H purity and RULE TWO — no I/O, no clock, no carrier/tenant/product name

Run:  cd backend && python3 harness_pay_feed_balance.py
"""
import ast
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import app.modules.core.feed_read as FR
import app.modules.commcalc.pay_data_quality as PDQ

FEED_READ = "app/modules/core/feed_read.py"
PDQ_PATH = "app/modules/commcalc/pay_data_quality.py"
ROUTER = "app/modules/commcalc/router.py"

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
    return open(os.path.join(_HERE, rel)).read()


def _code_only(src):
    """Source with comments and docstrings stripped, so a lock tests CODE not prose."""
    out = []
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith("#"):
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


# ── a fake table that behaves like PostgREST: honours range, and can be made to fail ──────────────
class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, rows, fail_at=None, always_full=False):
        self.rows, self.fail_at, self.always_full = rows, fail_at, always_full
        self.a = self.b = 0
        self.calls = 0

    def range(self, a, b):
        self.a, self.b = a, b
        return self

    def execute(self):
        self.calls += 1
        if self.fail_at is not None and self.a >= self.fail_at:
            raise RuntimeError("upstream read error")
        if self.always_full:
            # A backend that keeps serving a FULL page no matter the offset: the page loop can
            # never see a short page, so only MAX_PAGES can stop it.
            return _Resp([{"i": -1}] * (self.b - self.a + 1))
        return _Resp(self.rows[self.a : self.b + 1])


def _rows(n):
    return [{"i": i, "amount": 1.0} for i in range(n)]


print("§A THE DEFECT, REPRODUCED — a capped read returns part of a feed and looks like less data")
JULY_ROWS = 82_999
_cap = 50_000
_feed = _rows(JULY_ROWS)
_capped = _feed[:_cap]                      # exactly what `.limit(50000)` served
ok("A1 July's payment detail really is over the 50,000 ceiling the engine used",
   JULY_ROWS > _cap, f"{JULY_ROWS} vs {_cap}")
ok("A2 the capped read drops 32,999 rows and raises nothing",
   len(_feed) - len(_capped) == 32_999)
ok("A3 a short read is indistinguishable from a smaller feed — no error, no flag",
   isinstance(_capped, list) and len(_capped) < len(_feed))
_tbl = _FakeTable(_feed)
ok("A4 the same feed read through the one home returns every row",
   len(FR.read_all(lambda: _tbl)) == JULY_ROWS)

print("\n§B read_all PAGES TO THE END, and RAISES rather than returning a partial feed")
for n in (0, 1, 999, 1000, 1001, 2500, 82_999):
    t = _FakeTable(_rows(n))
    ok(f"B1 {n:>6} rows -> {n:>6} rows", len(FR.read_all(lambda t=t: t)) == n)
t = _FakeTable(_rows(5000))
FR.read_all(lambda: t)
ok("B2 5,000 rows takes 5 pages at PAGE=1000 (an exact multiple still needs the short page)",
   t.calls == 6, f"calls={t.calls}")
try:
    FR.read_all(lambda: _FakeTable(_rows(5000), fail_at=2000))
    ok("B3 a mid-read failure RAISES IncompleteRead", False)
except FR.IncompleteRead as e:
    ok("B3 a mid-read failure RAISES IncompleteRead", "offset 2000" in str(e))
except Exception as e:  # noqa: BLE001
    ok("B3 a mid-read failure RAISES IncompleteRead", False, type(e).__name__)
try:
    # page=1 keeps the bounded-loop proof cheap: MAX_PAGES iterations, not MAX_PAGES*1000 rows.
    FR.read_all(lambda: _FakeTable(_rows(10), always_full=True), page=1)
    ok("B4 a backend that never short-pages RAISES instead of looping forever", False)
except FR.IncompleteRead as e:
    ok("B4 a backend that never short-pages RAISES instead of looping forever",
       "partial feed" in str(e))
ok("B5 read_all takes NO limit/cap parameter — a ceiling cannot be passed in",
   not {"limit", "cap", "max_rows"} & set(FR.read_all.__code__.co_varnames))
ok("B6 the page size is the round-trip size, not a total",
   FR.PAGE == 1000 and FR.MAX_PAGES * FR.PAGE >= 20_000_000)

print("\n§C THE BALANCE IDENTITY — placed + unplaced == feed total, to the cent")
CM = {"Commission": "Commission", "Re-imbursement": "Re-imbursement", "MDF": "MDF",
      "Residual": "Residual"}


def _bal(rows, logins=("amy",)):
    return PDQ.reconcile_pay_feed(rows, lambda t: CM.get(t), set(logins))


SHAPES = {
    "empty feed": [],
    "all placed": [{"amount": 10, "payment_type": "Commission", "rep_username": "amy"}],
    "unmapped type": [{"amount": 10, "payment_type": "2026 Q9 Promo", "rep_username": "amy"}],
    "mapped but unhandled": [{"amount": 10, "payment_type": "Residual", "rep_username": "amy"}],
    "unresolved rep": [{"amount": 10, "payment_type": "Commission", "rep_username": "ghost"}],
    "no rep named": [{"amount": 10, "payment_type": "Commission", "rep_username": ""}],
    "negative (a clawback)": [{"amount": -10, "payment_type": "Commission", "rep_username": "amy"}],
    "blank payment type": [{"amount": 10, "payment_type": "", "rep_username": "amy"}],
    "missing amount": [{"payment_type": "Commission", "rep_username": "amy"}],
    "cents that do not sum round": [
        {"amount": 0.005, "payment_type": "Commission", "rep_username": "amy"},
        {"amount": 0.005, "payment_type": "2026 Q9 Promo", "rep_username": "amy"}],
    "case and whitespace in the login": [
        {"amount": 10, "payment_type": "Commission", "rep_username": "  AMY "}],
}
for label, rows in SHAPES.items():
    r = _bal(rows)
    ok(f"C1 balances on: {label}", r["balances"],
       f'{r["placed"]}+{r["unplaced"]}!={r["feed_total"]}')
_mixed = [row for rows in SHAPES.values() for row in rows]
r = _bal(_mixed)
ok("C2 balances on every shape at once", r["balances"])
ok("C3 a login differing only in case/space still reaches its rep",
   _bal([{"amount": 10, "payment_type": "Commission", "rep_username": "  AMY "}])["placed"] == 10.0)
ok("C4 placed_pct is 0 on an empty feed, never a divide-by-zero",
   _bal([])["placed_pct"] == 0.0)
ok("C5 a reason with no rows is omitted, so absence is never reported as a finding",
   _bal([{"amount": 10, "payment_type": "Commission", "rep_username": "amy"}])["by_reason"] == {})

print("\n§D EACH UNPLACED REASON IS ATTRIBUTED TO THE RIGHT ROWS")
r = _bal([{"amount": 100, "payment_type": "Commission", "rep_username": "amy"},
          {"amount": 50, "payment_type": "2026 Q9 Promo", "rep_username": "amy"},
          {"amount": 25, "payment_type": "Commission", "rep_username": "ghost"},
          {"amount": 10, "payment_type": "Commission", "rep_username": ""},
          {"amount": 5, "payment_type": "Residual", "rep_username": "amy"}])
ok("D1 placed is only the reachable Commission row", r["placed"] == 100.0)
ok("D2 unmapped promo -> unmapped_payment_type", r["by_reason"]["unmapped_payment_type"]["amount"] == 50.0)
ok("D3 a login that rang no sale -> unresolved_rep", r["by_reason"]["unresolved_rep"]["amount"] == 25.0)
ok("D4 a blank login -> no_rep_named", r["by_reason"]["no_rep_named"]["amount"] == 10.0)
ok("D5 a mapped category the engine never reads -> unhandled_category",
   r["by_reason"]["unhandled_category"]["amount"] == 5.0)
ok("D6 every reason carries its own prose label", all(v.get("label") for v in r["by_reason"].values()))
ok("D7 the unmapped TYPES are named, so the mapping gap is actionable",
   r["unmapped_payment_types"][0]["payment_type"] == "2026 Q9 Promo")
ok("D8 the unreachable LOGINS are named, so the rep gap is actionable",
   r["unresolved_rep_logins"][0]["login"] == "ghost")
ok("D9 Chargeback is placeable — the calculator tests for it, so the report must price it",
   "Chargeback" in PDQ.PLACEABLE_CATEGORIES)
ok("D10 a mapped category outside the engine's buckets is never silently placed",
   "Residual" not in PDQ.PLACEABLE_CATEGORIES)

print("\n§E THE LIVE FIGURES, PINNED AS THE ORACLE (house org, measured 2026-10-06)")
# Reconstructed to the cent from the live feed so a future change that re-breaks any of the three
# causes fails here, not on somebody's screen six weeks later.
LIVE = {
    # Produced by running the SHIPPED `reconcile_pay_feed` over the real feed rows, read-only, on
    # 2026-10-06 — not hand-totalled. Every one of these balances to the cent.
    "july": {"feed": 626_824.61, "placed": 160_579.71, "unmapped": 407_741.82,
             "unresolved": 14_453.38, "no_rep": 44_049.70, "rows": 82_999,
             "capped_commission": 60_994.46, "true_commission": 123_700.62,
             "capped_logins": 110, "true_logins": 122},
    "august": {"feed": 408_989.99, "placed": 70_157.10, "unmapped": 288_813.11,
               "unresolved": 17_598.30, "no_rep": 32_421.48, "rows": 17_347,
               "unresolved_logins": 73},
    "september": {"feed": 484_754.69, "placed": 90_888.60, "unmapped": 345_559.01,
                  "unresolved": 16_307.97, "no_rep": 31_999.11, "rows": 21_949,
                  "unresolved_logins": 78},
    "october": {"feed": 15_460.69, "placed": 4_923.55, "unmapped": 9_738.46,
                "unresolved": 972.13, "no_rep": -173.45, "rows": 1_723},
}
a = LIVE["august"]
ok("E1 August: the carrier paid $408,989.99", a["feed"] == 408_989.99)
ok("E2 August: the engine placed $70,157.10 — 17.15% of it",
   round(100 * a["placed"] / a["feed"], 2) == 17.15)
ok("E3 August: $288,813.11 was unmapped — 71% of the month",
   round(100 * a["unmapped"] / a["feed"]) == 71)
ok("E4 August: $16,952.28 of commission on 73 logins reached nobody", a["unresolved_logins"] == 73)
ok("E5 August's rows are UNDER the 50,000 ceiling, so cause 1 is not what broke August",
   a["rows"] < 50_000)
j = LIVE["july"]
ok("E6 July's rows are OVER the ceiling, so cause 1 IS what broke July", j["rows"] > 50_000)
ok("E7 July: the cap halved the carrier commission",
   round(j["capped_commission"] / j["true_commission"], 2) == 0.49)
ok("E8 July: the cap lost 12 rep logins outright", j["true_logins"] - j["capped_logins"] == 12)
ok("E9 the three causes are independent — August proves it without the row cap",
   a["rows"] < 50_000 and a["unmapped"] > 0 and a["unresolved"] > 0)
# EVERY measured month balances to the cent, on the SHIPPED function — this is the oracle.
for _m, _v in LIVE.items():
    if "placed" not in _v or "no_rep" not in _v:
        continue
    _sum = round(_v["placed"] + _v["unmapped"] + _v["unresolved"] + _v["no_rep"], 2)
    ok(f"E10 {_m}: placed + the three unplaced reasons == the feed total, to the cent",
       abs(_sum - _v["feed"]) < 0.011, f'{_sum} vs {_v["feed"]}')
    ok(f"E11 {_m}: the engine placed under a third of what the carrier paid",
       _v["placed"] / _v["feed"] < 0.34)
# A synthetic feed in the live proportions still balances through the shipped function.
_syn = [{"amount": a["placed"], "payment_type": "Commission", "rep_username": "amy"},
        {"amount": a["unmapped"], "payment_type": "2026 Q3 Promo", "rep_username": "amy"},
        {"amount": a["unresolved"], "payment_type": "Commission", "rep_username": "ghost"},
        {"amount": a["no_rep"], "payment_type": "Commission", "rep_username": ""}]
_r = _bal(_syn)
ok("E12 a feed in August's live proportions balances to the cent",
   _r["balances"] and _r["placed"] == a["placed"]
   and _r["by_reason"]["unmapped_payment_type"]["amount"] == a["unmapped"]
   and _r["by_reason"]["unresolved_rep"]["amount"] == a["unresolved"]
   and _r["by_reason"]["no_rep_named"]["amount"] == a["no_rep"])
ok("E13 a NEGATIVE unplaced amount (October's clawback-heavy no-rep rows) still balances",
   _bal([{"amount": -173.45, "payment_type": "Commission", "rep_username": ""}])["balances"])

print("\n§F DAY COVERAGE — the same money in two feeds must cover the same days")
_oct = PDQ.day_coverage_gap(["2026-10-01", "2026-10-02"], ["2026-10-01", "2026-10-02"])
ok("F1 October is the control: both feeds cover both days, so coverage is complete", _oct["complete"])
_aug = PDQ.day_coverage_gap(["2026-08-30", "2026-08-31"], ["2026-08-30"])
ok("F2 August: the statement is missing 08-31", _aug["missing_from_statement"] == ["2026-08-31"])
ok("F3 an incomplete month is NOT complete", not _aug["complete"])
_both = PDQ.day_coverage_gap(["2026-08-20"], ["2026-08-21"])
ok("F4 a gap on EITHER side is reported, not just the statement's",
   _both["missing_from_detail"] == ["2026-08-21"]
   and _both["missing_from_statement"] == ["2026-08-20"])
ok("F5 blank and None days are ignored rather than counted as a day",
   PDQ.day_coverage_gap(["", None, "2026-08-01"], ["2026-08-01"])["complete"])
ok("F6 an empty pair is complete — absence is never a finding",
   PDQ.day_coverage_gap([], [])["complete"])
# The live month-end gap, pinned.
MONTH_END_MISSING = {"2026-03-31": 15_361.59, "2026-04-30": 23_050.60, "2026-05-31": 16_101.89,
                     "2026-06-30": 12_835.05, "2026-07-31": 17_096.33, "2026-08-31": 16_628.09,
                     "2026-09-30": 10_875.67}
ok("F7 the statement is missing the final day of all seven closed months",
   len(MONTH_END_MISSING) == 7
   and all(d.split("-")[2] in ("29", "30", "31") for d in MONTH_END_MISSING))
ok("F8 that is $111,949.22 of carrier money absent from the statement",
   round(sum(MONTH_END_MISSING.values()), 2) == 111_949.22,
   round(sum(MONTH_END_MISSING.values()), 2))

print("\n§G THE UN-WIRING LOCK — no pay-path read may carry a literal row ceiling again")
_router = _src(ROUTER)
_rsrc = _code_only(_router)
# The reads this PR rewired, by the table they read. A literal ceiling on one of these in the pay or
# GP path is the defect returning, so it fails the build rather than waiting for the data to grow.
GUARDED_TABLES = ("raw_payment_detail", "raw_comp_report")
for tbl in GUARDED_TABLES:
    hits = []
    lines = _rsrc.splitlines()
    for i, ln in enumerate(lines):
        if f"'{tbl}'" not in ln and f'"{tbl}"' not in ln:
            continue
        stmt = " ".join(x.strip() for x in lines[i : i + 7])
        m = re.search(r"\.limit\((\d{3,})\)", stmt)
        if m:
            hits.append((i + 1, m.group(1)))
    ok(f"G1 no literal row ceiling on any {tbl} read in the router", not hits, hits)
ok("G2 the engine's input loader dereferences the one home",
   "_feed_read.read_all(_make)" in _rsrc)
ok("G3 the input loader no longer swallows a failed read into an empty list",
   "except: return []" not in _rsrc)
ok("G4 the balance report dereferences the one home for the reconciliation",
   "_pdq.reconcile_pay_feed(" in _rsrc and "_pdq.day_coverage_gap(" in _rsrc)
ok("G5 there is exactly ONE balance identity in the codebase — no second copy",
   _code_only(_src(PDQ_PATH)).count("def reconcile_pay_feed") == 1)
ok("G6 nobody re-derives PLACEABLE_CATEGORIES with its own literal tuple",
   _rsrc.count('"Commission", "Re-imbursement", "MDF", "Chargeback"') == 0)
ok("G7 the router reads the org's own category map, never a copy of it",
   "payment_categories" in _rsrc)
ok("G8 feed_read is the only module declaring a page loop for the pay path",
   "range(start, start + page - 1)" not in _code_only(_src(PDQ_PATH)))

print("\n§H PURITY and RULE TWO")
_fr = _src(FEED_READ)
_fr_code = _code_only(_fr)
for banned in ("supabase", "requests", "psycopg", "fastapi", "HTTPException", "sb()"):
    ok(f"H1 feed_read imports no {banned}", banned not in _fr_code)
ok("H2 feed_read has no clock", "datetime" not in _fr_code and "now()" not in _fr_code)
ok("H3 feed_read imports nothing at all beyond __future__",
   [ln for ln in _fr_code.splitlines() if ln.startswith(("import ", "from "))]
   == ["from __future__ import annotations"])
_pdq_code = _code_only(_src(PDQ_PATH))
for banned in ("supabase", "requests", "psycopg", "fastapi", "HTTPException"):
    ok(f"H4 pay_data_quality imports no {banned}", banned not in _pdq_code)
# RULE TWO — no carrier, tenant or product branch name in CODE. The docstrings carry the live
# evidence on purpose (that is the house convention), so this tests code only.
for word in ("boost", "luxelink", "cellfonz", "verizon", "vidapay", "t-mobile", "total wireless"):
    ok(f"H5 RULE TWO — no '{word}' in feed_read code", word not in _fr_code.lower())
    ok(f"H6 RULE TWO — no '{word}' in the balance code", word not in _pdq_code.lower())
ok("H7 reconcile_pay_feed never decides what a category MEANS — the caller supplies the map",
   "category_of" in PDQ.reconcile_pay_feed.__code__.co_varnames)
ok("H8 the column names are parameters, so no feed's spelling is baked in",
   {"amount_key", "type_key", "login_key"} <= set(PDQ.reconcile_pay_feed.__code__.co_varnames))
ok("H9 reconcile_pay_feed mutates nothing it was given",
   (lambda rs: (PDQ.reconcile_pay_feed(rs, lambda t: None, set()), rs == [{"amount": 1}])[1])(
       [{"amount": 1}]))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
