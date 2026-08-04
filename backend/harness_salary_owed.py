"""Pure-logic proof harness for the Daily Salary Owed engine (mod-people, EEP package, owner directive
2026-08-04, migration 419, backend/app/modules/storeops/salary_owed.py).

Runs the ACTUAL shipped functions against synthetic data — no DB, no network.

Run: `python3 harness_salary_owed.py` from backend/.

Proves:
  A. daily_hours_for_employee — act>0 -> 'actual'; act==0 -> scheduled fallback labeled 'scheduled';
     inactive phantom (act==0) shift contributes nothing and leaves the day open for a punch; a closed
     punch on a shift-covered day is dropped (no double-count); an open punch never counts; a mixed day
     (shift act==0 AND a separate real punch on a DIFFERENT day) behaves independently per day.
  B. apply_lunch_deduction — subtracts the configured day's minutes, never below 0, leaves days absent
     from the deduction map untouched, does not mutate the input dict.
  C. build_employee_salary_owed (hourly) — owed_total == round(total_hours * rate, 2) (the SAME
     rounding /payroll's own actual_pay uses); Σ day.owed foots EXACTLY to owed_total even with a
     non-round hourly split across 3 days; a zero-activity hourly employee owed_total == 0.
  D. build_employee_salary_owed (salaried) — owed_total EXACTLY matches
     payroll_salary.derive_salary_pay's own 'amount' for the identical (basis, amount, settings, lo,
     hi, hire, term) — the authoritative-figure guarantee; Σ day.owed foots exactly to owed_total for
     both a full-period range and a mid-period-hire partial range; days outside the employment window
     get owed=0.
  E. additional_payroll_excess — max(0, paid-earned): paid<earned -> 0 (never negative); paid>earned ->
     the exact excess; paid==earned -> 0 (the explicit boundary the spec calls out).
  F. earned_lookback_start — hire_date used when known and within the lookback window; falls back to
     the EARNED_LOOKBACK_DAYS cap when hire_date is missing or older than the cap.
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")

from app.modules.storeops.salary_owed import (  # noqa: E402
    daily_hours_for_employee, apply_lunch_deduction, build_employee_salary_owed,
    additional_payroll_excess, earned_lookback_start, EARNED_LOOKBACK_DAYS,
)
from app.modules.storeops.payroll_salary import derive_salary_pay, parse_date  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


WEEKLY = {"pay_period_type": "weekly", "work_week_start_dow": 0, "payday_dow": 4,
          "payday_weeks_after": 1, "biweekly_anchor": None}

# ── A. daily_hours_for_employee ─────────────────────────────────────────────────────────────────────
shifts_a = [
    {"shift_date": "2026-08-01", "scheduled_hours": 8, "actual_hours": 7.5},   # act>0 -> actual
    {"shift_date": "2026-08-02", "scheduled_hours": 6, "actual_hours": 0},     # act==0 -> scheduled fallback
]
timelog_a = [
    {"work_date": "2026-08-01", "clock_out": "2026-08-01T17:00:00Z", "hours": 9.0},  # shift-covered -> dropped
    {"work_date": "2026-08-03", "clock_out": "2026-08-03T17:00:00Z", "hours": 5.25},  # no shift -> counts
    {"work_date": "2026-08-04", "clock_out": None, "hours": None},             # open punch -> never counts
]
days_a = daily_hours_for_employee(shifts_a, timelog_a, is_inactive=False)
check("A1 act>0 day basis=actual, hours=7.5", days_a["2026-08-01"]["hours"] == 7.5 and days_a["2026-08-01"]["basis"] == "actual")
check("A2 act==0 day falls back to scheduled, basis=scheduled", days_a["2026-08-02"]["hours"] == 6.0 and days_a["2026-08-02"]["basis"] == "scheduled")
check("A3 shift-covered punch dropped (no double-count)", "2026-08-01" not in [d for d in days_a if days_a[d]["hours"] == 16.5])
check("A4 unshifted closed punch counts on its own day", days_a["2026-08-03"]["hours"] == 5.25 and days_a["2026-08-03"]["basis"] == "actual")
check("A5 open punch never appears", "2026-08-04" not in days_a)

# inactive phantom shift (act==0) contributes nothing; day stays open for a punch
shifts_a2 = [{"shift_date": "2026-08-05", "scheduled_hours": 6, "actual_hours": 0}]
timelog_a2 = [{"work_date": "2026-08-05", "clock_out": "2026-08-05T14:00:00Z", "hours": 4.0}]
days_a2 = daily_hours_for_employee(shifts_a2, timelog_a2, is_inactive=True)
check("A6 inactive phantom shift excluded entirely", "2026-08-05" in days_a2 and days_a2["2026-08-05"]["hours"] == 4.0,
      days_a2)
check("A7 inactive phantom day basis=actual (from the punch, not the phantom)", days_a2["2026-08-05"]["basis"] == "actual")

# inactive REAL shift (act>0) blocks a same-day punch (no double count for inactive either)
shifts_a3 = [{"shift_date": "2026-08-06", "scheduled_hours": 6, "actual_hours": 6.0}]
timelog_a3 = [{"work_date": "2026-08-06", "clock_out": "2026-08-06T14:00:00Z", "hours": 6.0}]
days_a3 = daily_hours_for_employee(shifts_a3, timelog_a3, is_inactive=True)
check("A8 inactive real shift blocks same-day punch (no 12h double-count)", days_a3["2026-08-06"]["hours"] == 6.0, days_a3)

# ── B. apply_lunch_deduction ────────────────────────────────────────────────────────────────────────
base_days = {"2026-08-01": {"date": "2026-08-01", "hours": 8.0, "basis": "actual"},
             "2026-08-02": {"date": "2026-08-02", "hours": 0.5, "basis": "actual"}}
ded = apply_lunch_deduction(base_days, {"2026-08-01": 0.5, "2026-08-02": 2.0})
check("B1 deduction subtracted", ded["2026-08-01"]["hours"] == 7.5)
check("B2 never goes below 0", ded["2026-08-02"]["hours"] == 0.0)
check("B3 day absent from deduction map untouched", ded == apply_lunch_deduction(ded, {}))
check("B4 input dict not mutated", base_days["2026-08-01"]["hours"] == 8.0)
check("B5 empty deduction map short-circuits to same dict", apply_lunch_deduction(base_days, {}) is base_days)

# ── C. build_employee_salary_owed (hourly) ──────────────────────────────────────────────────────────
emp_hourly = {"employee_id": "E1", "pay_rate": 17.33, "pay_basis": "hourly", "pay_amount": None}
day_hours_c = {"2026-08-01": {"hours": 6.4, "basis": "actual"},
               "2026-08-02": {"hours": 5.1, "basis": "scheduled"},
               "2026-08-03": {"hours": 3.7, "basis": "actual"}}
res_c = build_employee_salary_owed(emp_hourly, WEEKLY, date(2026, 8, 1), date(2026, 8, 3), day_hours_c)
expected_total = round((6.4 + 5.1 + 3.7) * 17.33, 2)
check("C1 owed_total == round(total_hours*rate,2)", res_c["owed_total"] == expected_total, (res_c["owed_total"], expected_total))
check("C2 days foot exactly to owed_total", round(sum(d["owed"] for d in res_c["days"]), 2) == res_c["owed_total"])
check("C3 basis == hourly", res_c["basis"] == "hourly")
check("C4 each day's basis/hours carried through unchanged", res_c["days"][0]["hours"] == 6.4 and res_c["days"][0]["basis"] == "actual")

res_c_zero = build_employee_salary_owed(emp_hourly, WEEKLY, date(2026, 8, 1), date(2026, 8, 3), {})
check("C5 zero-activity hourly employee owed_total == 0", res_c_zero["owed_total"] == 0.0)
check("C6 zero-activity days list still spans the full range", len(res_c_zero["days"]) == 3)

# ── D. build_employee_salary_owed (salaried) ────────────────────────────────────────────────────────
emp_sal = {"employee_id": "E2", "pay_rate": 0, "pay_basis": "annual", "pay_amount": 52000,
           "hire_date": None, "termination_date": None}
lo_d, hi_d = date(2026, 8, 3), date(2026, 8, 9)   # one exact weekly period (Mon-Sun-ish, matches WEEKLY cfg)
res_d = build_employee_salary_owed(emp_sal, WEEKLY, lo_d, hi_d, {})
authoritative = derive_salary_pay("annual", 52000, WEEKLY, lo_d, hi_d, None, None)
check("D1 salaried owed_total EXACTLY matches derive_salary_pay's own amount",
      res_d["owed_total"] == authoritative["amount"], (res_d["owed_total"], authoritative["amount"]))
check("D2 days foot exactly to owed_total (full period)", round(sum(d["owed"] for d in res_d["days"]), 2) == res_d["owed_total"])
check("D3 basis == annual", res_d["basis"] == "annual")

# mid-period hire: employee hired 3 days into the range
hire = date(2026, 8, 5)
res_d2 = build_employee_salary_owed({**emp_sal, "hire_date": "2026-08-05"}, WEEKLY, lo_d, hi_d, {})
authoritative2 = derive_salary_pay("annual", 52000, WEEKLY, lo_d, hi_d, hire, None)
check("D4 mid-period hire owed_total matches derive_salary_pay (prorated)",
      res_d2["owed_total"] == authoritative2["amount"], (res_d2["owed_total"], authoritative2["amount"]))
check("D5 mid-period hire: days before hire_date are 0", all(
    d["owed"] == 0.0 for d in res_d2["days"] if date.fromisoformat(d["date"]) < hire))
check("D6 mid-period hire: days foot exactly to owed_total", round(sum(d["owed"] for d in res_d2["days"]), 2) == res_d2["owed_total"])

# hours/basis still shown for a salaried employee (informational), even though pay isn't hours-derived
day_hours_d3 = {"2026-08-04": {"hours": 6.0, "basis": "actual"}}
res_d3 = build_employee_salary_owed(emp_sal, WEEKLY, lo_d, hi_d, day_hours_d3)
d3_row = next(d for d in res_d3["days"] if d["date"] == "2026-08-04")
check("D7 salaried day still carries the real hours (informational)", d3_row["hours"] == 6.0 and d3_row["basis"] == "actual")
check("D8 salaried owed_total UNCHANGED by hours (not hours-derived)", res_d3["owed_total"] == res_d["owed_total"])

# unusable pay_amount on a salaried basis degrades to the hourly path (never a $0 salaried figure that
# silently looks authoritative)
emp_sal_broken = {"employee_id": "E3", "pay_rate": 15.0, "pay_basis": "monthly", "pay_amount": None}
res_d4 = build_employee_salary_owed(emp_sal_broken, WEEKLY, lo_d, hi_d, {"2026-08-03": {"hours": 8.0, "basis": "actual"}})
check("D9 unusable pay_amount falls back to hourly path", res_d4["basis"] == "hourly" and res_d4["owed_total"] == round(8.0 * 15.0, 2))

# ── E. additional_payroll_excess ────────────────────────────────────────────────────────────────────
check("E1 paid < earned -> 0", additional_payroll_excess(100, 150) == 0.0)
check("E2 paid > earned -> exact excess", additional_payroll_excess(150, 100) == 50.0)
check("E3 paid == earned -> 0 (boundary)", additional_payroll_excess(100, 100) == 0.0)
check("E4 never negative even with garbage input", additional_payroll_excess(-10, 50) == 0.0)

# ── F. earned_lookback_start ────────────────────────────────────────────────────────────────────────
pe = date(2026, 8, 31)
check("F1 hire_date used when within window", earned_lookback_start(date(2026, 8, 1), pe) == date(2026, 8, 1))
check("F2 hire_date older than cap -> cap wins", earned_lookback_start(date(2020, 1, 1), pe) == pe - timedelta(days=EARNED_LOOKBACK_DAYS))
check("F3 no hire_date -> cap", earned_lookback_start(None, pe) == pe - timedelta(days=EARNED_LOOKBACK_DAYS))

print(f"\n{'='*70}\nharness_salary_owed: {len(PASS)} PASS, {len(FAIL)} FAIL\n{'='*70}")
for f in FAIL:
    print("FAIL:", f)
if FAIL:
    sys.exit(1)
print("ALL PASS")
