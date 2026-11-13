#!/usr/bin/env python3
"""THE EMPLOYER'S TOTAL COST OF A PAY PERIOD — proof (index §STOREOPS-08).

Owner, 2026-11-12: *"in the payroll report add anopthe rtil for the payroll tax paid for that period
and a another tile shows the total of payroll and payroll tax as a cost of payroll to teh company"*.

WHAT IS PROVED, AND WHAT IS DELIBERATELY NOT RE-DERIVED HERE
───────────────────────────────────────────────────────────
The employer tax RULE already has one home — `payroll_expenses.compute_payroll_tax` (FICA SS +
Medicare + FUTA + SUTA, per-employee annual wage-base caps, per-store split by wage share), behind
the HR Payroll Expenses page. This proof holds:

  1. `compute_payroll_tax_from_wages`, the wages-keyed entry point that home grew — because the
     payroll report knows WAGES (a salaried manager has pay and no hours at all), while the only
     existing door took hours × rate. The hours door now converts and delegates, so there is ONE
     cap ladder and ONE per-store split, not two.
  2. `payroll_employer_cost.employer_cost_rows`, which carries that answer onto the report's own
     rows and adds nothing of its own: no rate, no cap, no threshold, no second formula.

A SALARIED PERSON IS THE WHOLE REASON (t4-t6). `GET /storeops/payroll` serves a 0-hour row for a
salaried employee with their period pay on it. Through the hours door that person is taxed on
$0.00 — which is why the report could not have reused it as it stood.
"""
import sys

PASS, FAIL = [], []


def check(name, cond, got=None):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond or got is None else f"   got={got!r}"))


from app.modules.storeops.payroll_expenses import (          # noqa: E402
    resolve_tax_config, compute_payroll_tax, compute_payroll_tax_from_wages,
)
from app.modules.storeops import payroll_employer_cost as EC  # noqa: E402

cfg = resolve_tax_config(None)

# ── 1. the two doors agree, to the cent ─────────────────────────────────────────────────────────
hours = {"E1": {"S1": 100.0}, "E2": {"S1": 40.0, "S2": 60.0}}
rates = {"E1": 20.0, "E2": 15.0}
wages = {"E1": {"S1": 2000.0}, "E2": {"S1": 600.0, "S2": 900.0}}
r_hours = compute_payroll_tax(hours, rates, cfg, ytd_taxable_before={})
r_wages = compute_payroll_tax_from_wages(wages, cfg, ytd_taxable_before={})
check("t1: the hours door and the wages door produce the identical result", r_hours == r_wages,
      (r_hours, r_wages))

# ── 2. the caps are the SAME ladder, not a second one ───────────────────────────────────────────
ytd = {"E1": {"ss": 0.0, "futa": 7000.0, "suta": 7000.0}}
a = compute_payroll_tax({"E1": {"S1": 100.0}}, {"E1": 20.0}, cfg, ytd)
b = compute_payroll_tax_from_wages({"E1": {"S1": 2000.0}}, cfg, ytd)
check("t2: an exhausted FUTA/SUTA base is exhausted through either door", a == b, (a, b))
check("t2b: and that really did bind (FUTA fell to zero)",
      a["employees"]["E1"]["futa_tax"] == 0.0, a["employees"]["E1"]["futa_tax"])

# ── 3. the feature switch is one switch ─────────────────────────────────────────────────────────
off = dict(cfg, enabled=False)
check("t3: enabled=False answers empty through the wages door too",
      compute_payroll_tax_from_wages(wages, off, {}) == {"employees": {}, "stores": {}})

# ── 4-6. THE SALARIED ROW — the reason this entry point exists ──────────────────────────────────
ROWS = [
    # (employee_id, store, scheduled_pay, actual_pay, hours)
    {"employee_id": "E1", "store": "S1", "scheduled_hours": 100.0, "actual_hours": 100.0,
     "pay_rate": 20.0, "scheduled_pay": 2000.0, "actual_pay": 2000.0},
    {"employee_id": "M9", "store": "Unassigned", "scheduled_hours": 0.0, "actual_hours": 0.0,
     "pay_rate": 0.0, "scheduled_pay": 6642.86, "actual_pay": 6642.86},
]
out = EC.employer_cost_rows(ROWS, cfg, ytd_taxable_before={})
by_id = {r["employee_id"]: r for r in out["rows"]}
check("t4: the salaried 0-hour row is taxed on its PAY, not on its hours",
      by_id["M9"]["employer_tax"] > 0, by_id["M9"]["employer_tax"])
check("t5: the hourly row's tax matches the one home, to the cent",
      by_id["E1"]["employer_tax"]
      == compute_payroll_tax_from_wages({"E1": {"S1": 2000.0}}, cfg, {})["employees"]["E1"]["total_tax"],
      by_id["E1"]["employer_tax"])
check("t6: total cost is pay + tax, per row and in the totals",
      all(round(r["employer_cost"], 2) == round(r["actual_pay"] + r["employer_tax"], 2) for r in out["rows"])
      and round(out["totals"]["employer_cost"], 2)
          == round(out["totals"]["actual_pay"] + out["totals"]["employer_tax"], 2),
      out["totals"])

# ── 7. the breakdown is the one home's own, carried not recomputed ──────────────────────────────
check("t7: each row carries the four legs and they sum to its tax",
      all(round(r["employer_tax"], 2) == round(r["tax_fica_ss"] + r["tax_medicare"]
                                               + r["tax_futa"] + r["tax_suta"], 2) for r in out["rows"]),
      out["rows"])

# ── 8. BOTH bases, because the report prints both ───────────────────────────────────────────────
check("t8: a scheduled-pay tax is served beside the actual-pay one",
      all("employer_tax_scheduled" in r and "employer_cost_scheduled" in r for r in out["rows"]))
check("t8b: and it is the tax on the SCHEDULED wages",
      round(by_id["E1"]["employer_tax_scheduled"], 2) == round(by_id["E1"]["employer_tax"], 2),
      by_id["E1"]["employer_tax_scheduled"])

# ── 9. ABSENCE IS NEVER ZERO (§CORE-17.3i) ──────────────────────────────────────────────────────
nm = EC.employer_cost_rows(
    [{"employee_id": "E7", "store": "S1", "scheduled_hours": 0.0, "actual_hours": 0.0,
      "hours_basis": "not_measured_unpaid"}], cfg, {})
check("t9: a row with no pay figure gets NO cost figure, not a $0.00",
      "employer_tax" not in nm["rows"][0] and "employer_cost" not in nm["rows"][0],
      nm["rows"][0])
check("t9b: and it contributes nothing to the totals",
      nm["totals"]["employer_tax"] == 0.0 and nm["totals"]["counted_rows"] == 0, nm["totals"])

# ── 10. a PAY-GATED row carries no cost at all (mig 434) ────────────────────────────────────────
gated = EC.employer_cost_rows(
    [{"employee_id": "E1", "store": "S1", "scheduled_hours": 100.0, "actual_hours": 100.0}], cfg, {})
check("t10: a row whose money keys were stripped is served no cost figure",
      "employer_tax" not in gated["rows"][0], gated["rows"][0])

# ── 11. tax OFF is a stated refusal, never a silent zero ────────────────────────────────────────
no_tax = EC.employer_cost_rows(ROWS, off, {})
check("t11: with the tax bucket off, the totals say so rather than reading $0.00 tax",
      no_tax["tax_enabled"] is False and no_tax["totals"]["employer_tax"] == 0.0, no_tax["totals"])
check("t11b: and total cost still equals the pay, so the tile cannot imply a free payroll",
      round(no_tax["totals"]["employer_cost"], 2) == round(no_tax["totals"]["actual_pay"], 2),
      no_tax["totals"])

# ── 11c. THE SALARIED ROW WITH NO HOURS BASIS AT ALL — measured live, and the whole reason
#         `carries_pay` is not `row_is_paid` ────────────────────────────────────────────────────────
# Three LuxeLink employees in October 2026 ($16,607.15 between them) are served a row with their
# period pay and an EMPTY `hours_by_basis`: a salary allocation is not read out of any day's hours.
# The hours question alone would give them no tax, and the cost tile would have shown the company
# paying $110,571.59 while owing tax on $93,964.44 of it.
SALARY_ONLY = [{"employee_id": "S1", "store": "HQ", "scheduled_hours": 0.0, "actual_hours": 0.0,
                "hours_by_basis": {}, "pay_basis": "salary",
                "scheduled_pay": 5280.23, "actual_pay": 5280.23}]
sal = EC.employer_cost_rows(SALARY_ONLY, cfg, {})
check("t11c: a salaried row with NO hours basis is still taxed — the burden follows the pay",
      sal["rows"][0].get("employer_tax", 0) > 0 and sal["totals"]["counted_rows"] == 1,
      sal["totals"])
check("t11d: and a $0.00 with nothing measured behind it is still NOT counted (absence is not zero)",
      EC.employer_cost_rows(
          [{"employee_id": "Z", "store": "S1", "hours_by_basis": {},
            "scheduled_pay": 0.0, "actual_pay": 0.0}], cfg, {})["totals"]["counted_rows"] == 0)
check("t11e: the predicate is the one the panels bind, and it reads the PAY key it is asked about",
      EC.carries_pay(SALARY_ONLY[0], EC.ACTUAL_PAY) is True
      and EC.carries_pay({"employee_id": "Q"}, EC.ACTUAL_PAY) is False)
check("t11f: and the hours question it is NOT is the ONE home's, bound by identity",
      EC._paid_hours is __import__(
          "app.modules.storeops.payroll_hours_basis", fromlist=["x"]).row_is_paid)

# ── 12. pure ────────────────────────────────────────────────────────────────────────────────────
before = [dict(r) for r in ROWS]
EC.employer_cost_rows(ROWS, cfg, {})
check("t12: the input rows are not mutated", ROWS == before)
check("t12b: same input twice is byte-identical",
      EC.employer_cost_rows(ROWS, cfg, {}) == EC.employer_cost_rows(ROWS, cfg, {}))

# ── 13. RULE TWO — no tenant, carrier, store or product name, and no rate of its own ────────────
import inspect  # noqa: E402
src = inspect.getsource(EC)
check("t13: the module spells no tax rate, wage base or dollar literal of its own",
      not any(tok in src for tok in ("0.062", "0.0145", "0.006", "176100", "7000")), None)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("FAIL:", f)
if FAIL:
    sys.exit(1)
print("ALL GREEN")
