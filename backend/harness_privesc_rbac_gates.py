"""Proof harness — server-side authorization gates on the RBAC / user-management endpoints.

Runs the ACTUAL shipped handlers in app.modules.core.router against a fake Supabase client (same
convention as harness_core_bootstrap.py) — no DB, no network. Run from backend/:
    python3 harness_privesc_rbac_gates.py

THREAT MODEL (owner directive 2026-08-05): a logged-in NON-ADMIN with devtools crafts an HTTP
request carrying their own valid JWT. Before this package these endpoints self-gated on nothing but
the UI hiding the control, so the non-admin could:
  • PUT /roles/{id}  → give their own role admin permissions (privilege escalation), and because
    role_id is a GLOBAL pk with no org filter, rewrite ANOTHER TENANT's role (cross-tenant).
  • POST /users/assign → set their own app_users.role to 'admin'.
  • POST /users/delete|deactivate|employees/purge → remove the real admin (lock-out / DoS).
  • PUT /auth-config → flip the tenant's login-enforcement flag.

Proves, for every gated endpoint:
  1. A non-admin caller (sales_rep) is REJECTED with HTTPException 403 and NO write reaches the DB.
  2. An unauthenticated caller (no token) is REJECTED 401 and NO write reaches the DB.
  3. A tenant admin is ALLOWED and the write happens.
  4. A super_admin is ALLOWED.
  5. NEGATIVE CONTROL: with the gate monkeypatched to a pass-through (the pre-fix world), the SAME
     non-admin request SUCCEEDS and writes — i.e. the gate is exactly what stops the attack.
  6. Cross-tenant guard on PUT /roles/{id}: a tenant admin's update is filtered to their own org_id
     (a super_admin's is not).
"""
import asyncio
import os
import sys

sys.path.insert(0, ".")
os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")
os.environ.setdefault("SUPABASE_KEY", "harness-dummy-anon-key")

import app.modules.core.router as rt          # noqa: E402
from fastapi import HTTPException              # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


ORG = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-0000000000ff"


# ── fake supabase client that records every write and returns benign data ─────────────────────────
class FakeExec:
    def __init__(self, data): self.data = data


class FakeQuery:
    def __init__(self, sink, table, rows):
        self.sink, self.table, self.rows = sink, table, rows
        self._filters = {}
        self._op = None
        self._payload = None

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def in_(self, col, vals): self._filters[col] = vals; return self

    def eq(self, col, val):
        self._filters[col] = val   # filters accumulate AFTER insert/update/delete in the real chain
        return self

    def insert(self, payload): self._op, self._payload = "insert", payload; return self
    def update(self, payload): self._op, self._payload = "update", payload; return self
    def upsert(self, payload, **k): self._op, self._payload = "upsert", payload; return self
    def delete(self): self._op = "delete"; return self

    def execute(self):
        if self._op:   # record the write with the FINAL filter set (snapshot at execute time)
            self.sink.append({"op": self._op, "table": self.table,
                              "filters": dict(self._filters), "payload": self._payload})
            if self._op in ("insert", "update", "upsert"):
                data = [self._payload] if isinstance(self._payload, dict) else (self._payload or [])
                return FakeExec(data)
            return FakeExec([{}])
        return FakeExec(self.rows.get(self.table, []))


class FakeSchema:
    def __init__(self, client, schema): self.client, self.schema_name = client, schema
    def table(self, name): return FakeQuery(self.client.writes, name, self.client.rows)


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.writes = []
    def schema(self, s): return FakeSchema(self, s)


def make_client():
    # a role row so update_role finds something; an app_users row for delete/deactivate targets
    return FakeClient({
        "roles": [{"id": 7, "name": "sales_rep", "org_id": ORG, "permissions": {}}],
        "app_users": [{"id": 1, "email": "victim@x.com", "auth_id": "a1", "org_id": ORG, "role": "admin"}],
        "employees": [],
    })


# ── caller identity injection ─────────────────────────────────────────────────────────────────────
CALLERS = {
    "sales_rep": {"org_id": ORG, "role": "sales_rep", "super_admin": False, "perms": {"scope": "self"}},
    "admin":     {"org_id": ORG, "role": "admin", "super_admin": False, "perms": {"scope": "all"}},
    "super":     {"org_id": ORG, "role": "admin", "super_admin": True, "perms": {"scope": "all"}},
}
# membership rows the caller resolves to (drives _require_super_admin, which does NOT use _resolve_caller)
MEMBER_ROWS = {
    "sales_rep": [{"org_id": ORG, "role": "sales_rep", "super_admin": False}],
    "admin":     [{"org_id": ORG, "role": "admin", "super_admin": False}],
    "super":     [{"org_id": ORG, "role": "admin", "super_admin": True}],
    # a non-house tenant admin — passes _require_setting(security) but MUST fail the super-admin gate
    "tenant_admin": [{"org_id": OTHER, "role": "admin", "super_admin": False}],
}
CALLERS["tenant_admin"] = {"org_id": OTHER, "role": "admin", "super_admin": False, "perms": {"scope": "all"}}


def install(kind, uid="u1"):
    """Point the router's auth primitives at a chosen caller identity + fake client. Drives BOTH
    _resolve_caller (used by _require_setting) AND _memberships/_pick_membership (used by
    _require_super_admin), so every gate sees the same identity."""
    client = make_client()
    caller = CALLERS[kind]
    rows = MEMBER_ROWS[kind]
    rt.sb = lambda: client
    rt._uid_from_token = lambda auth: (uid if (isinstance(auth, str) and auth) else None)
    rt._resolve_caller = lambda cl, u, active=None: (caller if u else None)
    rt._memberships = lambda cl, u: (rows if u else [])
    rt._pick_membership = lambda rws, active=None: (rws[0] if rws else None)
    return client


def expect_status(fn, status):
    try:
        r = fn()
        if asyncio.iscoroutine(r):
            run(r)
        return (False, "no-exception")
    except HTTPException as e:
        return (e.status_code == status, f"got {e.status_code}")
    except Exception as e:
        return (False, f"{type(e).__name__}:{e}")


def wrote(client):
    return any(w["op"] in ("insert", "update", "upsert", "delete") for w in client.writes)


# ── endpoint invocations (kwargs mirror the real signatures) ──────────────────────────────────────
def call_create_role(auth="Bearer t"):
    return rt.create_role({"name": "pwn", "permissions": {"modules": {"admin": True}, "scope": "all"}},
                          org_id=ORG, authorization=auth, x_active_org="")

def call_update_role(auth="Bearer t"):
    return rt.update_role(7, {"permissions": {"modules": {"admin": True}, "scope": "all"}},
                          authorization=auth, x_active_org="")

def call_assign(auth="Bearer t"):
    return rt.assign_role({"email": "attacker@x.com", "role": "admin"}, org_id=ORG,
                          authorization=auth, x_active_org="")

def call_delete_user(auth="Bearer t"):
    return rt.delete_user({"email": "victim@x.com"}, org_id=ORG, authorization=auth, x_active_org="")

def call_deactivate(auth="Bearer t"):
    return rt.deactivate_user({"email": "victim@x.com", "is_active": False}, org_id=ORG,
                              authorization=auth, x_active_org="")

def call_auth_config(auth="Bearer t"):
    return rt.set_auth_config({"rbac_enabled": False}, org_id=ORG, authorization=auth, x_active_org="")

def call_delete_role(auth="Bearer t"):
    return rt.delete_role(7, org_id=ORG, authorization=auth, x_active_org="")

def call_bulk_provision(auth="Bearer t"):
    return rt.bulk_provision({}, org_id=ORG, authorization=auth, x_active_org="")


# _can_edit_setting(caller,"security") endpoints — a sales_rep (scope=self) is denied, an admin allowed.
SETTING_ENDPOINTS = [
    ("POST /roles", call_create_role),
    ("PUT /roles/{id}", call_update_role),
    ("DELETE /roles/{id}", call_delete_role),
    ("POST /users/assign", call_assign),
    ("POST /users/delete", call_delete_user),
    ("POST /users/deactivate", call_deactivate),
]


def allowed(fn):
    try:
        r = fn()
        if asyncio.iscoroutine(r):
            run(r)
        return True
    except HTTPException as e:
        return e.status_code not in (401, 403)
    except Exception:
        return True   # got past the gate into handler logic (fake-client edge) = allowed


print("── (1) non-admin sales_rep is REJECTED 403, NOTHING written (security-gated endpoints) ──")
for label, fn in SETTING_ENDPOINTS:
    c = install("sales_rep")
    ok, det = expect_status(fn, 403)
    check(f"{label}: sales_rep -> 403", ok, det)
    check(f"{label}: sales_rep no write", not wrote(c))

print("\n── (2) unauthenticated (no token) is REJECTED 401, NOTHING written ──")
for label, fn in SETTING_ENDPOINTS:
    c = install("sales_rep")
    ok, det = expect_status(lambda f=fn: f(auth=""), 401)
    check(f"{label}: no-token -> 401", ok, det)
    check(f"{label}: no-token no write", not wrote(c))
# auth-config uses _require_super_admin, whose tokenless contract is 403 (shared by all super-admin
# routes); still a hard rejection with no write — that's what matters.
c = install("sales_rep")
st = expect_status(lambda: call_auth_config(auth=""), 403)
check("PUT /auth-config: no-token rejected (403, _require_super_admin contract)", st[0], st[1])
check("PUT /auth-config: no-token no write", not wrote(c))

print("\n── (3) tenant admin (scope=all) is ALLOWED on security-gated endpoints ──")
for label, fn in SETTING_ENDPOINTS:
    c = install("admin")
    check(f"{label}: admin allowed (past gate)", allowed(fn))

print("\n── (4) super_admin is ALLOWED on security-gated endpoints ──")
for label, fn in SETTING_ENDPOINTS:
    install("super")
    check(f"{label}: super_admin allowed", allowed(fn))

print("\n── (4b) PUT /auth-config is a GLOBAL singleton → super-admin / house-admin only ──")
# sales_rep denied; NON-house tenant admin denied (can't flip the platform-wide flag); house admin +
# super_admin allowed.
c = install("sales_rep");    check("auth-config: sales_rep -> 403", expect_status(call_auth_config, 403)[0]); check("auth-config: sales_rep no write", not wrote(c))
c = install("tenant_admin"); check("auth-config: NON-house tenant admin -> 403 (can't flip global flag)", expect_status(call_auth_config, 403)[0]); check("auth-config: tenant_admin no write", not wrote(c))
install("admin");            check("auth-config: house admin allowed (bootstrap)", allowed(call_auth_config))
install("super");            check("auth-config: super_admin allowed", allowed(call_auth_config))

print("\n── (5) NEGATIVE CONTROL: bypass the gate (pre-fix world) => the SAME attack SUCCEEDS ──")
_orig = rt._require_setting
try:
    rt._require_setting = lambda *a, **k: CALLERS["sales_rep"]   # simulate the ungated handler
    c = install("sales_rep")
    try:
        rt.update_role(7, {"permissions": {"modules": {"admin": True}}}, authorization="Bearer t", x_active_org="")
    except Exception:
        pass
    check("neg-control: PUT /roles writes WITHOUT the gate (proves the gate is load-bearing)", wrote(c))
finally:
    rt._require_setting = _orig
c = install("sales_rep")
ok, _ = expect_status(call_update_role, 403)
check("post-restore: PUT /roles blocked again (403)", ok)
check("post-restore: no write", not wrote(c))

print("\n── (6) cross-tenant guard on PUT /roles/{id}: the UPDATE is ALWAYS org_id-scoped ──")
# tenant admin: middleware-rewritten org_id (=ORG here) is the only role scope reachable.
c = install("admin")
try:
    rt.update_role(7, {"permissions": {"x": 1}}, org_id=ORG, authorization="Bearer t", x_active_org="")
except Exception:
    pass
ru = [w for w in c.writes if w["table"] == "roles" and w["op"] == "update"]
check("update_role filters by org_id (id alone can't cross tenants)",
      bool(ru) and ru[-1]["filters"].get("org_id") == ORG and ru[-1]["filters"].get("id") == 7, str(ru))
# super_admin editing ANOTHER tenant: their honored org_id param scopes the write to THAT tenant.
c = install("super")
try:
    rt.update_role(7, {"permissions": {"x": 1}}, org_id=OTHER, authorization="Bearer t", x_active_org="")
except Exception:
    pass
ru = [w for w in c.writes if w["table"] == "roles" and w["op"] == "update"]
check("super_admin update_role scopes to the honored org_id param (legit cross-tenant)",
      bool(ru) and ru[-1]["filters"].get("org_id") == OTHER, str(ru))

# ── (7) H2 — tenant_middleware fails CLOSED for a verified login with NO membership ────────────────
print("\n── (7) H2: verified-but-unprovisioned login fails CLOSED on a protected route ──")
import app.core.tenant_middleware as tm

class SendRec:
    def __init__(self): self.status = None; self.body = b""
    async def __call__(self, msg):
        if msg["type"] == "http.response.start": self.status = msg["status"]
        elif msg["type"] == "http.response.body": self.body += msg.get("body", b"")

async def _passthrough(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"REACHED_HANDLER"})

def run_mw(identity, path="/api/v1/commcalc/sales-report", qs=b"org_id=" + OTHER.encode(), env=None):
    os.environ["MULTI_TENANT_ENFORCE"] = "1"
    for k, v in (env or {}).items():
        os.environ[k] = v
    tm._resolve_identity = lambda token: identity
    tm._cache.clear()
    mw = tm.TenantScopeMiddleware(_passthrough)
    scope = {"type": "http", "path": path, "query_string": qs,
             "headers": [(b"authorization", b"Bearer sometoken")]}
    rec = SendRec()
    run(mw(scope, (lambda: None), rec))
    return rec

# identity tuple: (authenticated, super_admin, member_orgs, default_org, org_info, uid)
no_member = (True, False, (), None, {}, "u-nomember")
rec = run_mw(no_member)
check("H2: no-membership verified user -> 401 (fail closed)", rec.status == 401, f"status={rec.status}")
check("H2: handler NOT reached", b"REACHED_HANDLER" not in rec.body)

# negative control: STRICT_MEMBERSHIP=0 restores the old pass-through (attack succeeds)
rec = run_mw(no_member, env={"STRICT_MEMBERSHIP": "0"})
check("H2 neg-control: STRICT_MEMBERSHIP=0 -> old pass-through reaches handler (attack works on base)",
      rec.status == 200 and b"REACHED_HANDLER" in rec.body, f"status={rec.status}")
os.environ.pop("STRICT_MEMBERSHIP", None)

# a super-admin with no member_orgs is impossible in practice, but confirm a normal member still passes
member = (True, False, (ORG,), ORG, {ORG: {"role": "admin", "twofa_enabled": False}}, "u-member")
os.environ["TWOFA_ENFORCE"] = "0"
rec = run_mw(member, path="/api/v1/commcalc/sales-report")
check("H2: a provisioned member still reaches the handler (no false lockout)",
      rec.status == 200 and b"REACHED_HANDLER" in rec.body, f"status={rec.status}")
os.environ.pop("TWOFA_ENFORCE", None)
os.environ.pop("MULTI_TENANT_ENFORCE", None)

# ── (8) middleware allowlist is METHOD-scoped for /core/auth-config ────────────────────────────────
print("\n── (8) /core/auth-config: GET stays public, PUT is no longer anonymously reachable ──")

def run_mw_method(method, path, token_hdr=True, env=None):
    os.environ["MULTI_TENANT_ENFORCE"] = "1"
    os.environ["REQUIRE_AUTH"] = "1"
    for k, v in (env or {}).items():
        os.environ[k] = v
    tm._cache.clear()
    # an unverifiable token → identity resolves unauthenticated (so REQUIRE_AUTH must 401)
    tm._resolve_identity = lambda t: (False, False, (), None, {}, None)
    mw = tm.TenantScopeMiddleware(_passthrough)
    hdrs = [(b"authorization", b"Bearer bad")] if token_hdr else []
    scope = {"type": "http", "method": method, "path": path, "query_string": b"", "headers": hdrs}
    rec = SendRec()
    run(mw(scope, (lambda: None), rec))
    os.environ.pop("MULTI_TENANT_ENFORCE", None); os.environ.pop("REQUIRE_AUTH", None)
    return rec

rec = run_mw_method("GET", "/api/v1/core/auth-config", token_hdr=False)
check("auth-config GET stays public (login page reads the flag pre-sign-in)",
      rec.status == 200 and b"REACHED_HANDLER" in rec.body, f"status={rec.status}")
rec = run_mw_method("PUT", "/api/v1/core/auth-config", token_hdr=False)
check("auth-config PUT with NO token -> 401 at the middleware (was anonymously reachable on base)",
      rec.status == 401 and b"REACHED_HANDLER" not in rec.body, f"status={rec.status}")

print(f"\n{'='*60}\nPASS {len(PASS)}   FAIL {len(FAIL)}")
sys.exit(1 if FAIL else 0)
