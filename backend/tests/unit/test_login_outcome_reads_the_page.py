"""A LOGIN OUTCOME IS READ OFF WHAT THE PAGE IS DOING, NEVER OFF HOW IT IS BUILT.

The regression these tests exist for (index §COMMCALC-50.9, owner report 2026-10-31): three carrier
feeds stopped refreshing after 2026-10-09 and the platform said "credentials rejected" 1,056 times
running. That sentence was a substring search over `page.content()` - the MARKUP - so it could not
tell a real refusal from a single-page application that merely BUNDLES the word "invalid", and it
reported a human check and a changed sign-in flow as a wrong password too.

Every test below is about that: the same words are decisive in VISIBLE text and inert in markup, and
the five outcomes stay five, each with its own remedy.
"""
import pytest

from app.modules.commcalc import login_outcome as lo


# ── THE REGRESSION ITSELF ─────────────────────────────────────────────────────────────────────────

#: An excerpt in the shape that broke this: an application shell whose MARKUP carries the word
#: "invalid" (a bundled validation template) while the page itself shows the application. Nothing a
#: person can read on it refuses anything.
MARKUP_WITH_INVALID = (
    '<html><head><title>Portal</title>'
    '<script>var MSG={invalid:"Invalid user name or password",failed:"Login failed"};</script>'
    '</head><body><div id="passwordInput" style="display:none"></div>'
    '<div class="shell">Reports Commissions Logout</div></body></html>'
)

#: What a person actually SEES on that same page.
VISIBLE_OF_THAT_PAGE = "Portal Reports Commissions Logout"


def test_the_bundled_word_invalid_does_not_mean_the_credential_was_refused():
    """THE DEFECT. Markup saying "invalid" while the application is on screen is NOT a rejection."""
    assert lo.rejection_said(VISIBLE_OF_THAT_PAGE) is None
    v = lo.verdict(form_present=False, visible_text=VISIBLE_OF_THAT_PAGE)
    assert v["outcome"] == lo.SIGNED_IN
    assert not lo.is_credential_problem(v["outcome"])


def test_the_same_words_in_markup_are_inert_even_with_the_password_control_in_the_dom():
    """The old predicate's exact conjunction - "invalid" AND a password control - now decides
    nothing on its own. The form is hidden, so structurally it is not present."""
    assert "invalid" in MARKUP_WITH_INVALID.lower()
    assert "passwordinput" in MARKUP_WITH_INVALID.lower()
    v = lo.verdict(form_present=False, visible_text=VISIBLE_OF_THAT_PAGE)
    assert v["outcome"] != lo.REJECTED


def test_a_real_refusal_beside_a_real_form_is_still_caught():
    """The fix must not make the platform blind to a genuine rejection."""
    v = lo.verdict(form_present=True,
                   visible_text="Sign in\nInvalid user name or password.\nUser name Password")
    assert v["outcome"] == lo.REJECTED
    assert v["marker"] == "invalid"
    assert lo.is_credential_problem(v["outcome"])


# ── THE FIVE OUTCOMES STAY FIVE, EACH WITH ITS OWN REMEDY ────────────────────────────────────────

def test_a_human_check_is_not_a_password_problem():
    v = lo.verdict(form_present=False, visible_text="Please verify you are human",
                   page_verdict={"kind": "challenge", "marker": "verify you are human"})
    assert v["outcome"] == lo.HUMAN_CHECK
    assert not lo.is_credential_problem(v["outcome"])
    assert "password is not implicated" in v["note"]


def test_a_refusal_page_is_not_a_password_problem():
    v = lo.verdict(form_present=False,
                   visible_text="The requested URL was rejected. Please consult with your administrator.",
                   page_verdict={"kind": "refusal", "marker": "request is blocked"})
    assert v["outcome"] == lo.REFUSED
    assert not lo.is_credential_problem(v["outcome"])


def test_a_challenge_is_disbelieved_while_the_form_is_on_screen():
    """§COMMCALC-50.8's rule, applied here rather than copied: a page SHOWING us the form has not
    served something instead of it. A sign-in page carrying a reCAPTCHA is the common case."""
    v = lo.verdict(form_present=True, visible_text="Sign in to continue. User name Password",
                   page_verdict={"kind": "challenge", "marker": "captcha"})
    assert v["outcome"] == lo.CHANGED
    assert v["outcome"] != lo.HUMAN_CHECK


def test_a_form_that_says_nothing_is_a_changed_flow_and_names_a_developer():
    v = lo.verdict(form_present=True, visible_text="Sign in to continue. User name Password")
    assert v["outcome"] == lo.CHANGED
    assert not lo.is_credential_problem(v["outcome"])
    assert "developer" in v["note"]
    assert "re-entering the credential will not help" in v["note"]


def test_nothing_recognised_is_reported_as_nothing_recognised():
    v = lo.verdict(form_present=False, visible_text="")
    assert v["outcome"] == lo.UNKNOWN
    assert not lo.is_credential_problem(v["outcome"])


def test_the_portals_refusal_wins_over_a_stray_application_marker():
    """A live form that is complaining is the most specific fact on the page: a leftover "Logout"
    link in a header must not turn a refusal into a successful sign-in."""
    v = lo.verdict(form_present=True,
                   visible_text="Logout\nSign in\nYour password is incorrect.")
    assert v["outcome"] == lo.REJECTED


# ── NO BRAND NAME DECIDES ANYTHING (RULE TWO) ────────────────────────────────────────────────────

def test_success_does_not_depend_on_a_portals_own_name():
    """The replaced predicate could only recognise success by the vendor's words in the title, so a
    retitled shell read as a failed login for every tenant. Any application's own words now do."""
    for shell in ("Welcome - Sign out", "Home | My Account", "Dashboard"):
        v = lo.verdict(form_present=False, visible_text=shell)
        assert v["outcome"] == lo.SIGNED_IN, shell


def test_no_vendor_or_tenant_name_decides_anything_in_the_module():
    """RULE TWO, asserted rather than trusted: no vendor or tenant name may appear in EXECUTABLE
    text. The scan strips the docstrings and comments first, on the house precedent that a comment
    naming the defect a fix is about otherwise reads as that fix's own violation - and this module's
    docstring has to quote the production rows it was measured from to be worth anything."""
    src = lo_executable_source().lower()
    for word in ("epay", "vidapay", "boost", "cellfonz", "luxelink", "vzone", "t-cetra",
                 "b2bsoft", "total access", "owner portal"):
        assert word not in src, f"{word!r} is a vendor/tenant name and must not decide anything here"


def lo_executable_source():
    """The module's source with every docstring and comment removed - what actually RUNS."""
    import ast
    import inspect
    import io
    import tokenize
    src = inspect.getsource(lo)
    # drop comments via the tokenizer, then drop docstring expressions via the AST
    kept = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        kept.append(tok[:2])
    stripped = tokenize.untokenize(kept)
    tree = ast.parse(stripped)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body.pop(0)
    return ast.unparse(tree)


# ── THE EVIDENCE IS REQUIRED, NEVER DEFAULTED ────────────────────────────────────────────────────

def test_a_caller_that_has_not_looked_at_the_page_gets_no_answer():
    """Both evidence arguments are keyword-only with NO default. A default here would be this module
    answering on a driver's behalf, which is exactly how the markup predicate always had something
    to say."""
    with pytest.raises(TypeError):
        lo.verdict()
    with pytest.raises(TypeError):
        lo.verdict(form_present=True)
    with pytest.raises(TypeError):
        lo.verdict(visible_text="whatever")
    with pytest.raises(TypeError):
        lo.verdict(True, "whatever")        # positional is refused too


# ── THE ALLOWLIST IS AN ALLOWLIST ────────────────────────────────────────────────────────────────

def test_only_a_rejection_is_a_credential_problem():
    assert lo.is_credential_problem(lo.REJECTED)
    for other in (lo.SIGNED_IN, lo.HUMAN_CHECK, lo.REFUSED, lo.CHANGED, lo.UNKNOWN):
        assert not lo.is_credential_problem(other), other


def test_an_unruled_outcome_reads_as_not_a_credential_problem():
    """An allowlist, not a denylist: a word nobody has ruled on stays quiet about the password."""
    assert not lo.is_credential_problem("something_invented_later")
    assert not lo.is_credential_problem(None)


def test_every_outcome_has_an_operator_sentence_except_success():
    for o in lo.ALL_OUTCOMES:
        n = lo.note(o)
        assert isinstance(n, str)
        if o == lo.SIGNED_IN:
            assert n == ""
        else:
            assert len(n) > 40, o


def test_note_never_raises_and_never_returns_none():
    for bad in (None, "", 0, [], {"kind": "x"}):
        assert lo.note(bad) == ""


def test_signed_in_is_the_one_test_for_being_in():
    assert lo.signed_in(lo.SIGNED_IN)
    for o in lo.FAILURE_OUTCOMES:
        assert not lo.signed_in(o), o
    assert set(lo.FAILURE_OUTCOMES) | {lo.SIGNED_IN} == set(lo.ALL_OUTCOMES)


def test_the_status_line_quotes_the_portal_rather_than_paraphrasing_it():
    msg = lo.message(lo.REJECTED, marker="account is locked", prefix="the owner portal")
    assert msg.startswith("the owner portal sign-in:")
    assert '"account is locked"' in msg
    assert "re-enter" in msg


# ── A DRIVER WITH NO BROWSER GETS THE SAME TWO FACTS, FROM THE SAME HOME ─────────────────────────
# `dlar_sweep` speaks plain HTTP: it holds a document, not a page. It must be able to ask the same
# question, which means recovering the READABLE text first and deciding from that - never deciding
# from the document. These tests are the proof that the recovery drops exactly what it must.

BUNDLED_HTML = (
    '<html><head><title>Portal</title>'
    '<script>var M={invalid:"Invalid user name or password",failed:"Login failed"};</script>'
    '<style>.err:after{content:"incorrect"}</style></head>'
    '<body><!-- login failed --><div>Commissions Reports Logout</div></body></html>'
)


def test_a_scripts_words_do_not_reach_the_readable_text():
    vis = lo.visible_from_html(BUNDLED_HTML)
    assert "invalid" not in vis.lower()
    assert "incorrect" not in vis.lower()
    assert "failed" not in vis.lower()


def test_what_a_person_reads_does_reach_it():
    vis = lo.visible_from_html(BUNDLED_HTML)
    assert "Commissions" in vis and "Logout" in vis


def test_the_bundled_word_page_is_not_a_rejection_from_html_either():
    """The same regression, through the HTTP driver's door rather than the browser's."""
    vis = lo.visible_from_html(BUNDLED_HTML)
    v = lo.verdict(form_present=lo.form_in_html(BUNDLED_HTML), visible_text=vis)
    assert v["outcome"] == lo.SIGNED_IN
    assert not lo.is_credential_problem(v["outcome"])


def test_a_sign_in_form_is_recognised_by_its_password_input_not_by_prose():
    assert lo.form_in_html('<form><input name="pw" type="password" /></form>')
    assert lo.form_in_html("<INPUT TYPE='PASSWORD'>")
    assert not lo.form_in_html("<p>Please enter your password on the portal.</p>")
    assert not lo.form_in_html("")
    assert not lo.form_in_html(None)


def test_a_real_refusal_in_html_is_still_caught():
    html = '<form><input type="password"></form><p class="error">Invalid credentials.</p>'
    v = lo.verdict(form_present=lo.form_in_html(html),
                   visible_text=lo.visible_from_html(html))
    assert v["outcome"] == lo.REJECTED
    assert lo.is_credential_problem(v["outcome"])


def test_entities_are_unescaped_and_whitespace_collapsed():
    assert lo.visible_from_html("<p>a &amp;  b</p>\n\n<p>c</p>") == "a & b c"


def test_the_html_readers_never_raise_on_junk():
    for junk in (None, "", 0, "<<<>>", "<script>", b"bytes"):
        assert isinstance(lo.visible_from_html(junk), str)
        assert isinstance(lo.form_in_html(junk), bool)


# ── THE THIRD DRIVER'S DISTINCTION, WHICH USED TO BE THE OPERATOR'S JOB ──────────────────────────
# `vidapay_sweep` lands on `state == "login"` after a submit and used to assert "Login rejected"
# for every such case, then ask the operator to decide from a diagnostic dict whether the portal had
# actually refused anything. Its evidence is already the right kind - the VISIBLE innerText of the
# page's error region - so the distinction belongs here, made once.

def test_a_portal_that_said_something_refused_us():
    """What `_b2b_error(page)` returns when the portal complained."""
    said = " ".join(["Invalid user name or password."])
    v = lo.verdict(form_present=True, visible_text=said)
    assert v["outcome"] == lo.REJECTED
    assert lo.is_credential_problem(v["outcome"])


def test_a_portal_that_said_nothing_has_changed_its_flow():
    """An EMPTY error region is the case the hedging prose was really about: the form is still
    there, the portal explained nothing, and no credential will fix that."""
    v = lo.verdict(form_present=True, visible_text=" ".join([]))
    assert v["outcome"] == lo.CHANGED
    assert not lo.is_credential_problem(v["outcome"])


def test_an_error_region_listing_several_messages_still_resolves():
    said = " ".join(["Please correct the errors below.", "Your account has been locked."])
    v = lo.verdict(form_present=True, visible_text=said)
    assert v["outcome"] == lo.REJECTED
    assert v["marker"] == "been locked"
