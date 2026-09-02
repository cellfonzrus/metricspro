"""Proof harness — sticky store-expenses carry-forward (owner bug 2026-09-02: "in the gross profit
the expenses column is not auto pulling from the expenses sheet … apply a systematic fix").

DB-free, pure-stdlib. Proves commcalc.expenses_effective — the ONE shared rule the Expenses sheet,
the GP report and the P&L now all read through:

  1. period keys parse BOTH spellings ('August 2026' / '2026-08') and junk never wins;
  2. the carry source is the LATEST STRICTLY-PRIOR period (same-month alternate spelling and
     future months never qualify — a month can never carry from itself or from the future);
  3. only MANUAL rows carry (source_key NULL) — a system line (payroll accrual / payroll_gross)
     is its own month's product and carrying it would double-book payroll;
  4. a period WITH its own rows never carries (byte-identical to the raw read);
  5. amounts are returned verbatim — carry-forward never invents or scales a dollar;
  6. the LIVE failure shape: rows end at 'July 2026' → August resolves to July's manual rows
     (the sheet's display and the money reports now agree).

Run: python3 backend/harness_expenses_carry_forward.py   (exit 0 = all proofs hold)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc.expenses_effective import (   # noqa: E402
    period_sort_key, pick_carry_period, manual_rows, effective_expense_rows)

FAIL = 0


def check(name, cond):
    global FAIL
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAIL += 1


print("1. period key parsing (both spellings, junk-safe)")
check("'August 2026' == '2026-08'", period_sort_key("August 2026") == period_sort_key("2026-08") == (2026, 8))
check("ordering: July < August < September",
      period_sort_key("July 2026") < period_sort_key("2026-08") < period_sort_key("September 2026"))
check("junk parses to (0,0)", period_sort_key("banana") == (0, 0) == period_sort_key(""))
check("month 13 rejected", period_sort_key("2026-13") == (0, 0))

print("2. carry source = latest strictly-prior period")
periods = ["March 2026", "May 2026", "July 2026", "June 2026", "April 2026"]
check("August carries from July", pick_carry_period(periods, "August 2026") == "July 2026")
check("numeric spelling of current works ('2026-08')", pick_carry_period(periods, "2026-08") == "July 2026")
check("same month (either spelling) never qualifies",
      pick_carry_period(["2026-08", "July 2026"], "August 2026") == "July 2026")
check("future months never qualify",
      pick_carry_period(["September 2026", "July 2026"], "August 2026") == "July 2026")
check("nothing prior → None", pick_carry_period(["September 2026"], "August 2026") is None)
check("junk current → None (fail-closed)", pick_carry_period(periods, "banana") is None)

print("3. only MANUAL rows carry")
rows = [
    {"expense_name": "Rent / Lease", "amount": 5000.0, "source_key": None},
    {"expense_name": "Employee Salaries", "amount": 108430.59, "source_key": ""},
    {"expense_name": "Paid Leave Accumulated", "amount": 812.5, "source_key": "pto_accrual"},
    {"expense_name": "Gross Payroll", "amount": 99999.0, "source_key": "payroll_gross"},
]
mr = manual_rows(rows)
check("system lines dropped, manual kept (2 of 4)",
      [r["expense_name"] for r in mr] == ["Rent / Lease", "Employee Salaries"])
check("amounts verbatim (never scaled)",
      [r["amount"] for r in mr] == [5000.0, 108430.59])


# ── 4-6: end-to-end through effective_expense_rows with a fake org-scoped client ────────────────
class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, table, db):
        self.table_, self.db, self.filters, self.cols = table, db, {}, "*"

    def select(self, cols):
        self.cols = cols
        return self

    def eq(self, k, v):
        self.filters[k] = ("eq", v)
        return self

    def in_(self, k, vals):
        self.filters[k] = ("in", list(vals))
        return self

    def limit(self, n):
        return self

    def execute(self):
        out = []
        for r in self.db.get(self.table_, []):
            ok = True
            for k, (op, v) in self.filters.items():
                rv = r.get(k)
                ok = ok and ((rv == v) if op == "eq" else (rv in v))
            if ok:
                out.append(dict(r))
        return _Res(out)


class FakeClient:
    def __init__(self, db):
        self.db = db

    def schema(self, name):
        return self

    def table(self, t):
        return _Q(t, self.db)


ORG = "org-1"
DB = {"store_expenses": [
    {"org_id": ORG, "period": "July 2026", "store_code": "B-1", "amount": 5000.0,
     "expense_name": "Rent / Lease", "source_key": None},
    {"org_id": ORG, "period": "July 2026", "store_code": "B-1", "amount": 812.5,
     "expense_name": "Paid Leave Accumulated", "source_key": "pto_accrual"},
    {"org_id": ORG, "period": "June 2026", "store_code": "B-1", "amount": 4900.0,
     "expense_name": "Rent / Lease", "source_key": None},
    {"org_id": "other-org", "period": "July 2026", "store_code": "X-9", "amount": 77.0,
     "expense_name": "Leak", "source_key": None},
]}
client = FakeClient(DB)

print("4. a period WITH its own rows never carries (byte-identical read)")
rows_jul, cf = effective_expense_rows(client, ORG, "July 2026", ["July 2026", "2026-07"],
                                      "store_code,amount")
check("July returns its own 2 rows, carried_from=None",
      cf is None and len(rows_jul) == 2)

print("5-6. empty month carries the latest prior month's MANUAL rows only (live failure shape)")
rows_aug, cf = effective_expense_rows(client, ORG, "August 2026", ["August 2026", "2026-08"],
                                      "store_code,amount")
check("August carried_from == 'July 2026'", cf == "July 2026")
check("only July's MANUAL row carried (system pto_accrual excluded)",
      len(rows_aug) == 1 and rows_aug[0]["amount"] == 5000.0)
check("org-scoped — another org's rows never leak",
      all(r.get("expense_name") != "Leak" for r in rows_aug))
rows_mar, cf_mar = effective_expense_rows(client, ORG, "March 2026", ["March 2026", "2026-03"],
                                          "store_code,amount")
check("a month with nothing prior books nothing", rows_mar == [] and cf_mar is None)

print()
if FAIL:
    print(f"{FAIL} proof(s) FAILED")
    sys.exit(1)
print("ALL PROOFS HOLD — store-expenses carry-forward")
