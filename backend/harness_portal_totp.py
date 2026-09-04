"""Proof harness: authenticator-app (TOTP) second factor — RFC 6238 vectors + secret hygiene.

WHY. Where a merchant portal offers AUTHENTICATOR-APP enrollment, the owner enrolls the account and
gives the platform the same shared secret their own authenticator holds; we then compute the same code
that app would show. The portal still demands a second factor and still receives a correct one — nothing
is bypassed. Two things must therefore be true, and are proven here:
  1. our codes are CORRECT — checked against the published RFC 6238 test vectors, for sha1/sha256/sha512;
  2. the secret NEVER leaks — not through a mask, not through an error message, not through describe().

This harness never touches SMS or email OTP: those are dispatched to a human out-of-band and are typed
by that human on the live-login screencast (live_login.py). There is no code here that could automate
them, by design.

No DB, no network. Run:  cd backend && python3 harness_portal_totp.py
"""
import base64
import sys

sys.path.insert(0, ".")

from app.modules.commcalc import portal_totp as t    # noqa: E402

PASS = FAIL = 0

# RFC 6238 Appendix B seeds. The RFC states them as ASCII strings; an authenticator carries them as
# base32, so they are DERIVED here rather than hand-typed — a mistyped fixture would either fail loudly
# or, worse, silently prove the wrong thing.
_ASCII = b"12345678901234567890"
SEED_SHA1 = base64.b32encode(_ASCII).decode()                       # 20 bytes
SEED_SHA256 = base64.b32encode((_ASCII * 2)[:32]).decode()          # 32 bytes
SEED_SHA512 = base64.b32encode((_ASCII * 4)[:64]).decode()          # 64 bytes

# (unix time, expected 8-digit code) — RFC 6238 Appendix B, verbatim.
RFC_SHA1 = [(59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
            (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130")]
RFC_SHA256 = [(59, "46119246"), (1111111109, "68084774"), (1234567890, "91819424")]
RFC_SHA512 = [(59, "90693936"), (1111111109, "25091201"), (1234567890, "93441116")]


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % msg)
    else:
        FAIL += 1
        print("  ✗ %s" % msg)


def eq(got, want, msg):
    ok(got == want, msg if got == want else "%s (got %r, want %r)" % (msg, got, want))


def main():
    print("\n1. RFC 6238 published test vectors — our codes are the RIGHT codes")
    for at, want in RFC_SHA1:
        eq(t.totp(SEED_SHA1, at=at, digits=8), want, "sha1 t=%d ⇒ %s" % (at, want))
    for at, want in RFC_SHA256:
        eq(t.totp(SEED_SHA256, at=at, digits=8, algo="sha256"), want, "sha256 t=%d ⇒ %s" % (at, want))
    for at, want in RFC_SHA512:
        eq(t.totp(SEED_SHA512, at=at, digits=8, algo="sha512"), want, "sha512 t=%d ⇒ %s" % (at, want))
    eq(t.totp(SEED_SHA1, at=59), "287082", "the 6-digit code a portal actually asks for")
    eq(len(t.totp(SEED_SHA1)), 6, "default is 6 digits")

    print("\n2. Window arithmetic — never hand a portal a code that dies mid-verification")
    eq(t.seconds_remaining(at=0), 30, "a fresh window has the full period")
    eq(t.seconds_remaining(at=29), 1, "…and one second at the end")
    late = t.current_code(SEED_SHA1, at=29)
    eq(late["code"], t.totp(SEED_SHA1, at=30),
       "with <3s left we return the NEXT window's code — a code that expires mid-check reads as WRONG, "
       "and a wrong code costs an attempt against the portal's lockout counter")
    eq(late["valid_for"], 30, "…and reports its full remaining life")
    early = t.current_code(SEED_SHA1, at=10)
    eq(early["code"], t.totp(SEED_SHA1, at=10), "comfortably inside a window ⇒ the current code")
    eq(early["valid_for"], 20, "…with its true remaining life")

    print("\n3. Secrets are accepted the way a human copies them")
    eq(t.normalize_secret("gezd gnbv gy3t qojq gezd gnbv gy3t qojq"), SEED_SHA1,
       "spaces and lowercase (how portals print an enrollment key)")
    eq(t.normalize_secret("GEZD-GNBV-GY3T-QOJQ-GEZD-GNBV-GY3T-QOJQ"), SEED_SHA1, "hyphenated")
    eq(t.normalize_secret(SEED_SHA1 + "======"), SEED_SHA1, "base32 padding tolerated")
    eq(t.normalize_secret("otpauth://totp/Portal:acct?secret=%s&issuer=Portal" % SEED_SHA1), SEED_SHA1,
       "a full otpauth:// URI pasted from a QR code")

    print("\n4. Bad secrets fail LOUDLY and never quote themselves")
    for bad, why in [("", "empty"), ("SHORT", "too short"), ("nope!nope!nope!nope!", "not base32"),
                     ("otpauth://totp/x?issuer=y", "otpauth URI with no secret")]:
        try:
            t.normalize_secret(bad)
            ok(False, "%s should have been rejected" % why)
        except t.TotpError as e:
            # (`bad in str(e)` is vacuously true for the empty string, so only check leakage when
            #  there is something that COULD leak.)
            leaked = bool(bad) and bad in str(e)
            ok(str(e) and not leaked, "%s ⇒ a clear error that does NOT quote the secret" % why)

    print("\n5. The secret never leaks through any surface")
    m = t.mask_totp_secret(SEED_SHA1)
    ok(SEED_SHA1 not in str(m) and set(str(m)) == {"•"},
       "the mask is fully opaque — unlike an API key there is NO trailing hint, because every "
       "character of a short high-entropy secret matters")
    ok(t.mask_totp_secret("") is None and t.mask_totp_secret(None) is None,
       "no secret ⇒ no mask (the UI shows 'not configured')")
    d = t.describe(SEED_SHA1)
    ok(d["configured"] and d["valid"] and SEED_SHA1 not in str(d),
       "describe() reports configured+valid without the secret appearing anywhere in it")
    dbad = t.describe("nope!")
    ok(dbad["configured"] and not dbad["valid"] and dbad["error"] and "nope!" not in str(dbad),
       "…and reports WHY a malformed secret is unusable, still without quoting it")
    eq(t.describe(""), {"configured": False, "valid": False, "mask": None, "error": None},
       "no secret configured is a clean, honest 'not configured'")

    print("\n6. Distinct secrets produce distinct codes (no accidental constant)")
    other = "MFRGGZDFMZTWQ2LKNNWG23TPOBYXE43UOV3HO6DZPI======"
    ok(t.totp(SEED_SHA1, at=59) != t.totp(other, at=59), "two secrets do not collide at the same time")
    ok(t.totp(SEED_SHA1, at=59) != t.totp(SEED_SHA1, at=59 + 30), "the code changes between windows")

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
