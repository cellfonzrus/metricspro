"""Proof harness for the P0 login-latency package (agent/core/perf-p0-latency).

Runs the ACTUAL shipped handlers in app.modules.core.router against a fake Supabase client (same
convention as harness_rule4_pii_export.py) — no DB, no network. Run from backend/:
    python3 harness_core_bootstrap.py

Proves:
  TASK B — _uid_from_token cache:
    1. Same token twice within the TTL → exactly ONE network auth.get_user call (positive cached).
    2. A different token verifies independently (per-token entries).
    3. Failed verification is NEVER cached (negative results retry every call).
    4. The cache is bounded (never grows past _UID_CACHE_MAX entries).
  TASK C — /core/bootstrap composition & parity with the old endpoints:
    5. No/bad token → HTTPException 401 (self-gating, mirroring /me).
    6. >1 membership + NO valid x-active-org → me:null + full tenants list (picker flow), and an
       INVALID x-active-org is treated the same (mirrors the frontend, which never called /me there).
    7. >1 membership + VALID x-active-org → me == the EXACT /me payload for that org (same helper).
    8. single membership + x-active-org naming some OTHER org → falls back to the one membership,
       byte-identical to /me with the same headers (pick_membership fallback semantics).
    9. zero memberships → me == the unprovisioned /me shape; active_org null.
   10. Old endpoints stay intact: /my-tenants and /pending-connections payloads == the bootstrap's
       tenants/pending fields (ONE shared source, no drift), and rbac_enabled flows through.
"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")

import app.modules.core.router as rt  # noqa: E402
from fastapi import HTTPException  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


# ── fakes ──────────────────────────────────────────────────────────────────────────────────────
class FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def execute(self):
        return SimpleNamespace(data=list(self._rows))

    def __getattr__(self, _name):          # select/eq/order/limit/in_/update/upsert/... → chain
        return lambda *a, **k: self


class FakeClient:
    def __init__(self, tables):
        self.tables = tables

    def schema(self, _name):
        return self

    def table(self, name):
        return FakeQuery(self.tables.get(name, []))


UID = "uid-00000000-0000-0000-0000-000000000042"
ORG_A = "aaaaaaaa-0000-0000-0000-000000000001"
ORG_B = "bbbbbbbb-0000-0000-0000-000000000002"


def mem_row(org, default):
    return {"id": f"row-{org[:4]}", "auth_id": UID, "org_id": org, "email": "x@y.com",
            "role": None, "is_default_org": default, "is_active": True, "super_admin": False,
            "created_at": "2026-01-01T00:00:00+00:00"}


def tables_for(mem_rows):
    return {
        "app_users": mem_rows,
        "tenants": [{"org_id": ORG_A, "name": "Alpha", "slug": "alpha"},
                    {"org_id": ORG_B, "name": "Beta", "slug": "beta"}],
        "app_config": [{"rbac_enabled": True}],
        "roles": [],
        "account_link_invite": [{"org_id": ORG_B, "email": "x@y.com", "status": "pending",
                                 "created_at": "2026-07-01T00:00:00+00:00"}],
    }


def wire(mem_rows):
    fake = FakeClient(tables_for(mem_rows))
    rt.sb = lambda: fake
    rt.needs_sync = lambda c, o: False     # entitlement sweep is out of scope here
    return fake


run = asyncio.run

# ── TASK B: _uid_from_token cache (REAL function, counting fake network) ───────────────────────
calls = {"n": 0, "mode": "ok"}


class _FakeAuthAdmin:
    class auth:                                            # noqa: N801 — mirrors client.auth shape
        @staticmethod
        def get_user(token):
            calls["n"] += 1
            if calls["mode"] == "fail":
                raise RuntimeError("verify down")
            return SimpleNamespace(user=SimpleNamespace(id=UID))


rt.get_supabase_admin = lambda: _FakeAuthAdmin()
rt._uid_cache.clear()
u1 = rt._uid_from_token("Bearer tok-1")
u2 = rt._uid_from_token("Bearer tok-1")
check("1. same token twice → ONE network verify, cached uid", u1 == UID and u2 == UID and calls["n"] == 1,
      f"calls={calls['n']}")
rt._uid_from_token("Bearer tok-2")
check("2. different token verifies independently", calls["n"] == 2, f"calls={calls['n']}")
calls["mode"] = "fail"
before = calls["n"]
bad1 = rt._uid_from_token("Bearer tok-bad")
bad2 = rt._uid_from_token("Bearer tok-bad")
check("3. failures never cached (retried each call)", bad1 is None and bad2 is None
      and calls["n"] == before + 2, f"calls={calls['n']}")
calls["mode"] = "ok"
rt._uid_cache.clear()
for i in range(rt._UID_CACHE_MAX + 50):
    rt._uid_from_token(f"Bearer flood-{i}")
check("4. cache stays bounded", len(rt._uid_cache) <= rt._UID_CACHE_MAX, f"size={len(rt._uid_cache)}")

# ── TASK C: bootstrap composition — endpoint-level token gate via a patched verifier ───────────
rt._uid_from_token = lambda auth: (UID if auth == "Bearer good" else None)

wire([mem_row(ORG_A, True), mem_row(ORG_B, False)])
try:
    run(rt.bootstrap(authorization="Bearer nope", x_active_org="", x_2fa_token=""))
    check("5. bad token → 401", False)
except HTTPException as e:
    check("5. bad token → 401", e.status_code == 401)

b = run(rt.bootstrap(authorization="Bearer good", x_active_org="", x_2fa_token=""))
check("6a. >1 membership, no choice → me null (picker)", b["me"] is None and b["active_org"] is None)
check("6b. tenants list still full for the picker", b["tenants"]["count"] == 2
      and b["tenants"]["default_org"] == ORG_A)
b_inv = run(rt.bootstrap(authorization="Bearer good", x_active_org="ffffffff-not-a-member", x_2fa_token=""))
check("6c. INVALID x-active-org treated as no-choice", b_inv["me"] is None)

b2 = run(rt.bootstrap(authorization="Bearer good", x_active_org=ORG_B, x_2fa_token=""))
me_direct = run(rt.whoami(authorization="Bearer good", x_active_org=ORG_B, x_2fa_token=""))
check("7a. valid choice → me resolved for THAT org",
      (b2["me"] or {}).get("user", {}).get("org_id") == ORG_B and b2["active_org"] == ORG_B)
check("7b. bootstrap me == /me payload (same shared helper)", b2["me"] == me_direct)

wire([mem_row(ORG_A, True)])
b3 = run(rt.bootstrap(authorization="Bearer good", x_active_org=ORG_B, x_2fa_token=""))
me3 = run(rt.whoami(authorization="Bearer good", x_active_org=ORG_B, x_2fa_token=""))
check("8a. single membership: stray x-active-org falls back to the one org",
      (b3["me"] or {}).get("user", {}).get("org_id") == ORG_A and b3["active_org"] == ORG_A)
check("8b. fallback byte-identical to /me", b3["me"] == me3)

wire([])
b4 = run(rt.bootstrap(authorization="Bearer good", x_active_org="", x_2fa_token=""))
me4 = run(rt.whoami(authorization="Bearer good", x_active_org="", x_2fa_token=""))
check("9. zero memberships → unprovisioned me shape, null active_org",
      b4["me"] == me4 == {"provisioned": False, "user": None, "permissions": {}}
      and b4["active_org"] is None and b4["tenants"]["count"] == 0)

wire([mem_row(ORG_A, True), mem_row(ORG_B, False)])
b5 = run(rt.bootstrap(authorization="Bearer good", x_active_org="", x_2fa_token=""))
t_direct = run(rt.my_tenants(authorization="Bearer good"))
p_direct = run(rt.pending_connections(authorization="Bearer good"))
check("10a. /my-tenants unchanged and == bootstrap.tenants", b5["tenants"] == t_direct)
check("10b. /pending-connections unchanged and == bootstrap.pending",
      b5["pending"] == p_direct and len(p_direct["pending"]) == 1)
check("10c. rbac_enabled flows through", b5["rbac_enabled"] is True
      and run(rt.get_auth_config()) == {"rbac_enabled": True})

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
