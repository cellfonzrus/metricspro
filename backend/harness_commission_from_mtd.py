"""HARNESS — commission computed DIRECTLY from the Executive MTD per-employee numbers (owner 2026-08-30).

Proves the pure math of the /commission-mtd endpoint: for each rep, commission =
    Total Activation × the plan's flat activation rate  +  Acc. Sales × the plan's accessory %.
So the payout basis is byte-identical to what the owner already sees on Executive MTD — the accessory
number visible on that report is the SAME one that pays, closing the "shows in Exec MTD but not in the
payout" gap (the two accessory classifiers had drifted).

  python3 backend/harness_commission_from_mtd.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc.router import _plan_mtd_rates, _commission_from_mtd_rows  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


# ── the NY plan shape: $10/activation (activation_bucket) + 10% accessories ─────────────────────────
NY_PLAN = {"rules": [
    {"match_field": "activation_bucket", "match_op": "in", "match_value": "premium,byod",
     "payout_kind": "flat_per_unit", "amount": 10, "pct": 0, "qualifies": True},
    {"match_field": "accessory", "match_op": "equals", "match_value": "yes",
     "payout_kind": "pct_price", "amount": 0, "pct": 0.10, "qualifies": True},
]}

print("── A. rate extraction from the plan rules ───────────────────────────────────────")
act, acc = _plan_mtd_rates(NY_PLAN)
check("activation flat rate = $10", act == 10.0, act)
check("accessory pct = 0.10", acc == 0.10, acc)

# a department-keyed activation rule (the service-plan variant) is also picked up
act2, _ = _plan_mtd_rates({"rules": [
    {"match_field": "department", "payout_kind": "flat_per_unit", "amount": 12, "qualifies": True}]})
check("department flat_per_unit is read as the activation rate ($12)", act2 == 12.0, act2)

# a disabled rule (qualifies=False) is ignored
act3, acc3 = _plan_mtd_rates({"rules": [
    {"match_field": "activation_bucket", "payout_kind": "flat_per_unit", "amount": 99, "qualifies": False}]})
check("a non-qualifying rule contributes no rate", act3 == 0.0 and acc3 == 0.0, (act3, acc3))

print("── B. per-rep commission from Exec MTD by_employee rows ─────────────────────────")
# Exactly the shape _exec_mtd returns: total_activation (excl. Upgrade on the AD basis) + acc_sales.
emp_rows = [
    {"employee": "Fozilova, Shakhnoza", "total_activation": 25, "acc_sales": 400.0},   # 25*10 + 40 = 290
    {"employee": "Navarro, Alondra",    "total_activation": 3,  "acc_sales": 0.0},      # 30 + 0   = 30
    {"employee": "Jacobo, Liset",       "total_activation": 0,  "acc_sales": 150.55},   # 0 + 15.06 (round)
]
rows = _commission_from_mtd_rows(emp_rows, act, acc)
by = {r["employee"]: r for r in rows}
check("rep A: 25 act × $10 + $400 × 10% = $290", by["Fozilova, Shakhnoza"]["commission"] == 290.0,
      by["Fozilova, Shakhnoza"])
check("rep A activation_pay = $250", by["Fozilova, Shakhnoza"]["activation_pay"] == 250.0, by["Fozilova, Shakhnoza"])
check("rep A accessory_pay = $40", by["Fozilova, Shakhnoza"]["accessory_pay"] == 40.0, by["Fozilova, Shakhnoza"])
check("rep B: 3 act × $10 + $0 = $30", by["Navarro, Alondra"]["commission"] == 30.0, by["Navarro, Alondra"])
check("rep C: 0 act + $150.55 × 10% = $15.06 (rounded)", by["Jacobo, Liset"]["commission"] == 15.06,
      by["Jacobo, Liset"])
check("rows are sorted by commission desc", [r["employee"] for r in rows][0] == "Fozilova, Shakhnoza",
      [r["employee"] for r in rows])

print("── C. accessory number on Exec MTD == the number that pays (the whole point) ─────")
# The owner's complaint: acc_sales shows on Exec MTD but not in the payout. Here the SAME acc_sales drives
# accessory_pay, so a non-zero Exec MTD accessory number can never silently pay $0.
only_acc = _commission_from_mtd_rows([{"employee": "X", "total_activation": 0, "acc_sales": 1000.0}], 10.0, 0.10)
check("an Exec-MTD accessory number always pays (1000 × 10% = $100)", only_acc[0]["commission"] == 100.0,
      only_acc[0])

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
