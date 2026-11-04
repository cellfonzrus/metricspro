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
