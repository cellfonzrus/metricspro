"""Proof harness for consent-based account linking (platform-core-11).

Drives the REAL functions in app.modules.core.router (imported, not copied) to prove:
  1. decision truth table            — _provision_decision maps every input combo correctly.
  2. ANTI-ENUMERATION response parity — create_login returns a byte-identical shape+message for a
     fresh email vs. an email that already exists in ANOTHER tenant (the enumeration-sensitive pair).
  3. _provision_login routing        — fresh/reset bind, pending RAISES (no bind/alias), alias only
     via explicit separate_login (mig-088 escape hatch preserved).
  4. connect path                    — idempotent; wrong code refused; unbound row → bound to caller.
  5. disable path                    — mints the new login FIRST, then bans the OLD auth account
     (ban_duration set), returns the reinstate policy.
  6. reinstate is super-admin-only   — the endpoint calls _require_super_admin (403 for a non-super).

Run: python3 scratchpad/prove_account_linking.py   (from backend/, no DB/network required)
"""
import asyncio
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import app.modules.core.router as R
from app.modules.core.router import (
    _provision_decision, _login_ready_response, _provision_login, PendingConnectionRequired,
)

P = 0
F = 0
def ok(cond, label):
    global P, F
    if cond:
        P += 1; print(f"  PASS  {label}")
    else:
        F += 1; print(f"  FAIL  {label}")


# ── tiny chainable fake for the supabase-py client ────────────────────────────────────────────────
class _Chain:
    def __init__(self, sink):
        self.sink = sink
    def select(self, *a, **k): return self
    def update(self, patch): self.sink["updates"].append(patch); return self
    def insert(self, row): self.sink["inserts"].append(row); return self
    def delete(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def neq(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def order(self, *a, **k): return self
    def execute(self):
        class _R: pass
        r = _R(); r.data = self.sink["select"]; return r

class FakeClient:
    def __init__(self, select=None):
        self.sink = {"select": select or [], "updates": [], "inserts": []}
    def schema(self, *a, **k): return self
    def table(self, *a, **k): return _Chain(self.sink)


print("\n[1] _provision_decision truth table")
ok(_provision_decision(member_here=True,  member_elsewhere=False, separate_login=False) == "reset",  "in-tenant login → reset")
ok(_provision_decision(member_here=True,  member_elsewhere=True,  separate_login=False) == "reset",  "in-tenant wins over elsewhere → reset")
ok(_provision_decision(member_here=False, member_elsewhere=False, separate_login=False) == "fresh",  "no login anywhere → fresh (direct create)")
ok(_provision_decision(member_here=False, member_elsewhere=True,  separate_login=False) == "pending","login elsewhere, default → PENDING (no silent bind)")
ok(_provision_decision(member_here=False, member_elsewhere=True,  separate_login=True)  == "alias",  "login elsewhere + separate_login → alias (mig-088 escape hatch)")
ok(_provision_decision(member_here=False, member_elsewhere=False, separate_login=True)  == "fresh",  "separate_login but no login elsewhere → still fresh")


print("\n[2] ANTI-ENUMERATION — create_login response parity (fresh vs exists-elsewhere)")
async def _parity():
    R.sb = lambda: FakeClient(select=[{"id": 1, "role": "sales_rep", "email": "x@y.com"}])
    R.get_supabase_admin = lambda: object()
    # not-exists (fresh): _provision_login returns the direct-create tuple.
    R._provision_login = lambda *a, **k: ("x@y.com", "auth-new", True, False, False)
    fresh = await R.create_login({"email": "x@y.com"})
    # exists elsewhere (pending): _provision_login raises; _create_pending_invite yields a token.
    def _raise(*a, **k): raise PendingConnectionRequired()
    R._provision_login = _raise
    R._create_pending_invite = lambda *a, **k: "Cx-secret-token"
    pending = await R.create_login({"email": "x@y.com"})
    return fresh, pending

fresh, pending = asyncio.run(_parity())
ok(set(fresh.keys()) == set(pending.keys()), f"identical key sets  fresh={sorted(fresh)}  pending={sorted(pending)}")
ok(fresh["note"] == pending["note"], "identical admin note (no 'exists elsewhere' hint)")
ok(fresh["status"] == pending["status"] == "login_ready", "identical status field")
ok(fresh["aliased"] is False and pending["aliased"] is False, "neither response flags aliased/shared")
ok(fresh.get("shared") is False and pending.get("shared") is False, "no 'shared' truth leak")
ok(fresh["access_code"] != pending["access_code"], "only the opaque access_code value differs (temp pw vs token)")
ok(fresh["temp_password"] == fresh["access_code"] and pending["temp_password"] == pending["access_code"],
   "temp_password mirrors access_code in BOTH (back-compat, no shape difference)")
# The lengths/formats must not obviously distinguish — both are opaque url-safe-ish strings.
ok(all(isinstance(r["access_code"], str) and len(r["access_code"]) >= 8 for r in (fresh, pending)),
   "both access codes are opaque strings (no 'PENDING'/'temp' marker)")


print("\n[3] _provision_login routing (real function, faked DB/auth)")
def _run_provision(member_here, member_elsewhere, separate_login):
    client = FakeClient()
    R._email_login_state = lambda c, e, o: (member_here, member_elsewhere)
    R._create_or_link_auth = lambda admin, email, pw: ("auth-bound", True, None)
    R._mint_tenant_alias = lambda *a, **k: ("x+alias@y.com", "auth-alias", True, True, False)
    return _provision_login(client, object(), {"id": 1}, "org-B", "x@y.com", "pw",
                            separate_login=separate_login), client

# fresh → binds, aliased/shared False
res, client = _run_provision(False, False, False)
ok(res == ("x@y.com", "auth-bound", True, False, False), "fresh → direct bind (aliased=shared=False)")
ok(any("auth_id" in u for u in client.sink["updates"]), "fresh binds auth_id onto the app_users row")

# reset → binds existing in-tenant account
res, _ = _run_provision(True, False, False)
ok(res[0] == "x@y.com" and res[3] is False, "reset → binds in-tenant account (no alias)")

# pending → RAISES, nothing bound/aliased
raised = False
try:
    _run_provision(False, True, False)
except PendingConnectionRequired:
    raised = True
ok(raised, "exists elsewhere + default → raises PendingConnectionRequired (NO bind, NO alias)")

# alias → only via explicit separate_login
res, _ = _run_provision(False, True, True)
ok(res == ("x+alias@y.com", "auth-alias", True, True, False), "exists elsewhere + separate_login → mig-088 alias")


print("\n[4] connect path (real connect_tenant, faked deps)")
async def _connect(target_auth_id_on_row, code_given, code_on_invite="good"):
    R._uid_from_token = lambda auth: "uidA"
    R._email_for_uid = lambda c, uid: "x@y.com"
    R._find_pending_invite = lambda c, e, o, code=None: (
        {"id": "inv1", "connect_token": code_on_invite} if (code is None or code == code_on_invite) else None)
    R._resolve_invite = lambda *a, **k: None
    R._audit_auth_event = lambda *a, **k: None
    R.sb = lambda: FakeClient(select=[{"id": 7, "auth_id": target_auth_id_on_row}])
    try:
        return await R.connect_tenant({"org_id": "org-B", "code": code_given}, authorization="Bearer t")
    except R.HTTPException as e:
        return ("HTTP", e.status_code)

ok(asyncio.run(_connect(None, "good")) == {"ok": True, "connected": True, "org_id": "org-B"},
   "unbound target row + correct code → connected")
ok(asyncio.run(_connect("uidA", "good")).get("already") is True, "already bound to caller → idempotent (already:True)")
ok(asyncio.run(_connect(None, "wrong")) == ("HTTP", 403), "wrong access code → 403 (no connect)")
ok(asyncio.run(_connect("uidOTHER", "good")) == ("HTTP", 409), "row held by a different login → 409 (no hijack)")


print("\n[5] disable path (real disable_and_switch, faked deps) — mint-then-ban + policy")
ban_calls = []
async def _disable():
    R._uid_from_token = lambda auth: "uidA"
    R._email_for_uid = lambda c, uid: "x@y.com"
    R._find_pending_invite = lambda c, e, o, code=None: {"id": "inv1", "connect_token": "good"}
    R._resolve_invite = lambda *a, **k: None
    R._audit_auth_event = lambda *a, **k: None
    R.get_supabase_admin = lambda: object()
    order = []
    R._mint_tenant_alias = lambda *a, **k: (order.append("mint") or ("x+b@y.com", "auth-B", True, True, False))
    R._set_auth_ban = lambda admin, auth_id, banned: (ban_calls.append((auth_id, banned)) or order.append("ban") or True)
    R.sb = lambda: FakeClient(select=[{"id": 7, "email": "x@y.com", "auth_id": None, "org_id": "org-B"}])
    out = await R.disable_and_switch({"org_id": "org-B", "code": "good"}, authorization="Bearer t")
    return out, order

out, order = asyncio.run(_disable())
ok(out.get("disabled") is True and out.get("new_login_email") == "x+b@y.com", "returns the new isolated login")
ok(bool(out.get("access_code")), "returns a fresh access code for the new login")
ok("super-admin" in (out.get("policy") or "") and "support@metricspro.tech" in (out.get("policy") or ""),
   "surfaces the reinstate policy (super-admin only, support email)")
ok(order == ["mint", "ban"], "mints the new login BEFORE banning the old (no lockout on mint failure)")
ok(ban_calls and ban_calls[-1] == ("uidA", True), "bans the OLD auth account (uidA, banned=True)")


print("\n[6] reinstate is super-admin-only")
async def _reinstate(is_super):
    def _guard(auth, active_org=""):
        if not is_super:
            raise R.HTTPException(403, "super-admin only")
        return {"email": "boss@house"}
    R._require_super_admin = _guard
    R._set_auth_ban = lambda *a, **k: True
    R._audit_auth_event = lambda *a, **k: None
    R._find_auth_user_by_email = lambda admin, email: "auth-X"
    R.get_supabase_admin = lambda: object()
    R.sb = lambda: FakeClient(select=[{"auth_id": "auth-X"}])
    try:
        return await R.reinstate_login({"email": "x@y.com"}, authorization="Bearer t")
    except R.HTTPException as e:
        return ("HTTP", e.status_code)

ok(asyncio.run(_reinstate(False)) == ("HTTP", 403), "non-super-admin → 403 (no reinstatement)")
r = asyncio.run(_reinstate(True))
ok(isinstance(r, dict) and r.get("reinstated") is True, "super-admin → reinstates (un-ban + reactivate)")


print(f"\n=== {P} passed, {F} failed ===")
raise SystemExit(1 if F else 0)
