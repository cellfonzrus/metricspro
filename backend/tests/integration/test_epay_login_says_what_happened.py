"""THE REAL DRIVER'S LOGIN, DRIVEN AGAINST A FAKE PAGE.

The unit tests next door prove the one home's RULE. These prove the DRIVER dereferences it: that
`epay_sweep._login` reads structure and visible text off the page it is handed, and that the three
pages which used to produce one indistinguishable "credentials rejected" now produce three
different, actionable outcomes.

Integration rather than unit because this exercises the real module under the real app's import
path, with no Playwright, no browser and no portal: the page is a double that answers the handful of
calls the driver makes on it. `tests/conftest.py` blocks every outbound socket, so this cannot reach
a portal even by accident.

Index §COMMCALC-50.9. The regression is the owner's report of 2026-10-31: three carrier feeds dark
since 2026-10-09, 1,056 consecutive runs all saying "credentials rejected".
"""
import pytest

from app.modules.commcalc import epay_sweep
from app.modules.commcalc import login_outcome as lo


class FakePage:
    """The smallest page the driver actually uses: a password control that is there or not, a
    visible text, and the selectors `_login` fills and clicks.

    `markup` exists ONLY so a test can prove it is NOT consulted. Nothing in the driver may read it.
    """

    def __init__(self, *, visible="", markup="", password_visible=True, has_password_node=True,
                 username_node=True, submit_node=True, never_appears=False):
        self.visible = visible
        self.markup = markup
        self._password_visible = password_visible
        self._has_password_node = has_password_node
        self._username_node = username_node
        self._submit_node = submit_node
        self.never_appears = never_appears
        self.filled = {}
        self.clicked = []
        self.content_reads = 0

    # — what the driver is allowed to read —
    def evaluate(self, js):
        if "innerText" in js:
            return self.visible
        return ""

    def query_selector(self, sel):
        if sel in ("#passwordInput", "input[type=password]"):
            if not self._has_password_node:
                return None
            return _El(self._password_visible)
        if sel == "#usernameInput":
            return _El(True) if self._username_node else None
        if sel in ("#loginButton", "button[type=submit]"):
            return _Btn(self) if self._submit_node else None
        return None

    def wait_for_selector(self, sel, timeout=None):
        if self.never_appears:
            raise TimeoutError(f"Timeout {timeout}ms exceeded waiting for {sel}")
        return _El(True)

    def fill(self, sel, value):
        self.filled[sel] = value

    def press(self, sel, key):
        self.clicked.append((sel, key))

    def wait_for_load_state(self, state, timeout=None):
        return None

    def wait_for_timeout(self, ms):
        return None

    def title(self):
        return ""

    # — the trap —
    def content(self):
        self.content_reads += 1
        return self.markup


class _El:
    def __init__(self, visible):
        self._v = visible

    def is_visible(self):
        return self._v


class _Btn(_El):
    def __init__(self, page):
        super().__init__(True)
        self._page = page

    def click(self):
        self._page.clicked.append("submit")


# ── THE SHAPE THAT CAUSED THE OUTAGE ─────────────────────────────────────────────────────────────

SPA_MARKUP_THAT_KNOWS_THE_WORD = (
    '<html><head><title>Portal</title>'
    '<script>var M={invalid:"Invalid user name or password",failed:"Login failed"};</script></head>'
    '<body><div id="passwordInput" style="display:none"></div>'
    '<div>Commissions Reports Logout</div></body></html>'
)


def test_a_signed_in_shell_is_not_reported_as_a_rejected_credential():
    """THE REGRESSION. The application is on screen; its markup merely bundles the word "invalid".
    The old predicate raised "credentials rejected" here — 1,056 times in a row."""
    page = FakePage(visible="Portal Commissions Reports Logout",
                    markup=SPA_MARKUP_THAT_KNOWS_THE_WORD,
                    has_password_node=True, password_visible=False)
    v = epay_sweep._login(page, "u", "p")
    assert lo.signed_in(v["outcome"])


def test_the_driver_never_reads_the_markup_to_decide_a_login():
    """Structural proof, not a code search: the double counts `content()` calls and the driver must
    make none. This is the lock on the class — a page judged by how it is BUILT."""
    page = FakePage(visible="Portal Commissions Reports Logout",
                    markup=SPA_MARKUP_THAT_KNOWS_THE_WORD, password_visible=False)
    epay_sweep._login(page, "u", "p")
    assert page.content_reads == 0, "the login decision read page.content() — that is the defect"


def test_the_credentials_are_still_filled_and_submitted():
    """The fix must not stop the driver logging in. Field names only; never the value."""
    page = FakePage(visible="Commissions Logout", password_visible=False)
    epay_sweep._login(page, "someone@example.test", "a-password")
    assert page.filled["#usernameInput"] == "someone@example.test"
    assert page.filled["#passwordInput"] == "a-password"
    assert "submit" in page.clicked


# ── THREE PAGES, THREE OUTCOMES, THREE REMEDIES ──────────────────────────────────────────────────

def test_a_real_refusal_is_reported_as_a_credential_to_fix():
    page = FakePage(visible="Sign in\nInvalid user name or password.", password_visible=True)
    with pytest.raises(epay_sweep.EpayLoginError) as e:
        epay_sweep._login(page, "u", "p")
    msg = str(e.value)
    assert "re-enter" in msg
    assert '"invalid"' in msg


def test_a_locked_account_is_reported_in_the_portals_own_words():
    """A portal that locks an account after repeated attempts says so, and the operator needs to
    read that rather than a paraphrase — the password may be perfectly correct."""
    page = FakePage(visible="Sign in\nYour account is locked. Contact your administrator.",
                    password_visible=True)
    with pytest.raises(epay_sweep.EpayLoginError) as e:
        epay_sweep._login(page, "u", "p")
    assert '"is locked"' in str(e.value)


def test_a_form_that_says_nothing_names_a_developer_not_a_password():
    page = FakePage(visible="Sign in to continue\nUser name\nPassword", password_visible=True)
    with pytest.raises(epay_sweep.EpayLoginError) as e:
        epay_sweep._login(page, "u", "p")
    msg = str(e.value)
    assert "developer" in msg
    assert "re-entering the credential will not help" in msg


def test_a_sign_in_page_carrying_a_human_check_marker_is_not_a_human_check():
    """§COMMCALC-50.8's rule reaching this driver too: the form is on screen, so the page has not
    served something instead of it. A sign-in page with a reCAPTCHA is the everyday case."""
    page = FakePage(visible="Sign in to continue. Please verify you are human.",
                    password_visible=True)
    with pytest.raises(epay_sweep.EpayLoginError) as e:
        epay_sweep._login(page, "u", "p")
    assert "developer" in str(e.value)        # CHANGED, not HUMAN_CHECK


# ── NO FORM EVER APPEARED ────────────────────────────────────────────────────────────────────────

def test_a_portal_refusal_page_says_so_instead_of_naming_a_selector():
    """What this container measures live on the portal's own host: an F5 rejection page. The driver
    used to surface "Page.wait_for_selector: Timeout 30000ms exceeded", which names no remedy."""
    page = FakePage(never_appears=True,
                    visible="Request Rejected. The requested URL was rejected. "
                            "Please consult with your administrator.")
    with pytest.raises(epay_sweep.EpayLoginError) as e:
        epay_sweep._login(page, "u", "p")
    msg = str(e.value)
    assert "wait_for_selector" not in msg
    assert "password is not implicated" in msg


def test_a_human_check_before_the_form_wants_a_live_sign_in_not_a_password():
    page = FakePage(never_appears=True, visible="Attention Required! Please verify you are human.")
    with pytest.raises(epay_sweep.EpayLoginError) as e:
        epay_sweep._login(page, "u", "p")
    assert "live screen" in str(e.value)


def test_a_silent_missing_form_is_reported_as_silent_not_guessed_at():
    page = FakePage(never_appears=True, visible="")
    with pytest.raises(epay_sweep.EpayLoginError) as e:
        epay_sweep._login(page, "u", "p")
    assert "did not say why" in str(e.value)


# ── EVERY TENANT, NOT ONE ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("shell", [
    "Welcome back - Sign out",            # one portal's wording
    "Home | My Account | Reports",        # another's
    "Dashboard Commissions",              # a third's
])
def test_any_tenants_portal_shell_reads_as_signed_in(shell):
    """The replaced predicate recognised success ONLY by one vendor's words in the document title,
    so every other tenant's portal read as a failed login. A feature is for every tenant."""
    page = FakePage(visible=shell, password_visible=False)
    v = epay_sweep._login(page, "u", "p")
    assert lo.signed_in(v["outcome"]), shell


def test_no_portal_or_tenant_name_decides_the_outcome():
    """A shell with none of the house portal's words still signs in; a form with them still fails.
    Nothing about WHICH portal it is may enter the decision."""
    page = FakePage(visible="Painel - Sair? no: Logout", password_visible=False)
    assert lo.signed_in(epay_sweep._login(page, "u", "p")["outcome"])


# ── THE PAGE SHOWING US OUR OWN NAME (index §COMMCALC-55) ────────────────────────────────────────
#
# Everything above still only recognises a portal that uses one of six house words — which is the
# platform guessing at a vocabulary it does not own, and the owner's own portal uses none of them.
# These drive the REAL driver against that page, and against the config route for a portal that
# greets nobody at all.

OWNER_PAGE = ("Home Hello rajiv.jaggi@cellularservices.net\n"
              "Multi-Door Owner Portal\n"
              "Commissions Boost Activations\n"
              "© 2015 Privacy Security")
OWNER_USER = "rajiv.jaggi@cellularservices.net"


def test_the_owners_own_signed_in_page_reads_as_signed_in():
    """THE REGRESSION, from the owner's screenshot of 2026-11-05. He signed in by hand and the sweep
    on the same credential reported "the sign-in page went away and we did not recognise what
    replaced it", with three carrier feeds frozen on a login that worked."""
    page = FakePage(visible=OWNER_PAGE, password_visible=False, has_password_node=False)
    v = epay_sweep._login(page, OWNER_USER, "p")
    assert lo.signed_in(v["outcome"])
    assert OWNER_USER in str(v.get("marker") or "")


def test_that_page_carries_none_of_the_house_words_which_is_why_it_broke():
    """Guards the premise of the test above: if somebody adds "home" to the house list, the test
    would still pass while proving nothing about the greeting."""
    assert lo.signed_in_said(OWNER_PAGE) is None


def test_a_portal_that_greets_nobody_is_recognised_from_a_DECLARED_row():
    """CONFIG, NEVER CODE. A page with no house word and no greeting is honestly unrecognised — and
    becomes recognised when the tenant declares its own wording, with no code change."""
    bare = "Multi-Door Owner Portal\nCommissions"
    page = FakePage(visible=bare, password_visible=False, has_password_node=False)
    with pytest.raises(epay_sweep.EpayLoginError):
        epay_sweep._login(page, "tok", "p")          # a token login: no user name to greet
    page2 = FakePage(visible=bare, password_visible=False, has_password_node=False)
    v = epay_sweep._login(page2, "tok", "p", signed_in_markers=("multi-door owner portal",))
    assert lo.signed_in(v["outcome"])


def test_a_greeting_never_overturns_the_portals_own_refusal():
    """The worst failure available to this signal: reading a refusal as a success. A refused form
    re-renders with the user name still typed into it."""
    page = FakePage(visible="Sign in\n%s\nInvalid user name or password." % OWNER_USER,
                    password_visible=True)
    with pytest.raises(epay_sweep.EpayLoginError) as e:
        epay_sweep._login(page, OWNER_USER, "p")
    assert "re-enter" in str(e.value)


def test_a_human_check_that_knows_our_name_is_still_a_human_check():
    page = FakePage(never_appears=True,
                    visible="Attention Required! %s, please verify you are human." % OWNER_USER)
    with pytest.raises(epay_sweep.EpayLoginError) as e:
        epay_sweep._login(page, OWNER_USER, "p")
    assert "live screen" in str(e.value)


# ── THE CONFIG READ, THROUGH THE REAL LOADER ─────────────────────────────────────────────────────

class _Rows:
    """The handful of calls `portal_backoff.load_markers` makes on a Supabase client. No network —
    `tests/conftest.py` blocks every outbound socket, so this could not reach one anyway."""

    def __init__(self, rows, boom=False):
        self._rows, self._boom = rows, boom

    def schema(self, *_a):
        return self

    def table(self, *_a):
        return self

    def select(self, *_a):
        return self

    def in_(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self._boom:
            raise RuntimeError("the table is not there yet")
        return type("R", (), {"data": list(self._rows)})()


HOUSE = "00000000-0000-0000-0000-000000000001"
ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"


def _row(org, marker, kind="signed_in"):
    return {"org_id": org, "processor": None, "marker": marker, "kind": kind, "enabled": True}


def test_a_tenants_declared_wording_reaches_the_driver():
    assert epay_sweep._declared_signed_in_markers(
        _Rows([_row(ORG_A, "multi-door owner portal")]), ORG_A) == ["multi-door owner portal"]


def test_one_tenants_wording_is_never_served_to_another():
    """A tenant-scoped read is where cross-tenant leakage lives (owner directive 2026-10-30)."""
    cl = _Rows([_row(HOUSE, "owner portal"), _row(ORG_A, "cellfonz words")])
    assert epay_sweep._declared_signed_in_markers(cl, ORG_B) == ["owner portal"]
    assert epay_sweep._declared_signed_in_markers(cl, ORG_A) == ["cellfonz words"]


def test_nothing_declared_is_None_and_not_an_empty_list():
    """`verdict` reads an empty list as "no words count" and None as "use the house policy", so an
    empty declaration must never reach it — it would switch the word list off for every portal the
    tenant owns."""
    for cl in (_Rows([]), _Rows([_row(ORG_A, "x", kind="refusal")])):
        got = epay_sweep._declared_signed_in_markers(cl, ORG_A)
        assert got == list(lo.DEFAULT_SIGNED_IN_MARKERS)
        assert got, "an empty list would switch the house word list off"


def test_a_database_that_cannot_be_reached_never_stops_the_login():
    """The migration is surfaced for approval, not applied, so this is the LIVE shape today: the
    kind cannot be stored yet and the driver must sign in regardless. An unreachable table reads as
    "nothing declared" — the house policy, never silence — and never raises."""
    assert epay_sweep._declared_signed_in_markers(_Rows([], boom=True), ORG_A) \
        == list(lo.DEFAULT_SIGNED_IN_MARKERS)
    page = FakePage(visible=OWNER_PAGE, password_visible=False, has_password_node=False)
    v = epay_sweep._login(page, OWNER_USER, "p",
                          signed_in_markers=epay_sweep._declared_signed_in_markers(
                              _Rows([], boom=True), ORG_A))
    assert lo.signed_in(v["outcome"])
