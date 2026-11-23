"""The app must never tell a caller to resend a request off TLS.

WRITTEN RED FIRST, 2026-11-22, from a MEASUREMENT of production (adversarial access probe run 326,
module `platform`, index §CORE-47):

    GET https://api.metricspro.tech/api/v1/core/me/
      → 307  Location: http://api.metricspro.tech/api/v1/core/me

`/api/v1/core/me` is the token-verified whoami. One extra trailing slash and the platform told a
signed-in client to resend the request — Authorization header included — over plaintext.

THE CLASS: the app did not know it sat behind a TLS-terminating edge, so EVERY absolute URL the
framework built downgraded the caller. These cases pin the rule, not the route.
"""
import pytest

from app.core import forwarded_tls as ft


@pytest.mark.parametrize("asgi,fwd,want", [
    ("http", "https", "https"),      # the production shape
    ("http", "HTTPS", "https"),      # casing
    ("http", "  https ", "https"),   # whitespace
    ("http", "http", "http"),        # the edge says plaintext
    ("http", None, "http"),          # no edge at all
    ("http", "", "http"),
    ("https", None, "https"),        # TLS straight to the app
    ("http", "gopher", "http"),      # junk is not believed
    (None, None, "http"),            # nothing known → never crash
])
def test_effective_scheme(asgi, fwd, want):
    assert ft.effective_scheme(asgi, fwd) == want


@pytest.mark.parametrize("fwd,hops,legacy,want", [
    ("https", 1, False, "https"),        # one edge, one entry
    ("https, http", 1, False, "http"),   # client forged "https"; the EDGE said http
    ("http, https", 1, False, "https"),  # client forged "http"; the edge said https
    ("https", 0, False, "http"),         # no trusted proxy ⇒ believe no entry
    ("https, http", 0, True, "https"),   # legacy (today's default) = the leftmost, unchanged
])
def test_which_entry_may_be_believed(fwd, hops, legacy, want):
    """`x-forwarded-proto` is an appended list, so a forged LEADING entry must not decide the
    scheme. The arithmetic is `client_ip.trusted_entry`'s, dereferenced — not a second rule."""
    assert ft.effective_scheme("http", fwd, hops=hops, legacy=legacy) == want


H = "api.example.test"


@pytest.mark.parametrize("loc,scheme,host,want", [
    (f"http://{H}/api/v1/core/me", "https", H, f"https://{H}/api/v1/core/me"),  # THE REGRESSION
    (f"http://{H}/docs", "https", H, f"https://{H}/docs"),
    (f"http://{H}:443/x", "https", f"{H}:443", f"https://{H}:443/x"),
    # NO LOCKOUT / least surprise — everything else is returned byte-identical
    (f"http://{H}/docs", "http", H, f"http://{H}/docs"),
    (f"https://{H}/docs", "https", H, f"https://{H}/docs"),
    ("/api/v1/core/me", "https", H, "/api/v1/core/me"),
    ("http://localhost:8000/docs", "https", H, "http://localhost:8000/docs"),
    ("http://evil.example/steal", "https", H, "http://evil.example/steal"),
    ("", "https", H, ""),
    (None, "https", H, None),
    (f"http://{H}/docs", "https", None, f"http://{H}/docs"),
])
def test_secure_location(loc, scheme, host, want):
    assert ft.secure_location(loc, scheme=scheme, host=host) == want


def test_break_glass_is_armed_by_default(monkeypatch):
    monkeypatch.delenv("FORCE_HTTPS_LOCATION", raising=False)
    assert ft.outbound_rewrite_enabled() is True
    for off in ("0", "false", "no", "off", "OFF"):
        monkeypatch.setenv("FORCE_HTTPS_LOCATION", off)
        assert ft.outbound_rewrite_enabled() is False
