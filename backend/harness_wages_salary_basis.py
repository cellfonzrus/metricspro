"""Proof harness — the P&L wage figure is salary-basis aware, and it is the PAYROLL REPORT'S figure
(owner bug 2026-09-02 "the employee salaries … are not getting autoloaded from the payroll"; owner
report 2026-11-16 "it is not reflecting per store expenses in dashboard, gross profit or p&l").

WHAT CHANGED HERE, 2026-11-16 (index §ACCOUNT-05 / §STOREOPS-10). These five rules used to be proved
against `coa.derive_wage_cells` — the finance module's OWN shifts x rate derivation. That derivation
is deleted: it was a second answer to "what did this store's labour cost this month", and every
store of every tenant disagreed with the Payroll Report because of it. The rules did not change and
none is weakened; they are proved where the fact now lives, END TO END through `coa.wages_by_store`
-> `storeops/labour_cost` -> `GET /storeops/payroll-by-store`, over a database double. A rule proved
only against a deleted function is a rule nothing holds.

  1. HOURLY employees: paid hours x pay_rate, per store, with the shift's store_code deciding where
     (a floater's hours land at the store they worked), home_store the fallback;
  2. a SALARIED employee (pay_basis weekly/monthly/annual + pay_amount — the same columns the
     payroll report pays them from) books ONE period salary, never hours x pay_rate (live LuxeLink
     failure: E173 monthly $8,000 with pay_rate 3,692.30 booked hours x 3,692.30 -> Aug 2026
     consolidated Wages $234,523.57 on $103,344.97 revenue);

     THE AMOUNT MOVED, and it is stated rather than hidden. The deleted finance derivation booked
     `monthly_salary_equivalent` — a twelfth of the annual figure. The Payroll Report books what the
     period actually PAYS (`payroll_salary.derive_salary_pay` over the range and the tenant's own
     pay-period settings), which for 1-31 October is 31/364ths of a year, not a twelfth: $78,000 a
     year reads $6,642.86 where the books used to read $6,500.00. The books now carry the figure the
     payroll run pays, which is the whole of the owner's ask. This harness dereferences the payroll
     rule rather than re-spelling either one, so it cannot drift from whichever is in force.
  3. the salary allocates across worked stores proportional to hours and SUMS EXACTLY to it;
  4. a zero-hours salaried employee books to home_store; with no home_store the pay is company-wide
     (key None) and NEVER a store called after the sentinel;
  5. conversion table: monthly = amount; annual = amount/12; weekly = amount x 52/12.

Pure-stdlib, no network, no database. Run: python3 backend/harness_wages_salary_basis.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAIL = 0


def check(name, cond, detail=None):
    global FAIL
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond or detail is None else f"  — {detail}"))
    if not cond:
        FAIL += 1
# THE database double, SHARED (harnesslib.FakeDB) rather than copied — three harnesses now run
# through the pay path, and one copy of the idea is enough.
import harnesslib as _hl                                                      # noqa: E402


STORE = {}
DB = _hl.FakeDB(STORE)

import app.modules.storeops.router as router_mod                      # noqa: E402
import app.modules.account.coa as coa                                 # noqa: E402
from app.modules.storeops import payroll_salary as ps                 # noqa: E402
from app.modules.account.coa import monthly_salary_equivalent         # noqa: E402

router_mod.get_supabase = lambda: DB

ORG = "ORG-UNDER-TEST"
PERIOD = "2026-10"
QV, GRAND, DIVERSEY = "QV", "Grand", "Diversey"
STORE["store_mapping"] = [
    {"org_id": ORG, "store_code": QV, "store_address": "218-80 Hempstead Avenue"},
    {"org_id": ORG, "store_code": GRAND, "store_address": "3966 W Grand Ave"},
    {"org_id": ORG, "store_code": DIVERSEY, "store_address": "4640-A W Diversey Ave"},
]
ADDR = {QV: "218-80 Hempstead Avenue", GRAND: "3966 W Grand Ave",
        DIVERSEY: "4640-A W Diversey Ave"}


def shift(eid, code, sched, act=0, day=1):
    return {"id": f"{eid}-{code}-{day}", "org_id": ORG, "employee_id": eid, "store_code": code,
            "scheduled_hours": sched, "actual_hours": act, "shift_date": "2026-10-%02d" % day,
            "is_deleted": False}


def period_salary(basis, amount):
    """What the PAYROLL RULE pays for 1-31 October — dereferenced from `payroll_salary`, never
    re-spelled here, so this harness proves the books agree with the payroll run rather than with a
    second copy of its arithmetic."""
    from datetime import date
    return ps.derive_salary_pay(basis, amount, ps.tenant_pay_period_settings({}),
                                date(2026, 10, 1), date(2026, 10, 31), None, None)["amount"]


def wages(employees, shifts):
    STORE["employees"] = employees
    STORE["shifts"] = shifts
    STORE["timelog"] = []
    return coa.wages_by_store(DB, ORG, PERIOD)


print("1. hourly employees — paid hours x rate, at the store the hours were worked")
out = wages(
    [{"employee_id": "H1", "org_id": ORG, "pay_rate": 15, "home_store": QV},
     {"employee_id": "H2", "org_id": ORG, "pay_rate": 21, "home_store": GRAND,
      "pay_basis": "hourly", "pay_amount": None, "is_active": True}],
    [shift("H1", QV, 9, act=8, day=1), shift("H1", QV, 4, day=2), shift("H2", GRAND, 8, act=7.5, day=1)])
check("hours x rate per store, a manual correction winning over the schedule",
      out == {ADDR[QV]: round(8 * 15 + 4 * 15, 2), ADDR[GRAND]: round(7.5 * 21, 2)}, out)

print("2. LIVE BUG CASE — a salaried employee books salary, never hours x pay_rate")
out = wages(
    [{"employee_id": "E173", "org_id": ORG, "pay_rate": 3692.3, "home_store": QV,
      "pay_basis": "monthly", "pay_amount": 8000.0, "is_active": True}],
    [shift("E173", QV, 50, act=50, day=d) for d in (1, 2, 3, 4)])          # 200 worked hours
check("a monthly salary books the period's SALARY, never 200h x 3,692.30 = $738,460",
      out == {ADDR[QV]: period_salary("monthly", 8000.0)}
      and out[ADDR[QV]] < 10000.0, (out, period_salary("monthly", 8000.0)))

print("3. salary allocation proportional to worked hours, exact-sum rounding")
out = wages(
    [{"employee_id": "S1", "org_id": ORG, "pay_rate": 1, "home_store": QV,
      "pay_basis": "annual", "pay_amount": 62000.0, "is_active": True}],
    [shift("S1", QV, 60, act=60, day=1), shift("S1", GRAND, 30, act=30, day=2),
     shift("S1", DIVERSEY, 10, act=10, day=3)])
pay = period_salary("annual", 62000.0)
check("shares proportional to 60/30/10 and sum EXACTLY to the period's salary",
      round(sum(out.values()), 2) == pay
      and out[ADDR[QV]] == round(pay * 0.6, 2) and out[ADDR[GRAND]] == round(pay * 0.3, 2), (out, pay))

print("4. zero-hours salaried: home_store, else company-wide — never a store named after the sentinel")
out = wages(
    [{"employee_id": "MGR", "org_id": ORG, "pay_rate": 3000, "home_store": None,
      "pay_basis": "annual", "pay_amount": 78000.0, "is_active": True},
     {"employee_id": "HOME", "org_id": ORG, "pay_rate": 0, "home_store": DIVERSEY,
      "pay_basis": "weekly", "pay_amount": 1200.0, "is_active": True}], [])
check("a blank-home salaried manager books COMPANY-WIDE (key None)",
      out.get(None) == period_salary("annual", 78000.0), out)
check("…and never under the 'no store' sentinel, which is not a store",
      ps.UNASSIGNED_STORE not in out and ps.UNASSIGNED_STORE.lower() not in out, out)
check("a no-hours salaried employee books to their home store",
      out.get(ADDR[DIVERSEY]) == period_salary("weekly", 1200.0), out)

print("5. conversion table")
check("monthly = amount", monthly_salary_equivalent("monthly", 8000) == 8000.0)
check("annual = amount/12", monthly_salary_equivalent("annual", 62000) == round(62000 / 12, 2))
check("weekly = amount x 52/12", monthly_salary_equivalent("weekly", 1200) == round(1200 * 52 / 12, 2))
check("hourly / unknown / non-positive -> None (stays on the hourly path)",
      monthly_salary_equivalent("hourly", 8000) is None
      and monthly_salary_equivalent("", 8000) is None
      and monthly_salary_equivalent("monthly", 0) is None
      and monthly_salary_equivalent("monthly", -5) is None)

print("6. THE WHOLE POINT — the books carry the Payroll Report's own per-store figure")
emps = [{"employee_id": "H1", "org_id": ORG, "pay_rate": 15, "home_store": QV, "is_active": True},
        {"employee_id": "S1", "org_id": ORG, "pay_rate": 1, "home_store": QV,
         "pay_basis": "annual", "pay_amount": 62000.0, "is_active": True}]
shifts = [shift("H1", QV, 8, act=8, day=1), shift("S1", GRAND, 8, act=8, day=1),
          shift("S1", DIVERSEY, 4, act=4, day=2)]
booked = wages(emps, shifts)
report = router_mod.get_payroll_by_store(month=PERIOD, org_id=ORG)["stores"]
reported = {}
for r in report:
    k = None if str(r["store_code"]).strip().casefold() in ("", ps.UNASSIGNED_STORE.casefold()) \
        else ADDR.get(str(r["store_code"]).strip(), str(r["store_code"]).strip())
    reported[k] = round(reported.get(k, 0.0) + float(r["amount"]), 2)
check("store for store and cent for cent, the P&L equals GET /storeops/payroll-by-store",
      booked == reported, (booked, reported))

print()
if FAIL:
    print(f"{FAIL} proof(s) FAILED")
    sys.exit(1)
print("ALL PROOFS HOLD — the wage figure is salary-basis aware AND is the Payroll Report's own")
