"""Offline proof (no live DB/network) for the two 2026-07-30 auto-fix-pipeline failure_log leads
dispatched to mod-people (both crash frames land in backend/app/modules/storeops/router.py):

  LEAD 1 — `DELETE /api/v1/storeops/shifts/{id}` | postgrest APIError (row 9b342c54, real repro
  shift_id=4535). Root cause: storeops.shifts carries a BEFORE DELETE trigger
  (storeops.soft_delete_shift(), migration 003) whose body runs
  `UPDATE storeops.shifts SET is_deleted = true, deleted_at = NOW() WHERE id = OLD.id` against the
  SAME row the outer DELETE is currently processing — the documented Postgres anti-pattern that
  raises "tuple to be updated was already modified by an operation triggered by the current
  command" on EVERY real DELETE (not intermittent), surfaced by postgrest-py as an unhandled
  APIError -> unhandled 500. Fix: `delete_shift` now soft-deletes via a plain UPDATE (never fires a
  DELETE trigger at all) and wraps the write in try/except so ANY other DB error is a clean 400.
  Section A proves this with a fake client whose `.delete()` raises exactly that Postgres error
  (simulating the live trigger bug) while `.update()` succeeds — the fixed handler must never call
  `.delete()` at all and must complete 200. Section A also re-verifies write-side org-scoping
  (org_id stays a query param, the write is `.eq(id).eq(org_id)`, a foreign shift_id is a no-op).

  LEAD 2 — `GET /api/v1/closing/summary` | KeyError (row e2ddbda6). closing_summary
  (backend/app/modules/closing/router.py, mod-retail-ops-owned) reaches exactly ONE piece of
  storeops/router.py code: `scope_keyset`/`in_keyset` (+ their transitive callees `caller_scope`,
  `_role_scope`, `_span_codes`, `_login_extra_codes`, `_rbac_enabled`) via
  `from app.modules.storeops.router import scope_keyset, in_keyset` at the end of the function,
  called UNGUARDED (no try/except in closing_summary). Section B drives every one of those
  storeops-owned functions through rows missing every optional key (no 'role', no 'employee_id', no
  'market', no 'store_code', no 'store_codes', no 'permissions', an RPC returning rows with no
  'store_code' key at all, stores metadata rows missing 'address') and proves none of them raises —
  confirming there is no live KeyError in the storeops code this endpoint reaches. This has been
  true since these functions were introduced (commit 85654cf, 2026-06-27) — no fix applied here;
  see docs/handoffs/people.md for the reclassify verdict + the FOR RETAIL-OPS note about a separate,
  unconfirmed bracket-access pattern found in closing/router.py's OWN code during this
  investigation (out of our ownership).

Run: `python3 harness_people_failure_leads.py` from backend/.
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ════════════════════════════════════════════════════════════════════════════════════════════════
# Shared fake-client scaffolding (same pattern as harness_storeops_market_dropdown.py)
# ════════════════════════════════════════════════════════════════════════════════════════════════
class Result:
    def __init__(self, data):
        self.data = data


class APIError(Exception):
    """Stand-in for postgrest.exceptions.APIError — same shape (a .message-bearing exception),
    without requiring the real postgrest package's exact constructor at import time."""
    def __init__(self, message, code=None):
        self.message = message
        self.code = code
        super().__init__(message)


class FakeQuery:
    def __init__(self, store, key, raise_on=None):
        self.store, self.key, self.filters = store, key, []
        self._mode, self._payload, self._in = None, None, None
        self._raise_on = raise_on or {}   # {"delete": APIError(...)} etc — simulates a DB-side error

    def select(self, *_a, **_k):
        self._mode = self._mode or "select"; return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def in_(self, k, vals):
        self._in = (k, set(vals)); return self

    def gte(self, *_a, **_k):
        return self

    def lte(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def update(self, payload):
        self._mode = "update"; self._payload = payload; return self

    def insert(self, payload):
        self._mode = "insert"; self._payload = payload; return self

    def delete(self):
        self._mode = "delete"; return self

    def _matches(self, row):
        if not all(str(row.get(k)) == str(v) for _, k, v in self.filters):
            return False
        if self._in and str(row.get(self._in[0])) not in {str(v) for v in self._in[1]}:
            return False
        return True

    def execute(self):
        if self._mode in self._raise_on:
            raise self._raise_on[self._mode]
        rows = self.store.setdefault(self.key, [])
        if self._mode == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            new_rows = []
            for p in payload:
                row = dict(p)
                row.setdefault("id", len(rows) + len(new_rows) + 1)
                new_rows.append(row)
            rows.extend(new_rows)
            return Result(new_rows)
        matched = [r for r in rows if self._matches(r)]
        if self._mode == "update":
            for r in matched:
                r.update(self._payload)
            return Result(matched)
        if self._mode == "delete":
            remaining = [r for r in rows if r not in matched]
            rows[:] = remaining
            return Result(matched)
        return Result(matched)


class FakeSchema:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def table(self, t):
        return FakeQuery(self.client.store, (self.name, t), raise_on=self.client.raise_on.get(t))

    def rpc(self, fn, params=None):
        return self.client.rpc(fn, params)


class FakeRPC:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return Result(self._data)


class FakeClient:
    def __init__(self):
        self.store = {}
        self.raise_on = {}          # {"table_name": {"delete": APIError(...)}}
        self.rpc_responses = {}     # {"fn_name": [rows...]}

    def schema(self, name):
        return FakeSchema(self, name)

    def table(self, t):
        return FakeQuery(self.store, ("storeops", t), raise_on=self.raise_on.get(t))

    def rpc(self, fn, params=None):
        return FakeRPC(self.rpc_responses.get(fn, []))

    def seed(self, schema, table, rows):
        self.store.setdefault((schema, table), []).extend(dict(r) for r in rows)


fake = FakeClient()

import app.modules.storeops.router as R  # noqa: E402

R.get_supabase = lambda: fake
R.sb = lambda: fake.schema("storeops")
# _who_for_log / caller_scope resolve identity via app.modules.core.router._uid_from_token — stub
# it so these tests don't need a real JWT, matching harness_storeops_market_dropdown's approach of
# patching only the seam under test.
import app.modules.core.router as CoreR  # noqa: E402
_CUR_UID = {"v": None}
CoreR._uid_from_token = lambda auth: _CUR_UID["v"]

ORG = "org-leads-1"
ORG2 = "org-leads-2"


def reset():
    fake.store.clear()
    fake.raise_on.clear()
    fake.rpc_responses.clear()
    _CUR_UID["v"] = None


print("── LEAD 1: DELETE /storeops/shifts/{id} — APIError from the soft-delete trigger ──")

# ══ A1: reproduce the LIVE bug shape — a raw DELETE on storeops.shifts raises the exact Postgres
#        trigger error. Prove the FIXED handler never issues that DELETE (so it never hits this). ═══
reset()
fake.seed("storeops", "shifts", [
    {"id": 4535, "org_id": ORG, "employee_id": "E1", "employee_name": "Pat Rep", "store_code": "T-100",
     "shift_date": "2026-07-29", "scheduled_hours": 8, "start_time": "09:00", "end_time": "17:00",
     "is_deleted": False},
])
fake.raise_on["shifts"] = {
    "delete": APIError("tuple to be updated was already modified by an operation triggered by the "
                        "current command", code="40001"),
}
out = R.delete_shift(4535, authorization="Bearer x", org_id=ORG)
check("A1a delete_shift(4535) returns 200 {'deleted': 4535} — no unhandled 500 even though the "
      "table's DELETE path is poisoned exactly like the live trigger bug",
      out == {"deleted": 4535}, out)
row = next(r for r in fake.store[("storeops", "shifts")] if r["id"] == 4535)
check("A1b the row is SOFT-deleted (is_deleted flips True) rather than physically removed — matches "
      "every reader's `.eq('is_deleted', False)` filter", row.get("is_deleted") is True, row)
check("A1c deleted_at got stamped", bool(row.get("deleted_at")), row)
check("A1d the row still PHYSICALLY EXISTS in storeops.shifts (proves no real DELETE was ever "
      "issued — .delete() would have raised, so if we got here it wasn't called)",
      any(r["id"] == 4535 for r in fake.store[("storeops", "shifts")]), fake.store[("storeops", "shifts")])

# ══ A2: org-scoping — a foreign org's shift_id is a silent no-op, not a cross-tenant delete ═══════
reset()
fake.seed("storeops", "shifts", [
    {"id": 9001, "org_id": ORG2, "employee_id": "E9", "employee_name": "Other Tenant Rep",
     "store_code": "Z-1", "shift_date": "2026-07-29", "is_deleted": False},
])
out2 = R.delete_shift(9001, authorization="Bearer x", org_id=ORG)   # caller is ORG, row is ORG2
check("A2a cross-tenant delete_shift call still returns 200 (matches existing no-op contract)",
      out2 == {"deleted": 9001}, out2)
other_row = next(r for r in fake.store[("storeops", "shifts")] if r["id"] == 9001)
check("A2b the OTHER org's row is UNTOUCHED — is_deleted still False (org_id scoping on the write "
      "held: .eq(id).eq(org_id) matched zero rows)", other_row.get("is_deleted") is False, other_row)

# ══ A3: any OTHER (non-trigger) DB error on the write is a clean 400, never an unhandled 500 ═════
reset()
fake.seed("storeops", "shifts", [
    {"id": 77, "org_id": ORG, "employee_id": "E1", "employee_name": "Pat", "store_code": "T-1",
     "shift_date": "2026-07-29", "is_deleted": False},
])
fake.raise_on["shifts"] = {"update": APIError("connection reset", code="08006")}
try:
    R.delete_shift(77, authorization="Bearer x", org_id=ORG)
    a3_ok, a3_detail = False, "did not raise"
except Exception as e:
    from fastapi import HTTPException
    a3_ok = isinstance(e, HTTPException) and e.status_code == 400
    a3_detail = repr(e)
check("A3 a genuinely different write failure surfaces as a clean HTTPException(400), never an "
      "unhandled 500", a3_ok, a3_detail)


print("\n── LEAD 2: GET /closing/summary — the ONLY storeops code it reaches (scope_keyset/"
      "in_keyset + transitive callees) never raises KeyError ──")

# ══ B1: _rbac_enabled off (today's default) -> caller_scope/scope_keyset are a strict no-op ══════
reset()
fake.seed("storeops", "app_config", [{"id": 1, "rbac_enabled": False}])
res = R.scope_keyset("Bearer x", org_id=ORG)
check("B1 RBAC disabled (default) -> scope_keyset returns None (unrestricted), zero further "
      "lookups needed", res is None, res)

# ══ B2: RBAC on, app_users row missing EVERY optional key -> no KeyError ══════════════════════════
reset()
_CUR_UID["v"] = "uid-1"
fake.seed("storeops", "app_config", [{"id": 1, "rbac_enabled": True}])
fake.seed("storeops", "app_users", [{"org_id": ORG, "auth_id": "uid-1"}])  # no role/employee_id/market/store_code/store_codes
try:
    r2 = R.caller_scope("Bearer x", org_id=ORG)
    b2_ok, b2_detail = True, r2
except KeyError as e:
    b2_ok, b2_detail = False, f"KeyError: {e}"
check("B2 app_users row with NO role/employee_id/market/store_code/store_codes keys at all -> "
      "caller_scope does not KeyError (missing role -> _role_scope('') -> 'all' -> unrestricted)",
      b2_ok, b2_detail)

# ══ B3: a real manager role, roles row present but missing 'permissions' key ══════════════════════
reset()
_CUR_UID["v"] = "uid-2"
fake.seed("storeops", "app_config", [{"id": 1, "rbac_enabled": True}])
fake.seed("storeops", "app_users", [
    {"org_id": ORG, "auth_id": "uid-2", "role": "market_manager", "employee_id": "M1"},
])
# Scope explicitly 'market' (not 'all') so the RPC-driven span path below is actually exercised —
# a roles row with NO permissions/scope key at all legitimately defaults to unrestricted ('all',
# see _role_scope), which is a SEPARATE case (already covered: B1/B2 exercise the unrestricted path).
fake.seed("storeops", "roles", [{"org_id": ORG, "name": "market_manager", "permissions": {"scope": "market"}}])
fake.rpc_responses["org_span_for_manager"] = [{"store_code": "T-100"}, {}]  # one row missing store_code entirely
try:
    r3 = R.caller_scope("Bearer x", org_id=ORG)
    b3_ok, b3_detail = True, r3
except KeyError as e:
    b3_ok, b3_detail = False, f"KeyError: {e}"
check("B3 an org_span_for_manager RPC row missing 'store_code' entirely (alongside a normal row) "
      "-> caller_scope does not KeyError", b3_ok, b3_detail)
check("B3b resolved span is a set containing the one real store_code, blank RPC row silently "
      "skipped (not a crash, not a bogus empty-string store)", r3 == {"T-100"}, r3)

# ══ B4: market/store-manager fallback (_login_extra_codes) with store_codes as None / missing ═════
reset()
_CUR_UID["v"] = "uid-3"
fake.seed("storeops", "app_config", [{"id": 1, "rbac_enabled": True}])
fake.seed("storeops", "app_users", [
    {"org_id": ORG, "auth_id": "uid-3", "role": "dm", "employee_id": "", "market": "LI",
     "store_code": None, "store_codes": None},
])
fake.seed("storeops", "roles", [{"org_id": ORG, "name": "dm", "permissions": {"scope": "market"}}])
fake.seed("storeops", "stores", [
    {"org_id": ORG, "store_code": "T-1", "market": "LI"},
    {"org_id": ORG, "store_code": "T-2", "market": "LI"},
])
try:
    r4 = R.caller_scope("Bearer x", org_id=ORG)
    b4_ok, b4_detail = True, r4
except KeyError as e:
    b4_ok, b4_detail = False, f"KeyError: {e}"
check("B4 market-scoped manager with store_code=None and store_codes=None (both explicitly null, "
      "not just absent) -> _login_extra_codes does not KeyError", b4_ok, b4_detail)
check("B4b resolves via market fallback alone -> {'T-1','T-2'}", r4 == {"T-1", "T-2"}, r4)

# ══ B5: scope_keyset's own stores-metadata lookup with an address-less row -> in_keyset still works ═
reset()
_CUR_UID["v"] = "uid-4"
fake.seed("storeops", "app_config", [{"id": 1, "rbac_enabled": True}])
fake.seed("storeops", "app_users", [
    {"org_id": ORG, "auth_id": "uid-4", "role": "dm", "employee_id": "M2", "market": "", "store_code": "T-1"},
])
fake.seed("storeops", "roles", [{"org_id": ORG, "name": "dm", "permissions": {"scope": "market"}}])
fake.rpc_responses["org_span_for_manager"] = []
fake.seed("storeops", "stores", [{"org_id": ORG, "store_code": "T-1"}])  # no 'address' key at all
try:
    ks = R.scope_keyset("Bearer x", org_id=ORG)
    b5_ok, b5_detail = True, ks
except KeyError as e:
    b5_ok, b5_detail = False, f"KeyError: {e}"
check("B5 stores metadata row with NO 'address' key -> scope_keyset does not KeyError", b5_ok, b5_detail)
check("B5b in_keyset() correctly matches the store_code, no crash from the missing-address row",
      R.in_keyset(ks, "T-1", None) is True, ks)
check("B5c in_keyset() correctly rejects an out-of-span code",
      R.in_keyset(ks, "T-99", None) is False, ks)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
