"""HARNESS — /core/my-tenants is on the public allowlist for GET only (mobile multi-tenant fix).

WHAT THIS PROTECTS. The native app calls GET /api/v1/core/my-tenants directly to list the companies a
login belongs to and drive the post-login tenant picker (the web uses the allowlisted /core/bootstrap,
which embeds the SAME payload). /core/my-tenants self-gates on the bearer token (401 without a valid
one) and resolves purely from the token's auth_id, so it works BEFORE an active tenant is chosen — but
it was never added to `_PUBLIC_EXACT`. So for a multi-company login with no active org yet, the tenant
middleware answered 409 tenant_choice_required at THIS endpoint, before the app could ever list its
companies: the picker and the in-app company switcher both went dark and every screen 409'd.

  1  GET /api/v1/core/my-tenants is public (bypasses the tenant gate; the handler self-gates on token)
  2  …for GET ONLY — POST/PUT/DELETE on the same path authenticate normally (no method inherits public)
  3  CONTROLS: /core/bootstrap and the /core/me prefix (its structural twins) are public GET too
  4  NEGATIVE: a sibling like /api/v1/core/members is NOT matched (no sloppy startswith over-match)
  5  the effective gate is `_is_public(path) AND _public_method_ok(path, method)` — both must hold

Pure/offline: the functions under test are side-effect free.
    python3 backend/harness_my_tenants_allowlist.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core import tenant_middleware as tm   # noqa: E402

_passed, _failed = 0, 0
MT = "/api/v1/core/my-tenants"


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}{(' — ' + str(detail)[:200]) if detail else ''}")


def public(path, method):
    """The middleware's effective test (see tenant_middleware ~line 1036): a request bypasses the
    tenant gate only when the path is allowlisted AND the method is one of its public methods."""
    return tm._is_public(path) and tm._public_method_ok(path, method)


def main():
    print(__doc__.splitlines()[0])

    # 1 — the fix.
    check("1a. /core/my-tenants is allowlisted", tm._is_public(MT) is True)
    check("1b. GET /core/my-tenants bypasses the tenant gate", public(MT, "GET") is True)

    # 2 — GET only; no other method inherits public.
    check("2a. POST /core/my-tenants is NOT public", public(MT, "POST") is False)
    check("2b. PUT /core/my-tenants is NOT public", public(MT, "PUT") is False)
    check("2c. DELETE /core/my-tenants is NOT public", public(MT, "DELETE") is False)
    check("2d. method scoping is explicit (GET only)",
          tm._PUBLIC_METHODS.get(MT) == ("GET",), tm._PUBLIC_METHODS.get(MT))

    # 3 — CONTROLS: the structural twins the app/web already rely on.
    check("3a. /core/bootstrap is public GET (its twin)", public("/api/v1/core/bootstrap", "GET") is True)
    check("3b. /core/me is public GET (its twin)", public("/api/v1/core/me", "GET") is True)

    # 4 — NEGATIVE: boundary-matched, so a different /core path is not caught.
    check("4a. /core/members is NOT public (no over-match)", tm._is_public("/api/v1/core/members") is False)
    check("4b. /core/my-tenants-secret is NOT public (exact match, not prefix)",
          tm._is_public("/api/v1/core/my-tenants-secret") is False)

    # 5 — a random authenticated route stays gated.
    check("5. a normal report route is not public", public("/api/v1/commcalc/exec-mtd/2026-09", "GET") is False)

    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
