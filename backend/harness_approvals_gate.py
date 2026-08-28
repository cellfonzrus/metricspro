"""Offline proof (no live DB, no network) for the Unified Approvals ACCESS GATE — the router-wide
`Depends(_require_member)` every /api/v1/approvals endpoint runs before its body.

THE BUG THIS EXISTS TO KEEP DEAD (live incident, masked ref 881ae411). approvals/router.py:15-19 and
chat/router.py:23-25 both define their gate as a thin wrapper whose body does

    from app.modules.storeops.router import _require_member as sm

and that name had never existed in storeops/router.py. Because the import lives INSIDE the function,
nothing failed at boot — `import app.modules.approvals.router` succeeded and the app served happily.
The name was only resolved when a REQUEST arrived, where it raised ImportError; ImportError is not an
HTTPException, so it escaped Starlette's inner handler, hit main.HardeningMiddleware, and came back as
the masked `A system error occurred. Reference: <8 hex>` 500. EVERY Approvals and Chat call answered
that way, for every caller including the company owner — while the page, which stored the failure in a
banner and left `rows` at [], still drew "Waiting on you → Nothing waiting. 🎉" underneath it.

So this proves two separate things:

  1. SYMBOL RESOLUTION. Every function-local `from app... import name` in the approvals + chat modules
     names something that actually exists, and every attribute reached through such an alias exists
     too. A deferred import is invisible to `import app.modules.approvals.router`, to `python -c
     "import ..."`, and to app boot — this section is the only thing that looks at it before a user
     does. That is the bug CLASS, not just the one instance.
  2. THE GATE'S DECISION. storeops.router._require_member fails CLOSED and delegates tenant resolution
     to the platform's canonical resolver: no token → 401, no membership in the acting tenant → 403,
     an ambiguous multi-tenant login → 409 (never a guess), a member → pass, a super-admin
     administering another tenant → pass. Proven against a faked identity layer, so it runs offline.

Run: `python3 harness_approvals_gate.py` from backend/.
"""
import ast
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import HTTPException  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  <- {detail}"))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# (1) Deferred-import symbol resolution across the approvals + chat modules
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# These modules are written almost entirely with function-local cross-module imports (deliberately —
# they avoid import cycles with storeops/closing/referral). The cost of that style is that a typo or a
# renamed helper survives every import-time check and only detonates on a live request, masked. This
# section pays that cost back: it resolves each one the way the request would.
MODULE_FILES = sorted(
    [Path("app/modules/approvals/router.py"), Path("app/modules/approvals/engine.py"),
     Path("app/modules/chat/router.py")]
    + sorted(Path("app/modules/approvals/adapters").glob("*.py"))
)


def _resolve(mod_name, attr):
    """Resolve `from mod_name import attr` the way Python does: an attribute on the package/module, or
    a submodule of it. Returns (ok, detail)."""
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:                       # the module itself is unimportable — also a real break
        return False, f"cannot import {mod_name}: {type(e).__name__}: {e}"
    if hasattr(mod, attr):
        return True, ""
    try:
        importlib.import_module(f"{mod_name}.{attr}")
        return True, ""
    except Exception:
        return False, f"{mod_name} has no attribute '{attr}'"


def _scan(path):
    """(deferred_import_count, [(where, problem)]) for one file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    problems, deferred = [], 0
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        aliases = {}                              # local name -> module it points at
        for node in ast.walk(fn):
            if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith("app."):
                continue
            for a in node.names:
                deferred += 1
                ok, why = _resolve(node.module, a.name)
                if not ok:
                    problems.append((f"{path}:{node.lineno}", why))
                elif a.asname or a.name:
                    aliases[a.asname or a.name] = f"{node.module}.{a.name}"
        # Attributes reached THROUGH such an alias (`S._apply_timeclock_permission_decision(...)`) are
        # the same failure mode one step later — a decision that only breaks when someone approves.
        for node in ast.walk(fn):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id in aliases):
                target = aliases[node.value.id]
                ok, _ = _resolve(target, node.attr)
                if not ok:
                    # `target` may be a function (not a module) — only flag when it IS a module.
                    try:
                        importlib.import_module(target)
                    except Exception:
                        continue
                    problems.append((f"{path}:{node.lineno}", f"{target} has no attribute '{node.attr}'"))
    return deferred, problems


print("\n(1) Every deferred cross-module import in approvals + chat resolves")
total_deferred, all_problems = 0, []
for f in MODULE_FILES:
    n, probs = _scan(f)
    total_deferred += n
    all_problems += probs
    check(f"1_ {f.as_posix()} — {n} deferred import(s) resolve",
          not probs, "; ".join(f"{w}: {p}" for w, p in probs))
check(f"1z the scan actually looked at something ({total_deferred} deferred imports)", total_deferred > 10,
      total_deferred)

# The exact symbol the incident turned on. Named explicitly so a future rename of storeops'
# _require_member fails HERE, loudly, instead of on the owner's Approvals page as a masked 500.
ok, why = _resolve("app.modules.storeops.router", "_require_member")
check("1r storeops.router._require_member exists (the 881ae411 symbol)", ok, why)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# (2) The gate's decision — fail closed, and never guess a tenant
# ══════════════════════════════════════════════════════════════════════════════════════════════════
import app.core.tenant_middleware as TM            # noqa: E402
import app.modules.approvals.router as A           # noqa: E402
import app.modules.core.router as CORE             # noqa: E402
import app.modules.storeops.router as S            # noqa: E402

ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-000000000009"

_real_uid, _real_lookup = CORE._uid_from_token, TM.caller_app_user_http


def fake_identity(uid, row):
    """Stand in for the two I/O calls the gate makes: token -> uid, and uid -> the app_users row for
    the tenant the middleware already validated. `row` may be an Exception to raise (the real resolver
    raises HTTPException(409) for an ambiguous multi-tenant login)."""
    CORE._uid_from_token = lambda _auth: uid

    def _lookup(_uid, _cols="org_id,email,role,super_admin"):
        if isinstance(row, Exception):
            raise row
        return row
    TM.caller_app_user_http = _lookup


def status_of(fn, *a):
    """The HTTP status `fn` refuses with, or 'pass' when it returns. An ImportError (the incident) is
    reported as itself so it can never be mistaken for a clean refusal. A MISSING gate is reported the
    same way rather than crashing the run — section (1) has already named it, and the house rule is
    that a harness ends in a count, not a traceback."""
    if fn is None:
        return "the gate function does not exist"
    try:
        fn(*a)
        return "pass"
    except HTTPException as e:
        return e.status_code
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def _gate(mod):
    return getattr(mod, "_require_member", None)


try:
    print("\n(2) storeops._require_member — the shared gate both routers mount")

    fake_identity(None, None)                     # no/invalid token -> no uid
    check("2a not signed in -> 401", status_of(_gate(S), "", ORG) == 401,
          status_of(_gate(S), "", ORG))

    fake_identity("uid-1", None)                  # signed in, but no membership row in this tenant
    check("2b signed in, not a member of the acting tenant -> 403",
          status_of(_gate(S), "Bearer t", ORG) == 403,
          status_of(_gate(S), "Bearer t", ORG))

    fake_identity("uid-1", {"org_id": ORG, "email": "rep@x.com", "role": "rep", "employee_id": "E1"})
    check("2c an ordinary member passes (a member is not required to be a manager)",
          status_of(_gate(S), "Bearer t", ORG) == "pass")
    check("2c2 ...and the caller's row is returned for the endpoint to use",
          ((_gate(S) or (lambda *_a: {}))("Bearer t", ORG) or {}).get("employee_id") == "E1")

    # A super-admin administering another tenant: caller_app_user pins org_id to the acting tenant and
    # returns their own row. The gate must honour that — cross-tenant administration is intentional,
    # and refusing it here is exactly the 2026-08-10 POS outage (owner 403'd on every endpoint).
    fake_identity("uid-super", {"org_id": OTHER_ORG, "email": "owner@x.com", "role": "admin",
                                "employee_id": "E0"})
    check("2d a super-admin acting on another tenant passes",
          status_of(_gate(S), "Bearer t", OTHER_ORG) == "pass")

    # Ambiguity is a REFUSAL, not a coin toss: answering a request for company X with company Y's data
    # is the multi-tenant leak this platform treats as absolute.
    fake_identity("uid-multi", HTTPException(409, "choose a company"))
    check("2e an ambiguous multi-tenant login -> 409 (never a guessed tenant)",
          status_of(_gate(S), "Bearer t", ORG) == 409,
          status_of(_gate(S), "Bearer t", ORG))

    # THE REGRESSION ITSELF. The approvals router's own wrapper must reach the storeops gate at CALL
    # time. Before the fix this raised ImportError here — i.e. a masked 500 on the live page — rather
    # than the honest 401 a signed-out caller earns.
    print("\n(3) The approvals router's wrapper reaches it AT REQUEST TIME (ref 881ae411)")
    fake_identity(None, None)
    got = status_of(_gate(A), "", ORG)
    check("3a approvals._require_member('') -> 401, NOT ImportError/500", got == 401, got)
    fake_identity("uid-1", {"org_id": ORG, "email": "rep@x.com", "role": "rep", "employee_id": "E1"})
    got = status_of(_gate(A), "Bearer t", ORG)
    check("3b approvals._require_member(member) -> passes", got == "pass", got)
finally:
    CORE._uid_from_token, TM.caller_app_user_http = _real_uid, _real_lookup


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# (4) The gate is actually mounted — on the ROUTER, so it cannot be forgotten on a new endpoint
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n(4) Every approvals route is behind the gate")
gate_names = {getattr(d.dependency, "__name__", "") for d in (A.router.dependencies or [])}
check("4a the approvals router mounts _require_member router-wide", "_require_member" in gate_names,
      gate_names)
routes = [r for r in A.router.routes if getattr(r, "path", "").startswith("/approvals")]
check(f"4b all {len(routes)} approvals route(s) inherit it", len(routes) >= 5, len(routes))
check("4c the engine is registered with the type adapters loaded",
      len(A.engine.registered_types()) >= 9, sorted(A.engine.registered_types()))


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
