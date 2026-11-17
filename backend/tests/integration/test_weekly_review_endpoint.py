"""INTEGRATION: GET /commcalc/weekly-review through the REAL app, for EVERY tenant.

Owner ask 2026-11-16: the performance deck as an in-app report on the Management tile, *"allow
access only for the admins"*, one pack per market plus a consolidated one, inventory aging per
carrier as a review item, generated weekly and ON DEMAND.

WHAT THIS PINS, and why each one would otherwise be a live defect:

  · THE GATE IS ON THE SERVER. Hiding a tile is not access control. A rep, a market manager and an
    unauthenticated caller are all refused 403 — on the read, on the config and on the on-demand
    run. (Project rule 2026-10-30: a security pass before go-live; this is its automated half.)
  · EVERY TENANT GETS ITS OWN PACK, in its OWN market words. Three tenants are seeded with three
    different market vocabularies, and no tenant's market, store or carrier may appear in another's
    pack.
  · A SCOPE PER MARKET, A CONSOLIDATED ONE, AND NOBODY DROPPED. A store no market claims is
    reviewed in its own named scope rather than vanishing between the market packs and the company
    one.
  · AN ITEM NOBODY MEASURED SAYS SO. With no feed seeded, every item must come back `not_measured`
    with a reason and a null figure — never 0, which on a review pack reads as "checked, nothing
    wrong".
"""
import pytest

REVIEW = "/api/v1/commcalc/weekly-review"
CONFIG = "/api/v1/commcalc/weekly-review/config"
RUN_NOW = "/api/v1/commcalc/weekly-review/run-now"
RUN_DUE = "/api/v1/commcalc/weekly-review/run-due"

HOUSE = "00000000-0000-0000-0000-000000000001"
ORG_B = "00000000-0000-0000-0000-0000000000b2"
ORG_C = "00000000-0000-0000-0000-0000000000b3"
TENANTS = [HOUSE, ORG_B, ORG_C]

#: Monday of the review meeting. The pack must cover the week that CLOSED, 9–15 November.
AS_OF = "2026-11-16"

#: Each tenant's own market vocabulary and stores — spelled so a leak is unmistakable.
OWN_MARKETS = {
    HOUSE: {"Queens": ["B-1598"], "Brooklyn": ["B-2101"]},
    ORG_B: {"Chicago": ["LUX-CHI-KEDZIE"], "New York": ["LUX-NY-AVENUEU"]},
    ORG_C: {"Harbor District": ["VZ-900"]},
}
#: A store in NO market, per tenant — it must be reviewed in the unplaced scope, never dropped.
ORPHAN = {HOUSE: "B-9999", ORG_B: "LUX-ORPHAN", ORG_C: "VZ-ORPHAN"}

ADMIN, MANAGER, REP = "admin", "market_manager", "rep"


def _roles():
    rows = []
    for i, org in enumerate(TENANTS):
        rows += [
            # The administrator: the `admin` module is what the gate reads.
            {"id": f"r{i}a", "org_id": org, "name": ADMIN,
             "permissions": {"scope": "all", "modules": ["admin", "commissions"]}},
            # Company-wide REPORTING scope but no admin module — deliberately refused.
            {"id": f"r{i}m", "org_id": org, "name": MANAGER,
             "permissions": {"scope": "all", "modules": ["commissions"]}},
            {"id": f"r{i}r", "org_id": org, "name": REP, "permissions": {"scope": "self"}},
        ]
    return rows


def _app_users():
    rows = []
    for i, org in enumerate(TENANTS):
        for who in (ADMIN, MANAGER, REP):
            rows.append({"id": f"u{i}{who}", "org_id": org, "auth_id": f"{i}-{who}",
                         "employee_id": f"E{i}{who}", "role": who, "is_active": True,
                         "full_name": f"{who} of {org[-2:]}", "email": f"{i}{who}@example.com"})
    return rows


def _stores():
    rows = []
    for org in TENANTS:
        for market, codes in OWN_MARKETS[org].items():
            for code in codes:
                rows.append({"org_id": org, "store_code": code, "address": f"{code} Street",
                             "market": market, "is_active": True})
        rows.append({"org_id": org, "store_code": ORPHAN[org], "address": f"{ORPHAN[org]} Street",
                     "market": None, "is_active": True})
    return rows


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    """Three tenants, each with its own markets, stores and three logins."""
    import app.modules.core.router as core_router

    for mod in ("app.modules.commcalc.router.get_supabase",
                "app.modules.commcalc.router.sb",
                "app.modules.storeops.router.get_supabase",
                "app.modules.asset.router.sb"):
        monkeypatch.setattr(mod, lambda: fake_supabase, raising=False)
    monkeypatch.setattr(core_router, "sb", lambda: fake_supabase, raising=False)
    monkeypatch.setattr(core_router, "get_supabase", lambda: fake_supabase, raising=False)
    holder = {"auth": f"0-{ADMIN}"}
    monkeypatch.setattr(core_router, "_uid_from_token", lambda *a, **k: holder["auth"])

    fake_supabase.seed("storeops.roles", _roles())
    fake_supabase.seed("storeops.app_users", _app_users())
    fake_supabase.seed("storeops.stores", _stores())
    fake_supabase.seed("storeops.tenants", [{"org_id": o} for o in TENANTS])
    fake_supabase.holder = holder
    return fake_supabase


def _as(wired, org, who):
    wired.holder["auth"] = f"{TENANTS.index(org)}-{who}"
    return {"authorization": "Bearer t", "x-active-org": org}


def _pack(api_client, wired, org, who=ADMIN, **params):
    r = api_client.get(REVIEW, params={"org_id": org, "as_of": AS_OF, **params},
                       headers=_as(wired, org, who))
    assert r.status_code == 200, r.text
    return r.json()


# ── the route exists where it is documented ───────────────────────────────────────────────────────
def test_the_route_is_registered(app_module):
    from app.core.path_redact import route_templates
    templates = set(route_templates(app_module))
    assert REVIEW in templates and CONFIG in templates and RUN_NOW in templates


# ── §A the gate is on the server ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("org", TENANTS)
def test_A1_an_administrator_may_open_the_review(wired, api_client, org):
    assert api_client.get(REVIEW, params={"org_id": org, "as_of": AS_OF},
                          headers=_as(wired, org, ADMIN)).status_code == 200


@pytest.mark.parametrize("org", TENANTS)
@pytest.mark.parametrize("who", [MANAGER, REP])
def test_A2_a_non_administrator_is_refused_even_with_company_wide_reporting_scope(
        wired, api_client, org, who):
    r = api_client.get(REVIEW, params={"org_id": org, "as_of": AS_OF}, headers=_as(wired, org, who))
    assert r.status_code == 403
    assert "administrator" in r.json()["detail"].lower()


def test_A3_an_unauthenticated_caller_is_refused(wired, api_client):
    wired.holder["auth"] = None
    assert api_client.get(REVIEW, params={"org_id": HOUSE, "as_of": AS_OF}).status_code == 403


@pytest.mark.parametrize("path", [CONFIG, RUN_NOW])
def test_A4_the_config_and_the_on_demand_run_are_gated_the_same_way(wired, api_client, path):
    call = api_client.post if path == RUN_NOW else api_client.get
    assert call(path, params={"org_id": HOUSE}, headers=_as(wired, HOUSE, REP)).status_code == 403
    assert call(path, params={"org_id": HOUSE}, headers=_as(wired, HOUSE, ADMIN)).status_code == 200


# ── §B the week ───────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("org", TENANTS)
def test_B1_the_pack_covers_the_week_that_closed_before_the_monday_meeting(wired, api_client, org):
    pack = _pack(api_client, wired, org)
    assert pack["week"]["date_from"] == "2026-11-09"
    assert pack["week"]["date_to"] == "2026-11-15"
    assert pack["as_of"] == AS_OF
    assert pack["period"] == "November 2026", "the month-keyed items say which month they are"


# ── §C one pack per market, one consolidated, nobody dropped ──────────────────────────────────────
@pytest.mark.parametrize("org", TENANTS)
def test_C1_every_declared_market_gets_its_own_pack_and_the_company_gets_one(wired, api_client, org):
    pack = _pack(api_client, wired, org)
    assert pack["company"]["scope"]["kind"] == "company"
    assert sorted(m["scope"]["label"] for m in pack["markets"]) == sorted(OWN_MARKETS[org])


@pytest.mark.parametrize("org", TENANTS)
def test_C2_a_store_no_market_claims_is_still_reviewed(wired, api_client, org):
    pack = _pack(api_client, wired, org)
    assert pack["unplaced"], "a store in no market must have a scope of its own"
    assert ORPHAN[org] in pack["unplaced"]["scope"]["store_codes"]
    assert ORPHAN[org] in pack["company"]["scope"]["store_codes"], \
        "the consolidated pack still counts every store"


@pytest.mark.parametrize("org", TENANTS)
def test_C3_a_market_filter_narrows_to_that_market(wired, api_client, org):
    one = sorted(OWN_MARKETS[org])[0]
    pack = _pack(api_client, wired, org, markets=[one])
    assert [m["scope"]["label"] for m in pack["markets"]] == [one]


# ── §D every item is present, and an unmeasured one says so ───────────────────────────────────────
@pytest.mark.parametrize("org", TENANTS)
def test_D1_every_declared_item_is_on_every_pack(wired, api_client, org):
    from app.modules.commcalc import weekly_review as wr
    pack = _pack(api_client, wired, org)
    for review in [pack["company"], pack["unplaced"]] + list(pack["markets"]):
        assert [i["key"] for i in review["items"]] == list(wr.ITEM_KEYS)
        for item in review["items"]:
            assert item["label"] and item["source"] and item["window"]


@pytest.mark.parametrize("org", TENANTS)
def test_D2_with_no_feed_seeded_nothing_is_reported_as_zero(wired, api_client, org):
    from app.modules.commcalc import weekly_review as wr
    pack = _pack(api_client, wired, org)
    for item in pack["company"]["items"]:
        if item["state"] == wr.NOT_MEASURED:
            assert item["figure"] is None, f"{item['key']} invented a figure"
            assert item["reason"], f"{item['key']} must say why it has no number"


def test_D3_the_inventory_aging_item_is_one_of_the_items_to_review(wired, api_client):
    pack = _pack(api_client, wired, HOUSE)
    aging = {i["key"]: i for i in pack["company"]["items"]}["aging"]
    assert "carrier" in aging["label"].lower() or "carrier" in aging["source"].lower()


# ── §E multi-tenant: no tenant's words reach another's pack ───────────────────────────────────────
@pytest.mark.parametrize("org", TENANTS)
def test_E1_no_other_tenants_market_or_store_reaches_this_tenants_pack(wired, api_client, org):
    blob = str(_pack(api_client, wired, org))
    for other in TENANTS:
        if other == org:
            continue
        for market in OWN_MARKETS[other]:
            assert market not in blob, f"{other}'s market {market!r} reached {org}'s pack"
        for codes in OWN_MARKETS[other].values():
            for code in codes:
                assert code not in blob, f"{other}'s store {code!r} reached {org}'s pack"
        assert ORPHAN[other] not in blob


# ── §F on demand ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("org", TENANTS)
def test_F1_the_on_demand_run_produces_the_same_pack_and_says_whether_it_was_stored(
        wired, api_client, org):
    r = api_client.post(RUN_NOW, params={"org_id": org, "as_of": AS_OF},
                        headers=_as(wired, org, ADMIN))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["generated"] is True and "stored" in out
    assert out["week"]["week_ending"] == "2026-11-15"
    assert sorted(m["scope"]["label"] for m in out["markets"]) == sorted(OWN_MARKETS[org])


def test_F2_the_on_demand_run_writes_only_its_own_snapshot(wired, api_client):
    """It is a REPORT: it may record that it ran, and it may touch nothing else. A review that could
    move money would be a different kind of object and would need a different approval."""
    api_client.post(RUN_NOW, params={"org_id": HOUSE, "as_of": AS_OF},
                    headers=_as(wired, HOUSE, ADMIN))
    for relation in ("commcalc.rep_commissions", "commcalc.payout_config", "commcalc.targets",
                     "commcalc.account_statements", "storeops.employees"):
        assert wired.rows(relation) == [], f"the review wrote to {relation}"
    # The snapshot it IS allowed to write names the week it covers.
    snaps = wired.rows("commcalc.weekly_review_run")
    assert [s["week_ending"] for s in snaps] == ["2026-11-15"]
    assert snaps[0]["org_id"] == HOUSE


# ── §G the standing run is a SWEEP, and a sweep has the sweep's gate ──────────────────────────────
# Found by the security pass on 2026-11-16, before this went live: /run-due reads EVERY tenant under
# the root client, so there is no caller whose own org could scope it. It had no gate at all, which
# let an anonymous stranger name another tenant in `org_id_filter`, read back that tenant's coverage
# and make the server build every tenant's pack on demand. The gate is the house sweep gate.
def test_G1_the_standing_run_refuses_a_caller_with_no_sweep_secret(api_client):
    assert api_client.post(RUN_DUE).status_code == 403
    assert api_client.post(RUN_DUE, params={"org_id_filter": ORG_B}).status_code == 403
    assert api_client.post(RUN_DUE, headers={"x-notify-secret": "wrong"}).status_code == 403


def test_G2_an_admin_token_is_not_a_sweep_secret(wired, api_client):
    """The review's own admin gate does NOT open the cross-tenant sweep: a tenant admin holds
    authority over ONE tenant, and this route answers for all of them."""
    r = api_client.post(RUN_DUE, headers=_as(wired, HOUSE, ADMIN))
    assert r.status_code == 403


def test_G3_a_refused_standing_run_reads_nothing_and_writes_nothing(wired, api_client):
    api_client.post(RUN_DUE, params={"org_id_filter": ORG_B})
    assert wired.rows("commcalc.weekly_review_run") == []


# ── §H the defects the owner reported on 2026-11-17 ───────────────────────────────────────────────
#
# Verbatim: *"The profit is not correct it should be same as in the finance dashboard , the inventory
# aging is not correct it shows all devices out of the Qpay's day window , it shows no commission
# paid for the month of October / The descriptive ppt is missing like one made before"*.
#
# Three of the four were reproduced live before anything was changed (October 2026): the review read
# the Gross Profit report's `net_profit` and headlined +$340,102.98 where the P&L said -$26,543.19;
# it composed the aging report's owed>0 subset, so every device read as past its carrier's window;
# and every commission row was dropped because the pay engine keys a rep by their store's ADDRESS
# while the scopes were built from declared CODES. The fourth is the deck.

def test_H1_the_profit_item_quotes_the_statement_and_never_the_gross_profit_report():
    """The one that matters most: two profit answers on one pack is the defect itself."""
    import inspect
    from app.modules.commcalc import router as _r
    src = inspect.getsource(_r._weekly_review_facts)
    assert "profit_by_store(" in src
    assert "get_gp_report(" not in src


def test_H2_the_aging_item_reads_the_whole_shelf_not_the_owed_subset():
    import inspect
    from app.modules.commcalc import router as _r
    src = inspect.getsource(_r._weekly_review_facts)
    aging = src.split('facts["aging"]', 1)[1].split("except", 1)[0]
    assert "aging_by_carrier" in aging and "buckets" not in aging


def test_H3_a_commission_row_keyed_by_address_still_reaches_the_pack(wired, api_client):
    """The regression for "it shows no commission paid": a row whose store is spelled as an address
    must be counted, not silently discarded for failing a code-shaped match."""
    from app.modules.commcalc import weekly_review as W
    scope = {"kind": "company", "key": "company", "label": "C",
             "store_codes": ["B-1598"], "note": ""}
    rows = [{"rep": "A", "store_code": "B-1598", "total_payout": 10.0},
            {"rep": "B", "store": "1598 Mount Ephraim Ave", "total_payout": 5.0}]
    item = W._commission(rows, scope)
    assert item["state"] == "measured"
    assert item["figure"] == 15.0, "a row the roster does not recognise is still the company's"
    assert len(item["rows"]) == 2


def test_H4_the_deck_is_served_and_carries_the_review_gate(wired, api_client):
    deck = "/api/v1/commcalc/weekly-review/deck"
    for who, expected in ((ADMIN, 200), (MANAGER, 403), (REP, 403)):
        r = api_client.get(deck, params={"org_id": HOUSE, "as_of": AS_OF},
                           headers=_as(wired, HOUSE, who))
        assert r.status_code == expected, (who, r.status_code, r.text[:200])
    assert api_client.get(deck, params={"org_id": HOUSE}).status_code in (401, 403)


def test_H5_the_deck_says_what_each_figure_says_rather_than_showing_it_alone(wired, api_client):
    """Owner: *"The descriptive ppt is missing like one made before"*. Every slide that carries a
    figure carries the fold's sentences about it, and an unmeasured item's sentence is its reason."""
    from app.modules.commcalc import weekly_review_deck as D
    pack = _pack(api_client, wired, HOUSE)
    payload = D.deck_payload(pack, company_label="A Tenant")
    kinds = [s["kind"] for s in payload["slides"]]
    assert kinds[0] == "title" and "agenda" in kinds
    items = [s for s in payload["slides"] if s["kind"] == "item"]
    assert len(items) >= len(pack["company"]["items"])
    assert all(s["bullets"] for s in items), "a slide with no sentence is the defect reported"
    for s, it in zip(items, pack["company"]["items"]):
        if it["state"] != "measured":
            assert it["reason"] in " ".join(s["bullets"])


def test_H6_the_deck_renders_to_a_real_file(wired, api_client):
    from app.modules.commcalc import weekly_review_deck as D
    from app.modules.notify import render as R
    data, filename, mime = R.render(D.deck_payload(_pack(api_client, wired, HOUSE)), "pptx")
    assert filename.endswith(".pptx") and mime.endswith("presentationml.presentation")
    assert data[:2] == b"PK" and len(data) > 10_000, "a .pptx is a zip container, never empty"
