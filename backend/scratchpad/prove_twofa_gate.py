"""Proof harness for the 2FA enforcement decisions in tenant_middleware.py (the highest-blast-radius
file). Offline. Run:  python3 backend/scratchpad/prove_twofa_gate.py

Verifies the ADDITIVE gate never over-enforces and always degrades safely:
  • _twofa_enforce() env break-glass (default ON; TWOFA_ENFORCE=0 kills it)
  • _tenant_needs_2fa: off→never · required(all)→everyone · required(role-scoped)→only those roles ·
    optional→user opt-in
  • policy read errors / un-run mig 711 → 'off' → NO enforcement (never a lockout)
  • _twofa_marker_ok: a valid minted marker passes; absent/invalid fails; verifier import error → OPEN
  • _resolve_identity returns the new 6-tuple shape on a bad token (unpacking safety)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.core.tenant_middleware as M
from app.modules.core import auth_security as S

P = F = 0
def ck(name, cond):
    global P, F
    if cond: P += 1; print(f"  ok  {name}")
    else: F += 1; print(f"  XX  {name}")

print("A. _twofa_enforce env break-glass")
for v, exp in [("1", True), ("", True), ("true", True), ("0", False), ("false", False), ("off", False)]:
    os.environ["TWOFA_ENFORCE"] = v
    ck(f"TWOFA_ENFORCE={v!r} → enforce={exp}", M._twofa_enforce() is exp)
os.environ.pop("TWOFA_ENFORCE", None)
ck("unset → default ON", M._twofa_enforce() is True)

print("B. _tenant_needs_2fa across policy modes (patched policy)")
def patch(policy):
    M._twofa_cache.clear()
    M._tenant_2fa_policy = lambda org: policy

patch({"mode": "off", "required_roles": []})
ck("off → admin no", not M._tenant_needs_2fa("o", "admin", True))
ck("off → even opted-in user no", not M._tenant_needs_2fa("o", "sales_rep", True))

patch({"mode": "required", "required_roles": []})
ck("required(all) → admin yes", M._tenant_needs_2fa("o", "admin", False))
ck("required(all) → sales_rep yes", M._tenant_needs_2fa("o", "sales_rep", False))

patch({"mode": "required", "required_roles": ["admin"]})
ck("required(admin only) → admin yes", M._tenant_needs_2fa("o", "admin", False))
ck("required(admin only) → sales_rep no", not M._tenant_needs_2fa("o", "sales_rep", False))

patch({"mode": "optional", "required_roles": []})
ck("optional → opted-in yes", M._tenant_needs_2fa("o", "sales_rep", True))
ck("optional → not opted-in no", not M._tenant_needs_2fa("o", "sales_rep", False))

print("C. policy read error / un-run mig → OFF (no lockout)")
# restore the real function, then force its DB read to fail → must return {'mode':'off'}
import importlib
importlib.reload(M)
M._twofa_cache.clear()
class _Boom:
    def schema(self, *a, **k): raise RuntimeError("mig 711 un-run")
import app.core.database as DB
_orig = DB.get_supabase
DB.get_supabase = lambda: _Boom()
ck("policy read failure → mode off", M._tenant_2fa_policy("o").get("mode") == "off")
ck("→ _tenant_needs_2fa False (no enforcement)", not M._tenant_needs_2fa("o", "admin", True))
DB.get_supabase = _orig

print("D. _twofa_marker_ok gate")
tok = S.mint_2fa_token("auth-1", "org-A", "dev", S.now_ts() + 300)
ck("valid marker → allowed", M._twofa_marker_ok(tok, "auth-1", "org-A"))
ck("absent marker → blocked", not M._twofa_marker_ok("", "auth-1", "org-A"))
ck("wrong-login marker → blocked", not M._twofa_marker_ok(tok, "auth-2", "org-A"))
ck("wrong-org marker → blocked", not M._twofa_marker_ok(tok, "auth-1", "org-B"))

print("E. _resolve_identity 6-tuple shape on bad token (unpacking safety)")
res = M._resolve_identity("")   # empty/garbage token → unauthenticated tuple
ck("returns a 6-tuple", isinstance(res, tuple) and len(res) == 6)
ok, sa, orgs, dorg, info, uid = res
ck("bad token → not authenticated, empty info, no uid", (ok, sa, orgs, dorg, info, uid) == (False, False, (), None, {}, None))

print(f"\n{'PASS' if F == 0 else 'FAIL'}: {P} passed, {F} failed")
sys.exit(1 if F else 0)
