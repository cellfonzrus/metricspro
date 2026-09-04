"""Proof harness: the durable-session HEALTH state machine (portal_session_health).

WHY. The 2FA "workaround" the owner asked for is durable session reuse — a human satisfies the portal's
challenge ONCE, and the saved session drives every daily pull afterwards. The failure mode that makes
that approach worthless is a session that dies quietly: the connector still looks configured, the
nightly pull returns nothing, and the hole is found weeks later in the recon. So the session's condition
must be COMPUTED and surfaced, and the computation must be provable. This is that proof.

No DB, no network. Run:  cd backend && python3 harness_portal_session_health.py
"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from app.modules.commcalc import portal_session_health as h    # noqa: E402

PASS = FAIL = 0
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


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


def at(hours):
    return (NOW + timedelta(hours=hours)).isoformat()


def state(row, **kw):
    return h.evaluate(row, now=NOW, **kw)["state"]


def main():
    print("\n1. Every state is reachable and correctly ordered by severity")
    eq(state({}), "never_linked", "no session at all ⇒ never_linked (nothing will ever pull)")
    eq(state({"has_session": True, "session_expires_at": at(48)}), "healthy", "comfortable life left")
    eq(state({"has_session": True, "session_expires_at": at(5)}), "expiring_soon",
       "inside the warn window ⇒ ask for a re-login in daylight")
    eq(state({"has_session": True, "session_expires_at": at(-1)}), "expired", "past expiry")
    eq(state({"has_session": True, "auth_status": "needs_2fa"}), "needs_login",
       "the portal itself invalidated us")
    eq(state({"has_session": True, "session_expires_at": at(48), "last_status": "error"}), "error",
       "a valid session whose last PULL failed is an error, not a session problem")
    eq(h.worse_of("healthy", "expiring_soon", "needs_login"), "needs_login",
       "worse_of picks the state that must be shown")
    eq(h.worse_of(), "healthy", "no signals ⇒ healthy")

    print("\n2. The portal's verdict OUTRANKS our stored clock (the quiet-death case)")
    eq(state({"has_session": True, "auth_status": "needs_2fa", "session_expires_at": at(500)}),
       "needs_login",
       "a session the portal rejected is needs_login even though our expiry says weeks left")

    print("\n3. No published expiry ⇒ an ASSUMED ttl, never 'good forever'")
    eq(state({"has_session": True, "session_linked_at": at(-1)}), "healthy",
       "linked an hour ago, no expiry published ⇒ healthy")
    eq(state({"has_session": True, "session_linked_at": at(-9)}), "expiring_soon",
       "…nearing the end of the assumed life it warns (the warn window scales to the ASSUMED ttl —")
    ok(h.DEFAULT_WARN_HOURS > h.ASSUMED_TTL_HOURS,
       "  …necessary because the house warn window is WIDER than the assumed ttl, so using it raw")
    ok(state({"has_session": True, "session_linked_at": at(-1)}) == "healthy",
       "  …would have marked every such session 'expiring soon' from the second it was linked)")
    eq(state({"has_session": True, "session_linked_at": at(-(h.ASSUMED_TTL_HOURS + 1))}), "expired",
       "…and past the assumed TTL it is EXPIRED, not silently healthy")

    print("\n4. The warn window is per-source config (RULE TWO)")
    row = {"has_session": True, "session_expires_at": at(30)}
    eq(state(row), "healthy", "30h left is healthy on the 24h house default")
    eq(state(dict(row, session_warn_hours=48)), "expiring_soon",
       "a source configured to warn 48h ahead flags the same session")
    eq(state(dict(row, session_warn_hours=0)), "healthy", "a nonsense warn window falls back to the default")
    eq(state(dict(row, session_warn_hours="abc")), "healthy", "…including an unparseable one")

    print("\n5. Works on the SECRET-STRIPPED public row (the chip must not need session_state)")
    eq(state({"session_state": {"cookies": []}, "session_expires_at": at(48)}), "healthy",
       "a raw row with session_state reads healthy")
    eq(state({"has_session": True, "session_expires_at": at(48)}), "healthy",
       "…and so does the API row that carries only has_session")
    out = h.evaluate({"session_state": {"cookies": [{"value": "SECRET"}]}, "has_session": True,
                      "session_expires_at": at(48)}, now=NOW)
    ok("SECRET" not in str(out), "no session material can appear in the health output")

    print("\n6. actionable / needs_human mark exactly the states a human can fix")
    for s, want in [("healthy", False), ("expiring_soon", False), ("error", False),
                    ("never_linked", True), ("expired", True), ("needs_login", True)]:
        rows = {"healthy": {"has_session": True, "session_expires_at": at(48)},
                "expiring_soon": {"has_session": True, "session_expires_at": at(5)},
                "error": {"has_session": True, "session_expires_at": at(48), "last_status": "error"},
                "never_linked": {},
                "expired": {"has_session": True, "session_expires_at": at(-1)},
                "needs_login": {"has_session": True, "auth_status": "needs_2fa"}}[s]
        eq(h.evaluate(rows, now=NOW)["needs_human"], want, "%s.needs_human == %s" % (s, want))

    print("\n7. Notify-once: escalate, don't nag")
    bad = h.evaluate({"has_session": True, "auth_status": "needs_2fa"}, now=NOW)
    good = h.evaluate({"has_session": True, "session_expires_at": at(48)}, now=NOW)
    ok(h.should_notify(bad, None, None, now=NOW), "first time an actionable state appears ⇒ notify")
    ok(not h.should_notify(good, None, None, now=NOW), "a healthy session never pages")
    ok(not h.should_notify(bad, "needs_login", at(-1), now=NOW),
       "the same bad news an hour later ⇒ silent (the chip carries it)")
    ok(h.should_notify(bad, "needs_login", at(-(h.RENOTIFY_HOURS + 1)), now=NOW),
       "…but re-notify once it has gone unfixed past the re-notify interval")
    expired = h.evaluate({"has_session": True, "session_expires_at": at(-1)}, now=NOW)
    ok(h.should_notify(bad, "expired", at(-1), now=NOW),
       "escalation (expired → needs_login) notifies immediately")
    ok(not h.should_notify(expired, "needs_login", at(-1), now=NOW),
       "de-escalation does not re-page")
    ok(h.should_notify(bad, "needs_login", None, now=NOW),
       "a recorded state with no timestamp is treated as due, not as recently sent")

    print("\n8. Roll-up for the settings banner")
    s = h.summarize([{"id": "a", "has_session": True, "session_expires_at": at(48)},
                     {"id": "b", "has_session": True, "session_expires_at": at(5)},
                     {"id": "c"}], now=NOW)
    eq(s["worst"], "never_linked", "the banner shows the worst source's state")
    eq(s["needs_human"], 1, "one source needs a human")
    eq(s["total"], 3, "all sources counted")
    eq(h.summarize([], now=NOW)["worst"], "healthy", "no sources ⇒ nothing to alarm about")

    print("\n9. Malformed timestamps degrade, never crash")
    eq(state({"has_session": True, "session_expires_at": "not-a-date"}), "healthy",
       "an unparseable expiry does not crash the chip")
    eq(state({"has_session": True, "session_expires_at": "2026-09-04T18:00:00Z"}), "expiring_soon",
       "a Z-suffixed timestamp parses")
    eq(state({"has_session": True, "session_expires_at": datetime(2026, 9, 6, 12, 0)}), "healthy",
       "a naive datetime is treated as UTC rather than rejected")

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
