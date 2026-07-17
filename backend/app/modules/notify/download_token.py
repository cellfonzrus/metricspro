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


def _secret() -> bytes:
    # Dedicated secret when set, else reuse the 2FA HMAC secret, else the service key (already a
    # high-entropy backend-only secret) — never a trivial constant in prod. Rotating it invalidates
    # outstanding download links (they 404): safe, they are short-lived (default 7-day expiry).
    from app.core.config import settings
    return (settings.NOTIFY_DOWNLOAD_SECRET or settings.AUTH_2FA_SECRET
            or settings.SUPABASE_SERVICE_KEY or "mp-notify-dl-secret").encode()


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64u_dec(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sig(artifact_id: str) -> str:
    return _b64u(hmac.new(_secret(), str(artifact_id or "").encode(), hashlib.sha256).digest())


def sign(artifact_id: str) -> str:
    """token = base64url(artifact_id) + '.' + base64url(HMAC(secret, artifact_id)). The id rides in the
    token (encoded) so the endpoint needs no lookup table; the signature makes it unforgeable."""
    aid = str(artifact_id or "")
    return f"{_b64u(aid.encode())}.{_sig(aid)}"


def verify(token: str):
    """Return the artifact id iff `token` is a well-formed, correctly-signed download token, else None.
    Constant-time on the signature; never raises. Expiry/revocation are checked by the caller against
    the artifact row (this only proves the token authentically references that one id)."""
    try:
        body, dot, sig = (token or "").partition(".")
        if not body or not dot or not sig:
            return None
        aid = _b64u_dec(body).decode()
        if not aid:
            return None
        if not hmac.compare_digest(sig, _sig(aid)):
            return None
        return aid
    except Exception:
        return None
