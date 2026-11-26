"""A HUMAN CHECK'S KIND IS LOAD-BEARING AT EVERY MOMENT, NOT JUST THE FIRST ONE.

Owner, 2026-11-26, on his portal's live login: *"does not give enough time to do teh captcha"*, then
*"it just disappears as soon as you click on it"*.

There was no box. That portal's widget is `data-size="invisible"` — it fires on submit, and the only
reCAPTCHA thing on screen is the badge, which expands on hover and collapses on click. He was told
to "complete the 'I'm not a robot' box on the screen", went looking for it, clicked the badge and
watched it vanish.

`vidapay_sweep.human_check` had answered this correctly all along — "visible" / "invisible" / "" —
and `live_login` had correct separate wording for both. But FIVE of the six places that set
`phase="human_action"` asked the BOOLEAN `_captcha_present`, discarded the kind, and spelled the
visible sentence by hand. One of six did it properly. Every tenant with an invisible widget got the
same wrong instruction, at five of the six moments it could be given.

These tests pin that the kind reaches the operator at every moment, and that the sentence has one
home which cannot be hand-copied back in.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.modules.commcalc import live_login as ll            # noqa: E402

SRC = open(ll.__file__).read()
MOMENTS = ("at_form", "appeared", "botwall", "login_rejected", "before_code", "after_code")
BOX_WORDS = ("not a robot", "box on the screen", "tick the box", "check the box")


# ── the table ────────────────────────────────────────────────────────────────────────────────────
def test_every_moment_answers_both_kinds():
    for moment in MOMENTS:
        for kind in ("visible", "invisible"):
            assert ll.human_check_note(kind, moment), (moment, kind)


def test_no_check_is_never_an_instruction():
    """"" is "no human check". A caller must not be able to turn that into something to do."""
    for moment in MOMENTS:
        assert ll.human_check_note("", moment) == ""
        assert ll.human_check_note(None, moment) == ""


def test_an_unknown_moment_still_says_something_true_about_the_kind():
    """A new call site with no entry of its own falls back to the at-form words rather than inventing
    any — the kind is the half that was being got wrong, so it is the half that must not be lost."""
    assert ll.human_check_note("invisible", "a_moment_nobody_declared") == \
        ll.human_check_note("invisible", "at_form")
    assert ll.human_check_note("visible", "") == ll.human_check_note("visible", "at_form")


# ── what the two kinds may and may not say ───────────────────────────────────────────────────────
def test_the_invisible_wording_never_sends_anyone_looking_for_a_box():
    """THE DEFECT, pinned at every moment. There is nothing on screen to tick."""
    for moment in MOMENTS:
        note = ll.human_check_note("invisible", moment).lower()
        for word in BOX_WORDS:
            assert word not in note, (moment, word, note)


def test_the_invisible_wording_says_what_to_press_instead():
    """It is not enough to stop saying "box" — the operator has to be told the check runs on submit,
    or the screen just reads as broken, which is how this was reported."""
    for moment in MOMENTS:
        note = ll.human_check_note("invisible", moment).lower()
        assert ("sign in" in note or "submit" in note or "continue" in note
                or "button" in note), (moment, note)


def test_the_visible_wording_does_name_the_box():
    for moment in MOMENTS:
        note = ll.human_check_note("visible", moment).lower()
        assert any(w in note for w in BOX_WORDS), (moment, note)


def test_the_two_kinds_never_say_the_same_thing():
    for moment in MOMENTS:
        assert ll.human_check_note("visible", moment) != ll.human_check_note("invisible", moment)


# ── one home, and it cannot be hand-copied back ──────────────────────────────────────────────────
def test_the_at_form_words_are_dereferenced_not_copied():
    assert ll._HUMAN_CHECK_MESSAGE is ll._HUMAN_CHECK_NOTE["at_form"]


def test_every_human_action_site_reads_the_one_home():
    """The scan that would have caught this. Each `phase="human_action"` must take its message from
    `human_check_note`, never from a string beside it.

    Over EXECUTABLE text only, through the one home for "this file with its prose removed" — the
    comment above the table quotes the phase name while explaining the defect, and hand-tokenising
    Python to strip prose is the mistake `py_code_only` exists to stop."""
    import harnesslib

    code = harnesslib.py_code_only(SRC)
    sites = [m.end() for m in re.finditer(r'phase="human_action"', code)]
    assert len(sites) == 6, len(sites)
    for at in sites:
        window = code[at:at + 260]
        assert "human_check_note(" in window, window[:200]


def test_the_sentence_is_not_spelled_outside_the_table():
    """Prose may explain the defect; executable text may not re-introduce it."""
    import harnesslib

    code = harnesslib.py_code_only(SRC)
    table = code[code.index("_HUMAN_CHECK_NOTE = {"):code.index("def human_check_note(")]
    rest = code.replace(table, "")
    for word in ("not a robot", "box on the screen"):
        assert word not in rest.lower(), word


def test_this_module_never_asks_the_boolean_again():
    """THE lock that would have stopped all five copies. `_captcha_present` stays — the proof scripts
    in backend/scratchpad and `harness_human_check_kind` call it, and "is one present at all" is a
    fair question — but it is now asked by NOBODY inside this module, because every caller here has
    to decide which of two instructions to show and the bool cannot answer that. A call site
    reappearing is a kind about to be discarded.

    Stronger than scanning for a hand-written sentence: `kind = "visible" if cap else ""` would pass
    every wording check and still tell a tenant with an invisible widget to tick a box."""
    import harnesslib

    code = harnesslib.py_code_only(SRC)
    body = code[code.index("def _captcha_present("):]
    body = body[body.index("\ndef "):]              # everything AFTER the definition
    assert "_captcha_present(" not in body, (
        "a caller in live_login is asking the boolean again — ask _human_check_kind")


def test_the_kind_reader_is_the_one_in_the_driver():
    """`_human_check_kind` must keep deferring to the driver's `human_check`, or this module would be
    answering "is a human check here" a second time."""
    code = open(ll.__file__).read()
    body = code[code.index("def _human_check_kind("):]
    body = body[:body.index("\ndef ")]
    assert "vp.human_check(page)" in body


# ── the shape the owner's portal actually has ────────────────────────────────────────────────────
def test_an_armed_invisible_widget_is_the_case_this_covers():
    """Not a vendor test — the SHAPE: a widget present but not visible, declared invisible. The
    driver answers "invisible" for it, and that answer must survive all the way to the sentence."""
    from app.modules.commcalc import vidapay_sweep as vp

    class _El:
        def __init__(self, attrs):
            self._a = attrs

        def is_visible(self):
            return False

        def get_attribute(self, name):
            return self._a.get(name)

    # Both halves, as his page has them: declared invisible AND carrying the success callback that
    # submits the form. Either alone is not an armed check.
    el = _El({"data-size": "invisible", "data-callback": "captchaSuccessCallback",
              "class": "g-recaptcha"})
    assert vp._widget_is_armed_invisible(el) is True
    assert vp._widget_is_armed_invisible(
        _El({"data-size": "invisible", "class": "g-recaptcha"})) is False
    assert ll.human_check_note("invisible", "appeared")
    assert "not a robot" not in ll.human_check_note("invisible", "appeared").lower()


def test_each_moment_is_used_by_exactly_one_site():
    """Every declared moment is reached, and no two sites share one. A site borrowing another's
    moment reads as plausible and gives wrong advice — "then Sign in" is wrong on the code screen —
    and a moment nothing uses is wording nobody will ever see."""
    import harnesslib

    code = harnesslib.py_code_only(SRC)
    for moment in MOMENTS:
        used = len(re.findall(r'human_check_note\([^)]*"%s"\)' % re.escape(moment), code))
        assert used == 1, (moment, used)
