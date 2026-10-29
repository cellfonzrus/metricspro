"""Integration: the Accessory Order Plan endpoints through the REAL app (index §COMMCALC-48).

Real routing, real middleware, the real endpoint bodies — over the in-memory database double, with
conftest blocking every outbound socket. There is no staging Supabase, so "integration" here means
the app, not a real database (docs/TESTING.md).

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT: that the three routes are reachable at their registered
paths; that the look-back really reads the sales DISPLAY UNION (so the report cannot be blind on the
open month — the §19.55 / §4a.3 defect that cost this platform three reports); that the device-line
test and the model parse are the shared homes rather than something re-spelled in the router; that a
ruling stored on `commcalc.device_model_alias` comes back as a projection; and that a database
without migration 1104 degrades to the house defaults instead of 500-ing the page.
"""
import pytest

ORG = "00000000-0000-0000-0000-000000000001"
PATH = "/api/v1/commcalc/accessory-order-plan"
LIFECYCLE_PATH = PATH + "/lifecycle"
CONFIG_PATH = PATH + "/config"

PERIOD = "October 2026"


def _sale(trans_id, product_desc, *, day="2026-10-04", store="B-1115", category="CellPhone",
          department="", period="2026-10", **kw):
    row = {"org_id": ORG, "period": period, "trans_id": trans_id, "trans_date": day,
           "store": store, "salesperson": "Arora, Tanish", "department": department,
           "category": category, "product_desc": product_desc, "contract_type": "New",
           "ext_price": 299.99, "gp": 50.0, "voided": None, "trans_type": "Sale",
           "serial_1": "", "mdn": "", "id": trans_id}
    row.update(kw)
    return row


#: Two models, one selling twice as well as the other, plus a line that is not a device at all.
A_MONTH = [
    _sale("T-1", "HANDSET A 128GB - 2026 Q3 Promo Offer - $550.00", id="r1"),
    _sale("T-2", "HANDSET A 128GB - 2026 Q3 Promo Offer - $550.00", id="r2"),
    _sale("T-3", "HANDSET A 128GB", id="r3"),
    _sale("T-4", "HANDSET B 64GB", id="r4"),
    _sale("T-5", "HANDSET B 64GB", id="r5"),
    _sale("T-6", "Unlimited Plus", category="Service", department="Plans", id="r6"),
    _sale("T-7", "HANDSET A 128GB", voided="YES", id="r7"),          # voided: never counted
]


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    from app.modules.commcalc import router as commcalc_router
    monkeypatch.setattr("app.modules.commcalc.router.get_supabase", lambda: fake_supabase)
    for name in ("_ACFG_CACHE", "_CFG_CACHE"):
        cache = getattr(commcalc_router, name, None)
        if isinstance(cache, dict):
            cache.clear()
    return fake_supabase


def test_the_three_routes_are_registered_at_their_documented_paths(app_module):
    from app.core.path_redact import route_templates
    served = set(route_templates(app_module))
    assert PATH in served
    assert LIFECYCLE_PATH in served
    assert CONFIG_PATH in served


def test_the_look_back_reads_the_sales_display_union(wired, api_client):
    """The daily feed is where an OPEN month lives. A report that read the month-end archive alone
    would report zero device sales for the month being planned, which is precisely the defect
    §19.55 and §4a.3 document. Seeding only the feed proves which reader is wired."""
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    r = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_meta"]["device_lines"] == 5
    assert [m["model"] for m in body["models"]] == ["HANDSET A 128GB", "HANDSET B 64GB"]


def test_the_promo_decoration_is_stripped_by_the_shared_model_parse(wired, api_client):
    """Three A lines are the SAME phone: two of them carry a promotion suffix. Counting them as two
    models would split the best seller in half and under-order for it."""
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    top = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()["models"][0]
    assert top["model"] == "HANDSET A 128GB"
    assert top["units_total"] == 3           # the voided line is not one of them
    assert top["rank"] == 1


def test_nothing_is_projected_until_somebody_rules_on_the_models(wired, api_client):
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 2}).json()
    assert body["projected_devices"] is None
    assert body["reason"] == "awaiting_declaration"
    assert body["models_awaiting_declaration"] == ["HANDSET A 128GB", "HANDSET B 64GB"]
    assert all(a["qty"] is None for a in body["accessories"])


def test_a_ruling_stored_on_the_model_registry_becomes_a_projection(wired, api_client):
    """The ruling lives on `commcalc.device_model_alias`, the registry that already maps a raw model
    to its canonical name — so a model curated once is one model on this screen and on the device
    forecast."""
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    wired.seed("commcalc.device_model_alias", [
        {"org_id": ORG, "raw_model": "HANDSET A 128GB", "canonical_model": "HANDSET A 128GB",
         "lifecycle": "current", "id": "a1"},
        {"org_id": ORG, "raw_model": "HANDSET B 64GB", "canonical_model": "HANDSET B 64GB",
         "lifecycle": "eol", "id": "a2"},
    ])
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 2}).json()
    assert body["projected_devices"] == 6                     # 3/month current x 2 months
    assert body["reason"] is None
    by = {a["key"]: a["qty"] for a in body["accessories"]}
    assert by == {"case": 6, "screen_protector": 6, "cable_c_to_c": 6, "charging_cube": 6}
    eol = [m for m in body["models"] if m["model"] == "HANDSET B 64GB"][0]
    assert eol["projected_units"] == 0 and eol["units_total"] == 2


def test_the_cases_come_back_split_by_the_house_colour_mix(wired, api_client):
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    wired.seed("commcalc.device_model_alias", [
        {"org_id": ORG, "raw_model": "HANDSET A 128GB", "canonical_model": "HANDSET A 128GB",
         "lifecycle": "current", "id": "a1"}])
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 10}).json()
    case = [a for a in body["accessories"] if a["key"] == "case"][0]
    assert case["qty"] == 30
    assert [(c["key"], c["qty"]) for c in case["colours"]] == [("dark", 21), ("bright", 9)]
    assert body["case_mix_basis"] == "house_default"


def test_the_store_filter_narrows_the_plan(wired, api_client):
    wired.seed("commcalc.daily_sales_feed",
               A_MONTH + [_sale("T-8", "HANDSET C 256GB", store="B-2001", id="r8")])
    allstores = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()
    assert len(allstores["models"]) == 3
    one = api_client.get(PATH, params={"period": PERIOD, "months": 1, "store": "B-2001"}).json()
    assert [m["model"] for m in one["models"]] == ["HANDSET C 256GB"]
    assert one["params"]["stores"] == ["B-2001"]


def test_the_window_controls_are_on_the_payload_and_clamped(wired, api_client):
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    body = api_client.get(PATH, params={"period": PERIOD, "months": 3, "horizon": 2}).json()
    assert body["lookback_months"] == 3
    assert body["horizon_months"] == 2
    assert body["months"][-1] == PERIOD                  # oldest first, ending at the period asked for
    assert len(body["months"]) == 3
    wild = api_client.get(PATH, params={"period": PERIOD, "months": 900, "horizon": 900}).json()
    assert wild["lookback_months"] == 24 and wild["horizon_months"] == 12


def test_the_defaults_are_the_three_and_two_the_owner_asked_for(wired, api_client):
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    body = api_client.get(PATH, params={"period": PERIOD}).json()
    assert body["lookback_months"] == 3
    assert body["horizon_months"] == 2


def test_a_declared_mix_on_the_config_row_is_what_the_plan_uses(wired, api_client):
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    wired.seed("commcalc.device_model_alias", [
        {"org_id": ORG, "raw_model": "HANDSET A 128GB", "canonical_model": "HANDSET A 128GB",
         "lifecycle": "current", "id": "a1"}])
    wired.seed("commcalc.accessory_config", [
        {"org_id": ORG, "accessory_order_case_mix": [{"key": "dark", "label": "Dark", "share": 50},
                                                     {"key": "bright", "label": "Bright", "share": 50}],
         "accessory_order_attach_plan": None, "accessory_order_lookback_months": None,
         "accessory_order_horizon_months": None, "id": "c1"}])
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 10}).json()
    assert body["case_mix_basis"] == "declared"
    case = [a for a in body["accessories"] if a["key"] == "case"][0]
    assert [(c["key"], c["qty"]) for c in case["colours"]] == [("dark", 15), ("bright", 15)]


def test_a_database_without_the_migration_still_serves_the_house_defaults(wired, api_client):
    """Mig 1104 is surfaced for approval, not applied by the app. Until it runs, the config columns
    and the lifecycle column do not exist — and the REPORT still has to load, on the house mix, with
    every model reading as awaiting a ruling. A 500 here would make the feature undeployable."""
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    r = api_client.get(PATH, params={"period": PERIOD, "months": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["case_mix_basis"] == "house_default"
    assert body["reason"] == "awaiting_declaration"


def test_a_period_with_no_device_sales_says_so_rather_than_planning_zero(wired, api_client):
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()
    assert body["models"] == []
    assert body["projected_devices"] is None
    assert body["reason"] == "no_sales_in_window"


def test_the_drill_panels_are_served_on_the_shared_mechanism(wired, api_client):
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    wired.seed("commcalc.device_model_alias", [
        {"org_id": ORG, "raw_model": "HANDSET A 128GB", "canonical_model": "HANDSET A 128GB",
         "lifecycle": "current", "id": "a1"}])
    panels = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()["drilldown"]
    assert "units_in_window" in panels and "projected_devices" in panels
    p = panels["units_in_window"]
    assert p["figure"]["label"] and p["rows"]
    assert all(r["sources"] for r in p["rows"])
    assert {t for r in p["rows"] for t in r["sources"]} <= {"daily_sales_feed", "raw_sales"}


def test_every_served_panel_ties_to_the_figure_it_explains(wired, api_client):
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    wired.seed("commcalc.device_model_alias", [
        {"org_id": ORG, "raw_model": "HANDSET A 128GB", "canonical_model": "HANDSET A 128GB",
         "lifecycle": "current", "id": "a1"}])
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()
    for key, panel in (body["drilldown"] or {}).items():
        assert abs(float(panel["figure"]["amount"] or 0) - float(body.get(key) or 0)) < 0.005, key


def test_no_rate_is_served_a_panel(wired, api_client):
    """A share and a per-month figure are not sums of anything (§CORE-17.3h). A panel under one would
    be a second rendering of the figures it divides, which already have their own."""
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    panels = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()["drilldown"]
    for refused in ("declared_share", "projected_devices_per_month", "case", "screen_protector"):
        assert refused not in panels


# ── the two writes ───────────────────────────────────────────────────────────────────────────────
def test_a_ruling_can_be_recorded_and_then_read_back_as_a_projection(wired, api_client):
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    r = api_client.post(LIFECYCLE_PATH, json={"rulings": [
        {"model": "HANDSET A 128GB", "lifecycle": "current"}]})
    assert r.status_code == 200, r.text
    assert r.json()["written"] == [{"model": "HANDSET A 128GB", "lifecycle": "current"}]
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 2}).json()
    assert body["projected_devices"] == 6


def test_a_ruling_the_vocabulary_does_not_know_is_refused(wired, api_client):
    r = api_client.post(LIFECYCLE_PATH, json={"rulings": [
        {"model": "HANDSET A 128GB", "lifecycle": "maybe"}]})
    assert r.status_code == 400
    assert "current" in r.text and "eol" in r.text


def test_an_empty_ruling_payload_is_refused_rather_than_silently_doing_nothing(wired, api_client):
    assert api_client.post(LIFECYCLE_PATH, json={}).status_code == 400


def test_the_declaration_comes_back_resolved_so_the_screen_shows_what_was_stored(wired, api_client):
    r = api_client.put(CONFIG_PATH, json={"case_mix": [{"key": "dark", "share": 60},
                                                       {"key": "bright", "share": 30}],
                                          "lookback_months": 6, "horizon_months": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["case_mix_basis"] == "declared_normalised"    # 90% is a typo, and it is NAMED
    assert body["case_mix"][0]["share"] == pytest.approx(2 / 3, abs=1e-6)
    assert body["lookback_months"] == 6 and body["horizon_months"] == 3


def test_an_empty_config_payload_is_refused(wired, api_client):
    assert api_client.put(CONFIG_PATH, json={}).status_code == 400
