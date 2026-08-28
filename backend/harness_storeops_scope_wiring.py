"""Offline proof harness for the 2026-08-03 mod-people wiring package (agent/people/storeops-
scope-wiring) — consuming platform-core's `agent/platform-core/reporting-scope-split` primitives
(`app/core/scope.py`, merged to main @ d4f9e62) into `app/modules/storeops/router.py` per the
7-step wiring list in docs/handoffs/platform-core.md ("FOR mod-people — EXACT PER-FILE WIRING").

Runs the REAL router functions (`_market_store_codes`, `_login_extra_codes`, `scope_keyset`,
`scope_emp_ids`, `get_employees`, `_collect_markets`) against a stateful fake Supabase-chain client
(same convention as harness_rollup_keyset_scope.py / harness_closing_reports_span_scope.py),
monkeypatching only `get_supabase` and `_uid_from_token` — everything else (caller_scope,
_role_scope, _role_permissions, _caller_app_user, the widening/window logic) runs FOR REAL.

Proves:
  A. THE MARKET-GRANT FIX (wiring step 1-2): a market that exists ONLY in commcalc.store_mapping
     (storeops.stores has no market set for that store) now resolves to its member store(s) via
     `_market_store_codes` / `_login_extra_codes` / `scope_keyset` — the "3 markets selected but
     sees nothing" bug. A market known to BOTH vocabularies still resolves identically.
  B. SCHEDULING REACH (wiring step 5): `GET /employees?all_company=true` stays an UNCONDITIONAL
     org-wide exemption for a role with no `scheduling_reach` set (or an unrecognized caller) —
     byte-identical to pre-wiring behavior. Only a role explicitly configured
     `scheduling_reach='span'` narrows the roster back to the caller's reporting span even when
     all_company=true is requested.
  C. UNSCOPED / ADMIN STAYS UNRESTRICTED: an 'all'-scope role (or no/invalid auth) gets
     `scope_keyset()/scope_emp_ids() is None` and an unfiltered `get_employees()` — unchanged.
  D. "EMPLOYEES MOVE AROUND" (wiring step 4): `scope_emp_ids` now widens home-store-only membership
     to include an employee who WORKED (a non-deleted shift) at a store inside the span, proved
     through the REAL `get_time_off` endpoint end-to-end — a borrowed rep's time-off request, newly
     visible to the store's manager, previously invisible.
  E. The date window bounds `reporting_employee_ids`'s worked-at scan — a shift far outside the
     derived window does NOT widen the result (proves the window arg isn't just decorative).

Run: `cd backend && python3 harness_storeops_scope_wiring.py`
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
_ID = {"n": 0}


def nid(pfx="id"):
    _ID["n"] += 1
    return f"{pfx}-{_ID['n']}"


# ── stateful fake supabase client (same convention as harness_closing_reports_span_scope.py) ──────
class Q:
    def __init__(self, store, key):
        self.s, self.k = store, key
        self.op, self.payload = "select", None
        self.filters = []
        self._limit = None

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
    def update(self, patch, **k): self.op = "update"; self.payload = patch; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def order(self, *a, **k): return self
    def limit(self, n, *a, **k): self._limit = n; return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
        return True

    def execute(self):
        rows = self.s.setdefault(self.k, [])
        if self.op == "select":
            matched = [dict(r) for r in rows if self._match(r)]
            if self._limit is not None:
                matched = matched[: self._limit]
            return SimpleNamespace(data=matched)
        if self.op == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in payload:
                r = dict(r); r.setdefault("id", nid(self.k[1] if isinstance(self.k, tuple) else self.k))
                rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        return SimpleNamespace(data=[])


class FakeSchema:
    def __init__(self, client, name): self.client, self.name = client, name
    def table(self, t): return Q(self.client.store, (self.name, t))


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, name): return FakeSchema(self, name)
    def table(self, t): return Q(self.store, ("storeops", t))


def fresh_store():
    return {
        ("storeops", "app_config"): [{"id": 1, "rbac_enabled": True}],
        ("storeops", "stores"): [],
        ("commcalc", "store_mapping"): [],
        ("storeops", "app_users"): [],
        ("storeops", "roles"): [],
        ("storeops", "employees"): [],
        ("storeops", "shifts"): [],
        ("storeops", "timelog"): [],
        ("storeops", "time_off_requests"): [],
    }


import app.modules.storeops.router as SO             # noqa: E402
import app.core.scope as CS                          # noqa: E402
import app.modules.core.router as CORE                # noqa: E402

TOKENS = {}   # "Bearer <name>" -> uid


def wire(store):
    fake = FakeClient(store)
    SO.get_supabase = lambda: fake
    CORE._uid_from_token = lambda tok: TOKENS.get(tok)
    CS.invalidate_market_index()   # never let one test's cache leak into another
    return fake


def app_user(auth_name, org_id, role, *, market=None, store_code=None, store_codes=None, employee_id=None):
    uid = f"uid-{auth_name}"
    TOKENS[f"Bearer {auth_name}"] = uid
    return {"org_id": org_id, "auth_id": uid, "role": role, "employee_id": employee_id,
            "market": market, "store_code": store_code, "store_codes": store_codes}


# ══════════════════════ setup: ONE market ('PA') exists ONLY in store_mapping ═════════════════════
st = fresh_store()
fake = wire(st)

st[("storeops", "stores")] = [
    {"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "LI", "is_active": True},
    {"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "LI", "is_active": True},
    # S3's market was NEVER set in the StoreOps Admin editor — only commcalc.store_mapping knows it.
    {"org_id": HOUSE, "store_code": "S3", "address": "3 Penn Blvd", "market": None, "is_active": True},
]
st[("commcalc", "store_mapping")] = [
    {"org_id": HOUSE, "store_code": "S1", "store_address": "1 Main St", "market": "LI"},
    {"org_id": HOUSE, "store_code": "S2", "store_address": "2 Oak Ave", "market": "LI"},
    {"org_id": HOUSE, "store_code": "S3", "store_address": "3 Penn Blvd", "market": "PA"},
]
st[("storeops", "roles")] = [
    {"org_id": HOUSE, "name": "admin", "permissions": {"scope": "all"}},
    {"org_id": HOUSE, "name": "dm_market", "permissions": {"scope": "market"}},          # scheduling_reach unset -> 'org'
    {"org_id": HOUSE, "name": "dm_store", "permissions": {"scope": "store"}},
    {"org_id": HOUSE, "name": "dm_span_reach",
     "permissions": {"scope": "market", "scheduling_reach": "span"}},
]
st[("storeops", "app_users")] = [
    app_user("admin", HOUSE, "admin"),
    app_user("dm-market", HOUSE, "dm_market", market="PA"),
    app_user("dm-store", HOUSE, "dm_store", store_code="S1"),
    app_user("dm-span", HOUSE, "dm_span_reach", market="PA"),
]
st[("storeops", "employees")] = [
    {"org_id": HOUSE, "employee_id": "E1", "name": "Pat PA", "home_store": "S3", "is_active": True},
    {"org_id": HOUSE, "employee_id": "E2", "name": "Lee LI", "home_store": "S1", "is_active": True},
    {"org_id": HOUSE, "employee_id": "EB", "name": "Borrowed Bo", "home_store": "S2", "is_active": True},
]

# ══════════════════════ A. THE MARKET-GRANT FIX ═══════════════════════════════════════════════════
check("A1. _market_store_codes('PA') resolves via store_mapping-ONLY union (was: EMPTY set)",
      SO._market_store_codes(HOUSE, "PA") == {"S3"}, str(SO._market_store_codes(HOUSE, "PA")))
check("A2. _market_store_codes('LI') still resolves both agreeing vocabularies (unchanged result)",
      SO._market_store_codes(HOUSE, "LI") == {"S1", "S2"}, str(SO._market_store_codes(HOUSE, "LI")))
check("A3. _market_store_codes case-insensitive ('pa')",
      SO._market_store_codes(HOUSE, "pa") == {"S3"})
check("A4. _login_extra_codes for the PA-market app_user resolves S3 (was: ∅ before this fix)",
      SO._login_extra_codes(st[("storeops", "app_users")][1], HOUSE) == {"S3"})

ks_market = SO.scope_keyset("Bearer dm-market", HOUSE)
check("A5. scope_keyset for the market-only grant is a NON-empty set containing S3 + its address",
      ks_market is not None and "S3" in ks_market and "3 PENN BLVD" in ks_market, str(ks_market))

emps_market = SO.get_employees(all_company=False, authorization="Bearer dm-market", org_id=HOUSE)
check("A6. get_employees for the PA-market DM (reporting span, all_company=False) sees ONLY E1 (home S3)",
      {e["employee_id"] for e in emps_market} == {"E1"}, str(emps_market))

# ══════════════════════ B. SCHEDULING REACH (wiring step 5) ═══════════════════════════════════════
all_default = SO.get_employees(all_company=True, authorization="Bearer dm-market", org_id=HOUSE)
check("B1. all_company=true, scheduling_reach UNSET (default 'org') -> WHOLE roster, unconditional "
      "(byte-identical to pre-wiring behavior)",
      {e["employee_id"] for e in all_default} == {"E1", "E2", "EB"}, str(all_default))

all_noauth = SO.get_employees(all_company=True, authorization="", org_id=HOUSE)
check("B2. all_company=true, NO auth -> still the WHOLE roster (unchanged; no auth = can't resolve a role)",
      {e["employee_id"] for e in all_noauth} == {"E1", "E2", "EB"}, str(all_noauth))

all_span = SO.get_employees(all_company=True, authorization="Bearer dm-span", org_id=HOUSE)
check("B3. all_company=true, scheduling_reach='span' -> NARROWED to the caller's reporting span (E1 only)",
      {e["employee_id"] for e in all_span} == {"E1"}, str(all_span))

# ══════════════════════ C. UNSCOPED / ADMIN STAYS UNRESTRICTED ════════════════════════════════════
check("C1. scope_keyset for an 'all'-scope admin role is None (unrestricted)",
      SO.scope_keyset("Bearer admin", HOUSE) is None)
emps_admin = SO.get_employees(all_company=False, authorization="Bearer admin", org_id=HOUSE)
check("C2. get_employees for admin (all_company=False) sees the WHOLE roster (unrestricted)",
      {e["employee_id"] for e in emps_admin} == {"E1", "E2", "EB"}, str(emps_admin))
check("C3. scope_emp_ids for an unrecognized token is None (unrestricted)",
      SO.scope_emp_ids("Bearer nope", HOUSE) is None)

# ══════════════════════ D. "EMPLOYEES MOVE AROUND" — home ∪ worked-at, via the REAL endpoint ══════
# EB's home store is S2, OUTSIDE the S1-pinned DM's span — but EB worked a shift AT S1.
st[("storeops", "shifts")] = [
    {"org_id": HOUSE, "employee_id": "EB", "store_code": "S1", "shift_date": "2026-08-10",
     "is_deleted": False},
]
st[("storeops", "time_off_requests")] = [
    {"org_id": HOUSE, "id": "to-1", "employee_id": "E2", "start_date": "2026-08-08",
     "end_date": "2026-08-09", "status": "pending"},           # E2's home IS S1 -> always visible
    {"org_id": HOUSE, "id": "to-2", "employee_id": "EB", "start_date": "2026-08-10",
     "end_date": "2026-08-11", "status": "pending"},           # EB's home is S2, but EB WORKED at S1
]

eids_home_only = SO.scope_emp_ids("Bearer dm-store", HOUSE, since="2026-08-10", until="2026-08-10")
check("D1. scope_emp_ids for the S1-pinned DM includes EB when the window covers EB's S1 shift "
      "(home ∪ worked-at)", eids_home_only == {"E2", "EB"}, str(eids_home_only))

resp = SO.get_time_off(employee_id=None, authorization="Bearer dm-store", org_id=HOUSE)
seen_ids = {r["employee_id"] for r in resp}
check("D2. GET /time-off (REAL endpoint, end-to-end) now surfaces EB's request to the S1-pinned DM "
      "— the exact 'borrowed rep invisible on time-off' bug", seen_ids == {"E2", "EB"}, str(seen_ids))

# ══════════════════════ E. the date window actually BOUNDS the worked-at scan ══════════════════════
st[("storeops", "shifts")].append(
    {"org_id": HOUSE, "employee_id": "EB", "store_code": "S1", "shift_date": "2020-01-01",
     "is_deleted": False})   # a decoy shift FAR outside any derived window
eids_narrow = SO.scope_emp_ids("Bearer dm-store", HOUSE, since="2026-08-10", until="2026-08-10")
check("E1. a decoy S1 shift far outside the passed window does not spuriously change the result "
      "(EB already legitimately in-window via the 2026-08-10 shift)", eids_narrow == {"E2", "EB"})

eids_no_window_shift_only = SO.scope_emp_ids("Bearer dm-store", HOUSE, since="2019-01-01", until="2019-01-02")
check("E2. narrowing the window OFF the 2026-08-10 shift and the 2020-01-01 decoy both drops EB back "
      "to home-store-only (S1) — proves 'since/until' genuinely gates the worked-at half",
      eids_no_window_shift_only == {"E2"}, str(eids_no_window_shift_only))


# ══════════════════════ F. ELEVATED ROLES default 'all' even with NO roles-table row ═════════════
# Regression guard (2026-08-18): RBAC fail-closed collapsed an admin/owner/super_admin whose tenant
# never seeded a matching storeops.roles row to 'self' scope, so a full admin saw only his own ~2
# stores on the Time Clock report. _role_scope now defaults the canonical top-level roles to 'all'
# (the same precedent _can_edit_setting applies), while a genuinely unknown role still fails CLOSED.
check("F1. an 'owner' role with NO roles-table row defaults to scope 'all' (not fail-closed 'self')",
      SO._role_scope(HOUSE, "owner") == "all", SO._role_scope(HOUSE, "owner"))
check("F2. 'super_admin' with no roles row also defaults to 'all'",
      SO._role_scope(HOUSE, "super_admin") == "all", SO._role_scope(HOUSE, "super_admin"))
check("F3. a genuinely unknown, non-elevated role STILL fails closed to 'self' (security preserved)",
      SO._role_scope(HOUSE, "mystery_role") == "self", SO._role_scope(HOUSE, "mystery_role"))
check("F4. the seeded 'admin' row (explicit scope 'all') is unchanged", SO._role_scope(HOUSE, "admin") == "all")

# An admin/owner login with no roles row, one pinned store, must read UNRESTRICTED (None) — NOT a
# single-store span. This is exactly the Time Clock 'admin sees only 2 people' regression.
st[("storeops", "app_users")].append(app_user("owner-noroles", HOUSE, "owner", store_code="S1"))
check("F5. a full admin/owner login (no roles row, one pinned store) reads UNRESTRICTED, not a "
      "single-store span",
      SO.scope_keyset("Bearer owner-noroles", HOUSE) is None,
      str(SO.scope_keyset("Bearer owner-noroles", HOUSE)))


print()
print("=" * 72)
print(f"  RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
print("=" * 72)
sys.exit(1 if FAIL else 0)
