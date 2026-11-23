"""A declared-public path must reach its HANDLER through the real app, not just the real router.

The companion unit test (tests/unit/test_public_path_gate_standdown.py) proves the POS router's
gate stands down. This proves it end to end on the assembled application — middleware, router-level
dependency and route in the same stack a browser meets — because the 2026-11-23 W-9 lockout was a
defect of COMPOSITION: the middleware allowlisted the path and the router gate refused it anyway,
and neither component was wrong on its own.

Measured on production before the fix:
    GET /api/v1/pos/public/w9/<token> -> 401 {"detail":"sign in to perform this action"}

The assertion is deliberately NOT "200". There is no database in this suite (conftest blocks the
network), so the handler fails after it is reached. What must never come back is an AUTH refusal:
reaching the handler is the whole property, and the token is what decides the rest.
"""
import pytest

AUTH_REFUSALS = (401, 403)


@pytest.mark.parametrize("path", [
    "/api/v1/pos/public/w9/a-token-that-does-not-exist",
])
def test_the_w9_return_link_is_not_refused_by_auth(api_client, path):
    r = api_client.get(path)
    assert r.status_code not in AUTH_REFUSALS, f"{path} -> {r.status_code} {r.text[:200]}"
    assert "sign in to perform this action" not in r.text


def test_a_gated_pos_path_is_still_refused(api_client):
    r = api_client.get("/api/v1/pos/catalog")
    assert r.status_code in AUTH_REFUSALS


def test_every_declared_public_route_the_app_mounts_is_not_auth_refused(api_client):
    """Derived from the live route table and the one home — so a public path added tomorrow under a
    gated router is covered by this test with no edit. A path whose handler verifies a caller of its
    own is excused by the DECLARATION, never by a list kept here."""
    import re

    from app.core import tenant_middleware as tm

    app = api_client.app
    checked, refused = 0, []
    for route in app.routes:
        path = getattr(route, "path", "")
        if "GET" not in (getattr(route, "methods", None) or set()):
            continue
        probe = re.sub(r"\{[^}]+\}", "1", path)
        if not tm.public_surface_allows(probe, "GET"):
            continue
        if tm.public_path_verifies_its_own_caller(probe):
            continue
        checked += 1
        status = api_client.get(probe).status_code
        if status in AUTH_REFUSALS:
            refused.append(f"GET {probe} -> {status}")
    assert not refused, "declared public, answered with an auth refusal: " + "; ".join(refused)
    assert checked >= 1, "no declared-public GET route was found — the derivation stopped working"
