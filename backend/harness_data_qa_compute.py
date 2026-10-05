#!/usr/bin/env python3
"""DB-FREE proof for the data assistant's ARITHMETIC — `core/data_qa_compute`.

This is the money harness for the assistant. The owner asked it to "perform calculations, create a
pivot table or create graphs"; if any of that arithmetic is wrong, the assistant is a confident
liar about a store's revenue. So every figure it can ever state is produced by a function proved
here, and the model is never the thing doing the adding up.

  §A  `to_number` — the ONE coercion, including the shapes reports actually emit
  §B  `group` — totals, counts, averages, and the blank-is-not-zero rule
  §C  `rank` — order, and "no figure reported" is never best and never worst
  §D  `pivot` — cells, row totals, the column footer, and the grand total that AGREES with them
  §E  `compare` — the gap, the percentage, the share, and division by zero
  §F  `chart_spec` — data not pictures, a blank stays blank, a pie takes one measure
  §G  `describe` / `numeric_columns` — what the model is told about a result
  §H  the invariants: a total is the sum of its parts, and no function mutates its input

Run: python3 backend/harness_data_qa_compute.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.core import data_qa_compute as calc   # noqa: E402

FAILS = []
CHECKS = 0


def ok(cond, label):
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILS.append(label)


def section(name):
    print(f"\n── {name}")


def near(a, b, eps=1e-6):
    return a is not None and b is not None and abs(a - b) < eps


# Rows shaped like the sales report's own output (store x rep x day, money as strings AND floats,
# because a report that unions two feeds really does return both).
ROWS = [
    {"store": "B-1115", "rep": "Abid",  "date": "2026-09-01", "revenue": 1000.0, "gp": "250.00",  "units": 4},
    {"store": "B-1115", "rep": "Abid",  "date": "2026-09-02", "revenue": "1,500.50", "gp": 300.5, "units": 5},
    {"store": "B-1115", "rep": "Sara",  "date": "2026-09-01", "revenue": 500.0,  "gp": "(50.00)", "units": 2},
    {"store": "B-2022", "rep": "Omar",  "date": "2026-09-01", "revenue": "$2,000.00", "gp": "600", "units": 7},
    {"store": "B-2022", "rep": "Omar",  "date": "2026-09-02", "revenue": 250.0,  "gp": None,      "units": 1},
    {"store": "B-3300", "rep": "Lena",  "date": "2026-09-01", "revenue": None,   "gp": "",        "units": 0},
]

# ── §A coercion ─────────────────────────────────────────────────────────────────────────────────
section("A. to_number — one home for 'is this cell a number'")
ok(calc.to_number(12) == 12.0, "A1 int")
ok(calc.to_number(12.5) == 12.5, "A2 float")
ok(calc.to_number("1,500.50") == 1500.5, "A3 a thousands separator")
ok(calc.to_number("$2,000.00") == 2000.0, "A4 a currency symbol")
ok(calc.to_number("(50.00)") == -50.0, "A5 accounting parentheses ARE negative")
ok(calc.to_number("12.5%") == 12.5, "A6 a percent sign is stripped, the number kept")
ok(calc.to_number(None) is None, "A7 None is not a number")
ok(calc.to_number("") is None, "A8 blank is not a number")
ok(calc.to_number("   ") is None, "A9 whitespace is not a number")
ok(calc.to_number("n/a") is None, "A10 'n/a' is not a number")
ok(calc.to_number("Abid") is None, "A11 a name is not a number")
ok(calc.to_number(True) is None, "A12 a BOOLEAN is not a number — True must never total as 1")
ok(calc.to_number("-0") == 0.0, "A13 negative zero")
ok(calc.to_number("()") is None, "A14 empty parentheses are not a number")

# ── §B group ────────────────────────────────────────────────────────────────────────────────────
section("B. group")
g = calc.group(ROWS, ["store"], ["revenue", "gp", "units"])
by = {r["store"]: r for r in g}
ok(len(g) == 3, "B1 one group per distinct store")
ok(near(by["B-1115"]["revenue"], 3000.5), "B2 revenue sums across mixed string/float cells")
ok(near(by["B-1115"]["gp"], 500.5), "B3 a parenthesised negative REDUCES the total (250+300.5-50)")
ok(near(by["B-2022"]["revenue"], 2250.0), "B4 a second group totals independently")
ok(near(by["B-2022"]["gp"], 600.0), "B5 a None cell is skipped, not counted as 0")
ok(by["B-3300"]["revenue"] is None,
   "B6 a group with NO reported figure is None, never 0.00 — 'not reported' is not 'sold nothing'")
ok(by["B-3300"]["_rows"] == 1, "B7 the row still counts in _rows even with no figure")
ok(by["B-1115"]["_rows"] == 3, "B8 _rows counts rows, not figures")

ga = calc.group(ROWS, ["store"], ["gp"], agg="avg")
ok(near({r["store"]: r for r in ga}["B-2022"]["gp"], 600.0),
   "B9 avg divides by REPORTED cells (1), not by rows (2) — a blank must not halve the average")
gc = calc.group(ROWS, ["store"], ["gp"], agg="count")
ok({r["store"]: r for r in gc}["B-2022"]["gp"] == 1, "B10 count counts reported figures")
ok({r["store"]: r for r in gc}["B-3300"]["gp"] == 0, "B11 count of nothing is 0, which IS a number")
gm = calc.group(ROWS, ["store"], ["revenue"], agg="min")
ok(near({r["store"]: r for r in gm}["B-1115"]["revenue"], 500.0), "B12 min")
gx = calc.group(ROWS, ["store"], ["revenue"], agg="max")
ok(near({r["store"]: r for r in gx}["B-1115"]["revenue"], 1500.5), "B13 max")
g2 = calc.group(ROWS, ["store", "rep"], ["revenue"])
ok(len(g2) == 4, "B14 a two-column grouping keys on the pair (Abid has two days in one store)")
ok(calc.group(ROWS, [], ["revenue"])[0]["revenue"] is not None,
   "B15 grouping by nothing gives the one grand row")
ok(calc.group([], ["store"], ["revenue"]) == [], "B16 no rows, no groups")
ok(calc.group([{"store": "A"}, "junk", None], ["store"], ["revenue"])[0]["_rows"] == 1,
   "B17 non-dict rows are ignored, not fatal")
try:
    calc.group(ROWS, ["store"], ["revenue"], agg="median")
    ok(False, "B18 an unknown aggregate is refused")
except ValueError:
    ok(True, "B18 an unknown aggregate is refused")

# ── §C rank ─────────────────────────────────────────────────────────────────────────────────────
section("C. rank")
r = calc.rank(g, "revenue")
ok(r[0]["store"] == "B-1115", "C1 best store first (3000.50 > 2250.00)")
ok(r[-1]["store"] == "B-3300",
   "C2 the store with NO reported revenue is last, not 'best' by accident")
ra = calc.rank(g, "revenue", descending=False)
ok(ra[0]["store"] == "B-2022", "C3 ascending gives the real smallest REPORTED figure")
ok(ra[-1]["store"] == "B-3300",
   "C4 and the unreported store is STILL last — so 'who is pulling me down' never names a missing feed")
ok(len(calc.rank(g, "revenue", limit=2)) == 2, "C5 limit truncates after ordering")
ok(calc.rank([], "revenue") == [], "C6 nothing to rank")
ok(len(calc.rank(g, "nope")) == 3, "C7 ranking on an absent column keeps every row rather than dropping data")

# ── §D pivot ────────────────────────────────────────────────────────────────────────────────────
section("D. pivot")
pv = calc.pivot(ROWS, ["store"], "date", "revenue")
ok(pv["columns"] == ["2026-09-01", "2026-09-02"], "D1 columns in first-seen order, not alphabetical")
prow = {r["store"]: r for r in pv["rows"]}
ok(near(prow["B-1115"]["2026-09-01"], 1500.0),
   "D2 a cell AGGREGATES every row in it — B-1115 on the 1st is Abid's 1000 plus Sara's 500")
ok(near(prow["B-1115"]["_total"], 3000.5), "D3 the row total is the row's cells")
ok(prow["B-3300"]["2026-09-02"] is None, "D4 a cell with no rows is None, never 0")
ok(prow["B-3300"]["_total"] is None, "D5 a row with nothing reported totals to None")
ok(near(pv["totals"]["2026-09-01"], 3500.0), "D6 the column footer totals the column")
ok(near(pv["totals"]["_total"], 5250.5), "D7 the grand total")
row_sum = sum(r["_total"] for r in pv["rows"] if r["_total"] is not None)
col_sum = sum(v for k, v in pv["totals"].items() if k != "_total" and v is not None)
ok(near(row_sum, pv["totals"]["_total"]) and near(col_sum, pv["totals"]["_total"]),
   "D8 THE TABLE ADDS UP both ways — rows, columns and the grand total agree")
pvc = calc.pivot(ROWS, ["store"], "date", "gp", agg="count")
ok({r["store"]: r for r in pvc["rows"]}["B-2022"]["_total"] == 1, "D9 a counted pivot")
ok(calc.pivot([], ["store"], "date", "revenue")["rows"] == [], "D10 an empty pivot is empty")
pv2 = calc.pivot(ROWS, ["store", "rep"], "date", "units")
ok(len(pv2["rows"]) == 4, "D11 a two-column side keys on the pair")
try:
    calc.pivot(ROWS, ["store"], "date", "revenue", agg="median")
    ok(False, "D12 an unknown aggregate is refused")
except ValueError:
    ok(True, "D12 an unknown aggregate is refused")

# ── §E compare ──────────────────────────────────────────────────────────────────────────────────
section("E. compare — 'who is pulling me down'")
cmp = calc.compare(g, "store", "revenue")
crow = {r["label"]: r for r in cmp["rows"]}
ok(near(cmp["total"], 5250.5), "E1 the total of the reported figures")
ok(near(cmp["mean"], 2625.25), "E2 the mean ignores the unreported row (5250.50 / 2)")
ok(near(crow["B-2022"]["delta"], -375.25), "E3 the gap to the baseline")
ok(crow["B-2022"]["pct"] is not None and crow["B-2022"]["pct"] < 0,
   "E4 behind the estate reads as negative")
ok(near(crow["B-1115"]["share"], 57.15, 0.02), "E5 share of the total, as a percentage")
ok(crow["B-3300"]["delta"] is None and crow["B-3300"]["share"] is None,
   "E6 an unreported row has no gap and no share — it is not 'down 100%'")
worst = min((r for r in cmp["rows"] if r["delta"] is not None), key=lambda r: r["delta"])
ok(worst["label"] == "B-2022",
   "E7 the furthest BELOW the rest is the answer, not simply the smallest number")
zero = calc.compare([{"s": "a", "v": 0, "b": 0}, {"s": "b", "v": 5, "b": 0}], "s", "v",
                    baseline_col="b")
ok(all(r["pct"] is None for r in zero["rows"]),
   "E8 a zero baseline gives NO percentage — a share of nothing is unanswerable, not 0% or infinity")
bl = calc.compare([{"s": "a", "now": 110, "then": 100}], "s", "now", baseline_col="then")
ok(near(bl["rows"][0]["pct"], 10.0), "E9 month-on-month against a second column")
ok(calc.compare([], "s", "v")["total"] == 0.0 and calc.compare([], "s", "v")["mean"] is None,
   "E10 nothing to compare is stated, not divided by zero")

# ── §F chart_spec ───────────────────────────────────────────────────────────────────────────────
section("F. chart_spec — data, never a picture")
ch = calc.chart_spec(g, "bar", "store", ["revenue", "gp"])
ok(ch["kind"] == "bar" and ch["labels"] == ["B-1115", "B-2022", "B-3300"], "F1 labels and kind")
ok(len(ch["series"]) == 2 and ch["series"][0]["name"] == "revenue", "F2 one series per measure")
ok(ch["series"][0]["values"][2] is None,
   "F3 a missing figure stays NULL, not 0 — a zero-height bar would read as 'sold nothing'")
ok(calc.chart_spec(g, "pie", "store", ["revenue", "gp"])["series"].__len__() == 1,
   "F4 a pie takes one measure by construction")
ok(calc.chart_spec(g, "bar", "store", ["revenue"], limit=2)["truncated"] is True,
   "F5 truncation is declared, so a chart never silently hides rows")
ok(calc.chart_spec(g, "bar", "store", ["revenue"])["truncated"] is False, "F6 and not when it fits")
ok("by store" in calc.chart_spec(g, "line", "store", ["gp"])["title"], "F7 a default title")
try:
    calc.chart_spec(g, "pyramid", "store", ["revenue"])
    ok(False, "F8 an unknown chart kind is refused")
except ValueError:
    ok(True, "F8 an unknown chart kind is refused")
ok(all(not isinstance(v, str) or v is None
       for s in ch["series"] for v in s["values"] if v is not None),
   "F9 series values are numbers — the frontend is never asked to parse '$1,000'")

# ── §G describe ─────────────────────────────────────────────────────────────────────────────────
section("G. describe — what the model is told")
d = calc.describe(ROWS)
ok(d["row_count"] == 6, "G1 the real row count")
ok(d["columns"][:2] == ["store", "rep"], "G2 columns in first-seen order")
ok(set(d["numeric_columns"]) == {"revenue", "gp", "units"},
   "G3 only the columns that hold numbers may be a MEASURE — the model cannot ask to sum a name")
ok(len(d["sample_rows"]) == 3, "G4 a bounded sample, not the whole report")
ok(calc.describe(ROWS, sample=0)["sample_rows"] == [], "G5 the sample can be suppressed")
ok(calc.numeric_columns([{"a": "x"}, {"a": "7"}]) == ("a",),
   "G6 a column numeric in ANY row counts (a report that unions feeds mixes types)")
ok(calc.numeric_columns([{"a": "x"}]) == (), "G7 a never-numeric column does not")
ok(calc.describe([])["row_count"] == 0, "G8 an empty result describes as empty")

# ── §H invariants ───────────────────────────────────────────────────────────────────────────────
section("H. invariants")
before = copy.deepcopy(ROWS)
calc.group(ROWS, ["store"], ["revenue"])
calc.pivot(ROWS, ["store"], "date", "revenue")
calc.compare(ROWS, "store", "revenue")
calc.chart_spec(ROWS, "bar", "store", ["revenue"])
calc.rank(ROWS, "revenue")
ok(ROWS == before, "H1 NO function mutates the rows a report returned")
tot_grouped = sum(r["revenue"] for r in calc.group(ROWS, ["store"], ["revenue"])
                  if r["revenue"] is not None)
tot_rows = sum(calc.to_number(r["revenue"]) for r in ROWS if calc.to_number(r["revenue"]) is not None)
ok(near(tot_grouped, tot_rows), "H2 grouping conserves the total — no row is lost or double-counted")
ok(near(calc.pivot(ROWS, ["store"], "date", "revenue")["totals"]["_total"], tot_rows),
   "H3 pivoting conserves the same total")
ok(near(calc.compare(calc.group(ROWS, ["store"], ["revenue"]), "store", "revenue")["total"],
        tot_rows), "H4 and so does comparing")

print(f"\n{'=' * 78}")
if FAILS:
    print(f"FAILED {len(FAILS)} of {CHECKS} checks:")
    for f in FAILS:
        print(f"  ✗ {f}")
    sys.exit(1)
print(f"OK — {CHECKS} checks passed (data-qa arithmetic)")
