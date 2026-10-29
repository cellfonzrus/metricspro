"""The structural promise: a test in this repo CANNOT reach production Supabase.

There is no staging database. The only thing standing between a careless integration test and live
commission money is this guard, so it is itself tested. conftest blocks every outbound socket for
the whole session; loopback stays open because TestClient and any local fixture need it.
"""
import socket

import pytest

from tests.conftest import OutboundNetworkBlocked


def test_an_outbound_connection_is_refused():
    with pytest.raises(OutboundNetworkBlocked):
        socket.create_connection(("etxdalernqqtwjcrtcuj.supabase.co", 443), timeout=1)


def test_a_raw_socket_connect_is_refused_too():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(OutboundNetworkBlocked):
        s.connect(("1.1.1.1", 443))


def test_loopback_is_still_allowed():
    # TestClient and local fixtures need loopback; blocking it would block the tests themselves.
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        c = socket.create_connection(srv.getsockname(), timeout=2)
        c.close()
    finally:
        srv.close()


def test_the_real_supabase_client_cannot_even_be_built_in_a_test():
    """TWO independent barriers, and the credential one fires first.

    conftest blanks SUPABASE_KEY, so `create_client` refuses before a socket is opened at all; the
    socket guard above is the second barrier for anything that carries its own URL (an HTTP call to
    a carrier portal, Resend, Shopify). Either refusal is a pass — a SILENT SUCCESS is the only
    failure, because a silent success means the test reached production.
    """
    from app.core.database import get_supabase
    with pytest.raises(Exception) as exc:
        get_supabase().schema("commcalc").table("store_mapping").select("*").limit(1).execute()
    blocked = isinstance(exc.value, OutboundNetworkBlocked) or "OutboundNetworkBlocked" in repr(exc.value)
    no_credential = "key is required" in str(exc.value).lower()
    assert blocked or no_credential, f"production was reachable from a test: {exc.value!r}"
