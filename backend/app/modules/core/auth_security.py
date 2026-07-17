"""Auth-hardening primitives — PURE, dependency-light, unit-provable (no DB, no FastAPI, no network).

Everything a password policy / OTP / 2FA-marker needs to be *decided* lives here as pure functions so
it can be exhaustively proven offline (scratchpad/prove_auth_security.py) and imported identically by
the router AND the tenant middleware (single source of truth — no drift). The only external import is
`app.core.config.settings` (for the OTP pepper + 2FA HMAC secret), which itself imports nothing from
any module, so there is no import cycle even when tenant_middleware imports this lazily.

Design invariants:
  • HARD ABSOLUTE CAP: any password > 128 chars is rejected BEFORE any other processing, regardless of
    tenant config (the 10 000-char DoS guard). The configurable max is clamped to ≤128.
  • OWNER-DIRECTED DEFAULTS (2026-07-17): min_length 8, max_length 12, require_special/upper/lower/digit
    all True ("as secure as possible within 8–12").
  • Auto-generated temp passwords ALWAYS satisfy the default policy (>=8, all four character classes).
"""
import base64
import hashlib
import hmac
import json
import secrets
import string
import time
from datetime import datetime, timezone

# ── Password policy ───────────────────────────────────────────────────────────────────────────────
HARD_MAX_PASSWORD = 128          # absolute cap, never overridable by tenant config (DoS guard)
_MIN_FLOOR = 4                   # min_length can never go below this (must fit the 4 character classes)

DEFAULT_PASSWORD_POLICY = {
    "min_length": 8,
    "max_length": 12,
    "require_upper": True,
    "require_lower": True,
    "require_digit": True,
    "require_special": True,
}

# The special-character set a password may use to satisfy require_special.
SPECIAL_CHARS = "!@#$%^&*()-_=+[]{};:,.?/"


def normalize_policy(raw) -> dict:
    """Merge a (possibly partial / possibly hostile) tenant override over the owner defaults and clamp
    every bound to a sane range. Always returns a complete, safe policy dict. Never raises."""
    p = dict(DEFAULT_PASSWORD_POLICY)
    if isinstance(raw, dict):
        for k in ("min_length", "max_length"):
            v = raw.get(k)
            if isinstance(v, bool):
                continue  # bool is an int subclass — reject it as a length
            if isinstance(v, (int, float)):
                p[k] = int(v)
        for k in ("require_upper", "require_lower", "require_digit", "require_special"):
            if k in raw:
                p[k] = bool(raw[k])
    # clamp: min in [_MIN_FLOOR, HARD_MAX]; max in [min, HARD_MAX]. Absolute cap wins over any config.
    p["min_length"] = max(_MIN_FLOOR, min(int(p["min_length"]), HARD_MAX_PASSWORD))
    p["max_length"] = max(p["min_length"], min(int(p["max_length"]), HARD_MAX_PASSWORD))
    return p


def password_errors(policy, pw) -> list:
    """Return a list of human-readable reasons `pw` violates `policy` (empty list = OK). The HARD cap is
    checked FIRST, before touching the (normalized) policy, so a 10 000-char body is rejected instantly."""
    pw = pw if isinstance(pw, str) else ""
    if len(pw) > HARD_MAX_PASSWORD:
        return [f"Password must be at most {HARD_MAX_PASSWORD} characters."]
    p = normalize_policy(policy)
    errs = []
    if len(pw) < p["min_length"]:
        errs.append(f"Use at least {p['min_length']} characters.")
    if len(pw) > p["max_length"]:
        errs.append(f"Use at most {p['max_length']} characters.")
    if p["require_upper"] and not any(c.isupper() for c in pw):
        errs.append("Include an uppercase letter.")
    if p["require_lower"] and not any(c.islower() for c in pw):
        errs.append("Include a lowercase letter.")
    if p["require_digit"] and not any(c.isdigit() for c in pw):
        errs.append("Include a number.")
    if p["require_special"] and not any(c in SPECIAL_CHARS for c in pw):
        errs.append("Include a special character (e.g. !@#$%).")
    return errs


def gen_temp_password(policy=None) -> str:
    """Generate a random temp password that satisfies BOTH the effective policy AND the owner default
    policy (>=8 chars, one of each of upper/lower/digit/special). Guarantees at least one character of
    each required class, then fills to length with the combined alphabet and shuffles."""
    p = normalize_policy(policy)
    # length: at least the default floor (8) so it always satisfies the default policy; never above the
    # tenant max (but never below what the 4 classes need). Kept modest (<=16) for usability.
    length = max(8, p["min_length"], 4)
    length = min(length if p["max_length"] < 8 else max(length, min(p["max_length"], 12)), 16)
    length = max(length, 8)  # final floor: default policy demands >=8 no matter the tenant max
    pools = [string.ascii_uppercase, string.ascii_lowercase, string.digits, SPECIAL_CHARS]
    chars = [secrets.choice(pool) for pool in pools]
    alphabet = string.ascii_letters + string.digits + SPECIAL_CHARS
    while len(chars) < length:
        chars.append(secrets.choice(alphabet))
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


# ── OTP (one-time codes for password reset · 2FA · phone verification) ──────────────────────────────
def _otp_pepper() -> bytes:
    from app.core.config import settings
    return (settings.AUTH_OTP_PEPPER or settings.SUPABASE_SERVICE_KEY or "mp-otp-pepper").encode()


def gen_otp(digits: int = 6) -> str:
    """A numeric OTP, leading zeros preserved (so '004212' is valid). Uniform over the full range."""
    n = secrets.randbelow(10 ** digits)
    return str(n).zfill(digits)


def hash_otp(code: str, email: str) -> str:
    """Deterministic HMAC-SHA256 of (email + code) under the server pepper — stored, never the code.
    Email-bound so a hash can't be replayed across accounts. Constant for a given (email, code)."""
    msg = f"{(email or '').strip().lower()}:{code or ''}".encode()
    return hmac.new(_otp_pepper(), msg, hashlib.sha256).hexdigest()


def otp_matches(row_hash: str, code: str, email: str) -> bool:
    return hmac.compare_digest(str(row_hash or ""), hash_otp(code, email))


def _to_ts(v) -> float:
    """Coerce an ISO-8601 string / datetime / epoch to a float epoch. 0.0 on failure (→ treated
    expired, the safe direction)."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, datetime):
        return v.timestamp()
    try:
        s = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def otp_is_expired(now_ts: float, expires_at) -> bool:
    return now_ts >= _to_ts(expires_at)


def otp_rate_limited(recent_count: int, max_per_window: int) -> bool:
    """True when too many codes were issued in the window (issue-side throttle)."""
    return int(recent_count or 0) >= int(max_per_window)


def otp_verify_decision(row, code, email, now_ts, *, max_attempts=5):
    """PURE verification decision for one stored OTP row. Returns (ok: bool, reason: str).
    reason ∈ {'ok','expired','too_many_attempts','used','mismatch','missing'}. Does NOT mutate the row
    or the DB — the caller applies the attempt increment / consume based on the result."""
    if not row:
        return (False, "missing")
    if row.get("consumed_at"):
        return (False, "used")
    if otp_is_expired(now_ts, row.get("expires_at")):
        return (False, "expired")
    if int(row.get("attempts") or 0) >= int(row.get("max_attempts") or max_attempts):
        return (False, "too_many_attempts")
    if otp_matches(row.get("code_hash"), code, email):
        return (True, "ok")
    return (False, "mismatch")


# ── Masking (never echo a full email/phone to an unauthenticated surface) ───────────────────────────
def mask_email(email: str) -> str:
    email = (email or "").strip()
    if "@" not in email:
        return "your email"
    local, _, domain = email.partition("@")
    show = local[0] if local else ""
    return f"{show}{'*' * max(1, len(local) - 1)}@{domain}"


def mask_phone(phone: str) -> str:
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if len(digits) < 4:
        return "your phone"
    return f"••• ••• {digits[-4:]}"


# ── 2FA "verified session" marker ───────────────────────────────────────────────────────────────────
# After a successful OTP verification the backend mints a compact signed token the client presents on
# every subsequent request (header x-2fa-token). It is a STATELESS HMAC assertion — no DB read to
# verify — carrying (auth_id, org, device, expiry). tenant_middleware verifies it purely.
def _twofa_secret() -> bytes:
    from app.core.config import settings
    return (settings.AUTH_2FA_SECRET or settings.SUPABASE_SERVICE_KEY or "mp-2fa-secret").encode()


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64u_dec(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def mint_2fa_token(auth_id: str, org: str, device: str, exp_ts: float) -> str:
    payload = {"a": auth_id or "", "o": org or "", "d": device or "", "e": int(exp_ts)}
    body = _b64u(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64u(hmac.new(_twofa_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_2fa_token(token: str, now_ts: float):
    """Return the payload dict if `token` is a well-formed, correctly-signed, unexpired 2FA marker,
    else None. Pure + constant-time on the signature; no DB. Never raises."""
    try:
        body, _, sig = (token or "").partition(".")
        if not body or not sig:
            return None
        expect = _b64u(hmac.new(_twofa_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(_b64u_dec(body).decode())
        if float(payload.get("e") or 0) <= now_ts:
            return None
        return payload
    except Exception:
        return None


def twofa_token_valid_for(token: str, auth_id: str, org: str, now_ts: float) -> bool:
    """True iff `token` is a valid 2FA marker for THIS login (auth_id) and (org, when the marker is
    org-scoped). A marker minted for org '' (account-level) is accepted for any org."""
    p = verify_2fa_token(token, now_ts)
    if not p:
        return False
    if p.get("a") != (auth_id or ""):
        return False
    tok_org = p.get("o") or ""
    return (not tok_org) or (tok_org == (org or ""))


def now_ts() -> float:
    return time.time()
