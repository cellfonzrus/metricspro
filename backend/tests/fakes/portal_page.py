"""A fake Playwright page, shaped like a REAL portal sign-in page.

Why a fake and not a browser: the drivers under test (`commcalc/vidapay_sweep.py`,
`commcalc/live_login.py`) only ever ask a page a handful of questions — which inputs are on it, is
this element visible, what are its attributes, what does its markup say. All of that is answerable
without Chromium, and the suite's network guard forbids reaching a portal anyway.

The default page is `frs_signin()`: the UPS Store Franchise Reporting System sign-in screen, built
from the VERBATIM markup in `tests/fixtures/frs_signin_excerpt.html` (captured 2026-10-31). It is
the page that matters because it is BOTH things at once — a perfectly working Username + Password
form, AND a page whose markup contains the word "captcha" nine times (the reCAPTCHA script include,
a `renderCaptcha` flag, and an invisible `g-recaptcha` widget armed with a `data-callback`).

Everything here is a test double. Nothing imports playwright.
"""
import os

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


class FakeElement:
    """One DOM element. `attrs` is what the page carries, `visible` is what is painted."""

    def __init__(self, tag="input", attrs=None, visible=True, text=""):
        self.tag = tag
        self.attrs = dict(attrs or {})
        self._visible = visible
        self._text = text
        self.value = ""
        self.typed = ""              # what type() delivered, keystroke by keystroke
        self.clicks = 0
        self.presses = []

    # ── what the drivers read ──────────────────────────────────────────────────────────────────
    def get_attribute(self, name):
        return self.attrs.get(name)

    def is_visible(self):
        return self._visible

    def inner_text(self):
        return self._text

    def input_value(self):
        return self.value

    # ── what the drivers do ────────────────────────────────────────────────────────────────────
    def fill(self, value):
        self.value = str(value)

    def type(self, value, delay=None):
        self.typed += str(value)
        self.value += str(value)

    def click(self, timeout=None):
        self.clicks += 1

    def press(self, key):
        self.presses.append(key)

    # convenience for matching
    def matches(self, selector):
        sel = selector.strip()
        if sel.startswith("#"):
            return self.attrs.get("id") == sel[1:]
        if sel.startswith("."):
            return sel[1:] in (self.attrs.get("class") or "").split()
        if sel.startswith("input[type=") and sel.endswith("]"):
            return self.tag == "input" and self.attrs.get("type") == sel[len("input[type="):-1]
        for part in [p.strip() for p in sel.split(",")]:
            if part and part == self.tag:
                return True
        return False


class FakeFrame:
    def __init__(self, elements=None, url="", text=""):
        self.elements = list(elements or [])
        self.url = url
        self._text = text

    def query_selector(self, selector):
        for el in self.elements:
            if el.matches(selector):
                return el
        return None

    def query_selector_all(self, selector):
        parts = [p.strip() for p in selector.split(",") if p.strip()]
        out = []
        for el in self.elements:
            if any(el.matches(p) for p in parts):
                out.append(el)
        return out

    def inner_text(self, selector="body"):
        return self._text

    def evaluate(self, *a, **k):
        return None

    def wait_for_function(self, *a, **k):
        return None


class FakePage:
    def __init__(self, markup="", text="", title="", elements=None, frames=None, url=""):
        self._markup = markup
        self._text = text
        self._title = title
        self.url = url
        self.reloads = 0
        self.waited_ms = 0
        main = FakeFrame(elements=elements, url=url, text=text)
        self._frames = [main] + list(frames or [])

    @property
    def frames(self):
        return self._frames

    @property
    def main_frame(self):
        return self._frames[0]

    def content(self):
        return self._markup

    def title(self):
        return self._title

    def inner_text(self, selector="body"):
        return self._text

    def query_selector(self, selector):
        return self._frames[0].query_selector(selector)

    def query_selector_all(self, selector):
        return self._frames[0].query_selector_all(selector)

    def wait_for_timeout(self, ms):
        self.waited_ms += ms

    def wait_for_load_state(self, *a, **k):
        return None

    def reload(self, **k):
        self.reloads += 1

    def evaluate(self, *a, **k):
        return None

    def screenshot(self, **k):
        return b""


# ── the pages ──────────────────────────────────────────────────────────────────────────────────
def frs_signin():
    """The UPS Store FRS sign-in page: a working form that ALSO ships an invisible reCAPTCHA."""
    markup = fixture("frs_signin_excerpt.html")
    user = FakeElement("input", {"name": "ctl00$EnterpriseBodyContentPlaceHolder$UsernameTextBox",
                                 "id": "UsernameTextBox", "type": "text", "autocomplete": "off",
                                 "placeholder": "Enter Username",
                                 "class": "tupss-input mobile required"})
    pw = FakeElement("input", {"name": "ctl00$EnterpriseBodyContentPlaceHolder$UserPasswordTextBox",
                               "id": "UserPasswordTextBox", "type": "password",
                               "autocomplete": "off", "placeholder": "Enter Password",
                               "class": "tupss-input mobile required"})
    search = FakeElement("input", {"id": "txtSearch", "placeholder": "Application Search"},
                         visible=False)
    widget = FakeElement("div", {"id": "SignInCaptcha", "class": "g-recaptcha",
                                 "data-sitekey": "6Let-lgpAAAAAOP35z0jUQyjNPizzeEmxJ84Wriv",
                                 "data-size": "invisible",
                                 "data-callback": "captchaSuccessCallback"}, visible=False)
    submit = FakeElement("button", {"id": "SignInButton", "type": "submit"}, text="Sign In")
    text = ("Franchise Reporting System Sign In Please enter the username and password for your "
            "account. Username Forgot Username Password Forgot Password Sign In")
    return FakePage(markup=markup, text=text,
                    title="The UPS Store | Franchise Reporting System v. 3.1.15.62495",
                    elements=[search, user, pw, widget, submit],
                    url="https://frs.theupsstore.com/Framework/SignIn.aspx")


def cloudflare_interstitial():
    """A real anti-automation interstitial: no form at all, and its markers are markup fingerprints."""
    markup = ('<html><head><title>Just a moment...</title></head><body>'
              '<h1>Verify you are human by completing the action below.</h1>'
              '<div id="challenge-running"></div>'
              '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>'
              '</body></html>')
    return FakePage(markup=markup, text="Verify you are human by completing the action below.",
                    title="Just a moment...", elements=[], url="https://portal.example/login")


def plain_login():
    """An ordinary sign-in form with no human check anywhere."""
    user = FakeElement("input", {"id": "Username", "name": "Username", "type": "text"})
    pw = FakeElement("input", {"id": "Password", "name": "Password", "type": "password"})
    return FakePage(markup="<form>username password sign in</form>",
                    text="Username Password Sign in", title="Sign in",
                    elements=[user, pw], url="https://portal.example/login")
