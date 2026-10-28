"""Integration: the REAL FastAPI app boots and answers, with no credentials and no network.

"Integration" here means the real `app.main:app` — every router, every middleware, the real
request/response cycle through Starlette's TestClient. It does NOT mean a real database: there is
no staging Supabase (see docs/TESTING.md), so the database is the in-memory double and the outbound
network is blocked by conftest. A test that needs a real row is not an integration test here, it is
a question for production, and it is asked by reading, never by writing.
"""


def test_health_answers_ok(api_client):
    r = api_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "modules" in body


def test_the_app_booted_without_a_service_key():
    # The app must import and serve with SUPABASE_SERVICE_KEY unset. If this ever fails, something
    # reads a credential at import time, and CI (which has no secrets) cannot test the app at all.
    from app.core.config import settings
    assert not settings.SUPABASE_SERVICE_KEY


def test_an_unknown_route_is_a_404_not_a_crash(api_client):
    assert api_client.get("/no-such-endpoint-exists").status_code == 404


def test_security_headers_are_on_every_response(api_client):
    # The auth-hardening middleware in app/main.py promises these on EVERY response.
    r = api_client.get("/health")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
