"""A path the platform DECLARES public must not be refused by a router-level auth gate.

THE DEFECT THIS REPRODUCES (found from outside 2026-11-23, access probe run 327, module `pos`).

`GET /api/v1/pos/public/w9/{token}` is the link a wholesale customer is EMAILED to send back their
completed Form W-9. It is declared public in `tenant_middleware._PUBLIC_PREFIXES` — the one-use
token IS the auth. The deployed platform answered every anonymous caller:

    GET https://api.metricspro.tech/api/v1/pos/public/w9/<token>
    → 401 {"detail":"sign in to perform this action"}

Not the declared 404-on-a-dead-token: a flat refusal, with the token never read. The `pos`
APIRouter carries `dependencies=[Depends(_require_pos_access)]`, a router-level dependency runs
BEFORE the endpoint body, and that gate asked for a signed-in member without ever asking whether
the platform had declared this path public. So 100% of W-9 return links were dead.

THE CLASS, not the instance: "is this path public" has ONE home
(`tenant_middleware.public_surface_allows`) and a router-level auth gate is a SECOND, independent
gate that did not dereference it. Every router carrying a router-level gate has the same hole for
every public path mounted under it — today and for every public path added tomorrow.

NO LOCKOUT is a defect of equal weight to a leak (security-testing-agent). These tests are the
regression, and `harness_router_gate_dereference.py` is the lock that stops a gate un-wiring.
"""
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import tenant_middleware as tm


# ── the one home answers path + method together ────────────────────────────────────────────────
def test_public_surface_allows_the_declared_w9_return_link():
    assert tm.public_surface_allows("/api/v1/pos/public/w9/abc123", "GET") is True
    assert tm.public_surface_allows("/api/v1/pos/public/w9/abc123/upload", "POST") is True


def test_public_surface_allows_refuses_anything_not_declared():
    assert tm.public_surface_allows("/api/v1/pos/products", "GET") is False
    assert tm.public_surface_allows("/api/v1/pos/public/customers", "GET") is False
    # a path next to the prefix but not under it — no sloppy startswith
    assert tm.public_surface_allows("/api/v1/pos/public/w9extra/abc", "GET") is False


def test_public_surface_allows_honours_the_method_scoping():
    # /core/auth-config is public for GET only: the PUT flips the global enforce-login flag.
    assert tm.public_surface_allows("/api/v1/core/auth-config", "GET") is True
    assert tm.public_surface_allows("/api/v1/core/auth-config", "PUT") is False


def test_public_surface_allows_is_the_middlewares_own_answer():
    """Not a second copy: the middleware's own branch must be this function."""
    for path, method in (("/health", "GET"),
                         ("/api/v1/core/auth-config", "GET"),
                         ("/api/v1/core/auth-config", "PUT"),
                         ("/api/v1/pos/public/w9/t", "GET"),
                         ("/api/v1/pos/products", "GET")):
        expected = tm._is_public(path) and tm._public_method_ok(path, method)
        assert tm.public_surface_allows(path, method) is bool(expected)


# ── the gates stand down for a declared-public path, and ONLY for one ───────────────────────────
def _client(router):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def test_pos_router_gate_stands_down_on_the_public_w9_link():
    """THE REGRESSION. Anonymous, no middleware, no database: the gate must not be what answers."""
    from app.modules.pos import router as pos_router
    r = _client(pos_router.router).get("/api/v1/pos/public/w9/sometoken")
    assert r.status_code != 401, (
        "the declared-public W-9 return link was refused by the router-level POS gate: "
        f"{r.status_code} {r.text[:160]}")
    assert "sign in to perform this action" not in r.text


def test_pos_router_gate_still_refuses_a_gated_path():
    """NOT a hole: standing down happens only where the declaration says public."""
    from app.modules.pos import router as pos_router
    r = _client(pos_router.router).get("/api/v1/pos/catalog")
    assert r.status_code == 401


def test_the_gate_stands_down_on_the_declared_paths_and_no_others():
    """Derived, not listed: for every GET route the POS router mounts, the gate must answer 401
    unless the platform declared that path public. A stand-down that over-matches is the hole."""
    from app.modules.pos import router as pos_router
    client = _client(pos_router.router)
    checked = 0
    for route in pos_router.router.routes:
        path = getattr(route, "path", "")
        if "GET" not in (getattr(route, "methods", None) or set()):
            continue
        probe = re.sub(r"\{[^}]+\}", "1", "/api/v1" + path)
        declared = tm.public_surface_allows(probe, "GET")
        status = client.get(probe).status_code
        checked += 1
        if declared:
            assert status != 401, f"declared public but refused: {probe} -> {status}"
        else:
            assert status == 401, f"gated but the gate did not answer: {probe} -> {status}"
    assert checked > 50, f"only {checked} GET routes seen - the derivation stopped working"
