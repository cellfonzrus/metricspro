"""A PREFILL THAT COULD NOT FILL MUST NOT REPORT THAT IT DID.

Second half of the owner's 2026-10-31 report. Once the portal's genuine sign-in form is no longer
mistaken for a bot wall (see `test_portal_challenge_vs_form.py`), the live session reaches the
human-driven path and calls `vidapay_sweep.prefill_login` — which types the saved credentials into
the form and hands the screen to the operator, because an invisible human check gates the submit.

THE DEFECT. `prefill_login` was best-effort and SILENT: every step wrapped in `except: pass`, no
return value, nothing read by the caller. So a blank saved credential, or a control it could not
find, produced an empty form — while the operator was told, in `_HUMAN_CHECK_MESSAGE["invisible"]`,
*"Your details are filled in"*. The one sentence that would have explained the screen in front of
him said the opposite of what had happened.

THE CLASS, already named in this codebase on 2026-10-28 (§COMMCALC-44): *an absence needs the RIGHT
reason, not just a reason.* "Nothing is in the form" has three different causes — no credential is
saved, the control was not found, or the value did not stick — and they send the operator to three
different places. So the fill REPORTS, and the operator's words dereference that report.
"""
from app.modules.commcalc import live_login as ll
from app.modules.commcalc import vidapay_sweep as vp
from tests.fakes import portal_page as fp


def _form(page):
    return vp._password_frame(page)


# ── the fill reports ────────────────────────────────────────────────────────────────────────────
def test_prefill_reports_the_fields_it_actually_filled():
    page = fp.frs_signin()
    fr, pw_el = _form(page)
    rep = vp.prefill_login(page, fr, pw_el, None, "tupss.user", "s3cret")

    assert page.query_selector("#UsernameTextBox").value == "tupss.user"
    assert pw_el.value == "s3cret"
    assert rep["filled"] == ["user id", "password"]
    assert rep["no_value"] == [] and rep["no_control"] == []
    assert rep["ok"] is True


def test_an_account_id_the_portal_does_not_ask_for_is_not_a_shortfall():
    """The FRS form is Username + Password only, and its saved row has no account id. That is
    correct, not missing — reporting it would send the operator to invent one."""
    page = fp.frs_signin()
    fr, pw_el = _form(page)
    rep = vp.prefill_login(page, fr, pw_el, None, "tupss.user", "s3cret")
    assert "account id" not in rep["no_value"] and rep["ok"] is True


def test_a_blank_saved_credential_is_reported_as_no_value():
    page = fp.frs_signin()
    fr, pw_el = _form(page)
    rep = vp.prefill_login(page, fr, pw_el, None, "", "s3cret")
    assert rep["no_value"] == ["user id"]
    assert rep["ok"] is False


def test_a_control_that_cannot_be_found_is_reported_separately():
    """Two causes, two names: nothing saved sends you to the login's settings, a missing control
    sends you to the screenshot."""
    page = fp.plain_login()
    fr, pw_el = _form(page)
    # Strip the username control, keeping the password one: the shape of a form we half-understand.
    fr.elements = [e for e in fr.elements if e.attrs.get("type") == "password"]
    rep = vp.prefill_login(page, fr, pw_el, None, "someone", "s3cret")
    assert rep["no_control"] == ["user id"]
    assert rep["no_value"] == []
    assert rep["ok"] is False


def test_the_report_is_credential_free():
    """It is written to an operator's status line and to the row. It may name FIELDS, never values."""
    page = fp.frs_signin()
    fr, pw_el = _form(page)
    rep = vp.prefill_login(page, fr, pw_el, "99", "tupss.user", "s3cret")
    blob = repr(rep)
    assert "s3cret" not in blob and "tupss.user" not in blob


def test_prefill_still_never_raises_and_still_never_submits():
    """Both guarantees it was written for: the human-driven path must not be interrupted, and the
    submit must stay with the human because the check is armed."""
    page = fp.frs_signin()
    fr, pw_el = _form(page)
    rep = vp.prefill_login(page, fr, pw_el, None, "u", "p")
    assert page.query_selector("#SignInButton").clicks == 0
    assert pw_el.presses == []
    assert isinstance(rep, dict)

    broken = vp.prefill_login(None, None, None, None, "u", "p")   # a driver that answers nothing
    assert broken["ok"] is False


# ── the operator's words dereference it ─────────────────────────────────────────────────────────
def test_the_filled_in_claim_moved_out_of_the_kind_message():
    """The kind message says what GATES the submit. What is in the form is a different fact and may
    not be asserted by a sentence that never looked."""
    for kind, msg in ll._HUMAN_CHECK_MESSAGE.items():
        assert "filled in" not in msg.lower(), kind


def test_the_note_confirms_a_fill_that_happened():
    note = ll._prefill_note({"filled": ["user id", "password"], "no_value": [], "no_control": [],
                             "ok": True})
    assert "filled in" in note.lower()


def test_the_note_says_what_is_missing_and_why():
    nothing_saved = ll._prefill_note({"filled": ["password"], "no_value": ["user id"],
                                      "no_control": [], "ok": False})
    assert "user id" in nothing_saved.lower()
    assert "filled in for you" not in nothing_saved.lower()
    assert "saved" in nothing_saved.lower()

    no_control = ll._prefill_note({"filled": ["password"], "no_value": [],
                                   "no_control": ["user id"], "ok": False})
    assert "user id" in no_control.lower()
    assert no_control != nothing_saved, "two causes, two sentences — that is the whole point"


def test_the_note_is_empty_when_there_is_nothing_to_report():
    assert ll._prefill_note(None) == ""
    assert ll._prefill_note({}) == ""


def test_the_live_session_builds_the_message_from_both_homes():
    """A regression on the wiring itself: writing the note and not reading it has happened three
    times in this repo (index §19.18)."""
    import inspect
    drive = inspect.getsource(ll.LiveLoginSession._drive)
    assert "_HUMAN_CHECK_MESSAGE[" in drive, "the kind still comes from its one home"
    assert "_prefill_note" in drive, "…and what is in the form comes from its one home"
    assert "filled in" not in drive, "no wording spelled at the call site"
