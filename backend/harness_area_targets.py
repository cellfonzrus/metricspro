"""HARNESS — DM collective (area) targets + per-store drill-down (mod-commission, 2026-08-03).

Proves the invariants the owner's ask depends on:

  A. AGGREGATE == Σ PER-STORE, per category, per metric. This is the whole point: a DM cannot act on
     a total that does not reconcile to the drill-down under it.
  B. SPAN — a span-restricted viewer's collective covers ONLY their stores, and an unrestricted admin's
     is unchanged (the full roll-up). Modelled by feeding `aggregate_stores` the exact row set the
     endpoint's keyset filter would have produced.
  C. RATIO-OF-SUMS conversion — the area rate is Σboxes ÷ Σbill-pays, NOT the mean of store rates
     (averaging percentages lets a 2-transaction store outvote a 400-transaction one).
  D. DEGENERATE inputs — no stores, zero targets, missing categories → no crash, no divide-by-zero.

Pure/offline: `targets_engine.aggregate_stores` has no I/O.
    python3 backend/harness_area_targets.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import targets_engine as te   # noqa: E402

CATS = te.CATEGORIES
METRICS = ("monthly", "achieved_mtd", "need", "today_target", "pace", "base_today")


def store(code, market, vals, boxes=0, billpays=0, trend_acc=0.0):
    """vals = {cat: (monthly, achieved, need, today, pace, base_today)}"""
    cats = {}
    for c, v in vals.items():
        cats[c] = {"unit": te.UNITS[c], "monthly": v[0], "achieved_mtd": v[1], "need": v[2],
                   "today_target": v[3], "pace": v[4], "base_today": v[5]}
    return {"store_code": code, "address": f"{code} Main St", "market": market,
            "categories": cats, "scheduled_hours_total": 160.0,
            "trending_acc_sales": trend_acc, "trending_acc_target": trend_acc, "trending_box": 0,
            "conversion": {"store": {"boxes": boxes, "billpays": billpays, "target": 30.0}}}


ROWS = [
    store("S1", "NY", {"activations": (100, 62, 38, 4.1, 2.7, 3.2),
                       "upgrades": (40, 18, 22, 1.9, 1.6, 1.3),
                       "byod": (35, 40, 0, 0.0, 0.0, 1.1),
                       "accessories": (12000, 7400.55, 4599.45, 410.20, 328.53, 385.00)},
          boxes=90, billpays=300, trend_acc=11200.0),
    store("S2", "NY", {"activations": (80, 91, 0, 0.0, 0.0, 2.6),
                       "upgrades": (30, 12, 18, 1.5, 1.3, 1.0),
                       "byod": (28, 15, 13, 1.1, 0.9, 0.9),
                       "accessories": (9000, 9600.10, 0.0, 0.0, 0.0, 290.00)},
          boxes=140, billpays=350, trend_acc=10100.0),
    store("S3", "NJ", {"activations": (60, 25, 35, 3.0, 2.5, 1.9),
                       "upgrades": (20, 5, 15, 1.4, 1.1, 0.6),
                       "byod": (18, 4, 14, 1.3, 1.0, 0.6),
                       "accessories": (7000, 2200.00, 4800.00, 420.00, 342.86, 225.00)},
          boxes=20, billpays=250, trend_acc=3400.0),
]


def _sum(rows, cat, metric):
    return sum(float(((r.get("categories") or {}).get(cat) or {}).get(metric) or 0) for r in rows)


def check_identity(label, rows):
    agg = te.aggregate_stores(rows)
    ok = True
    for cat in CATS:
        a = agg["categories"][cat]
        for m in METRICS:
            want = round(_sum(rows, cat, m), 1 if te.UNITS[cat] == "count" else 2)
            got = round(float(a.get(m) or 0), 1 if te.UNITS[cat] == "count" else 2)
            if abs(want - got) > 0.005:
                ok = False
                print(f"    MISMATCH {cat}.{m}: aggregate={got} vs Σstores={want}")
    print(f"  {label:<44} stores={agg['stores']:<3} "
          f"acc_target=${agg['categories']['accessories']['monthly']:>10,.2f} "
          f"acc_mtd=${agg['categories']['accessories']['achieved_mtd']:>10,.2f}  "
          f"{'PASS' if ok else 'FAIL'}")
    return ok, agg


def run():
    print("── A. AGGREGATE == Σ PER-STORE (every category × every metric) ─────────────────")
    ok_all, agg_all = check_identity("unrestricted admin (all 3 stores)", ROWS)

    print("── B. SPAN — restricted viewer sees ONLY their stores ──────────────────────────")
    ny = [r for r in ROWS if r["market"] == "NY"]          # what a NY-span DM's keyset would leave
    ok_ny, agg_ny = check_identity("NY-span DM (2 of 3 stores)", ny)
    solo = [ROWS[2]]
    ok_solo, agg_solo = check_identity("single-store manager (1 of 3)", solo)
    shrinks = (agg_ny["categories"]["activations"]["monthly"] < agg_all["categories"]["activations"]["monthly"]
               and agg_solo["categories"]["activations"]["monthly"] < agg_ny["categories"]["activations"]["monthly"])
    no_leak = set(agg_ny["store_codes"]) == {"S1", "S2"} and set(agg_solo["store_codes"]) == {"S3"}
    print(f"  {'restriction actually shrinks the total':<44} {'PASS' if shrinks else 'FAIL'}")
    print(f"  {'no out-of-span store code in the payload':<44} {'PASS' if no_leak else 'FAIL'}")
    print(f"  {'admin roll-up unchanged (3 stores, full Σ)':<44} "
          f"{'PASS' if agg_all['stores'] == 3 else 'FAIL'}")

    print("── C. CONVERSION = ratio of sums, not mean of rates ────────────────────────────")
    boxes, bp = 90 + 140 + 20, 300 + 350 + 250
    want = round(100.0 * boxes / bp, 1)
    mean_of_rates = round((30.0 + 40.0 + 8.0) / 3, 1)
    got = agg_all["conversion"]["rate"]
    conv_ok = got == want and got != mean_of_rates
    print(f"  ratio-of-sums={want}%  mean-of-rates={mean_of_rates}%  aggregate={got}%   "
          f"{'PASS' if conv_ok else 'FAIL'}")

    print("── D. DEGENERATE INPUTS ────────────────────────────────────────────────────────")
    deg = True
    try:
        e = te.aggregate_stores([])
        deg = deg and e["stores"] == 0 and e["categories"]["activations"]["attainment_pct"] is None
        z = te.aggregate_stores([store("Z", "NY", {c: (0, 0, 0, 0, 0, 0) for c in CATS})])
        deg = deg and z["categories"]["accessories"]["attainment_pct"] is None
        deg = deg and z["conversion"]["rate"] == 0.0
        partial = te.aggregate_stores([{"store_code": "P1", "categories": {"activations": {"monthly": 5, "achieved_mtd": 2}}}])
        deg = deg and partial["categories"]["activations"]["monthly"] == 5.0
        deg = deg and partial["categories"]["upgrades"]["monthly"] == 0.0
        print(f"  empty / all-zero / missing-categories rows        {'PASS' if deg else 'FAIL'}")
    except Exception as exc:
        deg = False
        print(f"  degenerate inputs raised: {exc}   FAIL")

    ok = ok_all and ok_ny and ok_solo and shrinks and no_leak and conv_ok and deg
    print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
