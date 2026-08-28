"""Application-level field encryption for sensitive employee PII (bank details / A-Number, and any
intake field a tenant defines and marks private).

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
import hashlib
import hmac as _hmac
import json as _json
import os
import re
from app.core.config import settings

_PREFIX = "enc:v1:"
_BLIND_PREFIX = "bi1_"  # marks a blind-index token; short + index-friendly


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


def encrypt_json(obj):
    """Encrypt an arbitrary JSON-serializable object into a wrapped envelope {'enc': 'enc:v1:…'} so a
    whole blob (e.g. an OCR payload full of PII) is stored OPAQUE at rest while keeping the column a
    JSONB. None passes through; with no key configured the object is returned unchanged (graceful, so
    search/read still work before the key is set). Reverse with decrypt_json()."""
    if obj is None:
        return None
    if isinstance(obj, dict) and set(obj.keys()) == {"enc"}:
        return obj  # already wrapped
    token = encrypt(_json.dumps(obj, separators=(",", ":"), default=str))
    if not is_encrypted(token):
        return obj  # no key / passthrough — leave as readable JSON
    return {"enc": token}


def decrypt_json(value):
    """Reverse encrypt_json(): unwrap {'enc': …} and JSON-decode. A plain (unwrapped) value passes
    through, so rows written before encryption still read. Returns None if the envelope can't be
    decrypted (key lost/rotated away)."""
    if isinstance(value, dict) and set(value.keys()) == {"enc"}:
        plain = decrypt(value.get("enc"))
        if plain is None:
            return None
        try:
            return _json.loads(plain)
        except Exception:
            return None
    return value


# ── Blind index (searchable encryption) ────────────────────────────────────────────────────────────
# An encrypted column is opaque — you cannot `WHERE phone = …` or `ILIKE` it. A blind index stores a
# keyed HMAC of the NORMALIZED value alongside the ciphertext, so exact/word lookups still work while
# the database only ever holds the ciphertext + an irreversible token. The token is keyed (HMAC, not a
# bare hash), so an attacker with the DB dump cannot brute-force common values without the key.
def _blind_key():
    """Derive a dedicated 256-bit HMAC key. Uses FIELD_BLIND_INDEX_KEY when set, else derives one from
    the field-encryption key so operators manage a SINGLE secret (domain-separated, so the blind-index
    key is never equal to the encryption key). Returns None when nothing is configured."""
    explicit = (getattr(settings, "FIELD_BLIND_INDEX_KEY", "") or "").strip()
    keys = _raw_keys()
    seed = explicit or (keys[0] if keys else "")
    if not seed:
        return None
    return hashlib.sha256(b"metricspro/blind-index/v1|" + seed.encode("utf-8")).digest()


def _bi_token(s: str) -> str:
    return _BLIND_PREFIX + _hmac.new(_blind_key(), s.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def blind_index(value, *, mode: str = "digits"):
    """Keyed HMAC token for EXACT-match search over an encrypted column. `mode`: 'digits' strips to
    digits (phone/IMEI), anything else lower-cases + trims. Returns None on empty value / no key."""
    if value is None or _blind_key() is None:
        return None
    s = re.sub(r"\D", "", str(value)) if mode == "digits" else str(value).strip().lower()
    return _bi_token(s) if s else None


def blind_index_words(*values):
    """Space-joined per-word HMAC tokens across the given text values, for WORD-level search over an
    encrypted text column (e.g. customer/device name): searching 'smith' matches 'John Smith' without
    any plaintext in the row. Returns None on no words / no key."""
    if _blind_key() is None:
        return None
    words: list[str] = []
    for v in values:
        if v is None:
            continue
        words += [w for w in re.sub(r"[^a-z0-9]+", " ", str(v).lower()).split() if w]
    # de-dup, keep order
    seen, uniq = set(), []
    for w in words:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    return " ".join(_bi_token(w) for w in uniq) if uniq else None


def blind_query_word_tokens(text):
    """Tokens for a SEARCH string, to AND-match against a blind_index_words() column via ILIKE. Returns
    [] when empty / no key (caller then skips the blind filter)."""
    if not text or _blind_key() is None:
        return []
    words = [w for w in re.sub(r"[^a-z0-9]+", " ", str(text).lower()).split() if w]
    seen, out = set(), []
    for w in words:
        if w not in seen:
            seen.add(w)
            out.append(_bi_token(w))
    return out
