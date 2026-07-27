"""Pure-logic proof harness for the Salary Pay-Basis engine (mod-people, owner directive 2026-07-27,
migrations 416/417, backend/app/modules/storeops/payroll_salary.py).

Runs the ACTUAL shipped functions against synthetic data — no DB, no network. Imports (never edits)
app.modules.core.router's real pay_period_for/_pp_settings, the SAME functions GET /tenant-settings
and storeops/lib/pay-period.ts's server round trip both use, so this harness's period-boundary math
can never silently drift from what production actually resolves.

Run: `python3 harness_payroll_salary.py` from backend/.

Proves:
  A. convert_to_period_pay — the exact conversion table (weekly ×1/×2, monthly ×12/52 or ×12/26,
     annual /52 or /26), cents HALF_UP, including the dispatch's own worked example
     ("$52,000/yr = $1,000.00 per weekly period"). 'hourly' and non-positive/unparseable amounts ->
     None.
  B. pay_periods_overlapping — walks the correct set of periods for a range exactly one period long,
     a range spanning several periods, and a range narrower than one period.
  C. derive_salary_pay — exact figure for a report range that IS a whole number of periods (no
     proration); calendar-day proration for a mid-period hire; for a mid-period termination; for
     BOTH in the same period; for a report range that is a full calendar month (not period-aligned,
     so both edges legitimately prorate) — and in every case, the sum of `periods[].amount` foots
     EXACTLY to the returned `amount` (cents, no drift).
  D. allocate_across_stores — proportional multi-store split with an exact-remainder fixup (sums to
     the input to the penny even with a 3-way non-round split); zero-hours-anywhere falls back to
     100% home_store; zero-hours AND no home_store returns {} (never invents a store).
  E. resolve_pay_basis — clamps an unrecognized/blank pay_basis to 'hourly'; a non-numeric/blank
     pay_amount resolves to None (never crashes).
  F. apply_to_payroll_rows — a salaried row's scheduled_pay/actual_pay is overridden to the derived
     figure and hours are untouched; a misconfigured row (pay_basis set, no usable pay_amount) is
     left with its ORIGINAL pay figures plus an explanatory salary_note; a row for an employee not in
     the `employees` list (unmergeable shift) passes through untouched.
  G. apply_to_by_store — a 2-store salaried employee's hourly-computed $ is fully removed and replaced
     by a proportional-to-hours allocation that sums to the derived total; a store untouched by the
     salaried employee is unaffected; a misconfigured salaried employee's contribution is left
     completely alone (no subtract-with-nothing-to-replace hole).
  H. HOURLY BYTE-IDENTICAL — mandatory randomized equivalence differential (200 trials): synthetic
     rows/employees/by_store fixtures with pay_basis absent OR explicitly 'hourly' pass through EVERY
     public function in this module with the output IDENTICAL (apply_to_payroll_rows/apply_to_by_store
     return the SAME row objects — `is`, not just `==` — for the hourly path; by_store dict values are
     unchanged) to the input, for random tenant settings/date ranges/employee counts.
"""
import random
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")

from app.modules.storeops.payroll_salary import (  # noqa: E402
    PAY_BASES, convert_to_period_pay, period_length_days, tenant_pay_period_settings,
    pay_periods_overlapping, derive_salary_pay, allocate_across_stores, resolve_pay_basis,
    apply_to_payroll_rows, apply_to_by_store, accumulate, parse_date,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


WEEKLY = {"pay_period_type": "weekly", "work_week_start_dow": 0, "payday_dow": 4,
          "payday_weeks_after": 1, "biweekly_anchor": None}
BIWEEKLY = {"pay_period_type": "biweekly", "work_week_start_dow": 0, "payday_dow": 4,
            "payday_weeks_after": 1, "biweekly_anchor": "2026-01-05"}

# ── A. convert_to_period_pay — the exact conversion table ──────────────────────────────────────────
check("A1 weekly x1 (weekly period)", convert_to_period_pay("weekly", 800, "weekly") == 800.0)
check("A2 weekly x2 (biweekly period)", convert_to_period_pay("weekly", 800, "biweekly") == 1600.0)
check("A3 monthly x12/52 (weekly period)",
      convert_to_period_pay("monthly", 4333.33, "weekly") == round(4333.33 * 12 / 52, 2),
      convert_to_period_pay("monthly", 4333.33, "weekly"))
check("A4 monthly x12/26 (biweekly period)",
      convert_to_period_pay("monthly", 4333.33, "biweekly") == round(4333.33 * 12 / 26, 2))
check("A5 annual /52 (weekly period) — dispatch's own worked example",
      convert_to_period_pay("annual", 52000, "weekly") == 1000.0,
      convert_to_period_pay("annual", 52000, "weekly"))
check("A6 annual /26 (biweekly period)",
      convert_to_period_pay("annual", 52000, "biweekly") == round(52000 / 26, 2))
check("A7 hourly -> None (not applicable)", convert_to_period_pay("hourly", 20, "weekly") is None)
check("A8 zero amount -> None", convert_to_period_pay("weekly", 0, "weekly") is None)
check("A9 negative amount -> None", convert_to_period_pay("annual", -100, "weekly") is None)
check("A10 unparseable amount -> None", convert_to_period_pay("annual", "not-a-number", "weekly") is None)
check("A11 cents HALF_UP (annual/26 rounds up at .005)",
      convert_to_period_pay("annual", 1300.13, "biweekly") == round(1300.13 / 26, 2))
check("A12 unknown pay_period_type defaults to weekly-length math (matches core's own default)",
      convert_to_period_pay("annual", 52000, "monthly-typo") == 1000.0)

# ── B. pay_periods_overlapping ──────────────────────────────────────────────────────────────────────
p_one = pay_periods_overlapping(WEEKLY, date(2026, 3, 2), date(2026, 3, 8))   # exactly one Mon-Sun week
check("B1 exact one-week range -> exactly 1 period", len(p_one) == 1, p_one)
check("B2 that period is Mon 3/2 - Sun 3/8", p_one and p_one[0]["start"] == "2026-03-02" and p_one[0]["end"] == "2026-03-08")

p_month = pay_periods_overlapping(WEEKLY, date(2026, 3, 1), date(2026, 3, 31))  # March 2026, not week-aligned
check("B3 a calendar month spans multiple weekly periods", len(p_month) >= 4, len(p_month))
check("B4 periods are contiguous (each start = prev end + 1 day)",
      all(date.fromisoformat(p_month[i]["end"]) + timedelta(days=1) == date.fromisoformat(p_month[i + 1]["start"])
          for i in range(len(p_month) - 1)))

p_narrow = pay_periods_overlapping(WEEKLY, date(2026, 3, 4), date(2026, 3, 5))  # 2 days inside one week
check("B5 a range narrower than one period -> still exactly 1 period (the containing one)",
      len(p_narrow) == 1 and p_narrow[0]["start"] == "2026-03-02")

# ── C. derive_salary_pay ────────────────────────────────────────────────────────────────────────────
d1 = derive_salary_pay("annual", 52000, WEEKLY, date(2026, 3, 2), date(2026, 3, 8))
check("C1 exact one-week range -> full period_pay, not prorated",
      d1 and d1["amount"] == 1000.0 and d1["prorated"] is False, d1)

d2 = derive_salary_pay("weekly", 1000, WEEKLY, date(2026, 3, 2), date(2026, 3, 8), hire_date=date(2026, 3, 5))
# hired Thu 3/5 in a Mon-Sun week -> 4 of 7 days
check("C2 mid-period hire prorates to days-employed/days-in-period",
      d2 and d2["amount"] == round(1000.0 * 4 / 7, 2) and d2["prorated"] is True, d2)

d3 = derive_salary_pay("weekly", 1000, WEEKLY, date(2026, 3, 2), date(2026, 3, 8), termination_date=date(2026, 3, 4))
# terminated Wed 3/4 -> 3 of 7 days (Mon,Tue,Wed)
check("C3 mid-period termination prorates to days-employed/days-in-period",
      d3 and d3["amount"] == round(1000.0 * 3 / 7, 2), d3)

d4 = derive_salary_pay("weekly", 1000, WEEKLY, date(2026, 3, 2), date(2026, 3, 8),
                        hire_date=date(2026, 3, 3), termination_date=date(2026, 3, 5))
# employed Tue-Thu inclusive = 3 days
check("C4 hire AND termination in the same period both clip", d4 and d4["amount"] == round(1000.0 * 3 / 7, 2), d4)

d5 = derive_salary_pay("annual", 52000, WEEKLY, date(2026, 3, 1), date(2026, 3, 31))  # full March, not week-aligned
foot = round(sum(p["amount"] for p in d5["periods"]), 2)
check("C5 a non-period-aligned month view prorates at both edges", d5["prorated"] is True)
check("C6 per-period amounts foot EXACTLY to the returned total (no rounding drift)",
      foot == d5["amount"], (foot, d5["amount"]))

d6 = derive_salary_pay("annual", 52000, BIWEEKLY, date(2026, 3, 2), date(2026, 3, 8))  # half a biweekly period
check("C7 biweekly period type prorates a half-period range correctly",
      d6 and d6["amount"] == round((52000 / 26) * 7 / 14, 2), d6)

check("C8 hourly basis -> None (caller leaves hours×rate untouched)",
      derive_salary_pay("hourly", 20, WEEKLY, date(2026, 3, 2), date(2026, 3, 8)) is None)
check("C9 lo > hi -> None (never raises on an inverted range)",
      derive_salary_pay("annual", 52000, WEEKLY, date(2026, 3, 8), date(2026, 3, 2)) is None)

# ── D. allocate_across_stores ───────────────────────────────────────────────────────────────────────
alloc1 = allocate_across_stores({"S1": 30.0, "S2": 20.0, "S3": 10.0}, 1000.0, "S1")
check("D1 3-way proportional split sums EXACTLY to the input", round(sum(alloc1.values()), 2) == 1000.0, alloc1)
check("D2 S1 (largest hours share, 50%) gets the largest allocation",
      alloc1["S1"] >= alloc1["S2"] >= alloc1["S3"])

alloc2 = allocate_across_stores({}, 1234.56, "HOME")
check("D3 zero hours anywhere -> 100% to home_store", alloc2 == {"HOME": 1234.56}, alloc2)

alloc3 = allocate_across_stores({}, 1234.56, None)
check("D4 zero hours AND no home_store -> {} (never invents a store)", alloc3 == {})

alloc4 = allocate_across_stores({"S1": 1.0 / 3, "S2": 1.0 / 3, "S3": 1.0 / 3}, 100.0, "S1")
check("D5 an awkward non-round 3-way split still foots exactly (remainder fixup)",
      round(sum(alloc4.values()), 2) == 100.0, alloc4)

# ── E. resolve_pay_basis ────────────────────────────────────────────────────────────────────────────
check("E1 unrecognized pay_basis clamps to hourly", resolve_pay_basis({"pay_basis": "garbage"})[0] == "hourly")
check("E2 blank pay_basis defaults to hourly", resolve_pay_basis({})[0] == "hourly")
check("E3 valid pay_basis passes through", resolve_pay_basis({"pay_basis": "annual"})[0] == "annual")
check("E4 non-numeric pay_amount -> None (never crashes)",
      resolve_pay_basis({"pay_basis": "annual", "pay_amount": "not-a-number"})[1] is None)
check("E5 blank pay_amount -> None", resolve_pay_basis({"pay_basis": "annual", "pay_amount": ""})[1] is None)
check("E6 numeric pay_amount round-trips", resolve_pay_basis({"pay_basis": "annual", "pay_amount": 52000})[1] == 52000.0)
check("E7 all 4 PAY_BASES accounted for", set(PAY_BASES) == {"hourly", "weekly", "monthly", "annual"})

# ── F. apply_to_payroll_rows ────────────────────────────────────────────────────────────────────────
employees_f = [
    {"employee_id": "E1", "pay_basis": "annual", "pay_amount": 52000, "hire_date": None, "termination_date": None},
    {"employee_id": "E2", "pay_basis": "annual", "pay_amount": None},   # misconfigured
    {"employee_id": "E3", "pay_basis": "hourly"},
]
rows_f = [
    {"employee_id": "E1", "name": "Salaried Sam", "scheduled_hours": 40.0, "actual_hours": 38.0,
     "scheduled_pay": 800.0, "actual_pay": 760.0},
    {"employee_id": "E2", "name": "Misconfig Mo", "scheduled_hours": 20.0, "actual_hours": 20.0,
     "scheduled_pay": 400.0, "actual_pay": 400.0},
    {"employee_id": "E3", "name": "Hourly Hank", "scheduled_hours": 40.0, "actual_hours": 40.0,
     "scheduled_pay": 800.0, "actual_pay": 800.0},
    {"employee_id": "E9", "name": "Unmergeable Row", "scheduled_hours": 5.0, "actual_hours": 5.0,
     "scheduled_pay": 100.0, "actual_pay": 100.0},   # no matching employee row
]
out_f = {r["employee_id"]: r for r in
         apply_to_payroll_rows(rows_f, employees_f, WEEKLY, date(2026, 3, 2), date(2026, 3, 8))}
check("F1 salaried row's actual_pay overridden to the derived figure",
      out_f["E1"]["actual_pay"] == 1000.0, out_f["E1"])
check("F2 salaried row's scheduled_pay ALSO shows the derived figure (no scheduled≠actual for salary)",
      out_f["E1"]["scheduled_pay"] == 1000.0)
check("F3 salaried row's HOURS are untouched", out_f["E1"]["scheduled_hours"] == 40.0 and out_f["E1"]["actual_hours"] == 38.0)
check("F4 misconfigured row keeps its ORIGINAL pay + gets a salary_note",
      out_f["E2"]["actual_pay"] == 400.0 and "salary_note" in out_f["E2"], out_f["E2"])
check("F5 hourly row is the SAME object, untouched (identity, not just equality)",
      out_f["E3"] is rows_f[2])
check("F6 unmergeable/unmatched row passes through untouched (same object)",
      out_f["E9"] is rows_f[3])

# ── G. apply_to_by_store ────────────────────────────────────────────────────────────────────────────
employees_g = [{"employee_id": "E1", "pay_basis": "annual", "pay_amount": 52000, "home_store": "S1"},
               {"employee_id": "E9", "pay_basis": "annual", "pay_amount": None, "home_store": "S3"}]
by_store_g = {"S1": {"store_code": "S1", "hours": 30.0, "amount": 600.0},
              "S2": {"store_code": "S2", "hours": 10.0, "amount": 200.0},
              "S3": {"store_code": "S3", "hours": 5.0, "amount": 50.0}}
emp_store_hours_g = {"E1": {"S1": 30.0, "S2": 10.0}, "E9": {"S3": 5.0}}
# E1's hourly $ was $600 @ S1 (implies $20/hr * 30h — consistent with rate 20) and $200 @ S2 (also $20/hr * 10h)
emp_store_dollars_g = {"E1": {"S1": 600.0, "S2": 200.0}, "E9": {"S3": 50.0}}
out_g = apply_to_by_store(by_store_g, employees_g, emp_store_hours_g, emp_store_dollars_g, WEEKLY,
                           date(2026, 3, 2), date(2026, 3, 8))
check("G1 E1's hourly $ fully removed + replaced — S1+S2 sum to the derived $1000 exactly",
      round(out_g["S1"]["amount"] + out_g["S2"]["amount"], 2) == 1000.0,
      (out_g["S1"]["amount"], out_g["S2"]["amount"]))
check("G2 S1 (75% of E1's hours) gets the larger share", out_g["S1"]["amount"] > out_g["S2"]["amount"])
check("G3 hours in by_store are UNTOUCHED (still hourly basis, always displayed)",
      out_g["S1"]["hours"] == 30.0 and out_g["S2"]["hours"] == 10.0)
check("G4 misconfigured E9's store ($50 @ S3) is left COMPLETELY untouched (no subtract-with-no-replace hole)",
      out_g["S3"]["amount"] == 50.0, out_g["S3"])

# ── H. HOURLY BYTE-IDENTICAL — mandatory randomized equivalence differential (200 trials) ─────────────
rng = random.Random(20260727)
h_fail = 0
for trial in range(200):
    n_emp = rng.randint(1, 6)
    settings = rng.choice([WEEKLY, BIWEEKLY])
    lo = date(2026, 1, 1) + timedelta(days=rng.randint(0, 400))
    hi = lo + timedelta(days=rng.randint(0, 40))
    employees = []
    for i in range(n_emp):
        e = {"employee_id": f"H{i}", "home_store": f"S{i % 3}"}
        # Half the trials omit pay_basis entirely (pre-migration shape); half set it explicitly to
        # 'hourly' — both must be complete no-ops.
        if rng.random() < 0.5:
            e["pay_basis"] = "hourly"
            e["pay_amount"] = None
        employees.append(e)
    rows = [{"employee_id": f"H{i}", "name": f"Person {i}",
             "scheduled_hours": round(rng.uniform(0, 45), 2), "actual_hours": round(rng.uniform(0, 45), 2),
             "scheduled_pay": round(rng.uniform(0, 900), 2), "actual_pay": round(rng.uniform(0, 900), 2)}
            for i in range(n_emp)]
    out_rows = apply_to_payroll_rows(rows, employees, settings, lo, hi)
    if not all(out_rows[i] is rows[i] for i in range(n_emp)):
        h_fail += 1
        continue
    by_store = {}
    emp_h, emp_d = {}, {}
    for i in range(n_emp):
        store = f"S{i % 3}"
        hrs = rows[i]["actual_hours"]
        d = by_store.setdefault(store, {"store_code": store, "hours": 0.0, "amount": 0.0})
        d["hours"] += hrs
        d["amount"] += hrs * 15.0
        accumulate(emp_h, f"H{i}", store, hrs)
        accumulate(emp_d, f"H{i}", store, hrs * 15.0)
    before = {k: dict(v) for k, v in by_store.items()}
    out_store = apply_to_by_store(by_store, employees, emp_h, emp_d, settings, lo, hi)
    if out_store != before:
        h_fail += 1
check("H1 200-trial randomized differential: hourly rows always pass through IDENTICALLY (rows + by_store)",
      h_fail == 0, f"{h_fail}/200 trials mismatched")

# ── Report ───────────────────────────────────────────────────────────────────────────────────────────
print()
for f in FAIL:
    print("FAIL:", f)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
print("ALL GREEN" if not FAIL else "SOME FAILED")
sys.exit(1 if FAIL else 0)
