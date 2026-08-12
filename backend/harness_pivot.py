"""Harness — the PIVOT builder (roadmap #4). Pure math, no DB.

A cross-tab is the shape where a wrong aggregate looks most plausible: the grid is full of numbers,
each one individually believable, and the only thing that betrays an error is that the totals don't
reconcile. So this harness is built around reconciliation and around the one operation that does NOT
survive a shortcut — the mean.

Run:  cd backend && python3 harness_pivot.py
"""
import sys
sys.path.insert(0, ".")
from app.modules.commcalc.custom_report import (  # noqa: E402
    pivot, pivot_axes, dataset_by_key, COUNT_MEASURE, _col,
)

P = F = 0


def ok(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print("  PASS  " + name)
    else:
        F += 1
        print("  FAIL  " + name + (("\n        " + detail) if detail else ""))


COLS = [
    _col("store", "Store", "text", group=True),
    _col("month", "Month", "text", group=True),
    _col("amount", "Amount", "money"),
    _col("rate", "Rate", "pct"),
]

# Deliberately RAGGED: 3 rows in one cell, 1 in another. A mean-of-means is only correct on a
# balanced grid, so an unbalanced fixture is the only one that can catch the bug.
ROWS = [
    {"store": "A", "month": "Jul", "amount": 100, "rate": 10},
    {"store": "A", "month": "Jul", "amount": 200, "rate": 20},
    {"store": "A", "month": "Jul", "amount": 300, "rate": 60},
    {"store": "A", "month": "Aug", "amount": 400, "rate": 100},
    {"store": "B", "month": "Jul", "amount": 50,  "rate": 5},
]

print("\n── SHAPE ──")
p = pivot(ROWS, COLS, "store", "month", "amount")
ok("row keys", p["row_keys"] == ["A", "B"], str(p["row_keys"]))
ok("col keys", p["col_keys"] == ["Jul", "Aug"], str(p["col_keys"]))
ok("cell A/Jul sums", p["cells"]["A"]["Jul"] == 600, str(p["cells"]))
ok("cell A/Aug", p["cells"]["A"]["Aug"] == 400)
ok("empty cell is ABSENT, not 0 (0 is a real value and must stay distinguishable)",
   "Aug" not in p["cells"]["B"], str(p["cells"]["B"]))
ok("per-cell counts exposed for drill-through", p["counts"]["A"]["Jul"] == 3)

print("\n── RECONCILIATION — a total that disagrees with its own grid is the whole failure mode ──")
ok("row total = its cells", p["row_totals"]["A"] == 1000)
ok("col total = its cells", p["col_totals"]["Jul"] == 650)
ok("grand = Σ rows", p["grand_total"] == sum(p["row_totals"].values()))
ok("grand = Σ cols", p["grand_total"] == sum(p["col_totals"].values()))
ok("grand = Σ raw", p["grand_total"] == sum(r["amount"] for r in ROWS))

print("\n── THE MEAN-OF-MEANS TRAP ──")
q = pivot(ROWS, COLS, "store", "month", "rate")
ok("pct aggregates with avg", q["agg"] == "avg", q["agg"])
ok("cell A/Jul = mean(10,20,60) = 30", q["cells"]["A"]["Jul"] == 30)
# Store A raw rates are 10,20,60,100 → mean 47.5.
# Mean-of-CELLS would be (30 + 100)/2 = 65. That is the bug this asserts against.
ok("row subtotal is mean of RAW values (47.5), NOT mean of cells (65)",
   q["row_totals"]["A"] == 47.5, "got " + str(q["row_totals"]["A"]))
ok("col subtotal Jul = mean(10,20,60,5) = 23.75",
   q["col_totals"]["Jul"] == 23.75, "got " + str(q["col_totals"]["Jul"]))
ok("grand = mean of ALL raw rates (39.0), not a mean of subtotals",
   q["grand_total"] == 39.0, "got " + str(q["grand_total"]))

print("\n── COUNT MEASURE ──")
c = pivot(ROWS, COLS, "store", "month", COUNT_MEASURE)
ok("counts rows", c["cells"]["A"]["Jul"] == 3 and c["cells"]["B"]["Jul"] == 1)
ok("grand count == len(rows)", c["grand_total"] == len(ROWS))
ok("a TEXT column is not a measure — falls back to counting",
   pivot(ROWS, COLS, "store", "month", "month")["measure"] == COUNT_MEASURE)
ok("an unknown measure falls back to counting",
   pivot(ROWS, COLS, "store", "month", "nope")["measure"] == COUNT_MEASURE)

print("\n── BLANKS + MISSING VALUES ──")
b = pivot([{"store": None, "month": "", "amount": 5}], COLS, "store", "month", "amount")
ok("null/empty dimensions bucket as (blank), never dropped",
   b["row_keys"] == ["(blank)"] and b["col_keys"] == ["(blank)"])
n = pivot([{"store": "A", "month": "Jul", "amount": None},
           {"store": "A", "month": "Jul", "amount": 7}], COLS, "store", "month", "amount")
ok("non-numeric measure values are skipped, not coerced to 0", n["cells"]["A"]["Jul"] == 7)
ok("...and the COUNT still reflects them", n["counts"]["A"]["Jul"] == 1)

print("\n── WIDE PIVOTS ARE CAPPED LOUDLY, NEVER SILENTLY ──")
wide = [{"store": "A", "month": f"M{i:03d}", "amount": 1} for i in range(60)]
w = pivot(wide, COLS, "store", "month", "amount", max_cols=10)
ok("capped to max_cols", len(w["col_keys"]) == 10, str(len(w["col_keys"])))
ok("truncation is REPORTED", w["truncated_cols"] is True and len(w["dropped_cols"]) == 50)
ok("totals reconcile with what SURVIVED (not with the hidden rows)",
   w["grand_total"] == sum(w["col_totals"].values()) == 10)
ok("an uncapped pivot reports no truncation",
   pivot(ROWS, COLS, "store", "month", "amount")["truncated_cols"] is False)

print("\n── AXES COME FROM THE GATED CATALOG ──")
ds = dataset_by_key("sales_line")
dims, meas = pivot_axes(ds, grants=set())
ok("dimensions are the groupable columns", {d["field"] for d in dims} >= {"store", "market", "category"},
   str([d["field"] for d in dims]))
ok("measures are the numeric columns", {m["field"] for m in meas} >= {"ext_price", "gp"},
   str([m["field"] for m in meas]))
ok("row count is always offered first", meas[0]["field"] == COUNT_MEASURE)
ok("no dimension is also a measure", not ({d["field"] for d in dims} & {m["field"] for m in meas}))

# A gated money column must not become pivotable for someone who cannot see the column.
ma = dataset_by_key("ma_commission")
gated = [c for c in ma["columns"] if c.get("gate")]
if gated:
    g = gated[0]
    _, m_no = pivot_axes(ma, grants=set())
    _, m_yes = pivot_axes(ma, grants={g["gate"]})
    ok(f"gated measure `{g['field']}` hidden without the grant",
       g["field"] not in {m["field"] for m in m_no})
    ok(f"gated measure `{g['field']}` offered WITH the grant",
       g["field"] in {m["field"] for m in m_yes})
else:
    print("  (skip) ma_commission has no gated column in this build")

print("\n── EVERY SHIPPED DATASET CAN ACTUALLY PIVOT ──")
for key in ["sales_line", "rep_commissions", "targets_actuals", "kpi_metrics", "store_expenses",
            "chargebacks", "flags", "ma_commission", "ma_daily_tx"]:
    d = dataset_by_key(key)
    dims, meas = pivot_axes(d, grants=set())
    ok(f"{key}: {len(dims)} dims × {len(meas)} measures", len(dims) >= 1 and len(meas) >= 1,
       "a dataset with no groupable column cannot be pivoted")

print("\n── SAVED DEFINITIONS MUST ROUND-TRIP THE PIVOT ──")
# The validator is a whitelist. Before this package it dropped every pivot key, so the UI would have
# reported "Saved ✓" and the pivot would have been gone on reload — the same silent-drop class as the
# nav-layout write path. Asserted here so a future config field cannot regress it unnoticed.
from app.modules.commcalc.custom_report import validate_definition  # noqa: E402
okv, res = validate_definition(
    {"name": "Store × Month", "config": {"datasets": ["sales_line"], "pivot_rows": "store",
                                         "pivot_cols": "trans_date", "pivot_measure": "ext_price"}},
    {"sales_line"})
ok("definition validates", okv is True, str(res))
ok("pivot_rows survives the whitelist", res["config"].get("pivot_rows") == "store", str(res.get("config")))
ok("pivot_cols survives", res["config"].get("pivot_cols") == "trans_date")
ok("pivot_measure survives", res["config"].get("pivot_measure") == "ext_price")
ok("a definition with no pivot round-trips as empty strings, not None",
   validate_definition({"name": "x", "config": {"datasets": ["sales_line"]}}, {"sales_line"})[1]["config"]["pivot_rows"] == "")

print(f"\n{P}/{P + F} passed" + (f"  — {F} FAILED" if F else ""))
sys.exit(1 if F else 0)
