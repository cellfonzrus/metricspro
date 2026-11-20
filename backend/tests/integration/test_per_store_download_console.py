"""Integration: the PER-STORE DOWNLOAD CONSOLE through the real app (index §COMMCALC-82).

Owner, 2026-11-20: *"build these into the system as a browser in browser to download manually so we
pick each store from the drop down menu and then the browser opens up and we down load the file"*.
The back office this serves refuses automated clients (§COMMCALC-74/75), so until it delivers, a
person fetches each store's file — and the screen has to say which store-days are still outstanding.

WHAT ONLY THE WIRING CAN PROVE:
  · the route is reachable at its documented path, and answers both questions in ONE round trip;
  · ★ a login declared by one tenant, and a store-day landed by one tenant, are invisible to another
    at the HTTP boundary — where cross-tenant leakage actually lives (owner's 2026-10-30 rule);
  · ★ NO CREDENTIAL leaves through this route, in any field, under any key;
  · a login with no store declared is not a dropdown entry, because nothing could place its file;
  · each declared report carries the upload door its hand-fetched file arrives through — the SAME
    door the automated pull lands through, never a sibling;
  · a database that cannot be read answers `measured: false` rather than "no stores declared", which
    would send an operator to re-enter logins that already exist.
"""
import pytest

from app.modules.commcalc import processor_backoffice_sweep as pbs

ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "ba084e25-adcf-4d4d-9cc5-d3728775513f"
PATH = "/api/v1/commcalc/per-store-downloads"
DAYS = ("2026-11-18", "2026-11-19")

#: Deliberately a real-looking secret: every assertion below hunts for it in the response body.
SECRET = "n0t-a-real-password-9f3c"

REPORTS = [{"key": "daily_transaction_detail", "label": "Daily TX",
            "target": "processor_daily_tx", "nav": ["Reports"]}]


def _login(sid, org, store, username):
    return {"id": sid, "org_id": org, "processor": pbs.CONNECTOR, "label": "Back office " + store,
            "username": username, "password": SECRET, "portal_url": "https://portal.example.com",
            "store_code": store, "enabled": True, "portal_reports": REPORTS,
            "created_at": "2026-11-10T00:00:00Z"}


def _tx(org, store, day, tid):
    """One landed transaction line, stamped with the store its own login pulled it for."""
    return {"org_id": org, "transaction_id": tid, "transaction_source_id": "1",
            "settlement_date": day, "store_code": store, "terminal_id": "TERM-" + store,
            "user_name": "login", "product_title": "BOOST RTR", "retail": 50.0,
            "commission": 1.0, "is_fee": False}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.modules.commcalc.router.get_supabase", lambda: fake_supabase,
                        raising=False)
    fake_supabase.seed("commcalc.data_source", [
        _login("src-a1", ORG_A, "B-1115", "store1115"),
        _login("src-a2", ORG_A, "B-1750", "store1750"),
        # A login with NO store: it cannot place a file, so it is not a dropdown entry.
        dict(_login("src-a3", ORG_A, "", "company"), store_code=None),
        _login("src-b1", ORG_B, "T-9000", "other"),
    ])
    fake_supabase.seed("commcalc.raw_epay_daily_tx", [
        _tx(ORG_A, "B-1115", DAYS[0], "A-1"),
        _tx(ORG_B, "T-9000", DAYS[0], "B-1"),
    ])
    return fake_supabase


def _get(api_client, org, **kw):
    params = {"org_id": org, "date_from": DAYS[0], "date_to": DAYS[1]}
    params.update(kw)
    return api_client.get(PATH, params=params).json()


# ── 1. the route exists and answers both questions at once ───────────────────────────────────────
def test_the_route_is_registered_at_its_documented_path(app_module):
    from app.core.path_redact import route_templates
    assert PATH in set(route_templates(app_module))


def test_one_request_returns_both_the_store_picker_and_the_coverage_grid(api_client, wired):
    """A screen that cost two round trips could render a dropdown against another range's grid."""
    out = _get(api_client, ORG_A)
    assert out["measured"] is True
    assert [l["store_code"] for l in out["logins"]] == ["B-1115", "B-1750"]
    assert [s["store"] for s in out["coverage"]["stores"]] == ["B-1115", "B-1750"]
    assert out["coverage"]["days"] == list(DAYS)


def test_a_login_with_no_store_declared_is_not_a_dropdown_entry(api_client, wired):
    """Nothing could place its file, so offering it would invite an upload that lands under no store."""
    out = _get(api_client, ORG_A)
    assert "company" not in {l["username"] for l in out["logins"]}


def test_the_grid_says_which_store_days_are_still_to_fetch(api_client, wired):
    out = _get(api_client, ORG_A)
    cells = {(s["store"], c["day"]): c["state"]
             for s in out["coverage"]["stores"] for c in s["days"]}
    assert cells[("B-1115", DAYS[0])] == "have"
    assert cells[("B-1115", DAYS[1])] == "missing"
    assert cells[("B-1750", DAYS[0])] == "missing"
    assert out["coverage"]["have"] == 1 and out["coverage"]["missing"] == 3


def test_each_declared_report_names_the_door_its_hand_fetched_file_arrives_through(api_client, wired):
    """§COMMCALC-75: a feed lands the same however its file arrives. The screen must post the file to
    the SAME upload door the automated pull lands through, never a sibling route."""
    from app.modules.commcalc import epay_ingest as feed
    rep = _get(api_client, ORG_A)["logins"][0]["reports"][0]
    assert rep["key"] == "daily_transaction_detail"
    assert rep["arrival_type"] == feed.UPLOAD_TYPE
    assert rep["upload_path"] == "/api/v1/commcalc/upload/" + feed.UPLOAD_TYPE


def test_the_screen_opens_on_a_readable_window_when_no_range_is_named(api_client, wired):
    from app.modules.commcalc import feed_day_coverage as cov
    out = api_client.get(PATH, params={"org_id": ORG_A}).json()
    assert (out["date_from"], out["date_to"]) == cov.default_range()


# ── 2. the security pass: no credential, and no other tenant ─────────────────────────────────────
def test_no_credential_leaves_through_this_route_under_any_key(api_client, wired):
    """The passwords this feature exists for were typed by a person into a settings screen; a screen
    that lists logins is exactly where one leaks."""
    body = api_client.get(PATH, params={"org_id": ORG_A}).text
    assert SECRET not in body
    for login in _get(api_client, ORG_A)["logins"]:
        assert "password" not in login and "session_state" not in login
        assert login["has_password"] is True


@pytest.mark.parametrize("org,own,other", [(ORG_A, "B-1115", "T-9000"), (ORG_B, "T-9000", "B-1115")])
def test_one_tenants_logins_and_landed_days_are_invisible_to_another(api_client, wired, org, own,
                                                                    other):
    out = _get(api_client, org)
    body = api_client.get(PATH, params={"org_id": org, "date_from": DAYS[0],
                                        "date_to": DAYS[1]}).text
    assert own in {l["store_code"] for l in out["logins"]}
    assert other not in body
    assert out["coverage"]["undeclared_stores"] == []


# ── 3. an unreadable database says so, rather than "nothing is declared" ─────────────────────────
def test_a_database_that_cannot_be_read_says_so_instead_of_reporting_no_stores(api_client,
                                                                               monkeypatch,
                                                                               fake_supabase):
    """The defect class this guards (§PAYABLES-02): "I could not look" rendered as "there is nothing
    there" would send an operator to re-enter every store's login."""
    class _Boom:
        def schema(self, _n):
            return self

        def table(self, _n):
            return self

        def select(self, *_a, **_k):
            raise RuntimeError("transport is down")

    monkeypatch.setattr("app.modules.commcalc.router.get_supabase", lambda: _Boom(),
                        raising=False)
    out = api_client.get(PATH, params={"org_id": ORG_A}).json()
    assert out["measured"] is False and out["logins"] == []
    assert "could not be read" in out["note"]
    assert out["coverage"]["measured"] is False and out["coverage"]["missing"] == 0


# ── 4. the password the owner asked to SEE, shown only to someone allowed to change it ───────────
REVEAL = "/api/v1/commcalc/data-sources/src-a1/reveal-password"


def test_the_reveal_route_is_registered_at_its_documented_path(app_module):
    from app.core.path_redact import route_templates
    assert "/api/v1/commcalc/data-sources/{sid}/reveal-password" in set(route_templates(app_module))


def test_an_ungated_caller_cannot_read_a_stored_password(api_client, wired):
    """The gate is the one the other credential-editing routes use, and it FAILS CLOSED: a request
    with no token gets 401/403/503 — never the password."""
    res = api_client.post(REVEAL, json={})
    assert res.status_code in (401, 403, 503), res.text
    assert SECRET not in res.text


def test_the_password_is_never_in_the_console_payload_even_though_a_route_can_reveal_it(api_client,
                                                                                        wired):
    """Two doors, one secret: the list NEVER carries it, and the reveal is a POST of its own that
    writes an audit row first. A credential in the list payload would be in every browser cache."""
    assert SECRET not in api_client.get(PATH, params={"org_id": ORG_A}).text


def test_an_allowed_caller_gets_the_password_and_the_view_is_recorded(api_client, wired,
                                                                     monkeypatch):
    """Owner's choice, 2026-11-20: show it on screen. What makes that safe to ship is the row that
    says who looked — written BEFORE the credential is returned."""
    monkeypatch.setattr("app.modules.commcalc.router._require_import_admin",
                        lambda *_a, **_k: None)
    monkeypatch.setattr("app.modules.commcalc.router._caller_label", lambda *_a: "sanjot@example.com")
    out = api_client.post(REVEAL, params={"org_id": ORG_A}, json={}).json()
    assert out["ok"] is True and out["password"] == SECRET and out["audited"] is True
    audit = wired.rows("core.credential_reveal_audit")
    assert len(audit) == 1
    assert audit[0]["revealed_to"] == "sanjot@example.com"
    assert audit[0]["row_id"] == "src-a1" and audit[0]["column"] == "password"
    assert SECRET not in " ".join(str(v) for v in audit[0].values())


def test_one_tenant_cannot_reveal_another_tenants_login(api_client, wired, monkeypatch):
    """The org check is on the ROW that came back, so asking for another tenant's login id under your
    own org is "not found" rather than a password."""
    monkeypatch.setattr("app.modules.commcalc.router._require_import_admin",
                        lambda *_a, **_k: None)
    res = api_client.post("/api/v1/commcalc/data-sources/src-b1/reveal-password",
                          params={"org_id": ORG_A}, json={})
    assert res.status_code == 409 and SECRET not in res.text
    assert wired.rows("core.credential_reveal_audit") == []
