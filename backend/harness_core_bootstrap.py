"""Proof harness for the login-latency packages (P0 perf + agent/platform-core/login-bootstrap-perf).

Runs the ACTUAL shipped handlers in app.modules.core.router against a fake Supabase client (same
convention as harness_rule4_pii_export.py) — no DB, no network. The fake honors .eq/.in_/.limit
filters so keyed-map lookups and per-row queries are distinguishable. Run from backend/:
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
    7. >1 membership + VALID x-active-org → me == the EXACT /me payload for that org (same helper —
       this now also proves the batched tenant_row/role_map path returns byte-identical
       tenant/permissions to /me's per-query path).
    8. single membership + x-active-org naming some OTHER org → falls back to the one membership,
       byte-identical to /me with the same headers (pick_membership fallback semantics).
    9. zero memberships → me == the unprovisioned /me shape; active_org null.
   10. Old endpoints stay intact: /my-tenants and /pending-connections payloads == the bootstrap's
       tenants/pending fields (ONE shared source, no drift), and rbac_enabled flows through.
  LOGIN-BOOTSTRAP-PERF — batched fetches + deferred writes (2026-07-29 package):
   11. ONE bootstrap call touches app_users exactly once (membership fetch — the email re-read and
       the inline last_login write are gone), roles exactly once (batched .in_, was display+perms
       queries), tenants exactly twice (one batched .in_ + the per-invite name lookup).
   12. The response performs ZERO writes; exactly ONE background task is queued and executing it
       stamps last_login (the deferred post-login write).
   13. Degraded mode: if the batched tenants read raises, bootstrap still answers and stays
       byte-identical to /me under the same failure (per-helper fallbacks).
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")

import app.modules.core.router as rt  # noqa: E402
from fastapi import HTTPException, BackgroundTasks  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


# ── fakes ──────────────────────────────────────────────────────────────────────────────────────
class FakeQuery:
    """Honors .eq/.in_/.limit so keyed and per-row query paths return what a real DB would;
    records write ops (update/upsert) on the owning client. Everything else chains as a no-op."""

    def __init__(self, client, table, rows):
        self._client, self._table, self._rows = client, table, list(rows)

    def eq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def in_(self, col, vals):
        vals = set(vals)
        self._rows = [r for r in self._rows if r.get(col) in vals]
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def update(self, patch):
        self._client.ops.append(("update", self._table, dict(patch)))
        return self

    def upsert(self, *a, **k):
        self._client.ops.append(("upsert", self._table, None))
        return self

    def execute(self):
        return SimpleNamespace(data=list(self._rows))

    def __getattr__(self, _name):          # select/order/rpc/... → chain
        return lambda *a, **k: self


class FakeClient:
    def __init__(self, tables, fail_tables=()):
        self.tables = tables
        self.fail_tables = set(fail_tables)   # table names whose reads raise (degraded mode)
        self.calls = []                        # every .table() touch, in order
        self.ops = []                          # every write op

    def schema(self, _name):
        return self

    def table(self, name):
        self.calls.append(name)
        if name in self.fail_tables:
            raise RuntimeError(f"{name} read down (harness)")
        return FakeQuery(self, name, self.tables.get(name, []))


UID = "uid-00000000-0000-0000-0000-000000000042"
ORG_A = "aaaaaaaa-0000-0000-0000-000000000001"
ORG_B = "bbbbbbbb-0000-0000-0000-000000000002"


def mem_row(org, default):
    return {"id": f"row-{org[:4]}", "auth_id": UID, "org_id": org, "email": "x@y.com",
            "role": "admin", "is_default_org": default, "is_active": True, "super_admin": False,
            "created_at": "2026-01-01T00:00:00+00:00"}


def tables_for(mem_rows):
    # Role content is IDENTICAL for both orgs so /me's per-query path and bootstrap's keyed
    # role_map must produce the same payload (parity checks 7/8 compare them directly).
    return {
        "app_users": mem_rows,
        "tenants": [{"org_id": ORG_A, "name": "Alpha", "slug": "alpha"},
                    {"org_id": ORG_B, "name": "Beta", "slug": "beta"}],
        "app_config": [{"id": 1, "rbac_enabled": True}],
        "roles": [{"org_id": ORG_A, "name": "admin", "display_name": "Admin",
                   "permissions": {"modules": {"helpdesk": True}}},
                  {"org_id": ORG_B, "name": "admin", "display_name": "Admin",
                   "permissions": {"modules": {"helpdesk": True}}}],
        "account_link_invite": [{"org_id": ORG_B, "email": "x@y.com", "status": "pending",
                                 "created_at": "2026-07-01T00:00:00+00:00"}],
    }


def wire(mem_rows, fail_tables=()):
    # Fixture tenants rows carry NO seed_version key → seed_stale is False (mirrors needs_sync's
    # pre-mig-076 behaviour), so the deferred sync path stays off; only last_login is queued.
    fake = FakeClient(tables_for(mem_rows), fail_tables=fail_tables)
    rt.sb = lambda: fake
    return fake


def bt():
    return BackgroundTasks()


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
    rt.bootstrap(bt(), authorization="Bearer nope", x_active_org="", x_2fa_token="")
    check("5. bad token → 401", False)
except HTTPException as e:
    check("5. bad token → 401", e.status_code == 401)

b = rt.bootstrap(bt(), authorization="Bearer good", x_active_org="", x_2fa_token="")
check("6a. >1 membership, no choice → me null (picker)", b["me"] is None and b["active_org"] is None)
check("6b. tenants list still full for the picker", b["tenants"]["count"] == 2
      and b["tenants"]["default_org"] == ORG_A)
b_inv = rt.bootstrap(bt(), authorization="Bearer good", x_active_org="ffffffff-not-a-member", x_2fa_token="")
check("6c. INVALID x-active-org treated as no-choice", b_inv["me"] is None)

b2 = rt.bootstrap(bt(), authorization="Bearer good", x_active_org=ORG_B, x_2fa_token="")
me_direct = rt.whoami(bt(), authorization="Bearer good", x_active_org=ORG_B, x_2fa_token="")
check("7a. valid choice → me resolved for THAT org",
      (b2["me"] or {}).get("user", {}).get("org_id") == ORG_B and b2["active_org"] == ORG_B)
check("7b. bootstrap me == /me payload (same shared helper; batched maps == per-query)",
      b2["me"] == me_direct)
check("7c. permissions flow through the batched role_map",
      (b2["me"] or {}).get("permissions") == {"modules": {"helpdesk": True}})

wire([mem_row(ORG_A, True)])
b3 = rt.bootstrap(bt(), authorization="Bearer good", x_active_org=ORG_B, x_2fa_token="")
me3 = rt.whoami(bt(), authorization="Bearer good", x_active_org=ORG_B, x_2fa_token="")
check("8a. single membership: stray x-active-org falls back to the one org",
      (b3["me"] or {}).get("user", {}).get("org_id") == ORG_A and b3["active_org"] == ORG_A)
check("8b. fallback byte-identical to /me", b3["me"] == me3)

wire([])
b4 = rt.bootstrap(bt(), authorization="Bearer good", x_active_org="", x_2fa_token="")
me4 = rt.whoami(bt(), authorization="Bearer good", x_active_org="", x_2fa_token="")
check("9. zero memberships → unprovisioned me shape, null active_org",
      b4["me"] == me4 == {"provisioned": False, "user": None, "permissions": {}}
      and b4["active_org"] is None and b4["tenants"]["count"] == 0)

wire([mem_row(ORG_A, True), mem_row(ORG_B, False)])
b5 = rt.bootstrap(bt(), authorization="Bearer good", x_active_org="", x_2fa_token="")
t_direct = rt.my_tenants(authorization="Bearer good")
p_direct = rt.pending_connections(authorization="Bearer good")
check("10a. /my-tenants unchanged and == bootstrap.tenants", b5["tenants"] == t_direct)
check("10b. /pending-connections unchanged and == bootstrap.pending",
      b5["pending"] == p_direct and len(p_direct["pending"]) == 1)
check("10c. rbac_enabled flows through", b5["rbac_enabled"] is True
      and rt.get_auth_config() == {"rbac_enabled": True})

# ── LOGIN-BOOTSTRAP-PERF: batched fetches + deferred writes ────────────────────────────────────
fake = wire([mem_row(ORG_A, True)])
tasks = bt()
b6 = rt.bootstrap(tasks, authorization="Bearer good", x_active_org="", x_2fa_token="")
counts = {t: fake.calls.count(t) for t in ("app_users", "tenants", "roles")}
check("11a. app_users touched ONCE (email reused, last_login deferred)",
      counts["app_users"] == 1, f"calls={fake.calls}")
check("11b. roles touched ONCE (batched .in_ replaces display+perms queries)",
      counts["roles"] == 1, f"calls={fake.calls}")
check("11c. tenants touched TWICE (one batch + one per-invite name lookup)",
      counts["tenants"] == 2, f"calls={fake.calls}")
check("12a. the response performed ZERO writes", fake.ops == [], f"ops={fake.ops}")
check("12b. exactly ONE background task queued (post-login writes)", len(tasks.tasks) == 1)
tasks.tasks[0].func(*tasks.tasks[0].args)
check("12c. executing it stamps last_login on app_users",
      [op[:2] for op in fake.ops] == [("update", "app_users")]
      and "last_login" in (fake.ops[0][2] or {}), f"ops={fake.ops}")
check("12d. bootstrap payload intact under the perf path",
      (b6["me"] or {}).get("user", {}).get("org_id") == ORG_A
      and b6["me"]["permissions"] == {"modules": {"helpdesk": True}})

wire([mem_row(ORG_A, True)], fail_tables=("tenants",))
b7 = rt.bootstrap(bt(), authorization="Bearer good", x_active_org="", x_2fa_token="")
me7 = rt.whoami(bt(), authorization="Bearer good", x_active_org="", x_2fa_token="")
check("13. batched tenants read down → bootstrap still answers, byte-identical to /me",
      b7["me"] == me7 and (b7["me"] or {}).get("user", {}).get("org_id") == ORG_A
      and b7["tenants"]["tenants"][0]["name"] == "Tenant")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
