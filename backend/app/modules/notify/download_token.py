"""Signed, expiring, single-file download tokens for the no-login report-download link.

PURE + dependency-light (the only import is app.core.config for the HMAC secret) so it is exhaustively
unit-provable offline (scratchpad/prove_download_token.py). A token is an HMAC-SHA256 CAPABILITY over
exactly ONE notify.send_artifact id: it grants download of that single file and nothing else. Expiry is
enforced by the artifact row (expires_at) at download time — never encoded in the token — so a link can
never be extended by re-signing, and revocation is a row delete/expire.

Security properties (proven in the harness):
  • Unforgeable: the signature is HMAC(secret, artifact_id); a client can't mint a token for an id it
    doesn't already hold a valid signature for.
  • Unguessable: the artifact id is a random UUID AND must carry a matching signature.
  • Constant-time signature comparison (hmac.compare_digest); tamper → verify() returns None.
  • Never raises; any malformed/forged/empty token → None → the endpoint answers a uniform 404
    (no enumeration oracle: bad-token, unknown-id, and expired all look identical to the caller).
"""
import base64
import hashlib
import hmac


# Domain-separation prefix for the signed message: a notify-dl signature can never be mistaken for (or
# replayed against) any other HMAC in the app that happens to reuse the same fallback secret.
_MSG_PREFIX = b"notify-dl:"


def _secret():
    # M2 (2026-07-18) FAIL CLOSED: a dedicated secret when set, else the 2FA HMAC secret, else the service
    # key (all high-entropy, backend-only). With NONE configured we return None — there is NO literal
    # fallback constant, so tokens can never be forged from a public/guessable secret. In that state
    # sign()/_store_artifact() return None and the send degrades to the live-report link; verify() always
    # returns None (the endpoint 404s). Rotating the secret invalidates outstanding links (they 404): safe,
    # they are short-lived (default 7-day expiry).
    from app.core.config import settings
    s = (settings.NOTIFY_DOWNLOAD_SECRET or settings.AUTH_2FA_SECRET or settings.SUPABASE_SERVICE_KEY)
    return s.encode() if s else None


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64u_dec(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sig(artifact_id: str, secret: bytes) -> str:
    msg = _MSG_PREFIX + str(artifact_id or "").encode()
    return _b64u(hmac.new(secret, msg, hashlib.sha256).digest())


def sign(artifact_id: str):
    """token = base64url(artifact_id) + '.' + base64url(HMAC(secret, 'notify-dl:'+artifact_id)). The id
    rides in the token (encoded) so the endpoint needs no lookup table; the signature makes it unforgeable.
    Returns None when NO secret is configured (fail closed) → the caller falls back to the live-report link."""
    secret = _secret()
    if not secret:
        return None
    aid = str(artifact_id or "")
    return f"{_b64u(aid.encode())}.{_sig(aid, secret)}"


def verify(token: str):
    """Return the artifact id iff `token` is a well-formed, correctly-signed download token, else None.
    Constant-time on the signature; never raises. Returns None when NO secret is configured (fail closed).
    Expiry/revocation are checked by the caller against the artifact row (this only proves the token
    authentically references that one id)."""
    try:
        secret = _secret()
        if not secret:
            return None
        body, dot, sig = (token or "").partition(".")
        if not body or not dot or not sig:
            return None
        aid = _b64u_dec(body).decode()
        if not aid:
            return None
        if not hmac.compare_digest(sig, _sig(aid, secret)):
            return None
        return aid
    except Exception:
        return None
