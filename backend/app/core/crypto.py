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
from app.core.config import settings

_PREFIX = "enc:v1:"


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
