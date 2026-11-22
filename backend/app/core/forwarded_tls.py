"""ONE HOME for "what scheme did this request really arrive on", and for keeping a Location on it.

MEASURED ON PRODUCTION, 2026-11-22 (adversarial access probe run 326, module `platform`,
index §CORE-43):

    GET https://api.metricspro.tech/api/v1/core/me/   →  307  Location: http://api.metricspro.tech/api/v1/core/me

`/api/v1/core/me` is the token-verified whoami. A client that arrived over TLS with one extra
trailing slash was told, BY US, to resend the request — Authorization header and all — over
plaintext http. `/docs/` and `/redoc/` answered the same way.

THE CLASS: the app did not know its own scheme. Gunicorn ran the uvicorn worker with no
`--forwarded-allow-ips`, so uvicorn's default of `127.0.0.1` discarded the edge's
`x-forwarded-proto: https`, `scope["scheme"]` stayed "http", and EVERY absolute URL the framework
builds — the router's trailing-slash 307, any RedirectResponse, url_for — named http://. The
instance was one route; the class is every redirect generator in the app. Repairing the noticed
route would have left the others downgrading.

So the fix is the mechanism, in two layers, because the root cause lives outside Python:

  · ROOT (Dockerfile): grant the forwarded-proto trust, so `scope["scheme"]` is correct and every
    generator is fixed at once — nothing has to remember to call anything.
  · OUTBOUND (HardeningMiddleware, which already owns HSTS): dereference `secure_location` below on
    the way out, so a deploy that loses the flag cannot silently restore the downgrade. Defence in
    depth, not a second copy of the fact: both layers read THIS module.

NO LOCKOUT (equal weight to the leak): a request that genuinely arrived over plain http is left on
http. A plain-HTTP staging box, a local `uvicorn app.main:app` and a health probe on the private
network keep working exactly as before. Break-glass `FORCE_HTTPS_LOCATION=0` disables the outbound
layer alone, with one env change and no code rollback — the same posture as REQUIRE_AUTH and
REP_CONFINE.

Trusting the edge's header is safe HERE because the container is reachable only through the edge,
and because a LIE in either direction is harmless: claiming https when the hop was plaintext only
keeps a redirect on TLS, and claiming http merely restores today's behaviour. No authorization
decision anywhere reads this scheme — it decides the spelling of a URL, nothing more.

Locked by harness_forwarded_tls.py.
"""
import os

from app.core import client_ip

# The header the TLS-terminating edge uses to report the CLIENT's own hop.
FORWARDED_PROTO_HEADER = "x-forwarded-proto"

_KNOWN = ("https", "http")


def effective_scheme(asgi_scheme, forwarded_proto, *, hops=None, legacy=None) -> str:
    """The scheme the CLIENT really used: the trustworthy entry of the forwarded header when it gave
    one we understand, otherwise the transport's own scheme. Never raises; unknown ⇒ "http".

    WHICH ENTRY may be believed is NOT decided here — `x-forwarded-proto` is an appended list exactly
    like `x-forwarded-for`, so its leftmost entry is whatever the client chose to send, and that
    arithmetic already has a home: `client_ip.trusted_entry` (index §CORE-47 extends §41906's one
    home rather than keeping a second rule). `hops`/`legacy` default to that module's environment
    switches (`TRUSTED_PROXY_HOPS`, `CLIENT_IP_LEGACY_XFF`), so a scheme can never be believed on
    terms the address is not. Pass them explicitly to keep a caller pure."""
    try:
        if hops is None:
            hops = client_ip.hops()
        if legacy is None:
            legacy = client_ip.legacy()
        fwd = client_ip.trusted_entry(forwarded_proto, hops=hops, legacy=legacy).strip().lower()
    except Exception:
        fwd = ""
    if fwd in _KNOWN:
        return fwd
    own = (asgi_scheme or "").strip().lower()
    return own if own in _KNOWN else "http"


def outbound_rewrite_enabled() -> bool:
    """Is the outbound Location layer armed? Break-glass FORCE_HTTPS_LOCATION=0."""
    return (os.environ.get("FORCE_HTTPS_LOCATION", "1") or "").strip().lower() \
        not in ("0", "false", "no", "off")


def secure_location(location, *, scheme, host):
    """PURE. A Location THIS APP generated must not move the caller off TLS.

    Rewrites `http://` → `https://` only when all of these hold, so it can never redirect a caller
    somewhere new:
      · the client arrived over https, and
      · the Location names a host, and that host is the SAME host the request was made to.

    A relative Location, an already-https one, a plain-http request and any third-party host are
    returned byte-identical. `None` in, `None` out."""
    if not location or not isinstance(location, str):
        return location
    if scheme != "https" or not host:
        return location
    prefix = "http://"
    if not location[:len(prefix)].lower() == prefix:
        return location
    rest = location[len(prefix):]
    # the authority ends at the first '/', '?' or '#'
    cut = len(rest)
    for ch in ("/", "?", "#"):
        i = rest.find(ch)
        if i != -1:
            cut = min(cut, i)
    if rest[:cut].lower() != str(host).strip().lower():
        return location          # a different host — never ours to rewrite
    return "https://" + rest
