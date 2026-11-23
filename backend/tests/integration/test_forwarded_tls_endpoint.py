"""Integration: a real redirect out of the REAL app stays on TLS, for any path and any tenant.

The unit proof (tests/unit/test_forwarded_tls.py) pins the rule. This pins that a real request
through the real middleware stack lands on it — which is the half that was broken on production,
where the rule was right and nothing applied it (access probe run 326, index §CORE-47).
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

HOST = "api.example.test"


@pytest.fixture()
def client():
    return TestClient(app, base_url=f"http://{HOST}")


@pytest.mark.parametrize("path", ["/docs/", "/redoc/", "/api/v1/core/me/"])
def test_a_redirect_behind_the_edge_stays_on_tls(client, path):
    r = client.get(path, headers={"x-forwarded-proto": "https"}, follow_redirects=False)
    assert r.status_code in (301, 307, 308), r.status_code
    assert r.headers.get("location", "").startswith("https://"), r.headers.get("location")


@pytest.mark.parametrize("path", ["/docs/", "/api/v1/core/me/"])
def test_no_lockout_a_genuine_plain_http_request_is_left_alone(client, path):
    """Gating a redirect must never break the people it exists for: a plain-HTTP staging box and the
    local dev server keep their http Location."""
    r = client.get(path, follow_redirects=False)
    assert r.status_code in (301, 307, 308), r.status_code
    assert r.headers.get("location", "").startswith("http://"), r.headers.get("location")


def test_a_redirect_is_never_pointed_somewhere_new(client):
    """The rewrite changes the SCHEME and nothing else — same host, same path, same query."""
    r = client.get("/api/v1/core/me/?x=1", headers={"x-forwarded-proto": "https"},
                   follow_redirects=False)
    assert r.headers.get("location") == f"https://{HOST}/api/v1/core/me?x=1"


def test_the_backstop_can_be_disabled_without_a_code_change(client, monkeypatch):
    """Negative control + break-glass: with FORCE_HTTPS_LOCATION=0 the pre-fix downgrade reappears,
    so these tests are measuring the backstop rather than something else."""
    monkeypatch.setenv("FORCE_HTTPS_LOCATION", "0")
    r = client.get("/docs/", headers={"x-forwarded-proto": "https"}, follow_redirects=False)
    assert r.headers.get("location", "").startswith("http://")
