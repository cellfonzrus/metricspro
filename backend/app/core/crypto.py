"""Application-level field encryption for sensitive employee PII (SSN / bank / A-Number, …).

Defense in depth. The database is ALREADY encrypted at rest (Supabase Postgres = AES-256 on the
underlying volume) and every hop is TLS, so data is protected at the infrastructure layer. This
adds a SECOND layer at the application: the most sensitive fields are stored as CIPHERTEXT in the
database, so anyone with raw DB / service-role access (a leaked key, a backup, a support engineer)
sees only opaque tokens — the values are decryptable only by the backend holding the encryption key.

Primitive: **Fernet** (AES-128-CBC + HMAC-SHA256, authenticated) from the vetted `cryptography`
library — we never roll our own crypto. 128-bit AES with authenticated encryption satisfies the
"128-bit or whatever is necessary" bar; for a 256-bit posture, rotate to an AES-256 scheme later
without changing callers (the 'enc:v1:' envelope versions the format).

Stored form: `enc:v1:<fernet-token>`. The prefix lets decrypt tell ciphertext from LEGACY PLAINTEXT,
so nothing breaks before the key is set or the data is backfilled — a value with no prefix is
returned as-is.

KEY MANAGEMENT (operator):
  • Generate:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  • Set env FIELD_ENCRYPTION_KEY to that value on the backend (Railway).
  • Rotation: set FIELD_ENCRYPTION_KEYS = "newkey,oldkey,…" (newest first). Decrypt tries each key;
    encrypt always uses the first. Re-run the backfill to re-encrypt old data under the new key.
  • ⚠️  If the key is LOST, encrypted values are UNRECOVERABLE. Store it in a secrets manager + back
    it up. With NO key configured, encryption is a safe no-op passthrough (values stay plaintext) and
    is_enabled() returns False so the UI can warn.
"""
import os
from app.core.config import settings

_PREFIX = "enc:v1:"


class EncryptionKeyMissing(RuntimeError):
    """Raised by encrypt() when strict mode is on, the app is in production, and NO key is configured —
    so a sensitive field is refused rather than silently written as plaintext (fail CLOSED). Callers see
    it as a masked 500; the fix is to set FIELD_ENCRYPTION_KEY, not to store plaintext."""


def _is_prod() -> bool:
    return (getattr(settings, "APP_ENV", "") or "").strip().lower() in ("production", "prod", "live")


def _strict_encryption() -> bool:
    """Fail-closed switch for field encryption (Security Controls Spec §2, item 4a). Default OFF so the
    control ships without risking a surprise outage on a deploy where the key isn't set yet; the
    operator flips FIELD_ENCRYPTION_STRICT=1 once the key is confirmed present in prod (daily item #5).
    Only ever effective in production — dev/test always pass through so local work needs no key."""
    return os.environ.get("FIELD_ENCRYPTION_STRICT", "0").lower() in ("1", "true", "yes")


def _raw_keys():
    multi = (getattr(settings, "FIELD_ENCRYPTION_KEYS", "") or "").strip()
    single = (getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "").strip()
    raw = multi or single
    return [k.strip() for k in raw.split(",") if k.strip()]


def _fernets():
    try:
        from cryptography.fernet import Fernet
    except Exception:
        return []
    out = []
    for k in _raw_keys():
        try:
            out.append(Fernet(k.encode("ascii") if isinstance(k, str) else k))
        except Exception:
            continue  # malformed key — skip (so one bad key doesn't disable the rest)
    return out


def is_enabled() -> bool:
    """True when at least one valid key is configured (encryption is active)."""
    return bool(_fernets())


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt(value):
    """Encrypt a string → 'enc:v1:<token>'. None/empty pass through unchanged; already-encrypted
    values pass through; with no key configured the plaintext is returned (graceful — is_enabled()
    is then False and the caller/UI can warn)."""
    if value is None or value == "":
        return value
    s = value if isinstance(value, str) else str(value)
    if s.startswith(_PREFIX):
        return s
    fs = _fernets()
    if not fs:
        # FAIL CLOSED in production when strict mode is on: refuse to store a sensitive value as
        # plaintext. Otherwise (dev/test, or strict off) keep the graceful passthrough — is_enabled()
        # is False and the UI warns. See _strict_encryption().
        if _strict_encryption() and _is_prod():
            raise EncryptionKeyMissing(
                "FIELD_ENCRYPTION_KEY is not set; refusing to store a sensitive field as plaintext "
                "(FIELD_ENCRYPTION_STRICT is on). Set the key, then retry.")
        return s
    return _PREFIX + fs[0].encrypt(s.encode("utf-8")).decode("ascii")


def decrypt(value):
    """Decrypt an 'enc:v1:' value. Legacy plaintext (no prefix) is returned as-is. Returns None if
    the value is encrypted but no configured key can decrypt it (key lost/rotated away) so callers
    can render '(unavailable)' instead of crashing."""
    if not is_encrypted(value):
        return value
    token = value[len(_PREFIX):].encode("ascii")
    for f in _fernets():
        try:
            return f.decrypt(token).decode("utf-8")
        except Exception:
            continue
    return None


def encrypt_map(d: dict, keys) -> dict:
    """Return a copy of dict `d` with the given `keys` encrypted (missing/blank keys untouched)."""
    out = dict(d or {})
    for k in keys:
        if k in out and str(out.get(k) or "").strip() and not is_encrypted(out[k]):
            out[k] = encrypt(out[k])
    return out
