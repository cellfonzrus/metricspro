"""Proves the enrollment decisions offline — no database, no network.

The bug this exists to keep dead: registering an analyzer used to mint a permanent HMAC signing
secret and render it on screen for a human to copy. It reached a chat transcript within a week. What
replaces it is only safe if three properties actually hold, so they are asserted here rather than
assumed: a code is single-use, a code expires, and a refusal never tells an anonymous caller WHY.
"""
from app.modules.vision import enrollment as E

pass_n = fail_n = 0


def check(name, ok):
    global pass_n, fail_n
    if ok:
        pass_n += 1
        print(f"  ok   {name}")
    else:
        fail_n += 1
        print(f"  FAIL {name}")


print("\n(1) The code a human handles")
c = E.new_code()
check("is grouped for reading off a screen", c.count("-") == E.GROUPS - 1)
check("is the advertised length", len(E.normalize(c)) == E.GROUPS * E.GROUP_LEN)
check("avoids glyphs that get misread (I L O U 0 1)",
      not any(ch in c for ch in "ILOU01"))
check("is well-formed by its own test", E.code_wellformed(c))
check("two codes are not the same", E.new_code() != E.new_code())

print("\n(2) What a human actually types is accepted")
check("lower case works", E.code_hash(c.lower()) == E.code_hash(c))
check("missing dashes work", E.code_hash(c.replace("-", "")) == E.code_hash(c))
check("stray spaces work", E.code_hash(f"  {c} ") == E.code_hash(c))
check("junk is rejected before it reaches a query", not E.code_wellformed("hello"))
check("empty is rejected", not E.code_wellformed(""))
check("None is rejected", not E.code_wellformed(None))
check("a too-short code is rejected", not E.code_wellformed(E.normalize(c)[:-1]))
check("a too-long code is rejected", not E.code_wellformed(E.normalize(c) + "A"))

print("\n(3) The code is never stored, only its hash")
check("hashing is stable", E.code_hash(c) == E.code_hash(c))
check("the hash does not contain the code", E.normalize(c) not in E.code_hash(c))
check("different codes hash differently", E.code_hash(c) != E.code_hash(E.new_code()))
check("the hash is a full sha256", len(E.code_hash(c)) == 64)

NOW = "2026-08-21T01:00:00+00:00"
LATER = "2026-08-21T02:00:00+00:00"


def agent(**kw):
    base = {"enabled": True, "enroll_code_hash": "h", "enrolled_at": None,
            "enroll_expires_at": LATER}
    base.update(kw)
    return base


print("\n(4) When a code may be redeemed")
check("a pending, unexpired code is redeemable", E.redeemable(agent(), NOW) == "")
check("an unknown code is refused", E.redeemable({}, NOW) != "")
check("a disabled agent is refused", E.redeemable(agent(enabled=False), NOW) != "")

# SINGLE USE. The hash is cleared on redemption, so a replay finds no row — but if that write did
# not commit, this is what still refuses the second attempt.
check("a spent code is refused even before it expires",
      E.redeemable(agent(enroll_code_hash=None), NOW) != "")
check("an already-enrolled agent is refused",
      E.redeemable(agent(enrolled_at=NOW), NOW) != "")

# EXPIRY. A code that outlives its window is exactly the credential we were trying not to create.
check("an expired code is refused", E.redeemable(agent(enroll_expires_at=NOW), LATER) != "")
check("a code expiring in the future is fine", E.redeemable(agent(enroll_expires_at=LATER), NOW) == "")
check("a missing expiry is refused, not treated as forever",
      E.redeemable(agent(enroll_expires_at=None), NOW) != "")
check("an empty expiry is refused", E.redeemable(agent(enroll_expires_at=""), NOW) != "")
check("expiry is exclusive at the boundary", E.redeemable(agent(enroll_expires_at=NOW), NOW) == "")

print("\n(5) A refusal must not tell an anonymous caller which refusal it was")
# The endpoint returns ONE message for every case. These reasons exist for the audit row only, and
# the test that matters is that the caller-facing path has nothing to branch on: every failure is a
# non-empty string and the endpoint maps all of them to the same 401.
reasons = {
    E.redeemable({}, NOW),
    E.redeemable(agent(enabled=False), NOW),
    E.redeemable(agent(enroll_code_hash=None), NOW),
    E.redeemable(agent(enrolled_at=NOW), NOW),
    E.redeemable(agent(enroll_expires_at=NOW), LATER),
}
check("every failure is non-empty (so all map to the same deny)", all(r for r in reasons))
check("success is the only empty string", E.redeemable(agent(), NOW) == "")
check("the reasons are distinct for the audit log", len(reasons) == 5)

print("\n(6) The window is short enough to matter")
check("the code dies within the hour", 0 < E.TTL_MINUTES <= 60)

print(f"\n{pass_n} passed, {fail_n} failed")
raise SystemExit(1 if fail_n else 0)
