"""The suite does not spend one rate-limit budget across every test.

WHY THIS FILE EXISTS. `RateLimitMiddleware` keeps a per-IP fixed-window counter in the process, and
every test reaches the app from the same TestClient address — so without the autouse fixture in
`conftest.py` the whole suite shares one 300-requests-per-minute budget. When it runs out, the test
that fails is whichever one was running, never the one that spent it. Adding 22 requests in a new
file (§POS-19, 2026-10-31) turned four Zero Sales tests red in a file that change does not touch.

The fixture walks the built middleware stack to find the limiter, and a walk that quietly finds
NOTHING would pass forever while clearing nothing. That is what this pins.
"""
from tests.conftest import clear_rate_limit_buckets


def test_the_limiter_is_reachable_once_a_client_has_served_a_request(api_client):
    """Starlette builds the middleware stack on startup, so this is only true with a live client —
    which is exactly when a budget exists to carry."""
    api_client.get("/health")
    assert clear_rate_limit_buckets() >= 1


def test_clearing_really_empties_the_counter(api_client):
    api_client.get("/health")
    from app.main import app
    seen, node, limiter = set(), getattr(app, "middleware_stack", None), None
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        if getattr(node, "_limiter", None) is not None:
            limiter = node._limiter
            break
        node = getattr(node, "app", None)
    assert limiter is not None, "the rate limiter is no longer where the fixture looks for it"
    limiter._buckets["probe"] = [0.0, 999]
    assert clear_rate_limit_buckets() >= 1
    assert limiter._buckets == {}


def test_a_test_still_gets_a_real_limiter_rather_than_a_disabled_one(api_client):
    """The fixture empties the budget; it does not switch enforcement off. A test that wants to
    prove a flood is refused must still be able to."""
    import os
    from app.core import rate_limit
    assert os.environ.get("RATE_LIMIT_ENFORCE", "1").lower() not in ("0", "false", "no", "off")
    assert rate_limit._enforce() is True
