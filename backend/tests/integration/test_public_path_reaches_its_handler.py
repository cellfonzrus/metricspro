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

★ THIS CONTROL WAS MEASURING ALMOST NOTHING (found 2026-11-26, access probe run 330, §CORE-52) ★

The derived sweep below walked `app.routes` and read `.path` and `.methods` off each entry. It swept
FIVE paths — /docs, /docs/oauth2-redirect, /openapi.json, /redoc and /health — of an app serving
1,558, and not one module route among them. Of the 21 declared-public GETs the app actually serves,
it checked one. Its own non-vacuity guard, `checked >= 1`, was satisfied by /health, so the control
reported green for three days while guarding nothing but the docs pages.

TWO failures at once, which is why neither showed:
  1. `app.routes` holds one lazy `_IncludedRouter` per included router, with no `.path` and no
     `.methods` — so every module route was skipped, silently.
  2. Flattening that (the known fix, documented in nine other files) still yields PREFIX-STRIPPED
     paths — `/remediation/whatsapp-webhook`, never `/api/v1/remediation/whatsapp-webhook`. Every
     declaration here is keyed by the SERVED path and is DEFAULT-DENY, so a relative path is
     answered `False` = "not declared public", and the route is skipped again. A control wired this
     way cannot fail.

The served route table now has ONE home, `app/core/route_table.py`, which both this control and the
outside probe read; the floor below is the tripwire the old guard should have been.
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


def _declared_public_gets(api_client):
    """Every path the app SERVES that the declaration calls public for GET, minus the ones whose
    handler verifies a caller of its own.

    The served path comes from `route_table.route_surface` — the ONE home — because this derivation
    had TWO silent failures at once before 2026-11-26 and neither had a symptom (see the module
    docstring)."""
    import re

    from app.core import route_table
    from app.core import tenant_middleware as tm

    out = []
    for path, method in route_table.route_surface(api_client.app):
        if method != "GET":
            continue
        probe = re.sub(r"\{[^}]+\}", "1", path)
        if not tm.public_surface_allows(probe, "GET"):
            continue
        if tm.public_path_verifies_its_own_caller(probe):
            continue
        out.append(probe)
    return sorted(set(out))


def test_the_declared_public_derivation_is_not_vacuous(api_client):
    """★ THE CONTROL'S OWN TRIPWIRE ★ — this is the assertion whose absence let the sweep below
    measure almost nothing for three days. `checked >= 1` was satisfied by `/health` alone.

    A derivation that answers "5" for a 1,558-path app has not found the module routes, and a
    control that silently checks nothing is worse than no control: it reports green."""
    paths = _declared_public_gets(api_client)
    # 11 today (21 declared-public GETs, 10 of them excused as self-verifying by the declaration).
    # The floor is a tripwire for the derivation collapsing, not a spec: it leaves room for one path
    # to move into `_PUBLIC_SELF_IDENTIFYING` legitimately, and is two orders above the 1 that the
    # old `checked >= 1` accepted.
    assert len(paths) >= 10, f"only {len(paths)} declared-public GETs found: {paths}"
    # The W-9 return link is the defect this whole file exists for. It must be reached BY THE
    # DERIVATION, not only by the hand-written case above — a list kept here is what the derivation
    # replaced, and a derivation that misses its own founding case is not derived.
    assert any(p.startswith("/api/v1/pos/public/w9/") for p in paths), paths
    # More than one module, or a derivation scoped to one router would pass the count by accident.
    assert len({p.split("/")[3] for p in paths if p.startswith("/api/v1/")}) >= 4, paths


def test_every_declared_public_route_the_app_mounts_is_not_auth_refused(api_client):
    """Derived from the live route table and the one home — so a public path added tomorrow under a
    gated router is covered by this test with no edit. A path whose handler verifies a caller of its
    own is excused by the DECLARATION, never by a list kept here.

    GET ONLY, and that is a stated gap, not an oversight: 74 declared-public pairs use another verb
    and ~60 of them refuse an anonymous caller correctly (a cron secret, an analyzer key, a 2FA
    self-service path). Declaring each of those is an owner-visible change to the declaration and is
    registered as its own work in index §CORE-52.3, not smuggled into a test."""
    refused = []
    for probe in _declared_public_gets(api_client):
        status = api_client.get(probe).status_code
        if status in AUTH_REFUSALS:
            refused.append(f"GET {probe} -> {status}")
    assert not refused, "declared public, answered with an auth refusal: " + "; ".join(refused)
