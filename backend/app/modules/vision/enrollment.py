"""Edge analyzer enrollment — trading a short-lived code for a long-lived secret.

The whole point is that the thing a human handles is NOT a credential. An enrollment code authorises
one machine, once, within half an hour; the signing secret it mints is generated on the analyzer's
first call, stored encrypted, and never rendered anywhere a person can copy it.

Every decision here is a pure function so the security properties are provable without a database:
what a code may look like, whether one is still redeemable, and — the one that matters — that a code
already used is refused even if it has not expired.
"""
import hashlib
import secrets

# Deliberately excludes I, L, O, U, 0 and 1: these get read aloud, written on a sticky note, and
# typed into a terminal by someone standing in a stockroom. Ambiguous glyphs cost support calls.
ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
GROUPS = 4
GROUP_LEN = 4
TTL_MINUTES = 30


def new_code() -> str:
    """A code with ~78 bits of entropy, in groups of four so a human can read it off a screen.

    Brute force is not a threat at this size (30^16 ≈ 4e23), which is what lets the redemption
    endpoint be public — it has to be, since the analyzer has no credential yet.
    """
    raw = "".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LEN))
    return "-".join(raw[i:i + GROUP_LEN] for i in range(0, len(raw), GROUP_LEN))


def normalize(code: str) -> str:
    """Accept what a human actually types: lower case, missing dashes, stray spaces."""
    return "".join(c for c in str(code or "").upper() if c in ALPHABET)


def code_hash(code: str) -> str:
    """Codes are stored only as a hash, so a database dump does not hand over pending enrollments.

    Plain sha256 is right here and bcrypt would be wrong: this is a high-entropy random token, not a
    human-chosen password, so there is no dictionary to slow down — only a lookup to keep constant.
    """
    return hashlib.sha256(normalize(code).encode()).hexdigest()


def code_wellformed(code: str) -> bool:
    """Reject junk before it reaches the database. A public endpoint should not turn every stray
    request into a query."""
    return len(normalize(code)) == GROUPS * GROUP_LEN


def redeemable(agent: dict, now_iso: str) -> str:
    """'' when this row may be enrolled right now; otherwise why not, for the log.

    The caller must treat every non-empty return as the SAME answer to the client. Telling an
    unauthenticated caller the difference between "already used" and "expired" confirms that a code
    was real, which is the one fact a public endpoint must not leak.
    """
    if not agent:
        return "no such code"
    if not agent.get("enabled"):
        return "agent disabled"
    # Single use is enforced by the hash being cleared on redemption; this is the belt to that
    # braces, and it is what makes a replayed request fail even if the clear did not commit.
    if not agent.get("enroll_code_hash"):
        return "code already redeemed"
    if agent.get("enrolled_at"):
        return "agent already enrolled"
    exp = str(agent.get("enroll_expires_at") or "")
    if not exp:
        return "no expiry recorded"
    # String comparison is valid and total on ISO-8601 UTC timestamps, and avoids parsing a value
    # that arrived from the database in whatever shape the driver chose.
    if exp < now_iso:
        return "code expired"
    return ""
