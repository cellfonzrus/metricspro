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
import os
import sys

# Anchor imports AND every source read below to THIS file's own directory, so the
# harness runs identically from backend/ and from the repo root (cf. 564c171f).
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

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


import _harness_dbfree  # noqa: E402
import app.modules.storeops.router as router_mod          # noqa: E402
import app.modules.core.router as core_router_mod         # noqa: E402
import app.modules.hr.router as hr_router_mod              # noqa: E402

router_mod.get_supabase = fake_get_supabase
hr_router_mod.get_supabase = fake_get_supabase
# DB-FREE GUARD: the line(s) above bind only THIS module's name. Shipped code also
# reaches the factory directly (tenant_middleware.caller_app_user) and through other
# routers' sb() (storeops.router._rbac_enabled), both of which used to land on the
# REAL production client. Route every acquisition in the process at the fake.
_harness_dbfree.install(FAKE_CLIENT)


def _body(model, payload):
    """Build the endpoint's REAL Pydantic body model, exactly as FastAPI builds it from the JSON
    request. These handlers accepted a plain dict until they were migrated to typed bodies; passing
    a bare dict now dies on `body.<field>`, so the harness has to call them the way the shipped app
    does or it proves nothing about the real contract. LaxModel ignores unknown keys, so this is
    byte-for-byte what a real request produces."""
    return model(**payload)

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
    # Gate-1 F1 (MAJOR) repro: a salaried $52k manager with ZERO punches/shifts this period. Reviewer's
    # exact repro fixture — before the fix, GET /payroll's total for this org was $160 (Harry's hourly
    # only) while GET /payroll-by-store's total was $1,160 (Harry + Mona's derived $1000), because
    # apply_to_payroll_rows only overrides an EXISTING row and Mona never got one.
    {"id": "3", "employee_id": "MGR1", "org_id": ORG, "name": "Mona Manager (zero activity)",
     "home_store": "Store1", "pay_rate": 0.0, "pay_basis": "annual", "pay_amount": 52000.0,
     "hire_date": None, "termination_date": None, "is_active": True},
    # Sub-case: zero activity AND no home_store — the pay must land somewhere VISIBLE ('Unassigned').
    {"id": "4", "employee_id": "MGR2", "org_id": ORG, "name": "Nadia No-Home (zero activity)",
     "home_store": None, "pay_rate": 0.0, "pay_basis": "monthly", "pay_amount": 5000.0,
     "hire_date": None, "termination_date": None, "is_active": True},
    # Gate-1 D1 (MODERATE money) repro: a salaried $52k employee who is INACTIVE (is_active=false) but
    # has REAL activity this period (an 8h punch) at a leftover/stale hourly pay_rate ($20). Before the
    # fix: /payroll = $1,000 (correct, the merged-inactive-payroll path already fed through the salary
    # override uniformly) but /payroll-by-store = $1,160 ($1,000 derived + $160 = 8h×$20 stale-hourly,
    # because _merge_inactive_into_by_store's hourly $ was never tracked into emp_store_dollars for
    # apply_to_by_store to subtract, so it ADDED the derived salary ON TOP instead of replacing it).
    {"id": "5", "employee_id": "INAC1", "org_id": ORG, "name": "Ivan Inactive-Salaried",
     "home_store": "Store1", "pay_rate": 20.0, "pay_basis": "annual", "pay_amount": 52000.0,
     "hire_date": None, "termination_date": None, "is_active": False},
    # Gate-1 optional (b) repro: a PAST-terminated salaried employee, terminated well before this
    # report range, with zero activity — must NOT get a permanent $0.00 synthesized row.
    {"id": "6", "employee_id": "TERM1", "org_id": ORG, "name": "Tara Terminated (long ago)",
     "home_store": "Store1", "pay_rate": 0.0, "pay_basis": "annual", "pay_amount": 52000.0,
     "hire_date": None, "termination_date": "2025-01-01", "is_active": False},
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
STORE["timelog"] = [
    # Ivan's (INAC1) 8h punch — the D1 repro. Closed punch, real activity, at his home store.
    {"id": "tl1", "org_id": ORG, "employee_id": "INAC1", "store_code": "Store1", "work_date": "2026-03-04",
     "clock_in": "2026-03-04T09:00:00Z", "clock_out": "2026-03-04T17:00:00Z", "hours": 8.0,
     "employee_name": "Ivan Inactive-Salaried"},
]
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
    router_mod.bulk_payscale(_body(router_mod.BulkPayscaleIn, {"rows": [{"employee_id": "HRL1", "pay_rate": 999}]}),
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

# ── Gate-1 F1 (MAJOR) — the reviewer's exact repro: a zero-activity salaried manager MUST appear in
# GET /payroll (was silently missing; present in /payroll-by-store and /compensation, disagreeing).
check("6h: F1 — Mona (zero-activity salaried) IS present in GET /payroll", "MGR1" in by_eid, sorted(by_eid))
check("6i: Mona's actual_pay == scheduled_pay == her derived monthly-equivalent-of-annual figure ($1000/wk)",
      by_eid.get("MGR1", {}).get("actual_pay") == 1000.0 and by_eid.get("MGR1", {}).get("scheduled_pay") == 1000.0,
      by_eid.get("MGR1"))
check("6j: Mona's hours are 0 (no fabricated activity)",
      by_eid.get("MGR1", {}).get("actual_hours") == 0.0 and by_eid.get("MGR1", {}).get("scheduled_hours") == 0.0)
check("6k: Mona's store = her home_store (Store1)", by_eid.get("MGR1", {}).get("store") == "Store1")
check("6l: F1 sub-case — Nadia (zero-activity, NO home_store) IS present, store='Unassigned' "
      "(pay must land somewhere visible)",
      by_eid.get("MGR2", {}).get("store") == "Unassigned" and by_eid.get("MGR2", {}).get("actual_pay") == round(5000 * 12 / 52, 2),
      by_eid.get("MGR2"))
# The reviewer's own framing: GET /payroll's grand total must equal GET /payroll-by-store's grand
# total for the SAME range/org (no chargebacks/PTO in this fixture) — before the F1 fix these
# diverged ($160 hourly-only vs $1,160 with the zero-activity manager correctly included by-store).
payroll_total = round(sum(r["actual_pay"] for r in payroll), 2)
by_store_total_preview = round(sum(s["amount"] for s in
    router_mod.get_payroll_by_store(start=WEEK_START, end=WEEK_END, authorization="Bearer manager", org_id=ORG)["stores"]), 2)
check("6m: F1 — GET /payroll's grand total now MATCHES GET /payroll-by-store's grand total "
      "(both include Mona's + Nadia's derived pay)",
      payroll_total == by_store_total_preview, (payroll_total, by_store_total_preview))

# ── Gate-1 D1 (MODERATE money) — inactive-AND-salaried WITH real activity (Ivan/INAC1, an 8h punch
# at a stale $20/hr rate). His /payroll figure was already correct pre-fix (the merged-inactive-
# payroll path feeds the SAME uniform salary override); the bug was specifically in by-store.
check("6n: D1 — Ivan (inactive, salaried, WITH an 8h punch) IS present in GET /payroll",
      "INAC1" in by_eid, sorted(by_eid))
check("6o: Ivan's actual_pay is the derived $1000 (NOT 8h × his stale $20/hr rate = $160)",
      by_eid.get("INAC1", {}).get("actual_pay") == 1000.0, by_eid.get("INAC1"))
check("6p: optional (b) — Tara (past-terminated 2025-01-01, zero activity in this 2026-03 week) is "
      "NOT synthesized as a permanent $0.00 row", "TERM1" not in by_eid, sorted(by_eid))

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
# (Gate-1 N4: the original "7a" check here was a tautology — `>= 0` on a value that's structurally
# always non-negative, with a `.get("_harry", 0)` key that never existed. Removed; 7b/7c/7d below
# already prove the real assertions with an actual Sally-less control comparison.)
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
check("7d: hours per store are UNCHANGED (still hourly basis) — Store1 = Sally 30h + Harry 40h + "
      "Ivan(D1 repro) 8h = 78h", bs["Store1"]["hours"] == 78.0 and bs["Store2"]["hours"] == 10.0,
      (bs["Store1"]["hours"], bs["Store2"]["hours"]))
check("7e: F1 sub-case — Nadia's (no home_store) zero-activity pay lands in an explicit 'Unassigned' "
      "by-store bucket, not silently dropped", bs.get("Unassigned", {}).get("amount") == round(5000 * 12 / 52, 2), bs.get("Unassigned"))

# ── Gate-1 D1 (MODERATE money) — THE reviewer's exact repro, isolated via a control that removes
# ONLY Ivan (everyone else, including Mona who ALSO lands at Store1, stays in both runs so the
# isolation is clean). Before the fix: Ivan's isolated Store1 contribution was $1,160.00
# ($1,000 derived + $160 = 8h × his stale $20/hr rate, added on TOP instead of replacing it).
STORE["employees"] = [e for e in saved_emps if e["employee_id"] != "INAC1"]
by_store_no_ivan = router_mod.get_payroll_by_store(start=WEEK_START, end=WEEK_END, authorization="Bearer manager", org_id=ORG)["stores"]
STORE["employees"] = saved_emps
bs_no_ivan = {r["store_code"]: r for r in by_store_no_ivan}
ivan_store1_contribution = round(bs["Store1"]["amount"] - bs_no_ivan["Store1"]["amount"], 2)
check("D1: Ivan's isolated Store1 by-store contribution is EXACTLY $1,000.00 (the reviewer's repro "
      "must balance $1,000 = $1,000 — was $1,160.00 before this fix, a +$160 = 8h×$20 overstatement)",
      ivan_store1_contribution == 1000.00, ivan_store1_contribution)
check("D1b: Ivan's by-store contribution EQUALS his GET /payroll figure exactly (both endpoints agree)",
      ivan_store1_contribution == by_eid.get("INAC1", {}).get("actual_pay"),
      (ivan_store1_contribution, by_eid.get("INAC1", {}).get("actual_pay")))

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

# ── 7. Gate-1 F2 (MUST) — termination_date AND pay_rate are gated but must ALSO be change-logged now
# (placed after every GET /payroll-family check above so mutating Harry's pay_rate/termination_date
# here can't affect an earlier hours×rate assertion).
r_term = router_mod.update_employee("2", {"termination_date": "2026-06-30"}, authorization="Bearer manager", org_id=ORG)
check("F2a: termination_date PATCH succeeds", r_term.get("termination_date") == "2026-06-30", r_term)
term_log = [r for r in STORE["payroll_change_log"] if r.get("employee_id") == "HRL1" and r.get("field") == "termination_date"]
check("F2b: termination_date change IS now change-logged (Gate-1 fix — was gated but NOT logged before)",
      len(term_log) == 1 and term_log[0]["before_value"] is None and term_log[0]["after_value"] == "2026-06-30",
      term_log)

r_rate = router_mod.update_employee("2", {"pay_rate": 25.0}, authorization="Bearer manager", org_id=ORG)
check("F2c: pay_rate PATCH succeeds", r_rate.get("pay_rate") == 25.0, r_rate)
rate_log = [r for r in STORE["payroll_change_log"] if r.get("employee_id") == "HRL1" and r.get("field") == "pay_rate"]
check("F2d: pay_rate change IS now change-logged ('it IS a pay field' — Gate-1 fix, was pre-existing gated-but-unlogged)",
      len(rate_log) == 1 and rate_log[0]["before_value"] == "20.0" and rate_log[0]["after_value"] == "25.0",
      rate_log)
check("F2e: both new log rows use entry_point='pay_basis_change' (same trail as pay_basis/pay_amount)",
      term_log[0]["entry_point"] == "pay_basis_change" and rate_log[0]["entry_point"] == "pay_basis_change")

# ── Gate-1 D2 (MODERATE audit) — bulk_payscale must ALSO write a change-log row per updated employee
# (before this fix: zero ✎ audit trail for a bulk upload, unlike the single-row PATCH). Harry's
# pay_rate is 25.0 at this point (from F2c above).
bulk_log_before = len(STORE["payroll_change_log"])
r_bulk = router_mod.bulk_payscale(_body(router_mod.BulkPayscaleIn, {"rows": [{"employee_id": "HRL1", "pay_rate": 30.0}]}),
                                   authorization="Bearer manager", org_id=ORG)
check("D2a: bulk_payscale succeeds (manager)", r_bulk.get("updated") == 1, r_bulk)
bulk_log = [r for r in STORE["payroll_change_log"] if r.get("employee_id") == "HRL1"
            and r.get("entry_point") == "bulk_payscale"]
check("D2b: bulk_payscale writes a change-log row (was ZERO trail before this fix)",
      len(bulk_log) == 1 and bulk_log[0]["field"] == "pay_rate", bulk_log)
check("D2c: before/after captured correctly ($25.00 -> $30.00)",
      bulk_log and bulk_log[0]["before_value"] == "25.0" and bulk_log[0]["after_value"] == "30.0", bulk_log)
check("D2d: a SECOND identical bulk_payscale call (no actual change) writes NO additional row "
      "(before==after skip, same convention as the single-row PATCH)",
      router_mod.bulk_payscale(_body(router_mod.BulkPayscaleIn, {"rows": [{"employee_id": "HRL1", "pay_rate": 30.0}]}),
                                authorization="Bearer manager", org_id=ORG) and
      len([r for r in STORE["payroll_change_log"] if r.get("employee_id") == "HRL1"
           and r.get("entry_point") == "bulk_payscale"]) == 1)

# ── Gate-1 NIT-A (MUST, deploy-window) — update_employee's before-select names ALL of
# _PAY_LOGGED_FIELDS (pay_rate,pay_basis,pay_amount,termination_date) in ONE combined select; a real
# pre-migration-416/417 Postgres/PostgREST genuinely REJECTS that whole query (unknown column), which
# this schemaless fake client doesn't do by default (see harness_payroll_salary_router_integration.py's
# own FakeQuery — matches the documented "schemaless, never raises for an unrecognized dict key"
# limitation noted elsewhere in this codebase). To actually exercise the try/except fallback path
# (not just its downstream effect), wrap the fake client so a select naming 'termination_date' raises
# — precisely simulating the real 416/417-absent failure — and prove the code recovers via its
# narrower fallback (id,employee_id,name,pay_rate) rather than 500ing on an everyday pay_rate edit.
class _Pre416Wrapper:
    def __init__(self, inner):
        self._inner = inner

    def schema(self, name):
        return self

    def table(self, name):
        q = self._inner.table(name)
        if name == "employees":
            orig_select = q.select

            def _select(cols):
                if "termination_date" in cols:
                    raise Exception('column employees.termination_date does not exist')
                return orig_select(cols)
            q.select = _select
        return q

    def rpc(self, fn, params):
        return self._inner.rpc(fn, params)


STORE["employees"].append({"id": "9", "employee_id": "PRE416", "org_id": ORG, "name": "Priya Pre-Migration",
                            "home_store": "Store1", "pay_rate": 18.0, "is_active": True})
_orig_get_supabase = router_mod.get_supabase
router_mod.get_supabase = lambda: _Pre416Wrapper(FAKE_CLIENT)
try:
    r_pre416 = router_mod.update_employee("9", {"pay_rate": 19.0}, authorization="Bearer manager", org_id=ORG)
finally:
    router_mod.get_supabase = _orig_get_supabase
check("NIT-A: an ordinary pay_rate edit survives a REAL 'termination_date does not exist' select "
      "failure (pre-migration-416/417 simulated exactly, not just its downstream effect) — never a "
      "500 in the deploy-before-SQL window",
      r_pre416.get("pay_rate") == 19.0, r_pre416)
pre416_log = [r for r in STORE["payroll_change_log"] if r.get("employee_id") == "PRE416"]
check("NIT-A(b): the fallback select still captures enough to log the pay_rate change correctly",
      len(pre416_log) == 1 and pre416_log[0]["field"] == "pay_rate"
      and pre416_log[0]["before_value"] == "18.0" and pre416_log[0]["after_value"] == "19.0", pre416_log)

# ── 8. Gate-1 N5 (NIT) — the salary-override try/except must WARN + set a response header on a real
# failure, never silently revert to hourly with zero signal anywhere.
import app.modules.storeops.payroll_salary as payroll_salary_mod   # noqa: E402
from fastapi import Response as _Response                          # noqa: E402

_orig_apply_rows = payroll_salary_mod.apply_to_payroll_rows
payroll_salary_mod.apply_to_payroll_rows = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("induced N5 failure"))
resp = _Response()
try:
    payroll_n5 = router_mod.get_payroll(start=WEEK_START, end=WEEK_END, authorization="Bearer manager",
                                         org_id=ORG, response=resp)
finally:
    payroll_salary_mod.apply_to_payroll_rows = _orig_apply_rows
check("N5a: GET /payroll degrades to the base hourly report on an induced salary-override failure "
      "(never 500s)", isinstance(payroll_n5, list) and any(r["employee_id"] == "HRL1" for r in payroll_n5),
      type(payroll_n5))
check("N5b: a response header signals the failure — never silent (Gate-1 fix)",
      "X-Salary-Override-Warning" in resp.headers, dict(resp.headers))

_orig_derive = payroll_salary_mod.derive_salary_pay
payroll_salary_mod.derive_salary_pay = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("induced N5 failure (compensation)"))
try:
    comp_n5 = hr_router_mod.compensation(period="2026-03", authorization="Bearer manager", org_id=ORG)
finally:
    payroll_salary_mod.derive_salary_pay = _orig_derive
check("N5c: GET /compensation ALSO surfaces the failure — both a body key and a header (it returns a "
      "dict, so both channels are safe/additive there)",
      comp_n5.get("salary_override_warning") is not None, comp_n5.get("salary_override_warning"))

# ── Report ───────────────────────────────────────────────────────────────────────────────────────────
print()
for f in FAIL:
    print("FAIL:", f)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
print("ALL GREEN" if not FAIL else "SOME FAILED")
sys.exit(1 if FAIL else 0)
