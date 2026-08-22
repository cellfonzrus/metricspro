"""Harness for Carrier Reconciliation (upload & reconcile).

Proves, with NO database:
  (a) the PURE parser reproduces the authoritative Jul-2026 totals from the real sample workbook, and
  (b) the Boost-vs-ours diff/merge logic (`build_comparison`) is correct, using Sheet1 (the raw
      per-transaction feed) as a SYNTHETIC "ours" side.

Run:  python harness_carrier_recon.py
"""

import io
import sys

import openpyxl

from app.modules.commcalc import carrier_recon as cr

SAMPLE = ("/root/.claude/uploads/30ae494a-a623-5c2d-9fab-a98a3000e8f7/"
          "4aaa0041-CellularOperationsNJRebate_ReconciliationJuly_2026.xlsx")
CENT = 0.011

_passed = 0
_failed = 0


def check(name, cond, got=None, want=None):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        extra = "" if got is None and want is None else f"   (got={got!r} want={want!r})"
        print(f"  FAIL  {name}{extra}")


def approx(a, b, tol=CENT):
    return abs(float(a) - float(b)) <= tol


def sheet1_ours_map(xlsx_bytes):
    """Build a synthetic OUR-side map from Sheet1: {norm(store): {rebate_paid, comm_paid, epay_paid}}
    by summing the raw feed's paid columns per store. This is the ground truth the workbook's Grand
    Total is itself pivoted from, so a correct diff against it is ~0 per store."""
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True, read_only=True)
    ws = wb["Sheet1"]
    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    si, ri, ci, ei = (hdr.index("Store"), hdr.index("Rebate Paid"),
                      hdr.index("Comm Paid"), hdr.index("ePay Paid"))

    def f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    out = {}
    for row in it:
        sv = row[si]
        if sv is None or str(sv).strip() == "":
            continue
        k = " ".join(str(sv).strip().split()).lower()
        b = out.setdefault(k, {"rebate_paid": 0.0, "comm_paid": 0.0, "epay_paid": 0.0})
        b["rebate_paid"] = round(b["rebate_paid"] + f(row[ri]), 2)
        b["comm_paid"] = round(b["comm_paid"] + f(row[ci]), 2)
        b["epay_paid"] = round(b["epay_paid"] + f(row[ei]), 2)
    wb.close()
    return out


def main():
    data = open(SAMPLE, "rb").read()
    parsed = cr.parse_workbook(data, period="Jul-2026")
    t = parsed["totals"]

    # ── (a) authoritative Jul-2026 totals ────────────────────────────────────────────────────────
    print("(a) Parser reproduces authoritative Jul-2026 totals:")
    check("store count = 19", t["store_count"] == 19, t["store_count"], 19)
    check("Device Cost = 249,382.87", approx(t["device_cost"], 249382.87), t["device_cost"])
    check("Rebate Expected = 210,879.19", approx(t["rebate_expected"], 210879.19), t["rebate_expected"])
    check("Rebate Paid = 207,179.24", approx(t["rebate_paid"], 207179.24), t["rebate_paid"])
    check("Rebate Diff = -3,699.95", approx(t["rebate_diff"], -3699.95), t["rebate_diff"])
    check("Comm Paid = 10,533.68", approx(t["comm_paid"], 10533.68), t["comm_paid"])
    check("ePay Paid = 216,646.53", approx(t["epay_paid"], 216646.53), t["epay_paid"])
    check("Withhold = 0", approx(t["withhold"], 0.0), t["withhold"])
    check("Escalation items = 124", t["escalation_count"] == 124, t["escalation_count"], 124)
    check("Escalation Expected = 3,822.55", approx(t["escalation_expected"], 3822.55),
          t["escalation_expected"])
    check("Unpaid Devices = 14", t["unpaid_count"] == 14, t["unpaid_count"], 14)
    check("Unpaid cost = 4,159.86", approx(t["unpaid_cost"], 4159.86), t["unpaid_cost"])
    check("Missing = 1", t["missing_count"] == 1, t["missing_count"], 1)
    check("Sheet1 raw_txn_count = 1063", parsed["raw_txn_count"] == 1063, parsed["raw_txn_count"], 1063)
    check("19 store records parsed", len(parsed["stores"]) == 19, len(parsed["stores"]), 19)
    check("rep drilldown populated (>0 reps total)",
          sum(len(s["reps"]) for s in parsed["stores"]) > 0)
    # per-store block1 == commissions block alignment sanity: every store has an epay_paid figure
    check("every store carries an ePay Paid figure",
          all("epay_paid" in s for s in parsed["stores"]))

    # ── (b) Boost-vs-ours diff/merge with Sheet1 as synthetic ours ───────────────────────────────
    print("\n(b) Boost-vs-ours diff/merge (Sheet1 as synthetic ours):")
    ours = sheet1_ours_map(data)
    # give GP the Boost figure so the money-field diffs are the focus and GP diff is exactly 0 too
    for s in parsed["stores"]:
        k = " ".join(str(s["store"]).strip().split()).lower()
        if k in ours:
            ours[k]["gp"] = round(float(s["gp"]), 2)

    cmp = cr.build_comparison(parsed, ours, resolve=None)
    check("all 19 stores matched", all(r["matched"] for r in cmp["per_store"]),
          sum(r["matched"] for r in cmp["per_store"]), 19)
    check("no unmatched stores when ours covers all", cmp["unmatched_stores"] == [],
          cmp["unmatched_stores"])

    worst = {f: 0.0 for f in ("rebate_paid", "comm_paid", "epay_paid", "gp")}
    for r in cmp["per_store"]:
        for f in worst:
            worst[f] = max(worst[f], abs(r["diff"][f]))
    check("per-store rebate_paid diff ~ 0 (boost==ours)", worst["rebate_paid"] <= CENT, worst["rebate_paid"])
    check("per-store comm_paid diff ~ 0", worst["comm_paid"] <= CENT, worst["comm_paid"])
    check("per-store epay_paid diff ~ 0", worst["epay_paid"] <= CENT, worst["epay_paid"])
    check("per-store gp diff ~ 0", worst["gp"] <= CENT, worst["gp"])

    # diff == boost - ours identity on a spot store
    spot = cmp["per_store"][0]
    ident = approx(spot["diff"]["rebate_paid"], spot["boost"]["rebate_paid"] - spot["ours"]["rebate_paid"])
    check("diff = boost - ours identity holds", ident)

    # company totals match the parsed Grand Total on the ours side too
    check("ours totals rebate_paid == workbook Grand Total",
          approx(cmp["totals"]["ours"]["rebate_paid"], 207179.24), cmp["totals"]["ours"]["rebate_paid"])
    check("ours totals epay_paid == workbook Grand Total",
          approx(cmp["totals"]["ours"]["epay_paid"], 216646.53), cmp["totals"]["ours"]["epay_paid"])
    check("boost totals comm_paid == workbook Grand Total",
          approx(cmp["totals"]["boost"]["comm_paid"], 10533.68), cmp["totals"]["boost"]["comm_paid"])

    # ── unmatched handling: drop one store from ours ─────────────────────────────────────────────
    print("\n(b') Unmatched handling (drop one store from ours):")
    dropped_name = parsed["stores"][0]["store"]
    dropped_key = " ".join(str(dropped_name).strip().split()).lower()
    ours2 = {k: v for k, v in ours.items() if k != dropped_key}
    cmp2 = cr.build_comparison(parsed, ours2, resolve=None)
    check("dropped store is listed in unmatched_stores", dropped_name in cmp2["unmatched_stores"],
          cmp2["unmatched_stores"])
    check("exactly one unmatched store", len(cmp2["unmatched_stores"]) == 1,
          len(cmp2["unmatched_stores"]))
    drow = next(r for r in cmp2["per_store"] if r["store"] == dropped_name)
    check("dropped store not matched", drow["matched"] is False, drow["matched"])
    check("dropped store ours zeroed", drow["ours"]["rebate_paid"] == 0.0, drow["ours"]["rebate_paid"])
    check("dropped store diff == boost (nothing on ours side)",
          approx(drow["diff"]["rebate_paid"], drow["boost"]["rebate_paid"]))
    check("dropped store STILL returned (never silently dropped)",
          any(r["store"] == dropped_name for r in cmp2["per_store"]))

    print(f"\n==== {_passed} passed, {_failed} failed ====")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
