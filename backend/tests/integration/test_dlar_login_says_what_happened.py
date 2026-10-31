"""THE SIBLING DRIVER'S LOGIN, DRIVEN AGAINST A FAKE HTTP SESSION.

`dlar_sweep` speaks plain HTTP, has no browser, and its DETECTION was already right: it asks for a
page that needs a session and treats a non-200 as "we are not in". What it got wrong is the same
thing `epay_sweep` got wrong — it called every such failure a rejected credential, so a portal
refusing us at the edge, and a route that moved, both sent the operator to re-type a password.

These prove the naming now comes from the one home, that the sound probe is untouched, and that a
signed-in page is still a signed-in page. No network: `tests/conftest.py` blocks outbound sockets
and the session here is a double.

Index §COMMCALC-50.9.
"""
import pytest

from app.modules.commcalc import dlar_sweep
from app.modules.commcalc import login_outcome as lo


class FakeResponse:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class FakeSession:
    """Answers the two GETs `login` makes: the CSRF form, then the protected page. `then` is what a
    followed redirect serves, so a test can hand the driver a body to read."""

    def __init__(self, *, protected, then=None):
        self.protected = protected
        self.then = then
        self.posted = []
        self.gets = []

    def get(self, url, **kw):
        self.gets.append(url)
        if url.endswith("/login") and len(self.gets) == 1:
            return FakeResponse(text='<form><input name="_token" value="t"></form>')
        if url.endswith("/reports/dlar"):
            return self.protected
        return self.then if self.then is not None else FakeResponse(text="")

    def post(self, url, **kw):
        self.posted.append((url, kw.get("data")))
        return FakeResponse(status_code=302)


SIGNED_IN_PAGE = FakeResponse(status_code=200, text="<div>DLAR Reports Logout</div>")


def test_a_successful_login_still_succeeds():
    s = FakeSession(protected=SIGNED_IN_PAGE)
    dlar_sweep.login(s, "u@example.test", "pw")
    assert s.posted and s.posted[0][1]["password"] == "pw"


def test_the_sound_probe_is_untouched():
    """A page that needs a session is still what proves we are in — the fix renamed the failure, it
    did not replace the test."""
    s = FakeSession(protected=SIGNED_IN_PAGE)
    dlar_sweep.login(s, "u", "p")
    assert any(g.endswith("/reports/dlar") for g in s.gets)


def test_a_real_refusal_is_named_a_credential_to_fix():
    s = FakeSession(protected=FakeResponse(
        status_code=302, headers={"Location": "/login"},
        text='<form><input type="password"></form><p>Invalid email or password.</p>'))
    with pytest.raises(dlar_sweep.DlarLoginError) as e:
        dlar_sweep.login(s, "u", "p")
    assert "re-enter" in str(e.value)


def test_an_edge_refusal_is_not_named_a_credential_problem():
    """What a WAF serves. The old sentence blamed the password for this."""
    s = FakeSession(protected=FakeResponse(
        status_code=403,
        text="<html><head><title>Request Rejected</title></head><body>The requested URL was "
             "rejected. Please consult with your administrator.</body></html>"))
    with pytest.raises(dlar_sweep.DlarLoginError) as e:
        dlar_sweep.login(s, "u", "p")
    msg = str(e.value)
    assert "password is not implicated" in msg
    assert "re-enter" not in msg


def test_a_bodiless_redirect_is_followed_once_to_read_what_the_portal_shows():
    """A 302 carries no body, so the old code had nothing to judge and guessed. The driver follows
    the redirect ONCE, read-only, and judges the page it lands on."""
    s = FakeSession(
        protected=FakeResponse(status_code=302, headers={"Location": "/login"}, text=""),
        then=FakeResponse(text='<form><input type="password"></form>'
                               '<p>Your account has been locked.</p>'))
    with pytest.raises(dlar_sweep.DlarLoginError) as e:
        dlar_sweep.login(s, "u", "p")
    assert '"been locked"' in str(e.value)


def test_a_silent_failure_is_reported_as_silent():
    s = FakeSession(protected=FakeResponse(status_code=500, text=""),
                    then=FakeResponse(text=""))
    with pytest.raises(dlar_sweep.DlarLoginError) as e:
        dlar_sweep.login(s, "u", "p")
    assert not lo.is_credential_problem(lo.UNKNOWN)
    assert "we cannot say" in str(e.value) or "did not recognise" in str(e.value)


def test_the_changed_login_page_still_raises_its_own_error():
    """A missing CSRF token is a different fact and keeps its own sentence — this fix must not
    swallow it into the outcome vocabulary."""
    class NoToken(FakeSession):
        def get(self, url, **kw):
            return FakeResponse(text="<p>nothing here</p>")
    with pytest.raises(dlar_sweep.DlarLoginError) as e:
        dlar_sweep.login(NoToken(protected=SIGNED_IN_PAGE), "u", "p")
    assert "CSRF" in str(e.value)
