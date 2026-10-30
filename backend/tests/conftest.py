"""Shared pytest setup for the MetricsPro backend suite.

THREE THINGS HAPPEN HERE, and the first one is the important one.

1. NO TEST CAN REACH THE NETWORK. There is no staging database: every environment in this project
   points at the SAME live Supabase, so "run the integration tests" and "write to production
   commission money" are one mistake apart. Rather than ask every author to remember that, the
   session blocks outbound sockets outright and leaves loopback open (TestClient needs it). A test
   that tries to talk to Supabase, Resend, Shopify or a carrier portal fails loudly with
   OutboundNetworkBlocked instead of succeeding quietly against production.

2. CREDENTIALS ARE BLANKED before app import, so nothing can authenticate even if a socket somehow
   opened, and so CI (which holds no secrets) runs the same code path a developer does.

3. FIXTURES: `api_client` is the REAL FastAPI app under Starlette's TestClient; `fake_supabase` is
   the in-memory database double (tests/fakes/supabase.py).

Tests are auto-marked by directory: tests/unit/* get `unit`, tests/integration/* get `integration`.
Nobody has to remember a decorator for the marker to be right.
"""
import os
import socket
import sys

import pytest

# The suite imports `app.*` and `tests.*`; backend/ is the root of both.
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# ── 2. blank the credentials BEFORE anything imports app.core.config ─────────────────────────────
for _var in ("SUPABASE_KEY", "SUPABASE_SERVICE_KEY", "RESEND_API_KEY", "NOTIFY_RUN_SECRET",
             "FIX_PIPELINE_SECRET", "HELPDESK_EXPORT_SECRET", "WHATSAPP_ACCESS_TOKEN",
             "ANTHROPIC_API_KEY", "DATABASE_URL", "DATABASE_WRITE_URL"):
    os.environ[_var] = ""
os.environ.setdefault("APP_ENV", "test")


# ── 1. the network guard ─────────────────────────────────────────────────────────────────────────
class OutboundNetworkBlocked(RuntimeError):
    """A test tried to open a socket to somewhere that is not loopback.

    This is not a flake and it is not a missing fixture. It means the code under test wants real
    production data. Seed `fake_supabase` instead, or make the unit smaller.
    """


_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testserver", ""}

_real_socket_connect = socket.socket.connect
_real_create_connection = socket.create_connection


def _host_of(address):
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


def _is_loopback(address) -> bool:
    host = _host_of(address)
    return host in _LOOPBACK or host.startswith("127.")


def _guarded_connect(self, address, *a, **kw):
    if not _is_loopback(address):
        raise OutboundNetworkBlocked(
            f"blocked outbound connection to {_host_of(address)!r}. Tests never reach production "
            "(there is no staging database) — use the `fake_supabase` fixture. See docs/TESTING.md."
        )
    return _real_socket_connect(self, address, *a, **kw)


def _guarded_create_connection(address, *a, **kw):
    if not _is_loopback(address):
        raise OutboundNetworkBlocked(
            f"blocked outbound connection to {_host_of(address)!r}. See docs/TESTING.md."
        )
    return _real_create_connection(address, *a, **kw)


def pytest_configure(config):
    socket.socket.connect = _guarded_connect
    socket.create_connection = _guarded_create_connection
    config.addinivalue_line("markers", "unit: pure logic, no app, no I/O")
    config.addinivalue_line("markers", "integration: the real FastAPI app over the database double")


def pytest_unconfigure(config):
    socket.socket.connect = _real_socket_connect
    socket.create_connection = _real_create_connection


def pytest_collection_modifyitems(config, items):
    """Mark by directory so `-m unit` / `-m integration` work without a decorator per test."""
    for item in items:
        path = str(item.fspath).replace(os.sep, "/")
        if "/tests/unit/" in path:
            item.add_marker(pytest.mark.unit)
        elif "/tests/integration/" in path:
            item.add_marker(pytest.mark.integration)


# ── 3. fixtures ──────────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def app_module():
    """The real FastAPI app. Session-scoped: importing every router takes seconds, not milliseconds."""
    from app.main import app
    return app


@pytest.fixture
def api_client(app_module):
    """Starlette TestClient over the real app — real routing, real middleware, no server, no network."""
    from fastapi.testclient import TestClient
    with TestClient(app_module) as client:
        yield client


@pytest.fixture(autouse=True)
def _no_cached_store_universe():
    """Drop `core.scope`'s cached store/market universe around EVERY test.

    `app.core.scope` caches the market index per org_id for a TTL, process-wide. That is correct in
    a server and poisonous in a suite: the first test to ask for an org's markets fills the cache
    from ITS seeded roster, and every later test in the same process reads that roster instead of
    its own. The symptom is the worst kind — a test that passes alone and fails in the suite, or
    passes in the suite and would fail alone, depending on file order.

    It is autouse rather than opt-in because the hazard belongs to the cache, not to the tests that
    happen to trip over it: a market-scoped sales-tax test found this on 2026-10-30, and nothing
    would have stopped the next one from finding it again.
    """
    from app.core import scope as _scope
    _scope.invalidate_market_index()
    yield
    _scope.invalidate_market_index()


@pytest.fixture
def fake_supabase():
    """An empty in-memory database double. Seed it with `.seed("schema.table", rows)`.

    To make a route use it, point the module's client getter at it:
        monkeypatch.setattr("app.modules.<mod>.router.get_supabase", lambda: fake_supabase)
    """
    from tests.fakes.supabase import FakeSupabase
    return FakeSupabase()
