"""A LIVE SESSION THAT CANNOT ACT MUST SAY WHICH OF THE REASONS APPLIES.

Sanjot, 2026-11-26 03:29 UTC, minutes after a sign-in the row recorded as `authenticated` with the
session good for another five hours: *"finally logged in again but when i hit read report it shows
cannot read as not loggedin"*. Then, one minute later: *"seems like the live login is logging in and
out of the portal, i don't want to get shut down."*

He was told he was not signed in AT THE MOMENT HE WAS, so the only move the sentence left him was to
sign in again — against a franchise portal, repeatedly, at half past three in the morning. A wrong
reason does not merely fail to help; it sends the operator at the one thing that can get the account
locked.

THE DEFECT. `can_calibrate()` and `can_pull()` each fold THREE different facts into one boolean — the
session has passed sign-in, the worker thread is still alive, and the function is configured — and then
two routers, having only the boolean, spell a sentence that GUESSES which one it was. Both guessed
"not signed in". For Sanjot the true reason was the third: he had signed in, and the live window had
since closed. `signed_in()` reads the CURRENT phase, so a session that authenticated and was later
cancelled reads exactly like one that never got in at all.

THE CLASS, already named here twice: an absence needs the RIGHT reason, not just a reason
(§COMMCALC-44), and a fact every caller needs has ONE home that callers dereference (§19.18). The
reason is now that one home, keyed by (action, reason), and the booleans are DERIVED from it rather
than the other way round — so a caller cannot get the verdict without being able to get the reason.
"""
import inspect

from app.modules.commcalc import live_login as ll


class _Sess:
    """The three facts `act_blocker` reads, and nothing else — a live session is far too heavy to
    build here, and the wiring tests below prove the real class answers the same three."""

    def __init__(self, phase="authenticated", ever=True, alive=True, calibrate=True, pull=True):
        self._phase, self._ever, self._alive = phase, ever, alive
        self.calibrate_fn = (lambda page: {}) if calibrate else None
        self.pull_fn = (lambda page: {}) if pull else None

    def signed_in(self):
        return self._phase in ll._SIGNED_IN_PHASES

    def ever_signed_in(self):
        return self._ever

    def worker_alive(self):
        return self._alive


# ── the reason itself ───────────────────────────────────────────────────────────────────────────
def test_a_session_that_can_act_has_no_blocker():
    for action in ("calibrate", "pull"):
        assert ll.act_blocker(_Sess(), action) == "", action


def test_no_session_at_all_is_its_own_reason():
    assert ll.act_blocker(None, "calibrate") == "no_session"


def test_a_session_that_never_signed_in_says_so():
    sess = _Sess(phase="awaiting_code", ever=False)
    assert ll.act_blocker(sess, "calibrate") == "not_signed_in"


def test_the_defect_sanjot_hit_a_closed_window_is_not_a_failed_sign_in():
    """THE REGRESSION. Signed in, then the live window closed. The old boolean collapsed this into
    the same False as 'never signed in', and he was told to sign in again."""
    closed = _Sess(phase="cancelled", ever=True, alive=False)
    assert ll.act_blocker(closed, "calibrate") == "window_closed"
    assert ll.act_blocker(closed, "pull") == "window_closed"


def test_a_signed_in_phase_with_a_dead_thread_is_also_a_closed_window():
    """The phase can still read 'authenticated' when the worker has gone. The window is what matters."""
    assert ll.act_blocker(_Sess(alive=False), "calibrate") == "window_closed"


def test_a_login_not_set_up_for_the_action_is_its_own_reason():
    assert ll.act_blocker(_Sess(calibrate=False), "calibrate") == "not_configured"
    assert ll.act_blocker(_Sess(pull=False), "pull") == "not_configured"
    # …and the two actions are asked separately: no capture configured does not block a pull.
    assert ll.act_blocker(_Sess(calibrate=False), "pull") == ""


def test_pulling_counts_as_signed_in_so_a_capture_during_the_auto_pull_is_not_refused():
    """The auto-pull fires the instant the session authenticates. An operator who presses Read this
    page during it is signed in, and must not be told otherwise."""
    assert ll.act_blocker(_Sess(phase="pulling"), "calibrate") == ""


def test_an_unknown_action_is_refused_rather_than_silently_allowed():
    import pytest
    with pytest.raises(ValueError):
        ll.act_blocker(_Sess(), "sign_the_lease")


# ── the sentence the operator reads ─────────────────────────────────────────────────────────────
def test_every_reason_has_a_sentence_for_every_action():
    reasons = ("no_session", "not_signed_in", "window_closed", "not_configured")
    for action in ("calibrate", "pull"):
        for reason in reasons:
            note = ll.cannot_act_note(action, reason)
            assert note and len(note) > 20, (action, reason)


def test_the_four_sentences_of_an_action_are_all_different():
    """Four reasons, four sentences — that is the whole point of the change."""
    for action in ("calibrate", "pull"):
        notes = [ll.cannot_act_note(action, r)
                 for r in ("no_session", "not_signed_in", "window_closed", "not_configured")]
        assert len(set(notes)) == 4, action


def test_the_closed_window_sentence_does_not_tell_him_he_is_signed_out():
    """The sentence he actually needs: the window went, the saved sign-in did not."""
    for action in ("calibrate", "pull"):
        note = ll.cannot_act_note(action, "window_closed").lower()
        assert "not signed in" not in note, action
        assert "closed" in note, action
        assert "saved" in note, action


def test_a_reason_with_no_sentence_is_empty_rather_than_a_guess():
    assert ll.cannot_act_note("calibrate", "") == ""
    assert ll.cannot_act_note("calibrate", "no_such_reason") == ""
    assert ll.cannot_act_note("no_such_action", "window_closed") == ""


# ── the booleans are DERIVED, and the callers dereference ───────────────────────────────────────
def test_the_booleans_are_derived_from_the_reason():
    """Not merely consistent — derived. Written as a separate conjunction they drift, which is how
    the sentence came to disagree with the verdict in the first place."""
    for name, action in (("can_calibrate", "calibrate"), ("can_pull", "pull")):
        src = inspect.getsource(getattr(ll.LiveLoginSession, name))
        assert "act_blocker" in src, name
        assert "is_alive()" not in src, name


def test_the_real_session_answers_the_three_facts_act_blocker_reads():
    for name in ("signed_in", "ever_signed_in", "worker_alive"):
        assert callable(getattr(ll.LiveLoginSession, name, None)), name


def _real_session():
    """A real session object, never started — no browser, no thread, no portal."""
    return ll.LiveLoginSession("sid", "org", {"username": "u", "password": "p"},
                               calibrate_fn=lambda page: {})


def test_the_real_session_latches_having_signed_in():
    """THE WIRING, on the real class. Writing `ever_signed_in` and leaving nothing to SET it is the
    §19.18 failure this project has shipped three times, and a fake that answers the question itself
    cannot catch it."""
    sess = _real_session()
    assert sess.ever_signed_in() is False
    sess._set(phase="authenticated")
    assert sess.ever_signed_in() is True


def test_the_real_session_that_signed_in_and_closed_reports_the_closed_window():
    """Sanjot's session, reproduced end to end on the real object: authenticated, then cancelled by
    the post-login inactivity timeout, with no worker thread. It must not read as a failed sign-in."""
    sess = _real_session()
    sess._set(phase="authenticated")
    sess._set(phase="cancelled", message="Live session closed after inactivity (post-login).")
    assert sess.signed_in() is False          # the phase alone still says "no"…
    assert ll.act_blocker(sess, "calibrate") == "window_closed"   # …and the reason says why
    assert sess.can_calibrate() is False


def test_the_real_session_that_never_signed_in_still_reports_that():
    sess = _real_session()
    sess._set(phase="awaiting_code")
    assert ll.act_blocker(sess, "calibrate") == "not_signed_in"


def _router_paths():
    """Resolved from `live_login`'s own location, never by importing the routers — the proof harness
    that shares these checks runs in a job with no web dependencies, and the two must agree."""
    import os
    modules = os.path.dirname(os.path.dirname(os.path.abspath(ll.__file__)))
    return {"commcalc": os.path.join(modules, "commcalc", "router.py"),
            "supply": os.path.join(modules, "supply", "router.py")}


def test_the_three_conditions_are_combined_in_exactly_one_place():
    """THE LOCK, read off the AST rather than off the prose. A function that weighs all three facts —
    did it sign in, is the worker alive, is the function configured — is deciding the question
    `act_blocker` owns. A second one is the copy this change removed, and the next change restoring
    it is exactly how the sentence came to disagree with the verdict."""
    import ast
    tree = ast.parse(open(ll.__file__).read())

    def names(node):
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | \
               {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}

    weighing = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        used = names(fn)
        signed = bool(used & {"signed_in", "ever_signed_in", "_SIGNED_IN_PHASES"})
        alive = bool(used & {"worker_alive", "is_alive"})
        configured = bool(used & {"calibrate_fn", "pull_fn", "_ACT_FN"})
        if signed and alive and configured:
            weighing.append(fn.name)
    assert weighing == ["act_blocker"], ("a second place weighs all three facts", weighing)


def test_no_router_spells_its_own_reason_for_a_live_session():
    """Both operator-facing callers read the one home. The literal sentences they used to carry are
    the ones that lied to him, so their words may not come back."""
    from harnesslib import py_code_only
    for who, path in _router_paths().items():
        code = py_code_only(open(path).read()).lower()
        assert "is not signed in yet" not in code, who
        assert "not signed in yet (or has closed)" not in code, who


def test_both_callers_dereference_the_one_home():
    from harnesslib import py_code_only
    for who, path in _router_paths().items():
        code = py_code_only(open(path).read())
        assert "act_blocker" in code, who
        assert "cannot_act_note" in code, who
