"""Proof harness — P&L wages estimate is salary-basis aware (owner bug 2026-09-02: "the employee
salaries … are not getting autoloaded from the payroll").

DB-free, pure-stdlib. Proves account/coa.derive_wage_cells + monthly_salary_equivalent:

  1. HOURLY employees are BYTE-IDENTICAL to the old estimate (hours actual→scheduled × pay_rate,
     shift store_code → address, home_store fallback);
  2. a SALARIED employee (pay_basis weekly/monthly/annual + pay_amount — the same columns the
     storeops payroll report pays them from) books ONE monthly salary equivalent, never
     hours × pay_rate (live LuxeLink failure: E173 monthly $8,000 with pay_rate 3,692.30 booked
     hours × 3,692.30 → Aug 2026 consolidated Wages $234,523.57 on $103,344.97 revenue);
  3. the salary allocates across worked stores ∝ hours and SUMS EXACTLY to the monthly equivalent;
  4. an active zero-hours salaried employee books to home_store (company-wide when blank);
     an INACTIVE salaried employee with no hours this month books nothing;
  5. conversion table: monthly = amount; annual = amount/12; weekly = amount×52/12.

Run: python3 backend/harness_wages_salary_basis.py   (exit 0 = all proofs hold)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.account.coa import derive_wage_cells, monthly_salary_equivalent  # noqa: E402

FAIL = 0


def check(name, cond):
    global FAIL
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAIL += 1


CODE2ADDR = {"QV": "218-80 Hempstead Avenue", "Grand": "3966 W Grand Ave",
             "Diversey": "4640-A W Diversey Ave"}

print("1. hourly employees — byte-identical to the old estimate")
emps_hourly = [
    {"employee_id": "H1", "pay_rate": 15, "home_store": "QV"},                       # pre-416 row shape
    {"employee_id": "H2", "pay_rate": 21, "home_store": "Grand", "pay_basis": "hourly",
     "pay_amount": None, "is_active": True},
]
shifts = [
    {"employee_id": "H1", "store_code": "QV", "actual_hours": 8, "scheduled_hours": 9},
    {"employee_id": "H1", "store_code": None, "actual_hours": 0, "scheduled_hours": 4},  # sched fallback + home_store
    {"employee_id": "H2", "store_code": "Grand", "actual_hours": 7.5, "scheduled_hours": 8},
]
out = derive_wage_cells(emps_hourly, shifts, CODE2ADDR)
check("hours×rate per store, actual→scheduled fallback, home_store fallback",
      out == {"218-80 Hempstead Avenue": round(8 * 15 + 4 * 15, 2),
              "3966 W Grand Ave": round(7.5 * 21, 2)})

print("2. LIVE BUG CASE — salaried employee books salary, never hours × pay_rate")
emps = [{"employee_id": "E173", "pay_rate": 3692.3, "home_store": "QV",
         "pay_basis": "monthly", "pay_amount": 8000.0, "is_active": True}]
shifts = [{"employee_id": "E173", "store_code": "QV", "actual_hours": 50, "scheduled_hours": 50}
          for _ in range(4)]                                    # 200 worked hours in the month
out = derive_wage_cells(emps, shifts, CODE2ADDR)
check("monthly $8,000 books exactly $8,000 (old code booked 200h × 3,692.30 = $738,460)",
      out == {"218-80 Hempstead Avenue": 8000.0})

print("3. salary allocation ∝ worked hours, exact-sum rounding")
emps = [{"employee_id": "S1", "pay_rate": 1, "home_store": "QV",
         "pay_basis": "annual", "pay_amount": 62000.0, "is_active": True}]
shifts = [
    {"employee_id": "S1", "store_code": "QV", "actual_hours": 60, "scheduled_hours": 0},
    {"employee_id": "S1", "store_code": "Grand", "actual_hours": 30, "scheduled_hours": 0},
    {"employee_id": "S1", "store_code": "Diversey", "actual_hours": 10, "scheduled_hours": 0},
]
out = derive_wage_cells(emps, shifts, CODE2ADDR)
meq = monthly_salary_equivalent("annual", 62000.0)
check("shares ∝ 60/30/10 and sum EXACTLY to the monthly equivalent",
      round(sum(out.values()), 2) == meq
      and out["218-80 Hempstead Avenue"] == round(meq * 0.6, 2)
      and out["3966 W Grand Ave"] == round(meq * 0.3, 2))

print("4. zero-hours salaried: active → home_store; blank home → company-wide; inactive → nothing")
emps = [
    {"employee_id": "MGR", "pay_rate": 3000, "home_store": None,
     "pay_basis": "annual", "pay_amount": 78000.0, "is_active": True},          # blank home → company-wide
    {"employee_id": "GONE", "pay_rate": 2115.38, "home_store": "Grand",
     "pay_basis": "annual", "pay_amount": 55000.0, "is_active": False},          # inactive, no hours → skip
    {"employee_id": "HOME", "pay_rate": 0, "home_store": "Diversey",
     "pay_basis": "weekly", "pay_amount": 1200.0, "is_active": True},            # active, no hours → home
]
out = derive_wage_cells(emps, [], CODE2ADDR)
check("active blank-home books company-wide (None key)",
      out.get(None) == monthly_salary_equivalent("annual", 78000.0))
check("inactive no-hours salaried books NOTHING", "3966 W Grand Ave" not in out)
check("active no-hours salaried books to home_store",
      out.get("4640-A W Diversey Ave") == monthly_salary_equivalent("weekly", 1200.0))

print("5. conversion table")
check("monthly = amount", monthly_salary_equivalent("monthly", 8000) == 8000.0)
check("annual = amount/12", monthly_salary_equivalent("annual", 62000) == round(62000 / 12, 2))
check("weekly = amount×52/12", monthly_salary_equivalent("weekly", 1200) == round(1200 * 52 / 12, 2))
check("hourly / unknown / non-positive → None (stays on the hourly path)",
      monthly_salary_equivalent("hourly", 8000) is None
      and monthly_salary_equivalent("", 8000) is None
      and monthly_salary_equivalent("monthly", 0) is None
      and monthly_salary_equivalent("monthly", -5) is None)

print()
if FAIL:
    print(f"{FAIL} proof(s) FAILED")
    sys.exit(1)
print("ALL PROOFS HOLD — salary-basis-aware wages estimate")
