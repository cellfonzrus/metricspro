"""Integration: one declaration, every login on a connector (index §COMMCALC-85).

§COMMCALC-83 took 27 per-store ePay logins from an operator's clipboard into the sealed column in
one post. This is the same gap one level up: until a login DECLARES a report — and where that
report's rows land — the download console has no upload door, so those 27 credentials are unusable.
Declaring one identical thing 27 times by hand is not a configuration step, it is a defect
generator.

The things only the wiring can prove:

  · the route is reachable at its documented path, and it is GATED — an ungated caller changes
    nothing;
  · ★ the landing SURVIVES the save: the stored declaration carries `target`, which is the property
    that was false for every declaration on the platform until this change;
  · ★ the legal landings come from each LOGIN'S OWN `processor`, read off the row — so a request
    cannot win a landing by claiming to be another connector;
  · safe by default: a login that already declares reports is left alone unless `overwrite`, and
    `dry_run` writes nothing at all;
  · ★ the fan-out cannot reach another tenant's logins;
  · a read that FAILED is not "this connector has no logins" — it refuses rather than report
    success over logins it never touched.
"""
import pytest

from app.modules.commcalc import processor_backoffice_sweep as pbs

ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "ba084e25-adcf-4d4d-9cc5-d3728775513f"
PATH = "/api/v1/commcalc/data-sources/declare-reports"
TABLE = "commcalc.data_source"

#: The declaration an operator sets up on ONE login and then applies to the rest.
TARGET = sorted(pbs.TARGETS)[0]
DECLARATION = [{"key": "daily_tx", "label": "Daily Transactions", "nav": ["Reports", "Daily"],
                "export": "csv", "target": TARGET,
                "params": [{"selector": "#ddlStore", "kind": "select", "value": "all",
                            "label": "Store"}]}]


def _login(org, sid, code, processor=pbs.CONNECTOR, reports=None):
    row = {"id": sid, "org_id": org, "store_code": code, "label": "ePay %s" % code,
           "processor": processor, "portal_url": "https://portal.example.com"}
    if reports is not None:
        row["portal_reports"] = reports
    return row


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.modules.commcalc.router.get_supabase", lambda: fake_supabase,
                        raising=False)
    # The gate is proved on its own below; these tests are about what the fan-out DOES once allowed.
    monkeypatch.setattr("app.modules.commcalc.router._require_import_admin", lambda *_a, **_k: None)
    fake_supabase.seed(TABLE, [
        _login(ORG_A, "s1", "B-1115"),
        _login(ORG_A, "s2", "B-1750"),
        _login(ORG_B, "s9", "T-9000"),
    ])
    return fake_supabase


def _post(api_client, org, **kw):
    body = {"processor": pbs.CONNECTOR, "reports": DECLARATION}
    body.update(kw)
    return api_client.post(PATH, params={"org_id": org}, json=body)


def _stored(db, sid):
    return [r for r in db.rows(TABLE) if r.get("id") == sid][0]


# ── 1. it is reachable, and it is gated ──────────────────────────────────────────────────────────
def test_the_route_is_registered_at_its_documented_path(app_module):
    from app.core.path_redact import route_templates
    assert PATH in set(route_templates(app_module))


def test_an_ungated_caller_declares_nothing(api_client, fake_supabase, monkeypatch):
    """Writing onto every login of a connector is a configuration change, so it fails closed."""
    monkeypatch.setattr("app.modules.commcalc.router.get_supabase", lambda: fake_supabase,
                        raising=False)
    fake_supabase.seed(TABLE, [_login(ORG_A, "s1", "B-1115")])
    res = _post(api_client, ORG_A)
    assert res.status_code in (401, 403, 503), res.text
    assert not _stored(fake_supabase, "s1").get("portal_reports")


def test_a_request_naming_no_connector_or_no_reports_is_refused(api_client, wired):
    assert _post(api_client, ORG_A, processor="").status_code == 400
    assert _post(api_client, ORG_A, reports=[]).status_code == 400


# ── 2. what it writes ────────────────────────────────────────────────────────────────────────────
def test_one_post_declares_the_report_on_every_login_of_that_connector(api_client, wired):
    out = _post(api_client, ORG_A).json()
    assert (out["ok"], out["filled"], out["overwritten"], out["skipped"]) == (True, 2, 0, 0)
    assert sorted(l["login"] for l in out["logins"]) == ["B-1115", "B-1750"]
    for sid in ("s1", "s2"):
        reports = _stored(wired, sid)["portal_reports"]
        assert [r["key"] for r in reports] == ["daily_tx"]


def test_the_landing_survives_the_save(api_client, wired):
    """★ The property that was false for every declaration on the platform before §COMMCALC-85:
    a declaration could be saved and still have nowhere to put its rows."""
    out = _post(api_client, ORG_A).json()
    assert all(l["note"] == "lands in %s" % TARGET for l in out["logins"])
    for sid in ("s1", "s2"):
        assert _stored(wired, sid)["portal_reports"][0]["target"] == TARGET


def test_a_landing_that_belongs_to_another_connector_is_refused_not_stored(api_client, wired):
    """★ The legal landings are the LOGIN'S OWN connector's, read off the row. A caller that sends
    somebody else's target gets a declaration with no landing, and is told so — never a row filed
    where that connector cannot land."""
    sent = [dict(DECLARATION[0], target="processor_not_this_connectors")]
    out = _post(api_client, ORG_A, reports=sent).json()
    assert out["filled"] == 2
    assert all("no landing is declared" in l["note"] for l in out["logins"])
    assert "target" not in _stored(wired, "s1")["portal_reports"][0]


def test_the_calibration_the_declaration_carries_is_stored_beside_it(api_client, wired):
    _post(api_client, ORG_A)
    cal = _stored(wired, "s1")["portal_calibration"]
    assert cal["daily_tx"]["params"] == [{"selector": "#ddlStore", "kind": "select",
                                          "value": "all", "label": "Store"}]


# ── 3. safe by default ───────────────────────────────────────────────────────────────────────────
def test_a_login_that_already_declares_reports_is_left_alone(api_client, wired):
    """A declaration is work somebody did — possibly a corrected selector no capture can replace."""
    mine = [{"key": "mine", "label": "Hand corrected", "nav": ["X"], "export": "csv"}]
    wired.seed(TABLE, [_login(ORG_A, "s1", "B-1115", reports=mine),
                       _login(ORG_A, "s2", "B-1750")])
    out = _post(api_client, ORG_A).json()
    assert (out["filled"], out["skipped"]) == (1, 1)
    assert [r["key"] for r in _stored(wired, "s1")["portal_reports"]] == ["mine"]
    assert [l["verdict"] for l in out["logins"] if l["login"] == "B-1115"] == ["already declared"]


def test_overwrite_replaces_it_and_says_which_were_replaced(api_client, wired):
    mine = [{"key": "mine", "label": "Hand corrected", "nav": ["X"], "export": "csv"}]
    wired.seed(TABLE, [_login(ORG_A, "s1", "B-1115", reports=mine),
                       _login(ORG_A, "s2", "B-1750")])
    out = _post(api_client, ORG_A, overwrite=True).json()
    assert (out["filled"], out["overwritten"], out["skipped"]) == (1, 1, 0)
    assert [r["key"] for r in _stored(wired, "s1")["portal_reports"]] == ["daily_tx"]


def test_a_dry_run_writes_nothing_and_says_what_would_happen(api_client, wired):
    out = _post(api_client, ORG_A, dry_run=True).json()
    assert out["dry_run"] is True and out["filled"] == 2
    assert "Nothing was written" in out["note"]
    assert all(not _stored(wired, sid).get("portal_reports") for sid in ("s1", "s2"))


def test_a_connector_with_no_logins_says_so_rather_than_reporting_success(api_client, wired):
    out = _post(api_client, ORG_A, processor="processor_nobody_has").json()
    assert (out["filled"], out["logins"]) == (0, [])
    assert "no logins" in out["note"]


# ── 4. the security pass ─────────────────────────────────────────────────────────────────────────
def test_the_fan_out_cannot_reach_another_tenants_logins(api_client, wired):
    """★ Cross-tenant at the HTTP boundary: ORG_B's login is on the same connector and the same
    portal, and a fan-out run by ORG_A must not touch it."""
    out = _post(api_client, ORG_A).json()
    assert "T-9000" not in str(out)
    assert not _stored(wired, "s9").get("portal_reports")


def test_it_refuses_rather_than_report_success_when_the_logins_cannot_be_read(api_client, wired,
                                                                             monkeypatch):
    """§COMMCALC-82's class: a read that FAILED is not "this connector has no logins". Reporting
    success over an empty list would tell an operator 27 logins were configured when none were."""
    class _Blind:
        def __init__(self, inner):
            self._inner, self._schema, self._table = inner, "", ""

        def schema(self, name):
            self._schema = name
            return self

        def table(self, name):
            self._table = name
            return self

        def select(self, *a, **k):
            if self._table == "data_source":
                raise RuntimeError("transport is down")
            return self._inner.schema(self._schema).table(self._table).select(*a, **k)

        def __getattr__(self, item):
            return getattr(self._inner, item)

    monkeypatch.setattr("app.modules.commcalc.router.get_supabase", lambda: _Blind(wired),
                        raising=False)
    res = _post(api_client, ORG_A)
    assert res.status_code == 409
    assert "nothing was declared" in res.json()["note"]


def test_more_logins_than_one_request_applies_to_is_refused_not_truncated(api_client, wired):
    """One post writes once per login, so the login count IS the work. Above the ceiling it writes
    NOTHING and says so: applying to the first N and reporting success is the silent-drop defect
    §COMMCALC-83 exists to prevent — "27 declared" must never mean 20."""
    from app.modules.commcalc import router as r
    many = [_login(ORG_A, "s%d" % i, "B-%04d" % i) for i in range(r.MAX_FANOUT_LOGINS + 1)]
    wired.seed(TABLE, many)
    res = _post(api_client, ORG_A)
    assert res.status_code == 400
    assert "Nothing was declared" in res.text
    assert all(not row.get("portal_reports") for row in wired.rows(TABLE))
