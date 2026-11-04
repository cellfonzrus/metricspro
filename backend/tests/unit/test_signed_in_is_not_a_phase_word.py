"""CLOSING THE WINDOW MUST NOT CANCEL THE SESSION IT JUST CREATED.

Owner, 2026-10-31, minutes after the login fix shipped (§COMMCALC-50.8): he signed in to the UPS
Store reporting portal successfully — `auth_status='authenticated'`, 21:02:07Z, session saved until
05:02 the next morning — pressed the live window's **Close**, went to the Reports editor and pressed
*Read this page*, and got *"No live session is open for this login."* Two minutes after signing in.

THE DEFECT. Signing in starts the automatic report pull, and for its whole duration the session's
phase is **`pulling`**, not `authenticated`. `can_pull` and `can_calibrate` both know that: each
asks `phase in ("authenticated", "pulling")`. The live window's close handler did NOT — it tested
`liveState?.phase !== 'authenticated'` and, for anything else, POSTed `/live-login/cancel`. So the
one control an operator has for leaving the window killed a signed-in session, and the window's
green "Signed in / Done" bar — whose Done button closes WITHOUT cancelling — appears only once the
pull has finished, so during the pull the only way out was the destructive one.

THE CLASS, and it is the second sighting today: *a surface decided a fact from a WORD instead of
asking where the fact lives.* "Has this session passed the portal's sign-in?" had three answers —
`can_pull`'s phase tuple, `can_calibrate`'s phase tuple, and the browser's string compare — and the
browser's was wrong. §COMMCALC-50.8 was a page judged by its markup rather than by what it was
doing; this is a session judged by its phase label rather than by whether it is signed in.

THE FIX. `LiveLoginSession.signed_in()` is the one answer, over one declared phase set, and it is
published in `state()` so the window can DEREFERENCE it instead of re-deciding. A payload without
the field (an older build mid-deploy) reads as "do not cancel", because cancelling a live session by
mistake is unrecoverable while leaving one open costs twenty idle minutes.
"""
import inspect
import json
import os
import re

import pytest

from app.modules.commcalc import live_login as ll

HOUSE = "00000000-0000-0000-0000-000000000001"
FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "frontend", "src")
PAGE = os.path.join(FRONTEND, "app", "(platform)", "commcalc", "email-imports", "page.tsx")


def _session(phase):
    sess = ll.LiveLoginSession.__new__(ll.LiveLoginSession)
    ll.LiveLoginSession.__init__(sess, "ds-1", HOUSE, {
        "portal_url": "https://portal.example/login", "username": "u", "password": "p"})
    sess.phase = phase
    return sess


# ── the one home ────────────────────────────────────────────────────────────────────────────────
def test_the_signed_in_phases_are_declared_once():
    assert tuple(ll._SIGNED_IN_PHASES) == ("authenticated", "pulling")


@pytest.mark.parametrize("phase", ["authenticated", "pulling"])
def test_a_session_past_the_portals_sign_in_says_so(phase):
    assert _session(phase).signed_in() is True


@pytest.mark.parametrize("phase", ["starting", "login", "human_action", "awaiting_code",
                                   "verifying", "action_needed", "error", "cancelled"])
def test_a_session_that_has_not_signed_in_says_so(phase):
    assert _session(phase).signed_in() is False


def test_the_pull_is_the_phase_the_defect_hid_behind():
    """ARMED: the browser's old rule, reproduced. It is the phase an operator is most likely to
    close during, because signing in starts the pull."""
    assert _session("pulling").phase != "authenticated"
    assert _session("pulling").signed_in() is True


def test_the_two_gates_dereference_it_rather_than_keeping_a_phase_tuple():
    for fn in (ll.LiveLoginSession.can_pull, ll.LiveLoginSession.can_calibrate):
        src = inspect.getsource(fn)
        assert "signed_in()" in src, fn.__name__
        assert '"authenticated"' not in src, "%s still keeps its own phase list" % fn.__name__


def test_the_state_payload_publishes_it_so_a_surface_need_not_guess():
    st = _session("pulling").state()
    assert st["signed_in"] is True
    assert _session("login").state()["signed_in"] is False
    # It travels as JSON to a browser; a bool, not a word to be re-parsed there.
    assert isinstance(json.loads(json.dumps(st))["signed_in"], bool)


# ── the surface dereferences it ─────────────────────────────────────────────────────────────────
def test_the_live_window_asks_whether_it_is_signed_in_not_what_the_phase_is_called():
    src = open(PAGE, encoding="utf-8").read()
    close = src[src.index("async function closeLive("):]
    close = close[:close.index("\n  }") + 4]
    assert "signed_in" in close, "the close handler must read the session's own answer"
    # Comments SHOULD name the old rule — that citation is why the next reader keeps it. What may
    # not survive is a phase word in a line the browser runs.
    code = "\n".join(l for l in close.splitlines() if not l.strip().startswith("//"))
    assert "'authenticated'" not in code and '"authenticated"' not in code, \
        "a phase word in the close handler is the defect"


def test_cancel_is_withheld_when_the_field_is_absent():
    """A payload from an older build mid-deploy must read as 'do not cancel': a session cancelled by
    mistake cannot be got back, while one left open closes itself in twenty idle minutes."""
    src = open(PAGE, encoding="utf-8").read()
    close = src[src.index("async function closeLive("):]
    close = close[:close.index("\n  }") + 4]
    assert re.search(r"signed_in\s*===\s*false", close), \
        "cancel must require an explicit false, never a falsy/absent field"


def test_the_operator_always_has_a_non_destructive_way_out():
    """The green 'Signed in' bar carrying Done appears only once the pull has RESULTS, so a Done must
    also sit in the window's header whenever the session is signed in."""
    src = open(PAGE, encoding="utf-8").read()
    header = src[src.index("🔴 Live login —"):]
    header = header[:header.index("liveState?.message")]
    assert "closeLive(false)" in header, \
        "the header offers only the cancelling exit while the session is pulling"
    assert "signed_in" in header, "...and it picks the exit from the session's own answer"


def test_the_capture_is_reachable_without_leaving_the_live_window():
    """Owner, 2026-10-31: "opening 2 tabs is not a good relaible way for a user , it is a work
    around". The filters are read THROUGH the live session, so the control that reads them must be
    reachable while that session is still open."""
    src = open(PAGE, encoding="utf-8").read()
    window = src[src.index("🔴 Live login —"):]
    window = window[:window.index("liveState?.message")]
    assert "setReportsFor(live.source)" in window, \
        "the live window offers no way to reach the report capture it is holding the session for"
    assert "liveState?.signed_in" in window, "...offered only once the session is signed in"
    # The SAME editor, not a second copy of the fold-in logic.
    assert src.count("<PortalReportEditor") == 1
