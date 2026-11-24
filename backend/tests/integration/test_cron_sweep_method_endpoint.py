"""Integration: the REAL app refuses an anonymous GET on a POST-only cron sweep path.

The unit proof (tests/unit/test_cron_sweep_is_method_scoped.py) pins the rule. This pins that a real
request through the real middleware stack lands on it — which is the half that was broken on
production, where the sweep allowlist was right about the PATH and silent about the METHOD (access
probe run 328, index §CORE-50).

Measured anonymously against production 2026-11-24, before the fix:
    GET /api/v1/storeops/salary-advance/additional-payroll/run-due -> 500 (the parameterised payroll
    read ran for a stranger); each of the other 38 sweep paths -> 405.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

SWEEP = "/api/v1/storeops/salary-advance/additional-payroll/run-due"
CONNECTORS = "/api/v1/commcalc/connectors/run-due"


@pytest.fixture()
def client(monkeypatch):
    """★ The middleware's enforcement is gated by MULTI_TENANT_ENFORCE, which is UNSET locally. ★

    A test that does not set it exercises the pass-through and proves nothing about the deployed
    platform — it is the shape of blindness this whole module exists to close (index §CORE-42). With
    it unset, an anonymous GET on the sweep path reproduces the production 500 verbatim
    (`ValueError: invalid literal for int() with base 10: 'run'`).
    """
    monkeypatch.setenv("MULTI_TENANT_ENFORCE", "1")
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    monkeypatch.setenv("CRON_PATH_LITERAL_ONLY", "1")
    return TestClient(app, base_url="http://api.example.test", raise_server_exceptions=False)


def test_the_enforcement_switch_is_actually_on(client):
    """A negative control on the fixture itself: a plainly-gated path must refuse a stranger, or
    every assertion below is measuring the pass-through."""
    r = client.get("/api/v1/core/members")
    assert r.status_code in (401, 403), (r.status_code, r.text[:200])


def test_an_anonymous_get_on_a_post_only_sweep_never_reaches_a_handler(client):
    """A stranger must be refused by the middleware, not routed to the colliding payroll read."""
    r = client.get(SWEEP)
    assert r.status_code in (401, 403), (r.status_code, r.text[:200])
    # The defect's signature was the handler RUNNING and blowing up on period="run-due".
    assert r.status_code != 500, r.text[:200]


def test_the_sweep_itself_is_not_locked_out(client):
    """pg_cron POSTs with its own secret: the sweep must still reach its handler and refuse on the
    SECRET (403 from the endpoint), never be turned away by the middleware's auth requirement."""
    r = client.post(SWEEP)
    assert r.status_code != 401, (r.status_code, r.text[:200])


def test_a_declared_public_path_keeps_its_public_method(client):
    """The GET the login page reads before sign-in must stay anonymous — no collateral lockout."""
    r = client.get("/api/v1/core/auth-config")
    assert r.status_code == 200, (r.status_code, r.text[:200])


@pytest.mark.parametrize("path", [SWEEP, CONNECTORS])
def test_an_anonymous_caller_is_refused_on_every_non_declared_method(client, path):
    for method in ("get", "put", "delete"):
        r = getattr(client, method)(path)
        assert r.status_code in (401, 403, 405), (method, path, r.status_code, r.text[:200])
