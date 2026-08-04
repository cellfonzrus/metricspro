"""Integration-style proof for the Salary Owed / Cash Advances / Additional Payroll ROUTER glue (not
just the pure engine — see harness_salary_owed.py for that). Runs the ACTUAL shipped functions from
app.modules.storeops.router (get_salary_owed, record_salary_advance, salary_advance_history,
get_additional_payroll, run_additional_payroll, plus the PRE-EXISTING get_payroll/get_payroll_by_store
it must never diverge from) against an in-memory fake Supabase client — no live DB/network. Same
fake-client convention as harness_payroll_salary_router_integration.py / harness_payroll_rpc_equivalence.py.

Run: `python3 harness_salary_owed_router_integration.py` from backend/.

Proves (mapping directly to the dispatch's verification requirements):
  (a) GET /storeops/salary-owed's hours/owed for an HOURLY employee, summed over a range, matches
      GET /storeops/payroll's OWN actual_hours/actual_pay row for the SAME employee/range EXACTLY — no
      divergence — including the shift-covered no-double-count guard and the open-punch exclusion. Same
      proof for a SALARIED employee's owed_total vs /payroll's derived actual_pay.
  (b) POST /storeops/salary-advance/record is org-stamped (a second tenant's read never sees the first
      org's advance) and the Additional-Payroll recompute triggered by each write is IDEMPOTENT — running
      it twice (or calling POST /salary-advance/additional-payroll/run/{period} twice) with no new
      advance produces the IDENTICAL cells, never an accumulating double-count.
  (c) Additional Payroll == max(0, cumulative cash paid to date − cumulative earned to date), including
      the exact zero-case boundary (paid == earned -> 0, not a rounding-noise nonzero).
  (d) GET /storeops/payroll's own output (an hourly + a salaried employee) is BYTE IDENTICAL before and
      after recording salary advances against those SAME employees — advances never touch payroll_gross
      / the payroll basis.
"""
import json
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── Fake Supabase client (identical convention to harness_payroll_salary_router_integration.py) ───
class FakeQuery:
    def __init__(self, store, table):
        self.store = store
        self.table_name = table
        self.filters = []
        self._limit = None
        self._mode = None
        self._payload = None
        self._order_desc = False

    def select(self, cols):
        self._mode = "select"; return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def gte(self, k, v):
        self.filters.append(("gte", k, v)); return self

    def lte(self, k, v):
        self.filters.append(("lte", k, v)); return self

    def lt(self, k, v):
        self.filters.append(("lt", k, v)); return self

    def in_(self, k, vals):
        self.filters.append(("in", k, set(vals))); return self

    def order(self, *a, **k):
        self._order_desc = k.get("desc", False); return self

    def limit(self, n):
        self._limit = n; return self

    def insert(self, payload):
        self._mode = "insert"; self._payload = payload; return self

    def update(self, payload):
        self._mode = "update"; self._payload = payload; return self

    def delete(self):
        self._mode = "delete"; return self

    def _match(self, row):
        for op, k, v in self.filters:
            rv = row.get(k)
            if op == "eq" and rv != v:
                return False
            if op == "gte" and not (rv is not None and str(rv) >= str(v)):
                return False
            if op == "lte" and not (rv is not None and str(rv) <= str(v)):
                return False
            if op == "lt" and not (rv is not None and str(rv) < str(v)):
                return False
            if op == "in" and rv not in v:
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self._mode == "select":
            matched = [dict(r) for r in rows if self._match(r)]
            if self._order_desc:
                matched = list(reversed(matched))
            if self._limit:
                matched = matched[: self._limit]
            return FakeResult(matched)
        if self._mode == "insert":
            payloads = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for p in payloads:
                row = dict(p)
                row.setdefault("id", f"{self.table_name}-{len(rows)}")
                rows.append(row)
                out.append(row)
            return FakeResult(out)
        if self._mode == "update":
            matched = [r for r in rows if self._match(r)]
            for r in matched:
                r.update(self._payload)
            return FakeResult(matched)
        if self._mode == "delete":
            matched = [r for r in rows if self._match(r)]
            self.store[self.table_name] = [r for r in rows if r not in matched]
            return FakeResult(matched)
        raise RuntimeError("no mode set")


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeRpcQuery:
    def execute(self):
        raise RuntimeError("function storeops.payroll_month_rows does not exist (407 not run)")


class FakeSchemaClient:
    def __init__(self, store):
        self.store = store

    def schema(self, name):
        return self

    def table(self, name):
        return FakeQuery(self.store, name)

    def rpc(self, fn, params):
        return FakeRpcQuery()


STORE = {}
FAKE_CLIENT = FakeSchemaClient(STORE)


def fake_get_supabase():
    return FAKE_CLIENT


import app.modules.storeops.router as router_mod          # noqa: E402
import app.modules.core.router as core_router_mod         # noqa: E402

router_mod.get_supabase = fake_get_supabase
core_router_mod._uid_from_token = lambda auth: ("mgr-uid" if auth == "Bearer manager" else
                                                 ("mgr2-uid" if auth == "Bearer manager2" else None))

ORG = "ORGOWED"
ORG2 = "ORGOWED2"

STORE["app_users"] = [
    {"auth_id": "mgr-uid", "org_id": ORG, "email": "boss@x.com", "role": "admin", "employee_id": "MGR1"},
    {"auth_id": "mgr2-uid", "org_id": ORG2, "email": "boss2@x.com", "role": "admin", "employee_id": "MGR2"},
]
STORE["tenants"] = [
    {"org_id": ORG, "pay_period_type": "weekly", "work_week_start_dow": 0, "payday_dow": 4,
     "payday_weeks_after": 1, "biweekly_anchor": None},
    {"org_id": ORG2, "pay_period_type": "weekly", "work_week_start_dow": 0, "payday_dow": 4,
     "payday_weeks_after": 1, "biweekly_anchor": None},
]
STORE["employees"] = [
    {"id": "1", "employee_id": "HRL1", "org_id": ORG, "name": "Harry Hourly", "home_store": "Store1",
     "pay_rate": 18.0, "pay_basis": "hourly", "pay_amount": None, "hire_date": None,
     "termination_date": None, "is_active": True},
    {"id": "2", "employee_id": "SAL1", "org_id": ORG, "name": "Sally Salaried", "home_store": "Store1",
     "pay_rate": 0.0, "pay_basis": "annual", "pay_amount": 52000.0, "hire_date": None,
     "termination_date": None, "is_active": True},
    # A second org's employee sharing the SAME employee_id string as ORG's HRL1 — proves org isolation.
    {"id": "3", "employee_id": "HRL1", "org_id": ORG2, "name": "Other-Org Harry", "home_store": "StoreX",
     "pay_rate": 999.0, "pay_basis": "hourly", "pay_amount": None, "hire_date": None,
     "termination_date": None, "is_active": True},
]
# One exact weekly period matching the WEEKLY tenant config (Mon 2026-03-02 .. Sun 2026-03-08).
WEEK_START, WEEK_END = "2026-03-02", "2026-03-08"
STORE["shifts"] = [
    # Harry: a normal 8h clocked day (act>0) ...
    {"id": 101, "org_id": ORG, "employee_id": "HRL1", "store_code": "Store1", "shift_date": "2026-03-02",
     "scheduled_hours": 8, "actual_hours": 8, "is_deleted": False},
    # ... a scheduled-only day (act==0 -> scheduled fallback) ...
    {"id": 102, "org_id": ORG, "employee_id": "HRL1", "store_code": "Store1", "shift_date": "2026-03-03",
     "scheduled_hours": 6, "actual_hours": 0, "is_deleted": False},
    # Sally: full week scheduled (irrelevant to her pay — salaried).
    {"id": 103, "org_id": ORG, "employee_id": "SAL1", "store_code": "Store1", "shift_date": "2026-03-02",
     "scheduled_hours": 40, "actual_hours": 40, "is_deleted": False},
]
STORE["timelog"] = [
    # Harry: a closed punch on a day with NO shift -> counts on its own.
    {"id": 201, "org_id": ORG, "employee_id": "HRL1", "work_date": "2026-03-04", "store_code": "Store1",
     "clock_in": "2026-03-04T09:00:00Z", "clock_out": "2026-03-04T14:15:00Z", "hours": 5.25},
    # Harry: a closed punch on 2026-03-02, SAME day as a shift -> must NOT double-count.
    {"id": 202, "org_id": ORG, "employee_id": "HRL1", "work_date": "2026-03-02", "store_code": "Store1",
     "clock_in": "2026-03-02T09:00:00Z", "clock_out": "2026-03-02T17:00:00Z", "hours": 8.0},
    # Harry: an OPEN punch -> must never count.
    {"id": 203, "org_id": ORG, "employee_id": "HRL1", "work_date": "2026-03-06", "store_code": "Store1",
     "clock_in": "2026-03-06T09:00:00Z", "clock_out": None, "hours": None},
]
STORE["salary_advance_ledger"] = []

# ── (a) hours/owed equivalence with GET /storeops/payroll on the SAME fixture/range ────────────────
payroll = router_mod.get_payroll(start=WEEK_START, end=WEEK_END, authorization="Bearer manager", org_id=ORG)
payroll_by_eid = {r["employee_id"]: r for r in payroll}
harry_payroll = payroll_by_eid["HRL1"]
sally_payroll = payroll_by_eid["SAL1"]

owed = router_mod.get_salary_owed(start=WEEK_START, end=WEEK_END, authorization="Bearer manager", org_id=ORG)
owed_by_eid = {e["employee_id"]: e for e in owed["employees"]}
harry_owed = owed_by_eid["HRL1"]
sally_owed = owed_by_eid["SAL1"]

harry_owed_total_hours = round(sum(d["hours"] for d in harry_owed["days"]), 2)
check("a1 Harry's total hours (salary-owed) == /payroll's actual_hours",
      harry_owed_total_hours == harry_payroll["actual_hours"],
      (harry_owed_total_hours, harry_payroll["actual_hours"]))
check("a2 Harry's owed_total == /payroll's actual_pay (byte-exact)",
      harry_owed["owed_total"] == harry_payroll["actual_pay"],
      (harry_owed["owed_total"], harry_payroll["actual_pay"]))
check("a3 Harry: 03-02 (shift+punch same day) not double-counted (8h, not 16h)",
      next(d for d in harry_owed["days"] if d["date"] == "2026-03-02")["hours"] == 8.0)
check("a4 Harry: 03-03 scheduled-fallback day basis='scheduled'",
      next(d for d in harry_owed["days"] if d["date"] == "2026-03-03")["basis"] == "scheduled")
check("a5 Harry: 03-04 unshifted punch counted, basis='actual'",
      next(d for d in harry_owed["days"] if d["date"] == "2026-03-04")["hours"] == 5.25)
check("a6 Harry: 03-06 open punch never appears / contributes 0",
      next((d for d in harry_owed["days"] if d["date"] == "2026-03-06"), {"hours": 0.0})["hours"] == 0.0)
check("a7 Σ day.owed foots exactly to owed_total (Harry)",
      round(sum(d["owed"] for d in harry_owed["days"]), 2) == harry_owed["owed_total"])

check("a8 Sally's owed_total == /payroll's actual_pay for the SAME exact weekly period ($1000)",
      sally_owed["owed_total"] == sally_payroll["actual_pay"] == 1000.0,
      (sally_owed["owed_total"], sally_payroll["actual_pay"]))
check("a9 Sally's basis == annual", sally_owed["pay_basis"] == "annual")
check("a10 Σ day.owed foots exactly to owed_total (Sally, salaried)",
      round(sum(d["owed"] for d in sally_owed["days"]), 2) == sally_owed["owed_total"])

# ── (d) GET /payroll unaffected by advances (byte-identical before/after) ──────────────────────────
payroll_before_json = json.dumps(payroll, sort_keys=True)

# ── (b) org-stamped write + idempotent recompute ────────────────────────────────────────────────────
r1 = router_mod.record_salary_advance(
    {"employee_id": "HRL1", "amount": 50.0, "paid_date": "2026-03-02", "store_code": "Store1",
     "withdrawal_ref": "env-1", "recorded_by": "dm1"},
    authorization="Bearer manager", org_id=ORG)
check("b1 record returns ok + a ledger id", r1["ok"] is True and r1["id"])
check("b2 ledger row org-stamped to the CALLER's org, not a constant",
      STORE["salary_advance_ledger"][-1]["org_id"] == ORG)
check("b3 small advance (< earned) -> zero excess, no push cells", r1["additional_payroll"]["cells"] == [])

# a SECOND org records an advance for an employee_id that COLLIDES with ORG's HRL1 string.
router_mod.record_salary_advance(
    {"employee_id": "HRL1", "amount": 10000.0, "paid_date": "2026-03-02", "store_code": "StoreX",
     "withdrawal_ref": "env-x", "recorded_by": "dm2"},
    authorization="Bearer manager2", org_id=ORG2)
owed_org2 = router_mod.get_salary_owed(start=WEEK_START, end=WEEK_END, authorization="Bearer manager2", org_id=ORG2)
check("b4 org isolation: ORG2's read never sees ORG's advance/employee data",
      all(e["employee_id"] != "HRL1" or e["cash_paid_total"] != 50.0 for e in owed_org2["employees"]))
owed_org1_after = router_mod.get_salary_owed(start=WEEK_START, end=WEEK_END, authorization="Bearer manager", org_id=ORG)
check("b5 org isolation: ORG's own read is unaffected by ORG2's advance",
      next(e for e in owed_org1_after["employees"] if e["employee_id"] == "HRL1")["cash_paid_total"] == 50.0)

# push Harry's cumulative cash well past his earned total this period -> excess appears.
router_mod.record_salary_advance(
    {"employee_id": "HRL1", "amount": 500.0, "paid_date": "2026-03-05", "store_code": "Store1",
     "withdrawal_ref": "env-2", "recorded_by": "dm1"},
    authorization="Bearer manager", org_id=ORG)
period = "2026-03"
run1 = router_mod.run_additional_payroll(period, authorization="Bearer manager", org_id=ORG)
run2 = router_mod.run_additional_payroll(period, authorization="Bearer manager", org_id=ORG)
check("b6 idempotent recompute: running twice with no new advance gives IDENTICAL cells",
      json.dumps(run1["cells"], sort_keys=True) == json.dumps(run2["cells"], sort_keys=True),
      (run1["cells"], run2["cells"]))
check("b7 idempotent recompute: employee excess figures identical across both runs",
      json.dumps(run1["employees"], sort_keys=True) == json.dumps(run2["employees"], sort_keys=True))

# ── (c) Additional Payroll == max(0, paid_to_date - earned_to_date), incl. the zero-case boundary ──
harry_row = next(e for e in run1["employees"] if e["employee_id"] == "HRL1")
expected_excess = round(max(0.0, harry_row["cash_paid_to_date"] - harry_row["earned_to_date"]), 2)
check("c1 excess == max(0, paid-earned) exactly", harry_row["excess"] == expected_excess,
      (harry_row["excess"], harry_row["cash_paid_to_date"], harry_row["earned_to_date"]))
check("c2 excess > 0 given the large advance ($550 paid vs a small weekly hourly total)",
      harry_row["excess"] > 0, harry_row)
check("c3 cells total equals the sum of positive-excess employees",
      round(sum(c["amount"] for c in run1["cells"]), 2) == round(sum(e["excess"] for e in run1["employees"] if e["excess"] > 0), 2))

# zero-case boundary: a fresh employee whose SOLE advance exactly equals their earned amount this period.
STORE["employees"].append({"id": "4", "employee_id": "EXACT1", "org_id": ORG, "name": "Xavier Exact",
                            "home_store": "Store1", "pay_rate": 20.0, "pay_basis": "hourly",
                            "pay_amount": None, "hire_date": None, "termination_date": None, "is_active": True})
STORE["shifts"].append({"id": 104, "org_id": ORG, "employee_id": "EXACT1", "store_code": "Store1",
                         "shift_date": "2026-03-02", "scheduled_hours": 5, "actual_hours": 5, "is_deleted": False})
router_mod.record_salary_advance(
    {"employee_id": "EXACT1", "amount": 100.0, "paid_date": "2026-03-02", "store_code": "Store1",
     "withdrawal_ref": "env-3", "recorded_by": "dm1"},
    authorization="Bearer manager", org_id=ORG)
run3 = router_mod.run_additional_payroll(period, authorization="Bearer manager", org_id=ORG)
xavier_row = next(e for e in run3["employees"] if e["employee_id"] == "EXACT1")
check("c4 paid == earned -> excess EXACTLY 0 (not rounding noise)", xavier_row["excess"] == 0.0, xavier_row)

# ── (d) GET /payroll byte-identical before vs after all the advances above ─────────────────────────
payroll_after = router_mod.get_payroll(start=WEEK_START, end=WEEK_END, authorization="Bearer manager", org_id=ORG)
payroll_after_json = json.dumps([r for r in payroll_after if r["employee_id"] in ("HRL1", "SAL1")], sort_keys=True)
payroll_before_json_filtered = json.dumps([r for r in payroll if r["employee_id"] in ("HRL1", "SAL1")], sort_keys=True)
check("d1 GET /payroll byte-identical before/after recording advances (payroll_gross basis untouched)",
      payroll_after_json == payroll_before_json_filtered)

# ── history + read-only preview endpoints sanity ────────────────────────────────────────────────────
hist = router_mod.salary_advance_history(start=WEEK_START, end=WEEK_END, authorization="Bearer manager", org_id=ORG)
check("history: available + org-scoped rows only", hist["available"] and
      all(r["employee_id"] in ("HRL1", "EXACT1") for r in hist["items"]), len(hist["items"]))

preview = router_mod.get_additional_payroll(period, authorization="Bearer manager", org_id=ORG)
check("preview matches the last run's cells (read-only, no double-push)",
      json.dumps(preview["cells"], sort_keys=True) == json.dumps(run3["cells"], sort_keys=True))

# ── validation ───────────────────────────────────────────────────────────────────────────────────
from fastapi import HTTPException  # noqa: E402
try:
    router_mod.record_salary_advance({"employee_id": "GHOST-NOT-REAL", "amount": 10, "paid_date": "2026-03-02"},
                                      authorization="Bearer manager", org_id=ORG)
    check("unknown employee_id rejected", False, "did not raise")
except HTTPException as e:
    check("unknown employee_id rejected (400, pick-don't-type)", e.status_code == 400)

try:
    router_mod.record_salary_advance({"employee_id": "HRL1", "amount": -5, "paid_date": "2026-03-02"},
                                      authorization="Bearer manager", org_id=ORG)
    check("non-positive amount rejected", False, "did not raise")
except HTTPException as e:
    check("non-positive amount rejected (400)", e.status_code == 400)

print(f"\n{'='*70}\nharness_salary_owed_router_integration: {len(PASS)} PASS, {len(FAIL)} FAIL\n{'='*70}")
for f in FAIL:
    print("FAIL:", f)
if FAIL:
    sys.exit(1)
print("ALL PASS")
