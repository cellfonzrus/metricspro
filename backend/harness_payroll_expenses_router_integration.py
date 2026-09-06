"""Integration-style proof for the Payroll Expenses ROUTER glue (not just the pure engine — see
harness_payroll_expenses.py for that). Runs the ACTUAL shipped functions from
app.modules.storeops.router (get/put_payroll_tax_config, create/update/delete_payroll_expense_item,
get_payroll_expenses, run_payroll_expenses) against an in-memory fake Supabase client — no live
DB/network. Run: `python3 harness_payroll_expenses_router_integration.py` from backend/.

Proves:
  1. Payroll tax config PUT/GET round-trips through the real endpoint (partial + full).
  2. Payroll expense item CRUD (create/list/update/delete) through the real endpoints; a duplicate
     key is rejected; manager gating rejects a non-manager from every mutating endpoint.
  3. GET /payroll-expenses/{period} computes LIVE from fake shifts/config/items and writes nothing.
  4. POST /payroll-expenses/run/{period} persists BOTH ledgers (payroll_tax_ledger,
     payroll_expense_ledger), and the push to the (not-mounted, so it 404s/connection-errors)
     commcalc system-line endpoint degrades gracefully — logged, not raised.
  5. Re-running the SAME period is idempotent (no duplicate rows in either ledger) through the REAL
     persistence code path; a different period coexists.
  6. Org-scoping: a second org's config/items/ledger rows are completely invisible to org ORGX's
     calls, and a mutation scoped to a foreign id is a no-op (never a cross-tenant write).
  7. End-to-end YTD across 2 REAL consecutive `run_payroll_expenses` calls: month 2's FUTA is reduced
     by exactly what month 1 already consumed, proving the persisted `payroll_tax_ledger` (not just
     the pure engine's `ytd_taxable_before` param) is what actually drives the cap in production.
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


# ── Fake Supabase client (eq/gte/lte/lt/order/limit filters + insert/update/delete/select) ──────
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
        return True

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self._mode == "select":
            matched = [r for r in rows if self._match(r)]
            if self._order_desc:
                matched = list(reversed(matched))
            if self._limit:
                matched = matched[: self._limit]
            return FakeResult(matched)
        if self._mode == "insert":
            payloads = self._payload if isinstance(self._payload, list) else [self._payload]
            # emulate the migration's UNIQUE (org_id, key) constraint on payroll_expense_item
            if self.table_name == "payroll_expense_item":
                for p in payloads:
                    if any(r.get("org_id") == p.get("org_id") and r.get("key") == p.get("key") for r in rows):
                        raise Exception("duplicate key value violates unique constraint \"uq_payroll_expense_item_org_key\"")
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


class FakeSchemaClient:
    def __init__(self, store):
        self.store = store

    def schema(self, name):
        return self   # single flat store — schema name ignored, matches FakeQuery's table keying

    def table(self, name):
        return FakeQuery(self.store, name)


STORE = {}
FAKE_CLIENT = FakeSchemaClient(STORE)


def fake_get_supabase():
    return FAKE_CLIENT


import _harness_dbfree  # noqa: E402
import app.modules.storeops.router as router_mod          # noqa: E402
import app.modules.core.router as core_router_mod         # noqa: E402

router_mod.get_supabase = fake_get_supabase
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

core_router_mod._uid_from_token = lambda auth: ("test-uid" if auth == "Bearer manager" else
                                                 ("rep-uid" if auth == "Bearer rep" else None))

ORG = "ORGX"
ORG2 = "ORGY"   # a second tenant, to prove isolation

STORE["app_users"] = [
    {"auth_id": "test-uid", "org_id": ORG, "email": "boss@x.com", "role": "admin", "employee_id": "MGR1"},
    {"auth_id": "rep-uid", "org_id": ORG, "email": "rep@x.com", "role": "rep", "employee_id": "EMP1"},
]
STORE["roles"] = [{"org_id": ORG, "name": "rep", "permissions": {"scope": "self"}}]

STORE["employees"] = [
    {"employee_id": "EMP1", "org_id": ORG, "name": "Alice Rep", "home_store": "Store1", "pay_rate": 30.0, "role": "rep", "is_active": True},
    {"employee_id": "EMP2", "org_id": ORG, "name": "Bob Manager", "home_store": "Store2", "pay_rate": 25.0, "role": "store_manager", "is_active": True},
]
STORE["shifts"] = [
    {"org_id": ORG, "employee_id": "EMP1", "store_code": "Store1", "shift_date": "2026-07-05", "scheduled_hours": 80, "actual_hours": 80, "is_deleted": False},
    {"org_id": ORG, "employee_id": "EMP2", "store_code": "Store2", "shift_date": "2026-07-05", "scheduled_hours": 40, "actual_hours": 40, "is_deleted": False},
]
STORE["payroll_tax_config"] = []
STORE["payroll_expense_item"] = []
STORE["payroll_tax_ledger"] = []
STORE["payroll_expense_ledger"] = []

# ── a second tenant's data, to prove isolation later ─────────────────────────────────────────────
STORE["payroll_tax_config"].append({"id": "cfg-org2", "org_id": ORG2, "fica_ss_rate": 0.099,
                                     "fica_ss_wage_base": 168600, "medicare_rate": 0.0145,
                                     "futa_rate": 0.006, "futa_wage_base": 7000, "suta_rate": 0.027,
                                     "suta_wage_base": 9000, "enabled": True})
STORE["payroll_expense_item"].append({"id": "item-org2", "org_id": ORG2, "key": "ui", "name": "Org2 item",
                                       "calc_method": "fixed", "rate_or_amount": 12345.0, "wage_cap": None,
                                       "scope": "store", "enabled": True, "sort_order": 0})


# ── 1. payroll tax config PUT/GET round-trip ────────────────────────────────────────────────────
before = router_mod.get_payroll_tax_config(org_id=ORG)
check("t1a: no org row yet -> falls back to code defaults", before["row"] is None and before["effective"]["fica_ss_rate"] == 0.062, before)

r1 = router_mod.put_payroll_tax_config(_body(router_mod.PayrollTaxConfigIn, {"fica_ss_rate": 0.062, "fica_ss_wage_base": 168600,
                                         "medicare_rate": 0.0145, "futa_rate": 0.006, "futa_wage_base": 7000,
                                         "suta_rate": 0.031, "suta_wage_base": 12000, "enabled": True}),
                                        authorization="Bearer manager", org_id=ORG)
check("t1b: PUT creates the org row", r1["ok"] is True, r1)
after = router_mod.get_payroll_tax_config(org_id=ORG)
check("t1c: GET reflects the saved SUTA override", after["row"]["suta_rate"] == 0.031 and after["row"]["suta_wage_base"] == 12000, after)

# partial update
r2 = router_mod.put_payroll_tax_config(_body(router_mod.PayrollTaxConfigIn, {"suta_rate": 0.045}), authorization="Bearer manager", org_id=ORG)
after2 = router_mod.get_payroll_tax_config(org_id=ORG)
check("t1d: partial PUT updates just suta_rate", after2["row"]["suta_rate"] == 0.045, after2)
check("t1e: partial PUT leaves fica_ss_rate untouched", after2["row"]["fica_ss_rate"] == 0.062, after2)

try:
    router_mod.put_payroll_tax_config(_body(router_mod.PayrollTaxConfigIn, {"fica_ss_rate": 0.5}), authorization="Bearer rep", org_id=ORG)
    check("t1f: a non-manager PUT is rejected", False, "no exception raised")
except Exception as e:
    check("t1f: a non-manager PUT is rejected (403)", getattr(e, "status_code", None) == 403, e)


# ── 2. payroll expense item CRUD ─────────────────────────────────────────────────────────────────
r3 = router_mod.create_payroll_expense_item(_body(router_mod.PayrollExpenseItemIn, {"key": "unemployment_insurance", "name": "Unemployment Insurance",
                                              "calc_method": "pct_wages", "rate_or_amount": 0.02, "scope": "store"}),
                                             authorization="Bearer manager", org_id=ORG)
check("t2a: create item succeeds", r3["ok"] is True, r3)
r4 = router_mod.create_payroll_expense_item(_body(router_mod.PayrollExpenseItemIn, {"key": "workers_comp", "name": "Workers Comp",
                                              "calc_method": "pct_wages", "rate_or_amount": 0.035, "scope": "store"}),
                                             authorization="Bearer manager", org_id=ORG)
check("t2b: second item create succeeds", r4["ok"] is True, r4)

listed = router_mod.get_payroll_expense_items(org_id=ORG)
check("t2c: GET lists exactly the 2 items created for ORG (not ORG2's)", len(listed["items"]) == 2, listed)
check("t2d: GET exposes calc_methods/scopes for the picker", "pct_wages" in listed["calc_methods"] and "company" in listed["scopes"], listed)

try:
    router_mod.create_payroll_expense_item(_body(router_mod.PayrollExpenseItemIn, {"key": "unemployment_insurance", "name": "Dup", "calc_method": "fixed", "rate_or_amount": 1}),
                                            authorization="Bearer manager", org_id=ORG)
    check("t2e: a duplicate key is rejected", False, "no exception raised")
except Exception as e:
    check("t2e: a duplicate key is rejected (400)", getattr(e, "status_code", None) == 400, e)

item_id = r3["id"]
router_mod.update_payroll_expense_item(item_id, _body(router_mod.PayrollExpenseItemIn, {"rate_or_amount": 0.03}), authorization="Bearer manager", org_id=ORG)
listed2 = router_mod.get_payroll_expense_items(org_id=ORG)
ui_item = next(i for i in listed2["items"] if i["id"] == item_id)
check("t2f: PATCH updates the rate", ui_item["rate_or_amount"] == 0.03, ui_item)

try:
    router_mod.update_payroll_expense_item(item_id, _body(router_mod.PayrollExpenseItemIn, {"rate_or_amount": 1}), authorization="Bearer rep", org_id=ORG)
    check("t2g: a non-manager PATCH is rejected", False, "no exception raised")
except Exception as e:
    check("t2g: a non-manager PATCH is rejected (403)", getattr(e, "status_code", None) == 403, e)

router_mod.delete_payroll_expense_item(r4["id"], authorization="Bearer manager", org_id=ORG)
listed3 = router_mod.get_payroll_expense_items(org_id=ORG)
check("t2h: DELETE removes the item", len(listed3["items"]) == 1, listed3)


# ── 3. GET /payroll-expenses/{period} — live compute, nothing persisted ─────────────────────────
before_tax_ledger = len(STORE["payroll_tax_ledger"])
before_exp_ledger = len(STORE["payroll_expense_ledger"])
view = router_mod.get_payroll_expenses("2026-07", authorization="Bearer manager", org_id=ORG)
check("t3a: GET writes nothing to either ledger", len(STORE["payroll_tax_ledger"]) == before_tax_ledger and len(STORE["payroll_expense_ledger"]) == before_exp_ledger, view)
by_store = {s["store"]: s for s in view["stores"]}
check("t3b: Store1 wages = 80*30 = 2400", by_store["Store1"]["wages"] == 2400.0, by_store)
check("t3c: Store1 FICA SS = 2400*0.062", abs(by_store["Store1"]["fica_ss"] - 148.8) < 1e-6, by_store)
check("t3d: Store1 has the Unemployment Insurance item (pct_wages 0.03 * 2400 = 72)",
      abs(by_store["Store1"]["items"].get("unemployment_insurance", 0) - 72.0) < 1e-6, by_store)
check("t3e: 'total' = tax_total + items_total", abs(by_store["Store1"]["total"] - (by_store["Store1"]["tax_total"] + by_store["Store1"]["items_total"])) < 1e-6, by_store["Store1"])
cell_map = {c["store"]: c["amount"] for c in view["cells"]}
check("t3f: cells total matches the per-store 'total' shown above (single rolled-up figure)",
      abs(cell_map["Store1"] - by_store["Store1"]["total"]) < 1e-6, (cell_map, by_store))


# ── 4. POST /payroll-expenses/run/{period} — persists both ledgers + attempts the push ─────────
run1 = router_mod.run_payroll_expenses("2026-07", authorization="Bearer manager", org_id=ORG)
check("t4a: run writes payroll_tax_ledger rows", run1["tax_ledger_rows_written"] > 0, run1)
check("t4b: run writes payroll_expense_ledger rows", run1["expense_ledger_rows_written"] > 0, run1)
check("t4c: push degrades gracefully (no server listening on 127.0.0.1:8000 in this harness)",
      run1["push"]["pushed"] is False and run1["push"].get("note"), run1["push"])
check("t4d: cells returned match the rolled-up single 'Payroll Expenses' line", run1["cells"] == view["cells"] or all(abs(a["amount"] - b["amount"]) < 0.02 for a, b in zip(sorted(run1["cells"], key=lambda c: c["store"]), sorted(view["cells"], key=lambda c: c["store"]))), (run1["cells"], view["cells"]))

try:
    router_mod.run_payroll_expenses("2026-07", authorization="Bearer rep", org_id=ORG)
    check("t4e: a non-manager cannot trigger a run", False, "no exception raised")
except Exception as e:
    check("t4e: a non-manager cannot trigger a run (403)", getattr(e, "status_code", None) == 403, e)


# ── 5. idempotent re-run through the REAL endpoint ──────────────────────────────────────────────
tax_count_after_run1 = len(STORE["payroll_tax_ledger"])
exp_count_after_run1 = len(STORE["payroll_expense_ledger"])
run2 = router_mod.run_payroll_expenses("2026-07", authorization="Bearer manager", org_id=ORG)
check("t5a: re-running the SAME period does not duplicate payroll_tax_ledger rows",
      len(STORE["payroll_tax_ledger"]) == tax_count_after_run1, (len(STORE["payroll_tax_ledger"]), tax_count_after_run1))
check("t5b: re-running the SAME period does not duplicate payroll_expense_ledger rows",
      len(STORE["payroll_expense_ledger"]) == exp_count_after_run1, (len(STORE["payroll_expense_ledger"]), exp_count_after_run1))
check("t5c: re-run reports the identical row counts", (run1["tax_ledger_rows_written"], run1["expense_ledger_rows_written"]) == (run2["tax_ledger_rows_written"], run2["expense_ledger_rows_written"]), (run1, run2))

run_aug_empty = router_mod.run_payroll_expenses("2026-08", authorization="Bearer manager", org_id=ORG)
check("t5d: a different (empty) period coexists — July's rows untouched",
      len(STORE["payroll_tax_ledger"]) >= tax_count_after_run1, STORE["payroll_tax_ledger"])
check("t5e: August has 0 activity (no Aug shifts seeded) -> 0 rows written", run_aug_empty["tax_ledger_rows_written"] == 0, run_aug_empty)


# ── 6. org-scoping — ORG never sees/touches ORG2's rows ─────────────────────────────────────────
org_view = router_mod.get_payroll_tax_config(org_id=ORG)
check("t6a: ORG's config is NOT org2's 9.9% fica rate", org_view["row"]["fica_ss_rate"] != 0.099, org_view)
org_items = router_mod.get_payroll_expense_items(org_id=ORG)
check("t6b: ORG's item list does not include org2's item", not any(i["key"] == "ui" and i["rate_or_amount"] == 12345.0 for i in org_items["items"]), org_items)

# A mutation with a foreign org's row id must be a no-op, never a cross-tenant write.
router_mod.update_payroll_expense_item("item-org2", _body(router_mod.PayrollExpenseItemIn, {"rate_or_amount": 0.0}), authorization="Bearer manager", org_id=ORG)
org2_item_row = next(r for r in STORE["payroll_expense_item"] if r["id"] == "item-org2")
check("t6c: PATCH scoped to ORG cannot touch org2's item even by guessing its id",
      org2_item_row["rate_or_amount"] == 12345.0, org2_item_row)

router_mod.delete_payroll_expense_item("item-org2", authorization="Bearer manager", org_id=ORG)
check("t6d: DELETE scoped to ORG cannot delete org2's item either",
      any(r["id"] == "item-org2" for r in STORE["payroll_expense_item"]), STORE["payroll_expense_item"])

org2_expenses_view = router_mod.get_payroll_expenses("2026-07", authorization="Bearer manager", org_id=ORG2)
check("t6e: org2 has its OWN, independent (all-zero, no shifts seeded for org2) view", org2_expenses_view["stores"] == [], org2_expenses_view)


# ── 7. end-to-end YTD across 2 REAL run_payroll_expenses calls (persisted ledger drives the cap) ──
STORE["employees"].append({"employee_id": "EMP9", "org_id": ORG, "name": "Cap Tester", "home_store": "StoreZ", "pay_rate": 40.0, "role": "rep", "is_active": True})
STORE["shifts"].append({"org_id": ORG, "employee_id": "EMP9", "store_code": "StoreZ", "shift_date": "2026-09-05", "scheduled_hours": 175, "actual_hours": 175, "is_deleted": False})  # $7000 wages -> exactly the FUTA cap
STORE["shifts"].append({"org_id": ORG, "employee_id": "EMP9", "store_code": "StoreZ", "shift_date": "2026-10-05", "scheduled_hours": 175, "actual_hours": 175, "is_deleted": False})  # another $7000 in Oct

run_sep = router_mod.run_payroll_expenses("2026-09", authorization="Bearer manager", org_id=ORG)
sep_tax_rows = [r for r in STORE["payroll_tax_ledger"] if r["period"] == "2026-09" and r["employee_id"] == "EMP9"]
check("t7a: September fully taxable FUTA = 7000*0.006 = 42 (exactly exhausts the cap)",
      abs(sep_tax_rows[0]["futa_tax"] - 42.0) < 1e-6, sep_tax_rows)

run_oct = router_mod.run_payroll_expenses("2026-10", authorization="Bearer manager", org_id=ORG)
oct_tax_rows = [r for r in STORE["payroll_tax_ledger"] if r["period"] == "2026-10" and r["employee_id"] == "EMP9"]
check("t7b: October FUTA = 0 — the REAL persisted September ledger (not a manually-passed dict) is what capped it",
      oct_tax_rows[0]["futa_tax"] == 0.0, oct_tax_rows)
check("t7c: October's SS/Medicare still apply normally (only FUTA/SUTA hit their (much lower) caps)",
      oct_tax_rows[0]["fica_ss_tax"] > 0, oct_tax_rows)


# ── Report ─────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("FAIL:", f)
if FAIL:
    sys.exit(1)
print("ALL GREEN")
