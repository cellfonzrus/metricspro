"""Integration: a live login against a portal whose sign-in page ships a reCAPTCHA.

Real modules, wired as the app wires them — `commcalc/portal_backoff.py` (the vocabulary),
`commcalc/vidapay_sweep.py` (the driver) and `commcalc/live_login.py` (the session) — over the fake
page in `tests/fakes/portal_page.py`, which is built from the VERBATIM markup of the UPS Store FRS
sign-in screen captured on 2026-10-31. No Chromium and no network: conftest blocks every outbound
socket, and there is no staging portal to log in to.

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT. The unit tests ask each home its own question. They
cannot see the SEQUENCE, which is where the owner's report lived: the driver decided the page was a
bot wall (`_wait_settle`) before the part that finds the form (`_wait_for_password`) ever ran, so
the login died ~60s in with a WAF accusation while a perfectly good Username + Password form sat on
the screen with both boxes empty. Any one of those three modules read on its own looks right.

So this walks the real order a live login walks, and asserts the end state an operator should get:
the form is found, the armed invisible check is recognised, the saved credentials are TYPED in, and
the session parks in `human_action` with words that match what is actually in the boxes.

MULTI-TENANT (project rule, 2026-10-30). Each case runs for three orgs with different shapes — the
house org, a tenant that has declared its OWN challenge vocabulary, and a tenant with an account id
on its login — because `portal_block_marker` is per-org config and a fix that held only for the
house seeds would leave the next tenant exactly where the owner was.
"""
import pytest

from app.modules.commcalc import live_login as ll
from app.modules.commcalc import portal_backoff as pb
from app.modules.commcalc import vidapay_sweep as vp
from tests.fakes import portal_page as fp

HOUSE = "00000000-0000-0000-0000-000000000001"
DECLARED = "11111111-2222-3333-4444-555555555555"   # declares its own challenge words
WITH_ACCT = "99999999-8888-7777-6666-555555555555"  # its portal also asks for an account id

TENANTS = [
    # org, the org's configured challenge markers (None = fall back to the house seeds), account id
    (HOUSE, None, None),
    (DECLARED, ["captcha", "robot check", "verify you are human"], None),
    (WITH_ACCT, ["captcha", "just a moment"], "169024"),
]


def _row(org, account_id):
    return {"id": "ds-1", "org_id": org, "label": "Team Portal", "processor": "pos_report",
            "portal_url": "https://portal.example/UI/Reporting/POS/POSReports.aspx",
            "account_id": account_id, "username": "tupss.user", "password": "s3cret-not-logged",
            "auto_pull_after_login": False}


def _session(org, account_id):
    """A real LiveLoginSession, with no worker thread started (the browser is what we replace)."""
    sess = ll.LiveLoginSession.__new__(ll.LiveLoginSession)
    row = _row(org, account_id)
    ll.LiveLoginSession.__init__(sess, "ds-1", org, row)
    return sess


@pytest.mark.parametrize("org,markers,account_id", TENANTS)
def test_the_login_reaches_the_form_and_parks_for_the_human(org, markers, account_id):
    """The whole sequence, in the order the live session runs it."""
    page = fp.frs_signin()

    # 1. the driver's pre-form settle — this is where the login used to die
    vp._wait_settle(page, timeout_s=2)
    assert page.reloads == 0

    # 2. the page is not refusing us, for THIS tenant's vocabulary
    assert vp.page_refusal(page, challenge=markers) is None
    assert vp._looks_like_bot_wall(page, challenge=markers) is False

    # 3. the form is found
    login_fr, pw_el = vp._wait_for_password(page, timeout_s=2)
    assert pw_el is not None

    # 4. the armed invisible check is recognised, so nothing is auto-submitted
    sess = _session(org, account_id)
    kind = ll._human_check_kind(vp, page)
    assert kind == "invisible"

    # 5. the credentials go in, and the session says so because it LOOKED
    rep = vp.prefill_login(page, login_fr, pw_el, sess.account_id, sess.user, sess.pw)
    assert rep["ok"] is True
    assert page.query_selector("#UsernameTextBox").value == "tupss.user"
    assert pw_el.value == "s3cret-not-logged"

    sess._set(phase="human_action",
              message=(ll._HUMAN_CHECK_MESSAGE[kind] + " " + ll._prefill_note(rep)).strip())
    snap = sess.state()
    assert snap["phase"] == "human_action"
    assert "filled in" in snap["message"].lower()
    assert "Sign in" in snap["message"]
    assert "s3cret-not-logged" not in str(snap), "no credential may reach a status line"
    assert page.query_selector("#SignInButton").clicks == 0, "the submit stays with the human"


@pytest.mark.parametrize("org,markers,account_id", TENANTS)
def test_the_defect_itself_is_reproduced_without_the_form_question(org, markers, account_id):
    """ARMED. Ask the vocabulary the way it was asked before 2026-10-31 and the owner's report comes
    straight back: the genuine form reads as a challenge, for every tenant."""
    page = fp.frs_signin()
    soup = vp._page_text(page)
    v = pb.page_verdict(soup, challenge=markers)      # form_present defaulted away
    assert v is not None and v["kind"] == pb.CHALLENGE_KIND
    assert v["marker"] == "captcha"


@pytest.mark.parametrize("org,markers,account_id", TENANTS)
def test_a_real_interstitial_still_stops_the_login_for_every_tenant(org, markers, account_id):
    """The control that matters: the fix must not teach the driver to walk into a bot wall."""
    page = fp.cloudflare_interstitial()
    assert vp.login_form_present(page) is False
    assert vp._looks_like_bot_wall(page, challenge=markers) is True
    with pytest.raises(vp.VidaPayLoginError):
        vp._wait_settle(page, timeout_s=1)


@pytest.mark.parametrize("org,markers,account_id", TENANTS)
def test_one_tenants_declared_vocabulary_never_answers_anothers_page(org, markers, account_id):
    """A declaration is an argument, never a default with another tenant's words behind it
    (owner directive 2026-10-30). A tenant that declares NOTHING identifies nothing."""
    page = fp.cloudflare_interstitial()
    soup = vp._page_text(page)
    assert pb.page_verdict(soup, challenge=[]) is None, \
        "an empty declaration means empty — it must not inherit the seeds"
    assert pb.page_verdict(soup, challenge=["just a moment"]) is not None


def test_the_live_login_start_endpoint_still_refuses_a_login_with_no_credentials(
        monkeypatch, fake_supabase, api_client):
    """The API gate around all of this: an empty user id or password never reaches a browser, so
    "the fields were not filled" can never be caused by a login the app agreed to start blind."""
    from app.modules.commcalc import router as cc

    fake_supabase.seed("commcalc.data_source",
                       [dict(_row(HOUSE, None), password=""),
                        dict(_row(HOUSE, None), id="ds-2", username="", account_id="")])
    monkeypatch.setattr(cc, "sb", lambda: fake_supabase)

    for sid in ("ds-1", "ds-2"):
        resp = api_client.post("/api/v1/commcalc/data-sources/%s/live-login/start?org_id=%s"
                               % (sid, HOUSE))
        assert resp.status_code in (400, 401, 403, 404, 503), resp.status_code
        if resp.status_code == 400:
            assert "Password" in resp.json().get("detail", "")
