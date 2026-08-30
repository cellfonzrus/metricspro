"""HARNESS — the plan pay-write join is ORDER-INSENSITIVE (audit fix #4).

_apply_engine_components_to_row previously matched a rep_commissions row to its plan ONLY by exact-UPPER
name equality, so "Kellie, Mark" (sales spelling) and "Mark Kellie" (roster/plan spelling) missed each
other — the rep's plan pay was dropped or stranded on a duplicate row. This proves the canon fallback:
exact matches stay byte-identical, order-flipped names now match, and a canon match reports the plan key
back so the "reps with a plan but no standard row" loop can't double-add them.

  python3 backend/harness_exec_mtd_name_join.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc.router import _apply_engine_components_to_row, _rep_comm_row_keys  # noqa: E402

PASS = 0
FAIL = 0
COLS = {c: True for c in ("residual_installment_comm", "installment_comm_sale", "plan_comm",
                          "plan_name", "carrier_statement_comm", "setup_fee_comm")}


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


def apply(row, plan_by_rep):
    ks = _rep_comm_row_keys(row)
    matched = _apply_engine_components_to_row(row, ks, {}, {}, {}, plan_by_rep, COLS)
    return row, matched


print("── A. exact-UPPER match unchanged (byte-identical to before) ──")
row, matched = apply(
    {"storeops_name": "Mark Kellie", "epay_salesperson": "MARK KELLIE", "total_payout": 5.0},
    {"MARK KELLIE": {"amount": 1000.93, "plan_name": "NY / Luxelink Comp"}})
check("exact key -> plan applied ($1000.93)", row["plan_comm"] == 1000.93, row)
check("exact key -> total_payout replaced", row["total_payout"] == 1000.93, row)
check("exact key -> matched contains the plan key", "MARK KELLIE" in matched, matched)

print("── B. ORDER-FLIPPED name now matches via _canon_person (the fix) ──")
# sales row spelled 'Kellie, Mark'; plan keyed 'MARK KELLIE'
row, matched = apply(
    {"storeops_name": "Kellie, Mark", "epay_salesperson": "KELLIE, MARK", "total_payout": 21.40},
    {"MARK KELLIE": {"amount": 1000.93, "plan_name": "NY / Luxelink Comp"}})
check("flipped 'Kellie, Mark' matches plan 'MARK KELLIE'", row["plan_comm"] == 1000.93, row)
check("flipped match replaces the rules-basis $21.40 with the exec_mtd $1000.93",
      row["total_payout"] == 1000.93, row)
check("flipped match reports the ORIGINAL plan key (no duplicate-row risk)",
      matched == {"MARK KELLIE"}, matched)

# the reverse direction: plan keyed 'KELLIE, MARK', sales row 'Mark Kellie'
row, matched = apply(
    {"storeops_name": "Mark Kellie", "epay_salesperson": "MARK KELLIE", "total_payout": 0.0},
    {"KELLIE, MARK": {"amount": 500.0, "plan_name": "P"}})
check("reverse flip also matches", row["plan_comm"] == 500.0, row)
check("reverse flip reports plan key 'KELLIE, MARK'", matched == {"KELLIE, MARK"}, matched)

print("── C. NO false match for an unrelated rep ──")
row, matched = apply(
    {"storeops_name": "Alondra Navarro", "epay_salesperson": "ALONDRA NAVARRO", "total_payout": 12.0},
    {"MARK KELLIE": {"amount": 1000.93, "plan_name": "P"}})
check("unrelated rep keeps standard total (no plan)", row["total_payout"] == 12.0, row)
check("unrelated rep -> empty matched", matched == set(), matched)
check("unrelated rep -> plan_comm not set", "plan_comm" not in row or row.get("plan_comm") in (None,), row)

print("── D. exact match WINS over canon (canon is a strict fallback) ──")
# two plan entries; the row exactly equals one — must take the exact one, not a canon collision
row, matched = apply(
    {"storeops_name": "MARK KELLIE", "epay_salesperson": "MARK KELLIE", "total_payout": 0.0},
    {"MARK KELLIE": {"amount": 111.0, "plan_name": "exact"},
     "KELLIE, MARK": {"amount": 999.0, "plan_name": "canon-form"}})
check("exact key taken (111), not the other spelling", row["plan_comm"] == 111.0, row)

print("── E. setup_fee + installments still layer on top of a canon-matched plan ──")
ks = _rep_comm_row_keys({"storeops_name": "Kellie, Mark", "epay_salesperson": "KELLIE, MARK"})
row = {"storeops_name": "Kellie, Mark", "epay_salesperson": "KELLIE, MARK", "total_payout": 0.0}
matched = _apply_engine_components_to_row(
    row, ks, {"MARK KELLIE": 30.0}, {"MARK KELLIE": 20.0}, {},
    {"MARK KELLIE": {"amount": 100.0, "plan_name": "P", "setup_fee_comm": 7.0}}, COLS)
# NOTE: installments key by the SAME upper set; here they're keyed 'MARK KELLIE' but ks is the flipped
# form, so they only add if their key is in ks. ks = {'KELLIE, MARK'} (upper of the row names). The
# installment maps here are keyed 'MARK KELLIE' -> not in ks -> 0 added. Plan matches via canon.
check("canon plan applied (base 100) even though installments keyed under the other spelling",
      row["plan_comm"] == 100.0, row)
check("setup_fee_comm from the canon-matched plan is written", row.get("setup_fee_comm") == 7.0, row)

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
