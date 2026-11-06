"""A PAGE THAT GREETS YOU BY THE NAME YOU SIGNED IN WITH IS A PAGE YOU ARE SIGNED IN TO.

The regression (index §COMMCALC-55, owner screenshot 2026-11-05). §COMMCALC-52 replaced "the
vendor's name is in the document title" — a carrier name in code — with a word list:
Logout / Log out / Sign out / My account / Dashboard. The owner then saved the right password, the
sweep signed in correctly, and the platform said:

    "The sign-in page went away and we did not recognise what replaced it."

The owner's screenshot of that very page shows why. It carries:

    Home                     Hello rajiv.jaggi@cellularservices.net ▾
    Multi-Door Owner Portal
    Commissions ▾   Boost Activations ▾
    © 2015  Privacy Security

A correct, complete sign-in — and not ONE of the five house words on it. Removing the brand name
was right; replacing it with a house word list was a second guess at a tenant's vocabulary, which
is the thing the house rules ban (*"a vocabulary a tenant declares is a REQUIRED argument, never a
default with house words behind it"*). A word list cannot be made long enough to be right.

THE GENERAL FACT, which needs no vocabulary at all: **the page is showing the user name we just
submitted.** An anonymous page cannot greet you by your own account name. That is evidence, not
words — true of any portal, for any tenant, with no brand anywhere in it — so it goes in the one
home as a signal beside the word list, and the words themselves become declarable per org for a
portal that greets nobody.
"""
import pytest

from app.modules.commcalc import login_outcome as lo


#: The owner's own screenshot, 2026-11-05, read off the image — the visible text of a page that IS
#: signed in and that the word list cannot recognise.
BOOST_LANDING = (
    "Home Hello rajiv.jaggi@cellularservices.net\n"
    "Multi-Door Owner Portal\n"
    "Commissions Boost Activations\n"
    "© 2015 Privacy Security"
)
USER = "rajiv.jaggi@cellularservices.net"


# ── THE REGRESSION ───────────────────────────────────────────────────────────────────────────────

def test_the_landing_page_really_carries_none_of_the_house_words():
    """Asserted rather than asserted-about: this is why the word list failed."""
    body = BOOST_LANDING.lower()
    assert not any(w in body for w in lo.DEFAULT_SIGNED_IN_MARKERS)


def test_without_the_user_name_that_page_is_still_unrecognised():
    """The old behaviour, pinned. A caller that does not say who it signed in as gets `unknown` —
    honest, and exactly what the owner saw."""
    v = lo.verdict(form_present=False, visible_text=BOOST_LANDING)
    assert v["outcome"] == lo.UNKNOWN


def test_the_greeting_is_recognised_as_signed_in():
    """THE FIX."""
    v = lo.verdict(form_present=False, visible_text=BOOST_LANDING, signed_in_user=USER)
    assert v["outcome"] == lo.SIGNED_IN
    assert lo.signed_in(v["outcome"])


def test_the_marker_quotes_the_name_that_was_found():
    v = lo.verdict(form_present=False, visible_text=BOOST_LANDING, signed_in_user=USER)
    assert USER.lower() in str(v["marker"]).lower()


# ── THE SIGNAL IS EVIDENCE, AND IT IS CAREFUL ────────────────────────────────────────────────────

def test_the_name_is_matched_whatever_the_case():
    v = lo.verdict(form_present=False, visible_text="Hello RAJIV.JAGGI@CELLULARSERVICES.NET",
                   signed_in_user=USER)
    assert v["outcome"] == lo.SIGNED_IN


@pytest.mark.parametrize("name", ["", "   ", None])
def test_no_user_name_means_no_signal(name):
    """A blank is not a greeting. An empty needle is in every haystack, which would make every
    page in the world read as signed in — including a refusal page."""
    assert lo.verdict(form_present=False, visible_text=BOOST_LANDING,
                      signed_in_user=name)["outcome"] == lo.UNKNOWN


@pytest.mark.parametrize("name", ["a", "ab", "nova", "sj"])
def test_a_short_user_name_is_not_trusted_as_a_greeting(name):
    """A two- or four-letter login is a substring of ordinary prose. `nova` is a real user name in
    this platform and appears inside "Innovation"; a signal that fires on that is worse than none,
    because it would call a REFUSAL page a successful login."""
    page = "Innovation notice. Your session could not be started."
    assert lo.verdict(form_present=False, visible_text=page,
                      signed_in_user=name)["outcome"] != lo.SIGNED_IN


def test_the_login_form_echoing_the_name_is_not_a_greeting():
    """THE TRAP THIS SIGNAL MUST SURVIVE. A sign-in form that failed usually re-renders with the
    user name still typed into it, so the name IS on the page while we are plainly still at the
    door. The form being present must win."""
    page = "Sign in\nUser name: %s\nPassword:\nInvalid user name or password" % USER
    v = lo.verdict(form_present=True, visible_text=page, signed_in_user=USER)
    assert v["outcome"] == lo.REJECTED


def test_a_name_on_a_page_that_still_shows_the_form_is_never_signed_in():
    """Even with no complaint on it: the form is up, so we are not through the door."""
    page = "Sign in as %s" % USER
    v = lo.verdict(form_present=True, visible_text=page, signed_in_user=USER)
    assert v["outcome"] != lo.SIGNED_IN


def test_a_human_check_naming_the_user_is_not_a_greeting():
    """A bot wall can address you by name. Structure first: the application shell is what proves
    we are in, and a challenge page is not it."""
    page = "Verify it's you, %s. Complete the security check to continue." % USER
    v = lo.verdict(form_present=False, visible_text=page, signed_in_user=USER,
                   page_verdict={"kind": "challenge", "marker": "security check"})
    assert v["outcome"] == lo.HUMAN_CHECK


# ── THE WORDS THEMSELVES ARE DECLARABLE, SO THE NEXT PORTAL NEEDS NO CODE ────────────────────────

def test_a_tenant_can_declare_its_own_signed_in_wording():
    v = lo.verdict(form_present=False, visible_text="Multi-Door Owner Portal",
                   signed_in_markers=("multi-door owner portal",))
    assert v["outcome"] == lo.SIGNED_IN
    assert v["marker"] == "multi-door owner portal"


def test_a_declaration_replaces_the_house_words_rather_than_adding_to_them():
    """A tenant that has declared its own vocabulary has ANSWERED the question; the house words
    must not keep answering underneath it."""
    v = lo.verdict(form_present=False, visible_text="Log out",
                   signed_in_markers=("multi-door owner portal",))
    assert v["outcome"] == lo.UNKNOWN


def test_the_greeting_works_alongside_a_declaration():
    """The two signals are independent: declaring words must not switch the name signal off."""
    v = lo.verdict(form_present=False, visible_text=BOOST_LANDING,
                   signed_in_markers=("nothing that is on this page",), signed_in_user=USER)
    assert v["outcome"] == lo.SIGNED_IN


# ── EVERY TENANT ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("user,page", [
    ("sanjot", "Welcome back, sanjot"),
    ("ups7710", "Signed in: ups7710 | Orders"),
    ("dchauhan", "Hello dchauhan"),
    ("b2breports@metricspro.tech", "Account: b2breports@metricspro.tech"),
])
def test_any_tenants_portal_that_greets_its_user_is_recognised(user, page):
    """No brand name, no tenant's words — the same evidence works for all of them."""
    assert lo.verdict(form_present=False, visible_text=page,
                      signed_in_user=user)["outcome"] == lo.SIGNED_IN


def test_one_tenants_user_name_never_signs_another_one_in():
    v = lo.verdict(form_present=False, visible_text="Hello someone.else@example.com",
                   signed_in_user=USER)
    assert v["outcome"] == lo.UNKNOWN


# ── THE EVIDENCE ARGUMENTS STAY REQUIRED ─────────────────────────────────────────────────────────

def test_the_new_argument_is_keyword_only_and_optional():
    """`signed_in_user` is EVIDENCE a caller may not have (a driver that signs in with a token has
    no user name), so unlike form_present/visible_text it is optional — but it is keyword-only, so
    it can never be passed by accident in the position of something else."""
    import inspect
    p = inspect.signature(lo.verdict).parameters["signed_in_user"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY
    assert p.default is None
    for required in ("form_present", "visible_text"):
        assert inspect.signature(lo.verdict).parameters[required].default is inspect.Parameter.empty
