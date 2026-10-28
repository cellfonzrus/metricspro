"""THE ONE HOME for "what is this request's client address" (security review 2026-10-28). PURE core.

WHY THIS EXISTS. Four modules each carried this line:

    fwd = (headers.get("x-forwarded-for") or "").split(",")[0].strip()

`X-Forwarded-For` is a list that each proxy APPENDS to, so the LEFTMOST entry is whatever the
ORIGINAL CLIENT sent. Send `X-Forwarded-For: 1.2.3.4` and the edge appends your real address after
it — `[0]` is then the value you chose, not your address. Everything keyed on that value was
attacker-controlled: the per-IP rate limiter (`core/rate_limit.py`), the IP block list
(`core/ip_block.py`), the `ip` column of `core.access_log`, the session guard's rows, and the
impersonation journal. One header defeated the limiter (a fresh "IP" per request), walked through a
ban, and wrote a chosen address into the audit trail.

THE CLASS, NOT THE INSTANCE (CLAUDE.md). The wrong general fact was "the first entry of a
client-controlled header is the client's address", and it was wrong in four places at once — which is
the duplicate defect the index rules already forbid. So the answer lives here and every caller
dereferences it (`harness_client_ip.py` §F/§G fails the build if a fifth copy appears, or if a wired
site stops dereferencing this module).

HOW THE TRUSTWORTHY ENTRY IS CHOSEN. With N trusted proxies in front of the app, the client's address
is the **Nth entry from the END** of the chain: that is the one the outermost proxy we control wrote,
and everything to its left is hearsay a client can forge. `TRUSTED_PROXY_HOPS` is that N. The ASGI
peer address (`scope["client"]`) is the floor — it is never forgeable — and is used whenever the chain
is too short to answer, or when N is 0.

WHY THE DEFAULT IS STILL THE OLD BEHAVIOUR, STATED PLAINLY. The right N is a property of the deploy,
not of the code, and this API is reached two ways — directly at `api.metricspro.tech` and same-origin
through the site's own proxy. Guessing N would either leave the hole open (too low) or collapse every
browser user into one shared rate-limit bucket and fill the access log with a proxy's address (too
high), and that second failure is an outage. So `TRUSTED_PROXY_HOPS` is UNSET by default and this
module then behaves exactly as the four copies did, byte-for-byte — and `core/security_posture.py`
reports the unset switch as a finding at every production boot, so the operator makes the choice
instead of inheriting it in silence. Measuring N takes one curl:
/mnt/project-files/security-review-2026-10-28/SET_TRUSTED_PROXY_HOPS.md

Break-glass: `CLIENT_IP_LEGACY_XFF=1` restores the leftmost parse regardless of the hop count, with
one env change and no code rollback — the same posture as REQUIRE_AUTH / TWOFA_ENFORCE.
"""
import os

# The historical cap every call site applied to the value before storing it. Kept so the column
# widths and the rate-limiter keys are unchanged.
MAX_LEN = 64

HEADER = "x-forwarded-for"


def _int_env(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name, "") or "").strip() or default)
    except (TypeError, ValueError):
        return default


def hops() -> int:
    """Number of trusted proxies in front of this app. 0 (the default) ⇒ legacy behaviour."""
    n = _int_env("TRUSTED_PROXY_HOPS", 0)
    return n if n > 0 else 0


def legacy() -> bool:
    """True while the leftmost (client-supplied) entry is still being used — either because
    `TRUSTED_PROXY_HOPS` is unset, or because the break-glass is on."""
    if (os.environ.get("CLIENT_IP_LEGACY_XFF", "") or "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return hops() == 0


def _parts(xff):
    """The forwarded chain, left to right, blanks dropped. PURE. Never raises.

    Not artificially capped: the whole header is already bounded by the ASGI server's header-size
    limit, and capping the LIST would silently change which entry the hop count selects — the exact
    class of bug this module exists to remove."""
    if not isinstance(xff, str) or not xff:
        return []
    return [v for v in (raw.strip() for raw in xff.split(",")) if v]


def from_xff(xff, peer="", hops=0, legacy=False) -> str:
    """The client address for this request. PURE — the whole decision, with nothing read from env.

      • `legacy` ⇒ the leftmost entry (the pre-2026-10-28 behaviour), else the peer.
      • `hops <= 0` ⇒ the forwarded chain is ignored entirely and the peer answers (an app with no
        proxy in front of it must not read a header the client wrote).
      • otherwise ⇒ the `hops`-th entry from the END of the chain, i.e. `parts[len(parts) - hops]`.
        With N trusted proxies and a client that sent no header, the chain is exactly N long and this
        is its leftmost entry — the address the innermost trusted proxy observed. If the client DID
        forge a header the chain is longer, and the forged entries all sit to the left of the one
        selected, so they are never read. A chain SHORTER than `hops` means the proxy chain is not
        what the configuration says, and the unforgeable peer answers rather than a client claim.

    Returns "" (never None) when there is nothing to answer with. Truncated to MAX_LEN."""
    try:
        peer = str(peer or "")[:MAX_LEN]
        parts = _parts(xff)
        if legacy:
            return (parts[0][:MAX_LEN] if parts else peer)
        n = int(hops or 0)
        if n <= 0 or not parts:
            return peer
        idx = len(parts) - n
        if idx < 0:
            # The chain is shorter than the configured hop count, so no entry in it was necessarily
            # written by a proxy we trust. Prefer the unforgeable peer; fall back to the leftmost
            # claim only when there is no peer at all (a test harness, a unix socket).
            return peer or parts[0][:MAX_LEN]
        return parts[idx][:MAX_LEN]
    except Exception:
        try:
            return str(peer or "")[:MAX_LEN]
        except Exception:
            return ""


def from_headers(headers, peer="") -> str:
    """`from_xff` with the switches read from the environment. `headers` is a lower-cased mapping."""
    try:
        xff = (headers or {}).get(HEADER) or ""
    except Exception:
        xff = ""
    return from_xff(xff, peer=peer, hops=hops(), legacy=legacy())


def _peer_of(scope) -> str:
    try:
        cl = (scope or {}).get("client") or ()
        return str(cl[0]) if cl else ""
    except Exception:
        return ""


def from_scope(scope, headers) -> str:
    """For pure-ASGI middleware: the peer comes from `scope["client"]`."""
    return from_headers(headers, peer=_peer_of(scope))


def from_request(request) -> str:
    """For FastAPI handlers holding a `Request`. Never raises."""
    try:
        return from_headers(request.headers, peer=(getattr(request, "client", None).host
                                                   if getattr(request, "client", None) else ""))
    except Exception:
        return ""
