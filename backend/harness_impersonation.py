"""Proof harness for admin "view as employee" (impersonation) — owner directive 2026-08-06.

Runs the ACTUAL shipped code (app/core/impersonation.py, the REAL TenantScopeMiddleware branch, the
REAL core.router._uid_from_token swap and the REAL impersonation_api gates) against fake Supabase
clients — no DB, no network. Run from backend/:

    python3 harness_impersonation.py

This is deliberately a SECURITY harness, not a happy-path harness. Sections:

  A. Grant/marker cryptography     — forgery, tampering, expiry, purpose confusion, domain separation
  B. resolve_request               — every rejection reason, incl. FAIL-CLOSED on a backend fault
  C. effective_uid                 — the identity swap + the actor-binding interlock
  D. middleware (real ASGI calls)  — no-header byte-identity, forged header, org pinning, super-admin
                                     NOT bypassing, escalation deny-list, fail-closed write journal
  E. permission gate               — default-deny for super-admin / scope-'all' / modules.admin
  F. start/stop/targets endpoints  — cross-tenant refusal, nesting refusal, audit-write fail-closed,
                                     self / super-admin / other-impersonator targets refused
  G. require_target_reauth         — the OWNER'S CARVE-OUT: refused without a fresh employee
                                     re-authentication, accepted with one, SINGLE-USE, wrong-session
                                     and expired markers refused, no-op when not impersonating
  H. policy                        — clamping + tenant disable
  I. session expiry                — an abandoned session dies on its own
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, ".")
os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")
os.environ.setdefault("MULTI_TENANT_ENFORCE", "1")

import asyncio                                          # noqa: E402
from fastapi import HTTPException                       # noqa: E402

import app.core.impersonation as imp                    # noqa: E402
import app.core.tenant_middleware as mw                 # noqa: E402
import app.modules.core.router as rt                    # noqa: E402
import app.modules.core.impersonation_api as api        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def raises(fn, status=None):
    try:
        fn()
        return False
    except HTTPException as e:
        return True if status is None else e.status_code == status
    except Exception:
        return False


# ── fakes ───────────────────────────────────────────────────────────────────────────────────────
ORG_A = "aaaaaaaa-0000-0000-0000-00000000000a"      # NON-house tenant (contract §2: never test house only)
ORG_B = "bbbbbbbb-0000-0000-0000-00000000000b"      # a DIFFERENT non-house tenant
HOUSE = "00000000-0000-0000-0000-000000000001"
ADMIN = "auth-admin-1"
EMP = "auth-emp-1"
OTHER = "auth-emp-2"
SID = "11111111-2222-3333-4444-555555555555"


class Q:
    def __init__(self, client, table, rows):
        self.c, self.t, self.rows = client, table, list(rows)
        self._patch = None

    def eq(self, col, val):
        self.rows = [r for r in self.rows if str(r.get(col)) == str(val)]
        return self

    def is_(self, col, val):
        if str(val).lower() == "null":
            self.rows = [r for r in self.rows if r.get(col) in (None, "")]
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    def order(self, *a, **k):
        return self

    def select(self, *a, **k):
        return self

    def insert(self, row):
        self.c.ops.append(("insert", self.t, dict(row)))
        store = self.c.tables.setdefault(self.t, [])
        key = self.c.unique.get(self.t)
        if key and any(all(str(r.get(k)) == str(row.get(k)) for k in key) for r in store):
            raise RuntimeError(f"duplicate key on {self.t} {key} (harness)")
        store.append(dict(row))
        self.rows = [dict(row)]
        return self

    def update(self, patch):
        self._patch = dict(patch)
        return self

    def execute(self):
        if self._patch is not None:
            for r in self.rows:
                for src in self.c.tables.get(self.t, []):
                    if src.get("id") == r.get("id") or src.get("nonce") == r.get("nonce"):
                        src.update(self._patch)
                r.update(self._patch)
            self.c.ops.append(("update", self.t, dict(self._patch)))
        return SimpleNamespace(data=[dict(r) for r in self.rows])


class Client:
    def __init__(self, tables, fail=(), unique=None):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.fail = set(fail)
        self.ops = []
        self.unique = unique or {}

    def schema(self, _n):
        return self

    def table(self, name):
        if name in self.fail:
            raise RuntimeError(f"{name} unavailable (harness)")
        return Q(self, name, self.tables.get(name, []))


def session_row(**over):
    r = {"id": SID, "org_id": ORG_A, "actor_auth_id": ADMIN, "target_auth_id": EMP,
         "actor_email": "admin@x.com", "target_email": "emp@x.com", "target_name": "Ann Employee",
         "target_role": "sales_rep", "started_at": "2026-08-06T00:00:00+00:00",
         "expires_at": "2099-01-01T00:00:00+00:00", "ended_at": None}
    r.update(over)
    return r


def base_tables(**over):
    t = {
        "impersonation_session": [session_row()],
        "impersonation_action": [],
        "impersonation_reauth": [],
        "app_users": [
            {"id": "u1", "auth_id": ADMIN, "org_id": ORG_A, "email": "admin@x.com",
             "full_name": "Al Admin", "role": "admin", "is_active": True, "super_admin": False,
             "created_at": "2026-01-01"},
            {"id": "u2", "auth_id": EMP, "org_id": ORG_A, "email": "emp@x.com",
             "full_name": "Ann Employee", "role": "sales_rep", "is_active": True,
             "super_admin": False, "created_at": "2026-01-02"},
            {"id": "u3", "auth_id": OTHER, "org_id": ORG_B, "email": "other@y.com",
             "full_name": "Bob Other", "role": "sales_rep", "is_active": True,
             "super_admin": False, "created_at": "2026-01-03"},
        ],
        "roles": [
            {"org_id": ORG_A, "name": "admin", "permissions": {"scope": "all", "modules": {"admin": True},
                                                               "impersonate": True}},
            {"org_id": ORG_A, "name": "sales_rep", "permissions": {"scope": "self"}},
            {"org_id": ORG_B, "name": "sales_rep", "permissions": {"scope": "self"}},
        ],
        "tenants": [{"org_id": ORG_A, "name": "Alpha", "impersonation_policy": None},
                    {"org_id": ORG_B, "name": "Beta", "impersonation_policy": None}],
    }
    t.update(over)
    return t


def wire(tables=None, fail=(), unique=None):
    c = Client(tables if tables is not None else base_tables(), fail=fail,
               unique=unique or {"impersonation_reauth": ("imp_session_id", "auth_session_id")})
    import app.core.database as db
    db.get_supabase = lambda: c
    rt.sb = lambda: c
    rt.get_supabase = lambda: c
    api.sb = lambda: c
    api.get_supabase = lambda: c
    imp.invalidate_session()
    imp.invalidate_policy()
    imp._brief_cache.clear()
    return c


def fresh_grant(exp_delta=600, **over):
    kw = {"session_id": SID, "actor_uid": ADMIN, "target_uid": EMP, "org_id": ORG_A,
          "exp_ts": time.time() + exp_delta}
    kw.update(over)
    return imp.mint_grant(**kw)


print("\n─── A. grant / marker cryptography ─────────────────────────────────────────────────────────")
g = fresh_grant()
check("A1. a freshly minted grant verifies", imp.verify_grant(g) is not None)
p = imp.verify_grant(g)
check("A2. payload carries session/actor/target/org", p and p["s"] == SID and p["a"] == ADMIN
      and p["t"] == EMP and p["o"] == ORG_A)
body, _, sig = g.partition(".")
check("A3. a HAND-FORGED header (random string) is refused", imp.verify_grant("not-a-token") is None)
check("A4. a grant with a WRONG signature is refused", imp.verify_grant(body + ".AAAA") is None)
# tamper: same signature, elevated payload
tampered = imp._b64u(json.dumps({**p, "t": "auth-someone-else"}, separators=(",", ":"),
                                sort_keys=True).encode()) + "." + sig
check("A5. TAMPERING the target while keeping the signature is refused", imp.verify_grant(tampered) is None)
check("A6. an EXPIRED grant is refused", imp.verify_grant(fresh_grant(exp_delta=-1)) is None)
# purpose confusion / domain separation
ra = imp.mint_reauth(session_id=SID, target_uid=EMP, nonce="n1", exp_ts=time.time() + 300)
check("A7. a RE-AUTH marker cannot be replayed as a grant", imp.verify_grant(ra) is None)
check("A8. a GRANT cannot be replayed as a re-auth marker", imp.verify_reauth(g) is None)
import app.modules.core.auth_security as _sec  # noqa: E402
tw = _sec.mint_2fa_token(ADMIN, ORG_A, "dev", time.time() + 600)
check("A9. a 2FA marker (same base secret) does NOT verify as a grant — domain separation",
      imp.verify_grant(tw) is None)
check("A10. the impersonation key differs from the raw 2FA secret",
      imp._key() != _sec._twofa_secret())
# a forger who knows the payload but not the key
guess = body + "." + imp._b64u(hmac.new(b"guessed-secret", body.encode(), hashlib.sha256).digest())
check("A11. signing with the WRONG key is refused", imp.verify_grant(guess) is None)
check("A12. an empty / missing header is refused", imp.verify_grant("") is None and imp.verify_grant(None) is None)
check("A13. a grant with no signature part is refused", imp.verify_grant(body) is None)

print("\n─── B. resolve_request (every rejection reason) ─────────────────────────────────────────────")
wire()
ctx, why = imp.resolve_request(fresh_grant(), ADMIN)
check("B1. valid grant + matching bearer + live session → ok", why == "ok" and ctx and ctx["target_uid"] == EMP)
ctx, why = imp.resolve_request(fresh_grant(), "auth-somebody-else")
check("B2. grant presented by a DIFFERENT login → not_actor (stolen grant is worthless)",
      ctx is None and why == "not_actor")
ctx, why = imp.resolve_request(fresh_grant(), "")
check("B3. no bearer identity at all → not_actor", ctx is None and why == "not_actor")
ctx, why = imp.resolve_request("forged.header", ADMIN)
check("B4. forged header → invalid", ctx is None and why == "invalid")
wire(base_tables(impersonation_session=[session_row(ended_at="2026-08-06T01:00:00+00:00")]))
ctx, why = imp.resolve_request(fresh_grant(), ADMIN)
check("B5. session ENDED server-side → ended (Exit is a real revocation)", ctx is None and why == "ended")
wire(base_tables(impersonation_session=[session_row(expires_at="2000-01-01T00:00:00+00:00")]))
ctx, why = imp.resolve_request(fresh_grant(), ADMIN)
check("B6. session row EXPIRED → expired (abandoned session dies on its own)",
      ctx is None and why == "expired")
wire(base_tables(impersonation_session=[]))
ctx, why = imp.resolve_request(fresh_grant(), ADMIN)
check("B7. no session row (grant for a session that never existed) → unknown", ctx is None and why == "unknown")
wire(base_tables(impersonation_session=[session_row(org_id=ORG_B)]))
ctx, why = imp.resolve_request(fresh_grant(), ADMIN)
check("B8. grant org ≠ session org → unknown (cross-tenant swap refused)", ctx is None and why == "unknown")
t = base_tables()
t["app_users"] = [r for r in t["app_users"] if r["auth_id"] != EMP]
wire(t)
ctx, why = imp.resolve_request(fresh_grant(), ADMIN)
check("B9. target no longer a member of the org → target_revoked", ctx is None and why == "target_revoked")
t = base_tables()
for r in t["app_users"]:
    if r["auth_id"] == EMP:
        r["is_active"] = False
wire(t)
ctx, why = imp.resolve_request(fresh_grant(), ADMIN)
check("B10. target deactivated → target_revoked", ctx is None and why == "target_revoked")
wire(base_tables(), fail=("impersonation_session",))
ctx, why = imp.resolve_request(fresh_grant(), ADMIN)
check("B11. audit store unreadable → FAIL CLOSED (unavailable), never 'ok'",
      ctx is None and why == "unavailable")
wire(base_tables(tenants=[{"org_id": ORG_A, "name": "Alpha",
                           "impersonation_policy": {"enabled": False}}]))
ctx, why = imp.resolve_request(fresh_grant(), ADMIN)
check("B12. tenant switched impersonation OFF → disabled", ctx is None and why == "disabled")
wire()
ctx, why = imp.resolve_request(fresh_grant(exp_delta=-5), ADMIN)
check("B13. grant past its own expiry → invalid even with a live session row",
      ctx is None and why == "invalid")

print("\n─── C. effective_uid — the identity swap + actor interlock ──────────────────────────────────")
check("C1. no context → the real uid is returned unchanged", imp.effective_uid(ADMIN) == ADMIN)
tok = imp.set_current({"session_id": SID, "actor_uid": ADMIN, "target_uid": EMP, "org_id": ORG_A})
check("C2. context present → the TARGET's uid is returned", imp.effective_uid(ADMIN) == EMP)
check("C3. INTERLOCK: a context whose actor ≠ this request's login does NOT swap",
      imp.effective_uid("auth-third-party") == "auth-third-party")
check("C4. is_impersonating / actor_uid / target_uid agree",
      imp.is_impersonating() and imp.actor_uid() == ADMIN and imp.target_uid() == EMP)
check("C5. assert_not_impersonating raises 403 inside a session",
      raises(lambda: imp.assert_not_impersonating("x"), 403))
imp.reset_current(tok)
check("C6. after reset the context is gone", not imp.is_impersonating() and imp.effective_uid(ADMIN) == ADMIN)
check("C7. assert_not_impersonating is a no-op outside a session",
      imp.assert_not_impersonating("x") is None)

print("\n─── C-bis. core.router._uid_from_token honours the swap ─────────────────────────────────────")


class _Auth:
    class auth:                                                # noqa: N801
        @staticmethod
        def get_user(token):
            return SimpleNamespace(user=SimpleNamespace(id=ADMIN))


rt.get_supabase_admin = lambda: _Auth()
rt._uid_cache.clear()
check("C8. no impersonation → _uid_from_token == the real login", rt._uid_from_token("Bearer t") == ADMIN)
tok = imp.set_current({"session_id": SID, "actor_uid": ADMIN, "target_uid": EMP, "org_id": ORG_A})
check("C9. impersonating → _uid_from_token returns the TARGET (this is what makes the app render as them)",
      rt._uid_from_token("Bearer t") == EMP)
check("C10. _real_uid_from_token is UNAFFECTED (the audit trail keeps the real human)",
      rt._real_uid_from_token("Bearer t") == ADMIN)
imp.reset_current(tok)
check("C11. token cache still holds the REAL uid (the swap is applied after, not cached)",
      rt._uid_from_token("Bearer t") == ADMIN)

print("\n─── D. middleware — the real ASGI branch ────────────────────────────────────────────────────")


class Recorder:
    """Captures what the app under the middleware actually received."""

    def __init__(self):
        self.scope = None
        self.calls = 0

    async def __call__(self, scope, receive, send):
        self.scope = scope
        self.calls += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


def run(scope_headers, path="/api/v1/storeops/timeclock/status", method="GET", query=b"",
        app=None):
    rec = app or Recorder()
    m = mw.TenantScopeMiddleware(rec)
    out = []

    async def send(msg):
        out.append(msg)

    scope = {"type": "http", "path": path, "method": method, "query_string": query,
             "headers": [(k.encode(), v.encode()) for k, v in scope_headers],
             "client": ("203.0.113.7", 1234)}
    asyncio.run(m(scope, None, send))
    status = next((o["status"] for o in out if o["type"] == "http.response.start"), None)
    body = b"".join(o.get("body", b"") for o in out if o["type"] == "http.response.body")
    return rec, status, body


# identity resolution stub for the middleware (the actor is a NORMAL admin of ORG_A)
def stub_identity(super_admin=False, uid=ADMIN, orgs=(ORG_A,)):
    mw._cache.clear()
    mw._resolve_identity = lambda token: (bool(token), super_admin, tuple(orgs),
                                          orgs[0] if orgs else None, {}, uid if token else None)


wire()
stub_identity()
rec, status, _ = run([("authorization", "Bearer t")], query=b"org_id=" + HOUSE.encode())
check("D1. NO impersonation header → normal path, request reaches the app",
      rec.calls == 1 and status == 200)
check("D2. …and the normal org rewrite still applies (unchanged behaviour)",
      b"org_id=" + ORG_A.encode() in rec.scope["query_string"])

rec, status, body = run([("authorization", "Bearer t"), ("x-impersonate", "forged.header")])
check("D3. FORGED impersonation header → 401, request NEVER reaches the app",
      status == 401 and rec.calls == 0)
check("D4. …with code impersonation_invalid so the client can exit cleanly",
      b"impersonation_invalid" in body)

rec, status, _ = run([("x-impersonate", fresh_grant())])
check("D5. impersonation header with NO bearer token → 401 (the grant is bound to a login)",
      status == 401 and rec.calls == 0)

g = fresh_grant()
rec, status, _ = run([("authorization", "Bearer t"), ("x-impersonate", g),
                      ("x-active-org", ORG_B)], query=b"org_id=" + ORG_B.encode())
qs = rec.scope["query_string"].decode() if rec.scope else ""
hdrs = {k.decode().lower(): v.decode() for k, v in (rec.scope["headers"] if rec.scope else [])}
check("D6. valid grant → the request reaches the app", status == 200 and rec.calls == 1)
check("D7. org_id QUERY PARAM is PINNED to the grant's org (client value discarded)",
      f"org_id={ORG_A}" in qs and ORG_B not in qs)
check("D8. x-active-org HEADER is PINNED to the grant's org too",
      hdrs.get("x-active-org") == ORG_A)

# super-admin must NOT take the "no rewrite, trust the client org_id" bypass while impersonating
stub_identity(super_admin=True)
rec, status, _ = run([("authorization", "Bearer t"), ("x-impersonate", fresh_grant())],
                     query=b"org_id=" + ORG_B.encode())
check("D9. SUPER-ADMIN impersonating does NOT get the org bypass — still pinned to the grant's org",
      status == 200 and f"org_id={ORG_A}" in rec.scope["query_string"].decode())
stub_identity()

# escalation deny-list
for pth, meth, name in (("/api/v1/core/roles/3", "PUT", "edit a role"),
                        ("/api/v1/core/users/assign", "POST", "assign a role"),
                        ("/api/v1/core/super-admins", "POST", "grant super-admin"),
                        ("/api/v1/core/impersonation/start", "POST", "NEST another impersonation"),
                        ("/api/v1/core/impersonation/stop", "POST", "manage sessions"),
                        ("/api/v1/core/me/set-password", "POST", "change the employee's password"),
                        ("/api/v1/core/tenants", "PATCH", "edit tenant settings")):
    rec, status, body = run([("authorization", "Bearer t"), ("x-impersonate", fresh_grant())],
                            path=pth, method=meth)
    check(f"D10.{name} is REFUSED while impersonating ({meth} {pth})",
          status == 403 and rec.calls == 0 and b"impersonation_forbidden" in body)

# reads outside the deny-list still work
rec, status, _ = run([("authorization", "Bearer t"), ("x-impersonate", fresh_grant())],
                     path="/api/v1/core/roles", method="GET")
check("D11. a READ of a deny-listed prefix still passes (only writes are refused)",
      status == 200 and rec.calls == 1)

# public/allowlisted path must still resolve as the TARGET
rec, status, _ = run([("authorization", "Bearer t"), ("x-impersonate", fresh_grant())],
                     path="/api/v1/core/me", method="GET")
check("D12. an ALLOWLISTED path (/core/me) is handled by the impersonation branch, not skipped",
      status == 200 and rec.calls == 1
      and {k.decode().lower(): v.decode() for k, v in rec.scope["headers"]}.get("x-active-org") == ORG_A)

# write journal — fail-closed
c = wire()
rec, status, _ = run([("authorization", "Bearer t"), ("x-impersonate", fresh_grant())],
                     path="/api/v1/storeops/shifts", method="POST")
journal = [o for o in c.ops if o[0] == "insert" and o[1] == "impersonation_action"]
check("D13. a MUTATING request writes an attribution row BEFORE the handler runs",
      status == 200 and len(journal) >= 1 and journal[0][2]["actor_auth_id"] == ADMIN
      and journal[0][2]["target_auth_id"] == EMP and journal[0][2]["method"] == "POST")
check("D14. the journal row names the real human, the target, the org and the path",
      journal[0][2]["org_id"] == ORG_A and journal[0][2]["path"] == "/api/v1/storeops/shifts")
check("D15. the journal row's status is back-filled after the response",
      any(o[0] == "update" and o[1] == "impersonation_action" for o in c.ops))
c = wire()
c2 = Client(base_tables(), fail=("impersonation_action",))
import app.core.database as _db  # noqa: E402
_db.get_supabase = lambda: c2
imp.invalidate_session()
rec, status, body = run([("authorization", "Bearer t"), ("x-impersonate", fresh_grant())],
                        path="/api/v1/storeops/shifts", method="POST")
check("D16. FAIL CLOSED: a write we cannot attribute is REFUSED (503), handler never runs",
      status == 503 and rec.calls == 0 and b"impersonation_audit_unavailable" in body)
rec, status, _ = run([("authorization", "Bearer t"), ("x-impersonate", fresh_grant())],
                     path="/api/v1/storeops/timeclock/status", method="GET")
check("D17. …but a READ is not blocked by the journal being down (reads are not journalled)",
      status == 200 and rec.calls == 1)
wire()
# context does not leak between requests
rec, status, _ = run([("authorization", "Bearer t")])
check("D18. after an impersonated request, the NEXT plain request has no context",
      status == 200 and not imp.is_impersonating())

print("\n─── E. the permission gate (DEFAULT-DENY, no bypass) ───────────────────────────────────────")
check("E1. empty permissions → cannot impersonate", api.can_impersonate({}) is False)
check("E2. scope 'all' does NOT grant it", api.can_impersonate({"scope": "all"}) is False)
check("E3. modules.admin does NOT grant it", api.can_impersonate({"modules": {"admin": True}}) is False)
check("E4. an explicit false is denied", api.can_impersonate({"impersonate": False}) is False)
check("E5. a truthy non-true value is denied (strict identity)", api.can_impersonate({"impersonate": 1}) is False)
check("E6. an explicit true grants it", api.can_impersonate({"impersonate": True}) is True)
check("E7. None permissions → denied", api.can_impersonate(None) is False)
# a super-admin whose role lacks the permission has NO reach
c = wire(base_tables(roles=[{"org_id": ORG_A, "name": "admin",
                             "permissions": {"scope": "all", "modules": {"admin": True}}}]))
rt._uid_cache.clear()
sa, granted = api._authority(c, ADMIN)
check("E8. a super-admin whose role lacks 'impersonate' reaches NO tenant", granted == {})
c = wire()
sa, granted = api._authority(c, ADMIN)
check("E9. a role WITH the permission reaches its own tenant", set(granted.keys()) == {ORG_A})

print("\n─── F. start / stop / targets endpoints ────────────────────────────────────────────────────")
c = wire()
rt._uid_cache.clear()
rt.get_supabase_admin = lambda: _Auth()
req = SimpleNamespace(headers={"user-agent": "harness"}, client=SimpleNamespace(host="203.0.113.7"))
res = api.start({"target": EMP, "reason": "repro clock issue"}, req, org_id=ORG_A, authorization="Bearer t")
check("F1. an authorized admin can start a session and receives a grant", bool(res.get("grant")))
check("F2. the audit row is written with actor, target, org and expiry",
      any(o[0] == "insert" and o[1] == "impersonation_session"
          and o[2]["actor_auth_id"] == ADMIN and o[2]["target_auth_id"] == EMP
          and o[2]["org_id"] == ORG_A and o[2].get("expires_at") for o in c.ops))
check("F3. the returned grant verifies and is bound to actor+target+org",
      (imp.verify_grant(res["grant"]) or {}).get("a") == ADMIN
      and (imp.verify_grant(res["grant"]) or {}).get("t") == EMP
      and (imp.verify_grant(res["grant"]) or {}).get("o") == ORG_A)
check("F4. no token leaks into the session summary shown to the UI",
      "grant" not in json.dumps(res["session"]))

# CROSS-TENANT: a permission holder in ORG_A may not reach ORG_B
c = wire()
check("F5. CROSS-TENANT: a non-super-admin cannot start a session in another tenant",
      raises(lambda: api.start({"target": OTHER}, req, org_id=ORG_B, authorization="Bearer t"), 403))
# a target that is not in the acting org
check("F6. a target who is not a member of the acting org is refused (404)",
      raises(lambda: api.start({"target": OTHER}, req, org_id=ORG_A, authorization="Bearer t"), 404))
check("F7. impersonating YOURSELF is refused",
      raises(lambda: api.start({"target": ADMIN}, req, org_id=ORG_A, authorization="Bearer t"), 400))
# super-admin target
t = base_tables()
for r in t["app_users"]:
    if r["auth_id"] == EMP:
        r["super_admin"] = True
c = wire(t)
check("F8. a SUPER-ADMIN can never be impersonated",
      raises(lambda: api.start({"target": EMP}, req, org_id=ORG_A, authorization="Bearer t"), 403))
# another impersonator as target
t = base_tables()
t["roles"] = t["roles"] + []
for r in t["roles"]:
    if r["name"] == "sales_rep" and r["org_id"] == ORG_A:
        r["permissions"] = {"scope": "self", "impersonate": True}
c = wire(t)
check("F9. a role that can itself sign in as others cannot be borrowed (no escalation chain)",
      raises(lambda: api.start({"target": EMP}, req, org_id=ORG_A, authorization="Bearer t"), 403))
# deactivated target
t = base_tables()
for r in t["app_users"]:
    if r["auth_id"] == EMP:
        r["is_active"] = False
c = wire(t)
check("F10. a deactivated employee cannot be impersonated",
      raises(lambda: api.start({"target": EMP}, req, org_id=ORG_A, authorization="Bearer t"), 403))
# no permission at all
c = wire(base_tables(roles=[{"org_id": ORG_A, "name": "admin",
                             "permissions": {"scope": "all", "modules": {"admin": True}}},
                            {"org_id": ORG_A, "name": "sales_rep", "permissions": {"scope": "self"}}]))
check("F11. WITHOUT the permission, start is refused (403) even for a full-scope admin",
      raises(lambda: api.start({"target": EMP}, req, org_id=ORG_A, authorization="Bearer t"), 403))
check("F12. …and the roster endpoint is refused too (no enumeration without the permission)",
      raises(lambda: api.list_targets(org_id=ORG_A, authorization="Bearer t"), 403))
# audit store down at start → fail closed, no grant
c = wire(base_tables(), fail=("impersonation_session",))
check("F13. FAIL CLOSED: audit write impossible ⇒ start refuses (503), NO grant is minted",
      raises(lambda: api.start({"target": EMP}, req, org_id=ORG_A, authorization="Bearer t"), 503))
# nesting
c = wire()
tok = imp.set_current({"session_id": SID, "actor_uid": ADMIN, "target_uid": EMP, "org_id": ORG_A})
check("F14. NESTING refused: cannot start an impersonation from inside one",
      raises(lambda: api.start({"target": EMP}, req, org_id=ORG_A, authorization="Bearer t"), 403))
check("F15. …and the policy cannot be edited from inside one",
      raises(lambda: api.put_policy({"policy": {"enabled": True}}, org_id=ORG_A,
                                    authorization="Bearer t"), 403))
imp.reset_current(tok)
# targets roster shape (RULE THREE)
c = wire()
tg = api.list_targets(org_id=ORG_A, authorization="Bearer t")
ids = [x["id"] for x in tg["targets"]]
check("F16. the roster is org-scoped and excludes the caller themselves",
      ids == [EMP], f"ids={ids}")
check("F17. roster rows are EntityPicker-shaped ('First Last' + email sublabel)",
      tg["targets"][0]["label"] == "Ann Employee" and tg["targets"][0]["sublabel"] == "emp@x.com")
# stop
c = wire()
res = api.stop({"session_id": SID}, req, org_id=ORG_A, authorization="Bearer t")
check("F18. stop closes the session and stamps ended_at",
      res["ok"] and any(o[0] == "update" and o[1] == "impersonation_session"
                        and o[2].get("ended_at") for o in c.ops))
check("F19. stop is idempotent (a second call is not an error)",
      api.stop({"session_id": SID}, req, org_id=ORG_A, authorization="Bearer t")["ok"])
c = wire()
rt._uid_cache.clear()


class _AuthOther:
    class auth:                                                # noqa: N801
        @staticmethod
        def get_user(token):
            return SimpleNamespace(user=SimpleNamespace(id=OTHER))


rt.get_supabase_admin = lambda: _AuthOther()
rt._uid_cache.clear()
check("F20. a DIFFERENT admin cannot stop someone else's session",
      raises(lambda: api.stop({"session_id": SID}, req, org_id=ORG_A, authorization="Bearer t"), 403))
rt.get_supabase_admin = lambda: _Auth()
rt._uid_cache.clear()

print("\n─── G. THE CARVE-OUT — clock in / clock out re-authentication ──────────────────────────────")
c = wire()
check("G1. NOT impersonating → require_target_reauth is a NO-OP (a normal punch is untouched)",
      imp.require_target_reauth("timeclock.clock_in") == {"impersonating": False})
check("G2. …and it performs no database work at all", c.ops == [])

ctx = {"session_id": SID, "actor_uid": ADMIN, "target_uid": EMP, "org_id": ORG_A, "reauth": ""}
tok = imp.set_current(ctx)
check("G3. impersonating with NO unlock → clock-in is REFUSED (403)",
      raises(lambda: imp.require_target_reauth("timeclock.clock_in"), 403))
imp.reset_current(tok)
tok = imp.set_current({**ctx, "reauth": "forged.marker"})
check("G4. a FORGED unlock is refused", raises(lambda: imp.require_target_reauth("timeclock.clock_out"), 403))
imp.reset_current(tok)
tok = imp.set_current({**ctx, "reauth": imp.mint_reauth(session_id="other-session", target_uid=EMP,
                                                        nonce="n", exp_ts=time.time() + 300)})
check("G5. an unlock minted for a DIFFERENT session is refused",
      raises(lambda: imp.require_target_reauth("timeclock.clock_in"), 403))
imp.reset_current(tok)
tok = imp.set_current({**ctx, "reauth": imp.mint_reauth(session_id=SID, target_uid="somebody-else",
                                                        nonce="n", exp_ts=time.time() + 300)})
check("G6. an unlock minted for a DIFFERENT employee is refused",
      raises(lambda: imp.require_target_reauth("timeclock.clock_in"), 403))
imp.reset_current(tok)
tok = imp.set_current({**ctx, "reauth": imp.mint_reauth(session_id=SID, target_uid=EMP,
                                                        nonce="n", exp_ts=time.time() - 1)})
check("G7. an EXPIRED unlock is refused", raises(lambda: imp.require_target_reauth("timeclock.clock_in"), 403))
imp.reset_current(tok)

# the real minting path: the EMPLOYEE's own token, verified server-side
c = wire()


def emp_token(iat_offset=0, sid="supa-session-1"):
    hdr = imp._b64u(b'{"alg":"HS256"}')
    pl = imp._b64u(json.dumps({"sub": EMP, "iat": int(time.time()) + iat_offset,
                               "session_id": sid}).encode())
    return f"{hdr}.{pl}.sig"


class _AuthEmp:
    class auth:                                                # noqa: N801
        @staticmethod
        def get_user(token):
            return SimpleNamespace(user=SimpleNamespace(id=EMP))


api.get_supabase_admin = lambda: _AuthEmp()
out = api.reauth({"session_id": SID, "token": emp_token()}, req, org_id=ORG_A, authorization="Bearer t")
check("G8. the employee's fresh token mints a single-use unlock", bool(out.get("reauth")) and out["single_use"])
marker = out["reauth"]
tok = imp.set_current({**ctx, "reauth": marker})
got = imp.require_target_reauth("timeclock.clock_in")
check("G9. WITH the unlock, clock-in is ALLOWED", got.get("impersonating") is True)
check("G10. SINGLE USE: the same unlock cannot buy a second punch",
      raises(lambda: imp.require_target_reauth("timeclock.clock_out"), 403))
imp.reset_current(tok)
check("G11. the consumed unlock is recorded (who/what it was spent on)",
      any(o[0] == "update" and o[1] == "impersonation_reauth" and o[2].get("consumed_at") for o in c.ops))
check("G12. REPLAY: the SAME Supabase sign-in session cannot mint a second unlock",
      raises(lambda: api.reauth({"session_id": SID, "token": emp_token()}, req, org_id=ORG_A,
                                authorization="Bearer t"), 409))
check("G13. a NEW password entry (new Supabase session) CAN mint another unlock",
      bool(api.reauth({"session_id": SID, "token": emp_token(sid="supa-session-2")}, req,
                      org_id=ORG_A, authorization="Bearer t").get("reauth")))
check("G14. a STALE employee token (older than the freshness window) is refused",
      raises(lambda: api.reauth({"session_id": SID, "token": emp_token(iat_offset=-9999, sid="s3")},
                                req, org_id=ORG_A, authorization="Bearer t"), 403))
api.get_supabase_admin = lambda: _Auth()      # resolves to ADMIN, i.e. the WRONG person
check("G15. the ADMIN's own password cannot unlock a punch (token must resolve to the EMPLOYEE)",
      raises(lambda: api.reauth({"session_id": SID, "token": emp_token(sid="s4")}, req,
                                org_id=ORG_A, authorization="Bearer t"), 403))
api.get_supabase_admin = lambda: _AuthEmp()
c = wire(base_tables(impersonation_session=[session_row(ended_at="2026-08-06T01:00:00+00:00")]))
check("G16. an ENDED session cannot mint an unlock",
      raises(lambda: api.reauth({"session_id": SID, "token": emp_token(sid="s5")}, req,
                                org_id=ORG_A, authorization="Bearer t"), 403))
c = wire()
tok = imp.set_current(ctx)
check("G17. the unlock endpoint itself is refused from INSIDE an impersonated session",
      raises(lambda: api.reauth({"session_id": SID, "token": emp_token(sid="s6")}, req,
                                org_id=ORG_A, authorization="Bearer t"), 403))
imp.reset_current(tok)
# the primitive fails closed when the marker store is unreadable
c = wire(base_tables(), fail=("impersonation_reauth",))
tok = imp.set_current({**ctx, "reauth": imp.mint_reauth(session_id=SID, target_uid=EMP, nonce="nX",
                                                        exp_ts=time.time() + 300)})
check("G18. FAIL CLOSED: unlock store unreadable ⇒ the punch is refused, not allowed",
      raises(lambda: imp.require_target_reauth("timeclock.clock_in"), 403))
imp.reset_current(tok)

print("\n─── H. the escalation table + policy ───────────────────────────────────────────────────────")
F = imp.is_forbidden_while_impersonating
check("H1. every method under /core/impersonation is refused",
      all(F("/api/v1/core/impersonation/start", m) for m in ("GET", "POST", "PUT", "DELETE")))
check("H2. role/user/tenant WRITES are refused",
      F("/api/v1/core/roles/1", "PUT") and F("/api/v1/core/users/assign", "POST")
      and F("/api/v1/core/super-admins", "DELETE") and F("/api/v1/core/tenants/x", "PATCH"))
check("H3. reads of those prefixes are allowed", not F("/api/v1/core/roles", "GET")
      and not F("/api/v1/core/users", "GET"))
check("H4. boundary matching — a same-prefix sibling is NOT caught",
      not F("/api/v1/core/rolesets", "POST") and not F("/api/v1/core/usersearch", "POST"))
check("H5. ordinary module writes are NOT blocked (rest all functions work)",
      not F("/api/v1/storeops/shifts", "POST") and not F("/api/v1/closing/submit", "POST"))
p = imp.normalize_policy(None)
check("H6. absent policy → defaults", p == imp.DEFAULT_POLICY)
check("H7. hostile values are clamped, never trusted",
      imp.normalize_policy({"max_minutes": 99999, "reauth_minutes": -5,
                            "reauth_token_max_age_s": 10 ** 9})
      == {"enabled": True, "max_minutes": 240, "reauth_minutes": 1, "reauth_token_max_age_s": 600})
check("H8. garbage types are ignored",
      imp.normalize_policy({"max_minutes": "abc", "reauth_minutes": True})["max_minutes"] == 45)
check("H9. enabled=false is honoured", imp.normalize_policy({"enabled": False})["enabled"] is False)
c = wire()
check("H10. load_policy degrades to defaults when the column is missing",
      imp.load_policy(Client({}, fail=("tenants",)), ORG_A) == imp.DEFAULT_POLICY)

print("\n─── I. session expiry (an abandoned session dies on its own) ───────────────────────────────")
c = wire()
# NOTE: mint_grant stores `int(exp_ts)`, so a sub-second TTL can truncate to ~0 and make this flaky.
# 2 s in / 2.5 s out gives an effective window of 1–2 s and a guaranteed expiry after the sleep.
short = imp.mint_grant(session_id=SID, actor_uid=ADMIN, target_uid=EMP, org_id=ORG_A,
                       exp_ts=time.time() + 2)
check("I1. a short-lived grant works now", imp.resolve_request(short, ADMIN)[1] == "ok")
time.sleep(2.5)
check("I2. …and stops working once its expiry passes, with no server action",
      imp.resolve_request(short, ADMIN)[1] == "invalid")
c = wire(base_tables(impersonation_session=[session_row(expires_at="2000-01-01T00:00:00+00:00")]))
long_grant = imp.mint_grant(session_id=SID, actor_uid=ADMIN, target_uid=EMP, org_id=ORG_A,
                            exp_ts=time.time() + 99999)
check("I3. a long grant cannot outlive the DB row's expires_at (the row is the authority)",
      imp.resolve_request(long_grant, ADMIN)[1] == "expired")

print("\n─── K. THE LOAD-BEARING RUNTIME ASSUMPTION ─────────────────────────────────────────────────")
# The context is published by an ASGI middleware and read inside a SYNC (`def`) FastAPI handler,
# which Starlette runs in a worker THREAD via anyio.to_thread. If contextvars did not propagate into
# that thread, `require_target_reauth` would silently see "not impersonating" and the clock-in gate
# would be a no-op — the worst possible failure. Proven here against the REAL FastAPI + Starlette +
# anyio stack that ships, through the REAL middleware, with a REAL sync endpoint.
from fastapi import FastAPI as _FastAPI                 # noqa: E402

_probe = _FastAPI()
_seen = {}


@_probe.post("/api/v1/storeops/timeclock/clock-in")
def _sync_punch():                                      # SYNC def → Starlette threadpool
    _seen["impersonating"] = imp.is_impersonating()
    _seen["target"] = imp.target_uid()
    try:
        imp.require_target_reauth("timeclock.clock_in")
        _seen["gate"] = "allowed"
    except HTTPException as e:
        _seen["gate"] = f"refused-{e.status_code}"
    return {"ok": True}


@_probe.get("/api/v1/storeops/timeclock/status")
def _sync_status():
    _seen["impersonating"] = imp.is_impersonating()
    return {"ok": True}


wire()
stub_identity()
_seen.clear()
_, st, _b = run([("authorization", "Bearer t"), ("x-impersonate", fresh_grant())],
                path="/api/v1/storeops/timeclock/status", method="GET", app=_probe)
check("K1. a SYNC FastAPI handler (threadpool) SEES the impersonation context",
      _seen.get("impersonating") is True, f"seen={_seen} status={st}")
_seen.clear()
_, st, _b = run([("authorization", "Bearer t"), ("x-impersonate", fresh_grant())],
                path="/api/v1/storeops/timeclock/clock-in", method="POST", app=_probe)
check("K2. the REAL clock-in shape: gate REFUSES the punch without an unlock",
      _seen.get("gate") == "refused-403" and _seen.get("target") == EMP, f"seen={_seen}")
c = wire()
api.get_supabase_admin = lambda: _AuthEmp()
mk = api.reauth({"session_id": SID, "token": emp_token(sid="supa-k")}, req, org_id=ORG_A,
                authorization="Bearer t")["reauth"]
_seen.clear()
_, st, _b = run([("authorization", "Bearer t"), ("x-impersonate", fresh_grant()),
                 ("x-impersonate-reauth", mk)],
                path="/api/v1/storeops/timeclock/clock-in", method="POST", app=_probe)
check("K3. …and ALLOWS it once the employee's unlock rides along on the request",
      _seen.get("gate") == "allowed", f"seen={_seen}")
_seen.clear()
_, st, _b = run([("authorization", "Bearer t"), ("x-impersonate", fresh_grant()),
                 ("x-impersonate-reauth", mk)],
                path="/api/v1/storeops/timeclock/clock-in", method="POST", app=_probe)
check("K4. the SAME unlock cannot be replayed on a second HTTP request (single use, end to end)",
      _seen.get("gate") == "refused-403", f"seen={_seen}")
_seen.clear()
_, st, _b = run([("authorization", "Bearer t")], path="/api/v1/storeops/timeclock/clock-in",
                method="POST", app=_probe)
check("K5. a NORMAL punch (no impersonation) sails straight through the gate",
      _seen.get("impersonating") is False and _seen.get("gate") == "allowed", f"seen={_seen}")

print("\n─── J. route surface ───────────────────────────────────────────────────────────────────────")
from app.main import app as fastapi_app    # noqa: E402
paths = {r.path for r in fastapi_app.routes}
for p in ("/api/v1/core/impersonation/start", "/api/v1/core/impersonation/stop",
          "/api/v1/core/impersonation/status", "/api/v1/core/impersonation/reauth",
          "/api/v1/core/impersonation/targets", "/api/v1/core/impersonation/log",
          "/api/v1/core/impersonation/policy"):
    check(f"J. route mounted: {p}", p in paths)
check("J. the settings area is registered so the policy can be granted per role",
      any(a["key"] == "impersonation" for a in rt.SETTING_AREAS))

print("\n" + "=" * 92)
print(f"PASS {len(PASS)}   FAIL {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
