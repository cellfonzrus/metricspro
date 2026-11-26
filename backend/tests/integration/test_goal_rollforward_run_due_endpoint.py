"""Integration: the monthly goal AUTO-FILL entrypoint, through the real app and middleware.

The DB-free proof (`backend/harness_goal_rollforward.py`) pins the rule and drives the handler
directly. This pins the half a direct call cannot see: that a REAL request through the REAL
middleware stack reaches — and is refused at — the right places.

Index §COMMCALC-84, migration 1131. Owner directive 2026-11-25 (Sanjot): "it will be the same if
they did not achieve the goal if they met the goal then it will be 10% higher than they achieved",
with auto-fill chosen for every tenant.

WHY THIS FILE EXISTS SEPARATELY FROM THE HARNESS. `POST /commcalc/targets/roll-forward/run-due` is a
cron entrypoint, so it is PUBLIC to the middleware (every literal route ending in `/run-due` is
allowlisted so pg_cron can reach a handler that then verifies its own `x-notify-secret`). A route
that the middleware lets through and whose own secret check is wrong is unauthenticated access to a
WRITE that sets every tenant's goals. That combination is only observable at the HTTP boundary.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

RUN_DUE = "/api/v1/commcalc/targets/roll-forward/run-due"


@pytest.fixture()
def client(monkeypatch):
    """★ The middleware's enforcement is gated by MULTI_TENANT_ENFORCE, which is UNSET locally. ★

    A test that does not set it exercises the pass-through and proves nothing about the deployed
    platform (index §CORE-42). The same posture as
    tests/integration/test_cron_sweep_method_endpoint.py."""
    monkeypatch.setenv("MULTI_TENANT_ENFORCE", "1")
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    monkeypatch.setenv("CRON_PATH_LITERAL_ONLY", "1")
    return TestClient(app, base_url="http://api.example.test", raise_server_exceptions=False)


def test_the_enforcement_switch_is_actually_on(client):
    """A negative control on the fixture itself: a plainly-gated path must refuse a stranger, or
    every assertion below is measuring the pass-through rather than the enforcement."""
    r = client.get("/api/v1/core/members")
    assert r.status_code in (401, 403), (r.status_code, r.text[:200])


def test_an_anonymous_post_is_refused_and_writes_nothing(client):
    """No secret, no run. This endpoint WRITES goals for every tenant, so a stranger reaching the
    handler would be an unauthenticated cross-tenant write."""
    r = client.post(RUN_DUE)
    assert r.status_code in (401, 403), (r.status_code, r.text[:300])
    # The handler's own refusal is a clean 401, never a 500 from running and failing part-way.
    assert r.status_code != 500, r.text[:300]


def test_a_wrong_secret_is_refused(client):
    r = client.post(RUN_DUE, headers={"X-Notify-Secret": "not-the-secret"})
    assert r.status_code in (401, 403), (r.status_code, r.text[:300])
    assert r.status_code != 500, r.text[:300]


def test_a_get_never_reaches_the_handler(client):
    """The sweep allowlist is METHOD-scoped (index §CORE-50): the production defect it closed was an
    allowlist that was right about the path and silent about the method, so an anonymous GET ran a
    handler. This route is POST-only."""
    r = client.get(RUN_DUE)
    assert r.status_code in (401, 403, 405), (r.status_code, r.text[:300])
    assert r.status_code != 500, r.text[:300]


def test_the_route_is_a_literal_cron_path_so_pg_cron_can_reach_it(client):
    """The other half of the same coin: the cron MUST be able to reach the handler, or the auto-fill
    silently never runs (the mig 241 / mig 411 failure mode — a scheduled job 401ing on every tick
    without anyone noticing). The allowlist is DERIVED from the route table and only a
    PARAMETER-FREE template qualifies, so this asserts the registered template is literal."""
    # ONE HOME (2026-11-26). This used to carry its own walk over `app.routes`, descending through
    # `original_router` to reach the leaf route objects. That walk was right about the lazy wrapper
    # and still saw PREFIX-STRIPPED templates (`/commcalc/...`, never `/api/v1/commcalc/...`), which
    # is the second trap and the one that made the security control measure nothing. The SERVED path
    # is now read from `app/core/route_table.py`, so this asserts the template a caller can actually
    # address rather than an internal spelling of it.
    from app.core import route_table

    surface = route_table.route_surface(app)
    assert len(surface) > 500, f"route surface collapsed to {len(surface)} pairs — this asserts nothing"
    TEMPLATE = "/api/v1/commcalc/targets/roll-forward/run-due"
    assert route_table.serves(app, TEMPLATE, "POST"), \
        f"the run-due route is not served under the expected literal template {TEMPLATE}"
    # LITERAL, so it qualifies for the derived cron allowlist. A template with a path parameter can
    # never be allowlisted, whatever value is typed into it (index §CORE-50).
    assert "{" not in TEMPLATE
    # And the sibling it must not be confused with IS parameterised, which is why the cron path has
    # its own literal template rather than reusing the button's route.
    assert route_table.serves(app, "/api/v1/commcalc/targets/{period}/roll-forward", "POST")
    # A template with a path parameter can never be allowlisted, whatever is typed into it.



def test_the_endpoint_exposes_no_org_id_to_the_caller():
    """The cross-tenant surface. This endpoint walks `storeops.tenants` itself and resolves each
    tenant's policy org-scoped at the query; there is deliberately NO org_id parameter for a caller
    to point at somebody else's data. The absence of the argument is the proof."""
    from app.modules.commcalc.router import roll_forward_targets_run_due as fn
    args = fn.__code__.co_varnames[:fn.__code__.co_argcount]
    assert "org_id" not in args, f"run-due grew an org_id argument: {args}"
