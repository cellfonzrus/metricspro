"""A PAGE THAT IS SHOWING US THE FORM DID NOT SERVE SOMETHING INSTEAD OF IT.

Owner, 2026-10-31: *"the ups portal is not getting the iuser id and password autofilled in teh login
screen"*. He was looking at the real UPS Store FRS sign-in form, with Username and Password empty.

MEASURED against the live page from the container on 2026-10-31 (no proxy, default headless
Chromium, HTTP 200): `frs.theupsstore.com/UI/Reporting/POS/POSReports.aspx` redirects to
`/Framework/SignIn.aspx`, which renders `#UsernameTextBox` and `#UserPasswordTextBox`, both
visible. There is no WAF page, no IP block and no bot wall. But the markup contains the word
"captcha" NINE times — the `recaptcha.net/api.js` include, a `renderCaptcha = 'True'` flag, three
code comments, and `<div id="SignInCaptcha" class="g-recaptcha" data-size="invisible"
data-callback="captchaSuccessCallback">`. None of it is in the page's visible text.

THE DEFECT. `portal_backoff.page_verdict` matches its marker list against `_page_text(page)`, which
is the page's MARKUP plus its text. `"captcha"` is a seeded challenge marker, so the genuine form
read as `kind="challenge"`. `_looks_like_cloudflare` is that verdict, and `_wait_settle` spins on
it, reloads, spins again and then raises *"The portal's WAF is blocking this egress IP (bot
challenge did not clear, even after a reload)"* — from inside `_goto_login`, BEFORE
`_wait_for_password` is ever reached. So nothing was ever typed into a form that was sitting there
the whole time.

THE CLASS. *A page was judged by how it is BUILT, not by what it is DOING.* `page_verdict`'s whole
claim is "this page is refusing or challenging us RATHER THAN showing a form" — and it never looked
for the form. Any portal whose login page carries a reCAPTCHA (most of them) is read the same way,
for every tenant, which is why this is not a UPS fix.

THE SIBLING, already fixed once. "Is a human check gating this sign-in?" has had ONE home since
§COMMCALC-11: `vidapay_sweep.human_check(page)`, which reads the DOM and distinguishes a widget
that is armed from one that is merely included. `page_verdict`'s `"captcha"` marker was a SECOND
answer to that same question, by text search over markup, and it ran first. Two paths, one
question — and the cruder one won.
"""
import pytest

from app.modules.commcalc import portal_backoff as pb
from app.modules.commcalc import vidapay_sweep as vp
from tests.fakes import portal_page as fp

HOUSE = "00000000-0000-0000-0000-000000000001"
LUXELINK = "11111111-1111-1111-1111-111111111111"


# ── the rule, in the vocabulary's own home ──────────────────────────────────────────────────────
def test_page_verdict_withholds_a_verdict_when_the_form_is_on_the_page():
    """The claim is 'instead of a form'. With the form present, the claim is false."""
    markup = fp.fixture("frs_signin_excerpt.html").lower()
    assert "captcha" in markup, "fixture guard: this really is a page whose markup says captcha"

    assert pb.page_verdict(markup) is not None, \
        "control: without the form question, the markup alone still reads as a verdict"
    assert pb.page_verdict(markup, form_present=True) is None


def test_a_formless_challenge_page_is_still_a_challenge():
    """Cloudflare's interstitial has no form, and its markers ARE markup fingerprints. Untouched."""
    page = fp.cloudflare_interstitial()
    soup = (page.content() + page.inner_text() + page.title()).lower()
    v = pb.page_verdict(soup, form_present=False)
    assert v is not None and v["kind"] == pb.CHALLENGE_KIND


def test_a_refusal_page_that_shows_no_form_is_still_a_refusal():
    """The owner's UPS *billing* refusal (2026-10-30, §COMMCALC-50) must not be weakened by this."""
    ups = ("UPS Something went wrong, or you are trying to perform an action that is not allowed. "
           "Please hit back on your browser and try again.")
    v = pb.page_verdict(ups, form_present=False)
    assert v is not None and v["kind"] == pb.REFUSAL_KIND


def test_the_form_rule_also_covers_a_refusal_marker_in_the_markup():
    """One rule, both kinds: a page handing us a form has refused us nothing."""
    assert pb.page_verdict("access denied", form_present=True) is None


# ── the driver dereferences it ──────────────────────────────────────────────────────────────────
def test_login_form_present_is_one_home_over_the_password_frame():
    assert vp.login_form_present(fp.frs_signin()) is True
    assert vp.login_form_present(fp.plain_login()) is True
    assert vp.login_form_present(fp.cloudflare_interstitial()) is False


def test_page_refusal_clears_the_real_ups_sign_in_page():
    """The regression, end to end through the driver's one reader."""
    assert vp.page_refusal(fp.frs_signin()) is None


def test_the_three_predicates_built_on_it_all_clear_the_page():
    page = fp.frs_signin()
    assert vp._looks_like_cloudflare(page) is False
    assert vp._looks_like_bot_wall(page) is False
    assert vp._rate_limit_hit(page) is None


def test_wait_settle_no_longer_raises_the_waf_error_on_a_working_form():
    """This is the exact failure the owner hit: a 60-second spin ending in a WAF accusation."""
    page = fp.frs_signin()
    vp._wait_settle(page, timeout_s=3)
    assert page.reloads == 0, "it must not even reload a page that is showing the form"


def test_wait_settle_still_reloads_and_then_refuses_a_real_challenge():
    page = fp.cloudflare_interstitial()
    with pytest.raises(vp.VidaPayLoginError) as err:
        vp._wait_settle(page, timeout_s=1)
    assert "WAF" in str(err.value)
    assert page.reloads == 1, "the one reload that picks up a cf_clearance cookie is still spent"


# ── the human check is answered by the DOM home, not by the markup ──────────────────────────────
def test_the_armed_invisible_widget_is_still_recognised():
    """Withholding the challenge verdict must not lose the check — §COMMCALC-11 still answers."""
    assert vp.human_check(fp.frs_signin()) == "invisible"
    assert vp.human_check(fp.plain_login()) == ""


# ── every tenant, including one with its own configured vocabulary ──────────────────────────────
@pytest.mark.parametrize("org", [HOUSE, LUXELINK, "22222222-2222-2222-2222-222222222222"])
def test_a_tenants_own_configured_markers_obey_the_same_rule(org):
    """`portal_block_marker` is per-org config (RULE TWO). A tenant that declares its own challenge
    words gets the same corroboration — otherwise the fix would hold for the house seeds only."""
    declared = ["captcha", "robot check", "%s-specific-wall" % org[:4]]
    markup = fp.fixture("frs_signin_excerpt.html").lower()
    assert pb.page_verdict(markup, challenge=declared) is not None, "control: it does match"
    assert pb.page_verdict(markup, challenge=declared, form_present=True) is None


def test_no_tenant_or_portal_is_named_in_the_rule():
    """The fix is in the mechanism, not in a branch for the portal that surfaced it (RULE TWO).

    Docstrings and comments SHOULD cite the report — that is house style, and the citation is how
    the next reader knows why the rule exists. What may not exist is a portal or tenant name in a
    line the interpreter runs, so the source is stripped of comments and strings before the look.
    """
    import ast
    import inspect
    import textwrap

    for fn in (pb.page_verdict, vp.page_refusal, vp.login_form_present,
               vp._looks_like_cloudflare, vp.prefill_login):
        src = inspect.getsource(fn)
        tree = ast.parse(textwrap.dedent(src))
        for node in ast.walk(tree):          # drop every docstring and literal
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                node.value = ""
        code = ast.unparse(tree).lower()
        for word in ("theupsstore", "luxelink", "cellfonz", "vzone", "recaptcha", "captcha",
                     "cloudflare" if fn is not vp._looks_like_cloudflare else "\x00"):
            assert word not in code, \
                "%r is named in %s's code — that is per-portal code, not a design fix" % (
                    word, fn.__name__)
