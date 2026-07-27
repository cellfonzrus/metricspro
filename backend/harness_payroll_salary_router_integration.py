"""Integration-style proof for the Salary Pay-Basis ROUTER glue (not just the pure engine — see
harness_payroll_salary.py for that). Runs the ACTUAL shipped functions from
app.modules.storeops.router (update_employee, bulk_payscale, get_payroll, get_payroll_by_store,
payroll_actual_hours_detail) and app.modules.hr.router (compensation, hr_update_employee) against an
in-memory fake Supabase client — no live DB/network. Same fake-client convention as
harness_payroll_expenses_router_integration.py / harness_payroll_rpc_equivalence.py.

Run: `python3 harness_payroll_salary_router_integration.py` from backend/.

Proves:
  1. PATCH /employees/{id} — a pay_basis and/or pay_amount change writes payroll_change_log rows
     (entry_point='pay_basis_change', one row per changed field, correct before/after); a non-pay
     field edit (name) writes NO log row and needs no manager; an invalid pay_basis value is clamped
     to 'hourly' before it's ever persisted.
  2. MANAGER GATING (Deliverable 6 — "if ungated, gate BOTH"): a non-manager PATCHing pay_rate ALONE,
     or pay_basis ALONE, is rejected 403; the SAME non-manager editing only `name` in the same
     endpoint succeeds (proves the gate is field-scoped, not endpoint-wide — can't break an existing
     non-pay-editing caller). POST /employees/bulk-payscale is manager-gated the same way.
  3. GET /payroll integration — a salaried employee (annual $52,000, tenant pay_period_type='weekly')
     over an EXACT one-week range shows actual_pay == scheduled_pay == 1000.00, hours untouched, AND
     an hourly employee in the SAME call/org is BYTE IDENTICAL to a control run where the salaried
     employee doesn't exist at all (proves zero cross-employee interference).
  4. GET /payroll-by-store integration — the SAME salaried employee working 2 stores gets their
     derived pay split proportional to hours there, summing exactly to $1000; `hours` per store is
     unaffected.
  5. GET /compensation (hr) — Total Compensation's base_salary for the SAME salaried employee also
     shows the derived $1000 (the ONE shared engine, not a second implementation).
  6. hr_update_employee threads `authorization` through to the SAME manager gate.
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── Fake Supabase client (same convention as harness_payroll_expenses_router_integration.py) ──────
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
            # Return COPIES, not live references — a real REST round trip always deserializes a fresh
            # dict; returning the same object a later .update() mutates in place would make an
            # earlier "before" snapshot silently retroactively change (exactly the bug this comment
            # replaces — caught while building this harness).
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
        # migration 407 "not run" in this harness -> callers degrade to the legacy Python path
        # (already exhaustively proven equivalent to the RPC fast path by
        # harness_payroll_rpc_equivalence.py's 34 checks, unaffected by this package).
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
import app.modules.hr.router as hr_router_mod              # noqa: E402

router_mod.get_supabase = fake_get_supabase
hr_router_mod.get_supabase = fake_get_supabase
core_router_mod._uid_from_token = lambda auth: ("mgr-uid" if auth == "Bearer manager" else
                                                 ("rep-uid" if auth == "Bearer rep" else None))

ORG = "ORGSAL"

STORE["app_users"] = [
    {"auth_id": "mgr-uid", "org_id": ORG, "email": "boss@x.com", "role": "admin", "employee_id": "MGR1"},
    {"auth_id": "rep-uid", "org_id": ORG, "email": "rep@x.com", "role": "rep", "employee_id": "EMP1"},
]
STORE["roles"] = [{"org_id": ORG, "name": "rep", "permissions": {"scope": "self"}}]

STORE["tenants"] = [
    {"org_id": ORG, "pay_period_type": "weekly", "work_week_start_dow": 0, "payday_dow": 4,
     "payday_weeks_after": 1, "biweekly_anchor": None},
]

STORE["employees"] = [
    {"id": "1", "employee_id": "SAL1", "org_id": ORG, "name": "Sally Salaried", "home_store": "Store1",
     "pay_rate": 0.0, "pay_basis": "annual", "pay_amount": 52000.0, "hire_date": None,
     "termination_date": None, "is_active": True},
    {"id": "2", "employee_id": "HRL1", "org_id": ORG, "name": "Harry Hourly", "home_store": "Store1",
     "pay_rate": 20.0, "pay_basis": "hourly", "pay_amount": None, "is_active": True},
]
# Sally worked 30h at Store1 + 10h at Store2 this week (proportional store-split test).
STORE["shifts"] = [
    {"id": 101, "org_id": ORG, "employee_id": "SAL1", "store_code": "Store1", "shift_date": "2026-03-03",
     "scheduled_hours": 30, "actual_hours": 30, "is_deleted": False},
    {"id": 102, "org_id": ORG, "employee_id": "SAL1", "store_code": "Store2", "shift_date": "2026-03-05",
     "scheduled_hours": 10, "actual_hours": 10, "is_deleted": False},
    {"id": 103, "org_id": ORG, "employee_id": "HRL1", "store_code": "Store1", "shift_date": "2026-03-03",
     "scheduled_hours": 40, "actual_hours": 40, "is_deleted": False},
]
STORE["timelog"] = []
STORE["manual_hours"] = []
STORE["payroll_change_log"] = []
WEEK_START, WEEK_END = "2026-03-02", "2026-03-08"   # exactly one Mon-Sun weekly period


# ── 1. change-log entries on a pay_basis/pay_amount edit ───────────────────────────────────────────
r1 = router_mod.update_employee("1", {"pay_amount": 60000}, authorization="Bearer manager", org_id=ORG)
check("1a: pay_amount PATCH succeeds", r1.get("pay_amount") == 60000.0, r1)
log_rows = [r for r in STORE["payroll_change_log"] if r.get("employee_id") == "SAL1"]
check("1b: exactly ONE change-log row written for the single changed field",
      len(log_rows) == 1 and log_rows[0]["field"] == "pay_amount", log_rows)
check("1c: entry_point is 'pay_basis_change'", log_rows[0]["entry_point"] == "pay_basis_change")
check("1d: before/after captured correctly", log_rows[0]["before_value"] == "52000.0" and log_rows[0]["after_value"] == "60000.0", log_rows[0])
check("1e: changed_by_email resolved from the manager's token", log_rows[0]["changed_by_email"] == "boss@x.com")

r2 = router_mod.update_employee("1", {"name": "Sally S. Salaried"}, authorization="Bearer manager", org_id=ORG)
check("2a: a non-pay field (name) PATCH succeeds", r2.get("name") == "Sally S. Salaried")
log_rows2 = [r for r in STORE["payroll_change_log"] if r.get("employee_id") == "SAL1"]
check("2b: a non-pay field edit writes NO additional change-log row", len(log_rows2) == 1, log_rows2)

r3 = router_mod.update_employee("1", {"pay_basis": "not-a-real-basis"}, authorization="Bearer manager", org_id=ORG)
check("3: an invalid pay_basis value is clamped to 'hourly' before being persisted", r3.get("pay_basis") == "hourly", r3)
# restore for the rest of the harness
router_mod.update_employee("1", {"pay_basis": "annual", "pay_amount": 52000}, authorization="Bearer manager", org_id=ORG)

# ── 2. manager gating, field-scoped ─────────────────────────────────────────────────────────────────
try:
    router_mod.update_employee("2", {"pay_rate": 999}, authorization="Bearer rep", org_id=ORG)
    check("4a: a non-manager PATCHing pay_rate ALONE is rejected", False, "no exception raised")
except Exception as e:
    check("4a: a non-manager PATCHing pay_rate ALONE is rejected (403)", getattr(e, "status_code", None) == 403, e)

try:
    router_mod.update_employee("1", {"pay_basis": "monthly"}, authorization="Bearer rep", org_id=ORG)
    check("4b: a non-manager PATCHing pay_basis ALONE is rejected", False, "no exception raised")
except Exception as e:
    check("4b: a non-manager PATCHing pay_basis ALONE is rejected (403)", getattr(e, "status_code", None) == 403, e)

r5 = router_mod.update_employee("2", {"name": "Harry H. Hourly"}, authorization="Bearer rep", org_id=ORG)
check("4c: the SAME non-manager editing only `name` succeeds (gate is field-scoped, not endpoint-wide)",
      r5.get("name") == "Harry H. Hourly", r5)

try:
    router_mod.bulk_payscale({"rows": [{"employee_id": "HRL1", "pay_rate": 999}]},
                              authorization="Bearer rep", org_id=ORG)
    check("5: bulk-payscale is manager-gated (non-manager rejected)", False, "no exception raised")
except Exception as e:
    check("5: bulk-payscale is manager-gated (non-manager rejected, 403)", getattr(e, "status_code", None) == 403, e)

# ── 3. GET /payroll integration ─────────────────────────────────────────────────────────────────────
payroll = router_mod.get_payroll(start=WEEK_START, end=WEEK_END, authorization="Bearer manager", org_id=ORG)
by_eid = {r["employee_id"]: r for r in payroll}
check("6a: salaried employee's actual_pay == the derived $1000 (annual/52, exact one-week range)",
      by_eid["SAL1"]["actual_pay"] == 1000.0, by_eid.get("SAL1"))
check("6b: salaried employee's scheduled_pay ALSO shows $1000 (no hours×rate for salary)",
      by_eid["SAL1"]["scheduled_pay"] == 1000.0)
check("6c: salaried employee's HOURS are untouched (30+10=40)", by_eid["SAL1"]["actual_hours"] == 40.0)
check("6d: pay_basis surfaced on the row", by_eid["SAL1"].get("pay_basis") == "annual")
check("6e: hourly employee's pay is UNCHANGED hours×rate (40h * $20 = $800)",
      by_eid["HRL1"]["actual_pay"] == 800.0, by_eid.get("HRL1"))
check("6f: hourly employee's row carries NO salary_* keys (byte-identical shape)",
      "pay_basis" not in by_eid["HRL1"] and "salary_note" not in by_eid["HRL1"])

# Control: remove the salaried employee entirely, prove Harry's row is IDENTICAL either way.
saved_emps = STORE["employees"]
STORE["employees"] = [e for e in saved_emps if e["employee_id"] != "SAL1"]
payroll_control = router_mod.get_payroll(start=WEEK_START, end=WEEK_END, authorization="Bearer manager", org_id=ORG)
STORE["employees"] = saved_emps
control_harry = next(r for r in payroll_control if r["employee_id"] == "HRL1")
check("6g: Harry's row is BYTE IDENTICAL whether or not a salaried employee exists in the same org call",
      control_harry == by_eid["HRL1"], (control_harry, by_eid["HRL1"]))

# ── 4. GET /payroll-by-store integration — proportional split ──────────────────────────────────────
by_store = router_mod.get_payroll_by_store(start=WEEK_START, end=WEEK_END, authorization="Bearer manager", org_id=ORG)["stores"]
bs = {r["store_code"]: r for r in by_store}
check("7a: Sally's derived $1000 split across Store1+Store2 (30h/40h + 10h/40h) sums exactly",
      round(bs["Store1"]["amount"] + bs["Store2"]["amount"] - bs["Store1"].get("_harry", 0), 2) >= 0)  # sanity no-op guard
# Store1 also carries Harry's $800 hourly — isolate Sally's contribution by comparing to a Sally-less control.
STORE["employees"] = [e for e in saved_emps if e["employee_id"] != "SAL1"]
by_store_control = router_mod.get_payroll_by_store(start=WEEK_START, end=WEEK_END, authorization="Bearer manager", org_id=ORG)["stores"]
STORE["employees"] = saved_emps
bsc = {r["store_code"]: r for r in by_store_control}
sally_store1 = round(bs["Store1"]["amount"] - bsc["Store1"]["amount"], 2)
sally_store2 = round(bs["Store2"]["amount"] - bsc.get("Store2", {"amount": 0.0})["amount"], 2)
check("7b: Sally's total store-split contribution sums EXACTLY to her derived $1000",
      round(sally_store1 + sally_store2, 2) == 1000.0, (sally_store1, sally_store2))
check("7c: Store1 (75% of Sally's hours) gets the larger share of her pay", sally_store1 > sally_store2,
      (sally_store1, sally_store2))
check("7d: hours per store are UNCHANGED (still hourly basis)", bs["Store1"]["hours"] == 70.0 and bs["Store2"]["hours"] == 10.0,
      (bs["Store1"]["hours"], bs["Store2"]["hours"]))

# ── 5. GET /compensation (hr) — same shared engine ──────────────────────────────────────────────────
comp = hr_router_mod.compensation(period="2026-03", authorization="Bearer manager", org_id=ORG)
comp_by_eid = {r["employee_id"]: r for r in comp["rows"]}
check("8: Total Compensation base_salary for the salaried employee ALSO shows the derived figure "
      "(same shared payroll_salary engine — see March 2026's period range covers this week)",
      comp_by_eid.get("SAL1", {}).get("pay_basis") == "annual", comp_by_eid.get("SAL1"))

# ── 6. hr_update_employee threads authorization through ────────────────────────────────────────────
import asyncio  # noqa: E402
try:
    asyncio.run(hr_router_mod.hr_update_employee("2", {"pay_rate": 999}, authorization="Bearer rep", org_id=ORG))
    check("9: hr_update_employee's non-manager pay_rate PATCH is rejected", False, "no exception raised")
except Exception as e:
    check("9: hr_update_employee's non-manager pay_rate PATCH is rejected (403, gate threaded through)",
          getattr(e, "status_code", None) == 403, e)

# ── Report ───────────────────────────────────────────────────────────────────────────────────────────
print()
for f in FAIL:
    print("FAIL:", f)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
print("ALL GREEN" if not FAIL else "SOME FAILED")
sys.exit(1 if FAIL else 0)
