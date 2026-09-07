"""Authenticator-app (TOTP) second factor for merchant-portal logins — RFC 6238, stdlib only.

WHAT THIS IS, AND WHAT IT IS NOT (owner directive 2026-09-04, scope note: "HANDLE 2FA, NEVER DEFEAT IT").

IS: when a portal offers AUTHENTICATOR-APP enrollment, the owner enrolls the account in their own
authenticator AND gives this platform the same shared secret the QR code carries. Computing the current
6-digit code from a secret we were legitimately given is exactly what an authenticator app does — the
portal designed this factor to be satisfied by a device holding that secret. Nothing is bypassed: the
portal still demands a second factor and still gets a correct one.

IS NOT: this never touches SMS or email OTP (those are dispatched to a human out-of-band and MUST be
typed by that human on the live-login screencast — live_login.py already does exactly that), never
solves a captcha, and never automates around a control the portal intends a human to satisfy. A portal
without authenticator enrollment simply has no TOTP secret configured, and the human-in-the-loop path
is the only path. See merchant_portals.PORTALS[*]['auth_notes'].

SECRET POSTURE. The secret is stored on commcalc.data_source exactly like the portal password is
(mig 955 adds `totp_secret`; the column joins router._SOURCE_SECRETS so it is stripped from every API
read, and _strip_source_pw exposes only `has_totp`). It is NEVER logged, never echoed on any payload,
and never rendered — the UI shows only mask_totp_secret()'s opaque mask. Only the derived 6-digit code
ever leaves this module, and that code is short-lived and single-use by construction.

PURE: hmac/hashlib/base64/struct/time only. No DB, no network. harness_portal_totp.py proves it against
the RFC 6238 published test vectors.
"""
import base64
import hashlib
import hmac
import re
import struct
import time

DEFAULT_DIGITS = 6
DEFAULT_PERIOD = 30
DEFAULT_ALGO = "sha1"
_ALGOS = {"sha1": hashlib.sha1, "sha256": hashlib.sha256, "sha512": hashlib.sha512}


class TotpError(ValueError):
    """A TOTP secret that cannot be used. The message is safe to show an operator — it NEVER quotes the
    secret itself, only what is wrong with its shape."""


def mask_totp_secret(secret):
    """Never return the raw secret. Mirrors google_reviews.mask_api_key's posture, but a TOTP secret is
    SHORT (a 16-char base32 string is common) and every character is high-entropy, so unlike an API key
    there is NO trailing hint at any length — a 4-char tail of a 16-char secret is a quarter of it."""
    s = str(secret or "").strip()
    if not s:
        return None
    return "•" * 12


def normalize_secret(secret):
    """Accept a secret the way a human copies it out of a portal — spaces, lowercase, an otpauth:// URI
    — and return canonical base32. Raises TotpError (without quoting the secret) when it is unusable."""
    s = str(secret or "").strip()
    if not s:
        raise TotpError("No authenticator secret is configured for this source.")
    if s.lower().startswith("otpauth://"):
        m = re.search(r"[?&]secret=([A-Za-z2-7=]+)", s)
        if not m:
            raise TotpError("That otpauth:// URI carries no secret= parameter.")
        s = m.group(1)
    s = re.sub(r"[\s-]+", "", s).upper()
    if not re.fullmatch(r"[A-Z2-7]+=*", s or ""):
        raise TotpError("The authenticator secret must be base32 (letters A–Z and digits 2–7).")
    s = s.rstrip("=")
    if len(s) < 16:
        raise TotpError("That authenticator secret is too short to be a real enrollment secret.")
    return s


def _key_bytes(secret):
    s = normalize_secret(secret)
    pad = "=" * (-len(s) % 8)
    try:
        return base64.b32decode(s + pad, casefold=True)
    except Exception:
        raise TotpError("The authenticator secret is not decodable base32.")


def hotp(secret, counter, digits=DEFAULT_DIGITS, algo=DEFAULT_ALGO):
    """RFC 4226 HOTP for a counter value. PURE."""
    fn = _ALGOS.get(str(algo or DEFAULT_ALGO).lower())
    if fn is None:
        raise TotpError("Unsupported authenticator algorithm.")
    digest = hmac.new(_key_bytes(secret), struct.pack(">Q", int(counter)), fn).digest()
    off = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** int(digits))).zfill(int(digits))


def totp(secret, at=None, digits=DEFAULT_DIGITS, period=DEFAULT_PERIOD, algo=DEFAULT_ALGO):
    """RFC 6238 TOTP — the code an authenticator app would show at unix time `at` (default: now)."""
    now = int(time.time() if at is None else at)
    return hotp(secret, now // int(period or DEFAULT_PERIOD), digits=digits, algo=algo)


def seconds_remaining(at=None, period=DEFAULT_PERIOD):
    """Seconds the current code stays valid. The submit path uses this to avoid handing a portal a code
    that expires between our computing it and the portal's checking it."""
    now = int(time.time() if at is None else at)
    p = int(period or DEFAULT_PERIOD)
    return p - (now % p)


def current_code(secret, at=None, digits=DEFAULT_DIGITS, period=DEFAULT_PERIOD, algo=DEFAULT_ALGO,
                 min_validity=3):
    """The code to submit, plus how long it lives. If the current code has under `min_validity` seconds
    left we return the NEXT window's code — a portal that receives a code which expires mid-verification
    reports it as WRONG, and a wrong code on these portals costs an attempt against a lockout counter.

    Returns {"code", "valid_for", "window"}. The code is the ONLY thing that leaves this module."""
    now = int(time.time() if at is None else at)
    p = int(period or DEFAULT_PERIOD)
    left = p - (now % p)
    if left < int(min_validity):
        now += left                                  # roll into the next window
        left = p
    return {"code": totp(secret, at=now, digits=digits, period=p, algo=algo),
            "valid_for": left, "window": now // p}


def describe(secret):
    """What the settings UI may safely render about a configured secret — never the secret. A malformed
    secret reports WHY without quoting it, so the owner can fix a paste error."""
    s = str(secret or "").strip()
    if not s:
        return {"configured": False, "valid": False, "mask": None, "error": None}
    try:
        normalize_secret(s)
    except TotpError as e:
        return {"configured": True, "valid": False, "mask": mask_totp_secret(s), "error": str(e)}
    return {"configured": True, "valid": True, "mask": mask_totp_secret(s), "error": None}
