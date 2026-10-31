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

A MODEL ROW IS NOW A PHONE, NOT A SPELLING (owner 2026-10-30, §COMMCALC-49.11). The capacity is
collapsed out by `device_model_family`, so the row these tests assert on is `HANDSET A`, not
`HANDSET A 128GB` — and the LIFECYCLE RULING is therefore stored against the family, which is the
name the human actually ruled on. Production carried 148 alias rows and zero rulings when this
landed, so nothing stored had to be re-keyed.
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
    assert [m["model"] for m in body["models"]] == ["HANDSET A", "HANDSET B"]


def test_the_promo_decoration_is_stripped_by_the_shared_model_parse(wired, api_client):
    """Three A lines are the SAME phone: two of them carry a promotion suffix. Counting them as two
    models would split the best seller in half and under-order for it."""
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    top = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()["models"][0]
    assert top["model"] == "HANDSET A"
    assert top["units_total"] == 3           # the voided line is not one of them
    assert top["rank"] == 1


def test_nothing_is_projected_until_somebody_rules_on_the_models(wired, api_client):
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 2}).json()
    assert body["projected_devices"] is None
    assert body["reason"] == "awaiting_declaration"
    assert body["models_awaiting_declaration"] == ["HANDSET A", "HANDSET B"]
    assert all(a["qty"] is None for a in body["accessories"])


def test_a_ruling_stored_on_the_model_registry_becomes_a_projection(wired, api_client):
    """The ruling lives on `commcalc.device_model_alias`, the registry that already maps a raw model
    to its canonical name — so a model curated once is one model on this screen and on the device
    forecast."""
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    wired.seed("commcalc.device_model_alias", [
        {"org_id": ORG, "raw_model": "HANDSET A", "canonical_model": "HANDSET A",
         "lifecycle": "current", "id": "a1"},
        {"org_id": ORG, "raw_model": "HANDSET B", "canonical_model": "HANDSET B",
         "lifecycle": "eol", "id": "a2"},
    ])
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 2}).json()
    assert body["projected_devices"] == 6                     # 3/month current x 2 months
    assert body["reason"] is None
    by = {a["key"]: a["qty"] for a in body["accessories"]}
    assert by == {"case": 6, "screen_protector": 6, "cable_c_to_c": 6, "charging_cube": 6}
    eol = [m for m in body["models"] if m["model"] == "HANDSET B"][0]
    assert eol["projected_units"] == 0 and eol["units_total"] == 2


def test_the_cases_come_back_split_by_the_house_colour_mix(wired, api_client):
    wired.seed("commcalc.daily_sales_feed", A_MONTH)
    wired.seed("commcalc.device_model_alias", [
        {"org_id": ORG, "raw_model": "HANDSET A", "canonical_model": "HANDSET A",
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
    assert [m["model"] for m in one["models"]] == ["HANDSET C"]
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
        {"org_id": ORG, "raw_model": "HANDSET A", "canonical_model": "HANDSET A",
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
        {"org_id": ORG, "raw_model": "HANDSET A", "canonical_model": "HANDSET A",
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
        {"org_id": ORG, "raw_model": "HANDSET A", "canonical_model": "HANDSET A",
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
        {"model": "HANDSET A", "lifecycle": "current"}]})
    assert r.status_code == 200, r.text
    assert r.json()["written"] == [{"model": "HANDSET A", "lifecycle": "current"}]
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 2}).json()
    assert body["projected_devices"] == 6


def test_a_ruling_the_vocabulary_does_not_know_is_refused(wired, api_client):
    r = api_client.post(LIFECYCLE_PATH, json={"rulings": [
        {"model": "HANDSET A", "lifecycle": "maybe"}]})
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


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A PRE-1104 DATABASE, PROVED AGAINST A CLIENT THAT REALLY REFUSES THE COLUMN
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The in-memory double ignores the column list a `select()` is given, so the deployability test above
# only proves the page loads with no rows — it CANNOT prove what happens when PostgREST rejects
# `lifecycle` because migration 1104 has not been applied. These two tests drive the readers against
# a stub that raises on any unknown column, which is the real failure, and they are what makes the
# two readers' use of the shared any-subset reading rule (`core.column_tolerant`, index §4b.1) load
# bearing rather than decorative: a wide select with a narrower one behind it would answer these the
# same way while still being the ladder the house lock forbids.
class _StrictQuery:
    """A select that knows which columns the table HAS and raises on any other, one query deep."""

    def __init__(self, rows, known, cols):
        self._rows, self._known = rows, known
        asked = [c.strip() for c in str(cols).split(",") if c.strip()]
        if asked != ["*"]:
            for c in asked:
                if c not in known:
                    raise RuntimeError(f'column "{c}" does not exist')

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return type("R", (), {"data": [{k: v for k, v in r.items() if k in self._known}
                                       for r in self._rows]})()


class _StrictTable:
    def __init__(self, rows, known):
        self._rows, self._known = rows, known

    def select(self, cols="*", **_k):
        return _StrictQuery(self._rows, self._known, cols)


class _StrictClient:
    """`schema(...).table(...)` over ONE named table; anything else has no rows and no columns."""

    def __init__(self, table, rows, known):
        self._t, self._rows, self._known = table, rows, known

    def schema(self, _s):
        return self

    def table(self, name):
        return (_StrictTable(self._rows, self._known) if name == self._t
                else _StrictTable([], frozenset()))


def test_the_rulings_read_drops_the_lifecycle_column_a_pre_migration_database_lacks():
    from app.modules.commcalc import router as _r
    rows = [{"org_id": ORG, "raw_model": "HANDSET A 128GB", "canonical_model": "HANDSET A"}]
    pre = _StrictClient("device_model_alias", rows, frozenset(("org_id", "raw_model",
                                                               "canonical_model")))
    rulings, alias = _r._device_lifecycle(pre, ORG)
    assert rulings == {}, "a column that does not exist cannot carry a ruling"
    assert alias == {"handset a 128gb": "HANDSET A"}, "and the alias map still arrives whole"


def test_the_rulings_read_keeps_the_lifecycle_column_once_the_migration_has_run():
    from app.modules.commcalc import router as _r
    rows = [{"org_id": ORG, "raw_model": "HANDSET A 128GB", "canonical_model": "HANDSET A",
             "lifecycle": "current"}]
    post = _StrictClient("device_model_alias", rows, frozenset(("org_id", "raw_model",
                                                                "canonical_model", "lifecycle")))
    rulings, alias = _r._device_lifecycle(post, ORG)
    assert rulings == {"HANDSET A": "current"}
    assert alias == {"handset a 128gb": "HANDSET A"}


def test_the_config_read_degrades_to_the_house_defaults_on_a_pre_migration_database():
    from app.modules.commcalc import router as _r
    pre = _StrictClient("accessory_config", [{"org_id": ORG, "id": "c1"}],
                        frozenset(("org_id", "id")))
    assert _r._accessory_order_config(pre, ORG) == {}, "no declared column is no declaration"
    post = _StrictClient("accessory_config",
                         [{"org_id": ORG, "id": "c1", "accessory_order_lookback_months": 6}],
                         frozenset(("org_id", "id", "accessory_order_lookback_months")))
    assert _r._accessory_order_config(post, ORG) == {"accessory_order_lookback_months": 6}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# EVERY TENANT, NOT THE ONE WHOSE WORDS ARE IN THE CODE (owner directive 2026-10-30)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE DEFECT. This report shipped reading `demand_projection`'s default device-department list, which
# was the house tenant's POS labels. A second live tenant files its handsets under `BrandedHandset`
# with category `KittedBranded`, so the report told it that it had sold no devices at all — 1,034
# handsets in one month, reported as zero, with nothing on the screen to suggest a vocabulary problem.
# Owner: *"how come it is only working for cellfonz r us and not luxlink ... it should be universal
# add on of a feture ... all features should be for all tenanats"*.
#
# These are the tests that would have caught it. Every row below is the SHAPE of a real feed read from
# production on 2026-10-30 — not an invented vocabulary — and the declaration each is judged under is
# the tenant's own `accessory_config.box_departments` row.
OTHER_TENANT_DEPTS = ["Android - XP", "IPHONE - XP", "TABLET - XP", "BrandedHandset", "Handset"]
HOUSE_DEPTS = ["Android - XP", "IPHONE - XP", "TABLET - XP"]

#: A month in the OTHER tenant's feed shape: the department carries the fact, the category is a
#: kitting word, and neither appears anywhere in the platform's code.
ANOTHER_TENANTS_MONTH = [
    _sale("L-1", "HANDSET Z 128GB", department="BrandedHandset", category="KittedBranded", id="l1"),
    _sale("L-2", "HANDSET Z 128GB", department="BrandedHandset", category="KittedBranded", id="l2"),
    _sale("L-3", "HANDSET Z 128GB", department="BrandedHandset", category="KittedBranded", id="l3"),
    _sale("L-4", "HANDSET Y 64GB", department="BrandedHandset", category="KittedBranded", id="l4"),
    _sale("L-5", "Boost Wallet Refill", department="Rtr", category="Other Carr. payments", id="l5"),
]


def _declare_departments(wired, departments):
    """The tenant's own box-department declaration — `accessory_config.box_departments`, mig 218, the
    SAME row the box count, the product mix and the GP report read. No new table, no new column."""
    wired.seed("commcalc.accessory_config", [{"org_id": ORG, "box_departments": departments,
                                              "id": "cfg-dept"}])


def test_a_tenant_whose_pos_names_departments_differently_still_gets_its_order(wired, api_client):
    """THE REPORTED DEFECT, as a test. With the other tenant's declaration in place, its feed shape
    produces the same report the house tenant gets: four handsets over three models, ranked."""
    _declare_departments(wired, OTHER_TENANT_DEPTS)
    wired.seed("commcalc.daily_sales_feed", ANOTHER_TENANTS_MONTH)
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()
    assert body["units_in_window"] == 4, "three of model Z and one of model Y; the refill is not a device"
    assert [m["model"] for m in body["models"]] == ["HANDSET Z", "HANDSET Y"]
    assert body["reason"] == "awaiting_declaration", "found the sales, now waiting on a human ruling"


def test_the_same_feed_reads_as_nothing_under_the_house_declaration(wired, api_client):
    """The defect's mechanism, pinned. The identical rows under the HOUSE tenant's three departments
    are not devices — which is correct for that declaration, and is exactly why the list may not be a
    literal in the code. A regression here would be silent: a wrong number, never an error."""
    _declare_departments(wired, HOUSE_DEPTS)
    wired.seed("commcalc.daily_sales_feed", ANOTHER_TENANTS_MONTH)
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()
    assert body["units_in_window"] == 0
    assert body["reason"] == "no_sales_in_window"


def test_an_org_that_has_declared_no_departments_is_told_so_not_given_someone_elses(wired, api_client):
    """An org with an EMPTY declaration gets no DEPARTMENT-identified devices, and the report says it
    found no device sales. It does NOT quietly borrow another tenant's departments: a confident wrong
    order is worse than an empty one, and the empty one sends somebody to the admin screen."""
    _declare_departments(wired, [])
    wired.seed("commcalc.daily_sales_feed", ANOTHER_TENANTS_MONTH)
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()
    assert body["units_in_window"] == 0
    assert body["reason"] == "no_sales_in_window"


def test_the_category_tolerance_survives_an_empty_department_declaration(wired, api_client):
    """AND THE LIMIT OF THAT, stated rather than left to be discovered. The CATEGORY half of the test
    is not a tenant's vocabulary — it is the older export FORMAT's marker for a handset line, carried
    with a blank department — so it is a house tolerance and it still applies when an org has declared
    no departments. A feed in that format is therefore never silenced by a missing declaration, which
    is why the empty-declaration rule above is about DEPARTMENTS and says so."""
    _declare_departments(wired, [])
    wired.seed("commcalc.daily_sales_feed", A_MONTH)        # category='CellPhone', department blank
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()
    assert body["units_in_window"] == 5
    assert [m["model"] for m in body["models"]] == ["HANDSET A", "HANDSET B"]


def test_the_order_is_built_from_the_other_tenants_units_once_ruled(wired, api_client):
    """End to end on the other tenant's vocabulary: rule the models, get the accessory order, and the
    colour split still foots to the case quantity."""
    _declare_departments(wired, OTHER_TENANT_DEPTS)
    wired.seed("commcalc.daily_sales_feed", ANOTHER_TENANTS_MONTH)
    assert api_client.post(LIFECYCLE_PATH, json={"rulings": [
        {"model": "HANDSET Z", "lifecycle": "current"},
        {"model": "HANDSET Y", "lifecycle": "eol"}]}).status_code == 200
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 2}).json()
    assert body["projected_devices"] == 6, "three a month, ruled current, over two months"
    case = [a for a in body["accessories"] if a["key"] == "case"][0]
    assert case["qty"] == 6
    assert sum(c["qty"] for c in case["colours"]) == case["qty"]


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ONE PHONE IS ONE ROW, AND THE ORDER IS BOUGHT AT TWO GRAINS (owner 2026-10-30)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Owner: *"accessory order plan should combine the accessories for all line items for iphone 13 oin
# different colors with a drill down option to see what was sold, , iph 13 is different from 13 pro
# ... the order is per phone ... charger and cables should be consolidated based on the sales, not
# per phone"*.
#
# The descriptions below are the SHAPE of the live feed read from production on 2026-10-30: the
# colour sits in the middle of the description, the capacity beside it, and a certified-pre-owned
# marker after it that must not be mistaken for a colour.
COLOUR_MONTH = [
    _sale("C-1", "HANDSET A 128GB Pink CPO", id="c1"),
    _sale("C-2", "HANDSET A 128GB Pink CPO", id="c2"),
    _sale("C-3", "HANDSET A 128GB Black CPO", id="c3"),
    _sale("C-4", "HANDSET A 128GB Midnight CPO", id="c4"),
    _sale("C-5", "HANDSET A PRO 128GB Black CPO", id="c5"),
    _sale("C-6", "Home Internet Router", id="c6"),          # inside a device department, takes no case
    _sale("C-7", "SIM Kit only", id="c7"),                  # same
]
COLOUR_WORDS = ["pink", "black", "midnight"]


def _declare(wired, **cols):
    """The org's own `commcalc.accessory_config` row — the existing per-tenant config row, not a new
    table. Everything this report declares hangs off it."""
    wired.seed("commcalc.accessory_config", [dict({"org_id": ORG, "id": "cfg"}, **cols)])


def _rule_current(wired, *models):
    wired.seed("commcalc.device_model_alias",
               [{"org_id": ORG, "raw_model": m, "canonical_model": m, "lifecycle": "current",
                 "id": f"a{i}"} for i, m in enumerate(models)])


def test_the_colours_of_one_phone_become_one_row_with_what_was_sold_under_it(wired, api_client):
    """THE REPORTED DEFECT, end to end. Four lines in three colours are ONE phone to order cases for;
    the Pro is a different phone; and the row carries the colour spellings so the screen can open it
    without a second query."""
    _declare(wired, box_departments=HOUSE_DEPTS, device_colour_tokens=COLOUR_WORDS)
    wired.seed("commcalc.daily_sales_feed", COLOUR_MONTH)
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()
    assert [m["model"] for m in body["models"]] == ["HANDSET A CPO", "HANDSET A PRO CPO"]
    top = body["models"][0]
    assert top["units_total"] == 4
    assert top["variant_count"] == 3
    assert sorted(v["model"] for v in top["variants"]) == [
        "HANDSET A 128GB Black CPO", "HANDSET A 128GB Midnight CPO", "HANDSET A 128GB Pink CPO"]
    assert sum(v["units_total"] for v in top["variants"]) == top["units_total"]


def test_a_router_and_a_sim_kit_are_not_phones_to_order_cases_for(wired, api_client):
    """Both sit INSIDE the tenant's own device departments, and one tenant's September carried 498 of
    them. Counting them buys cases for routers."""
    _declare(wired, box_departments=HOUSE_DEPTS, device_colour_tokens=COLOUR_WORDS)
    wired.seed("commcalc.daily_sales_feed", COLOUR_MONTH)
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()
    assert body["units_in_window"] == 5, "five handsets; the router and the SIM kit are neither"
    assert body["source_meta"]["skipped_by_class"] == {"home_internet": 1, "accessory": 1}
    assert body["accessory_device_classes"] == ["phone", "tablet"]


def test_without_a_declared_colour_vocabulary_every_colour_stays_its_own_row(wired, api_client):
    """CLAUDE.md, *"every feature is for EVERY tenant"*: a colour word is a tenant's own spelling, so
    there is no house list behind it. The report says `no_vocabulary` and the screen sends somebody
    to the setup panel rather than grouping by another tenant's words."""
    _declare(wired, box_departments=HOUSE_DEPTS)
    wired.seed("commcalc.daily_sales_feed", COLOUR_MONTH)
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()
    assert body["colour_tokens_basis"] == "not_declared"
    assert body["colour_tokens"] == []
    assert len(body["models"]) == 4, "three colours and the Pro, each still its own row"


def test_another_tenants_colour_words_do_not_collapse_this_tenants_spellings(wired, api_client):
    """The negative assertion that pins the mechanism: the same rows under a vocabulary that does not
    contain their colours must NOT fold."""
    _declare(wired, box_departments=HOUSE_DEPTS, device_colour_tokens=["blk", "jetblack", "navy"])
    wired.seed("commcalc.daily_sales_feed", COLOUR_MONTH)
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()
    assert body["colour_tokens_basis"] == "declared"
    assert len(body["models"]) == 4


def test_the_grid_is_one_row_per_phone_with_the_styles_as_columns_and_it_foots(wired, api_client):
    _declare(wired, box_departments=HOUSE_DEPTS, device_colour_tokens=COLOUR_WORDS)
    wired.seed("commcalc.daily_sales_feed", COLOUR_MONTH)
    _rule_current(wired, "HANDSET A CPO", "HANDSET A PRO CPO")
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 2}).json()

    assert [c["category"] for c in body["grid_columns"]] == ["case", "screen_protector"]
    assert [o["key"] for o in body["grid_columns"][0]["options"]] == [
        "silicone", "kickstand", "heavy_duty", "floral", "diamond"]

    rows = body["model_orders"]
    assert [r["model"] for r in rows] == ["HANDSET A CPO", "HANDSET A PRO CPO"]
    for r in rows:
        for cell in r["cells"].values():
            assert sum(o["qty"] for o in cell["options"]) == cell["qty"]
    case = rows[0]["cells"]["case"]
    assert sum(c["qty"] for c in case["colours"]) == case["qty"]
    assert [c["label"] for c in case["colours"]] == ["Blacks and blues", "Pinks and reds"]


def test_cables_and_cubes_consolidate_across_phones_rather_than_per_phone(wired, api_client):
    """*"the cables are common to a lot of phones"* — so two phones on the same connector are ONE
    order line, and the line names the phones it covers."""
    _declare(wired, box_departments=HOUSE_DEPTS, device_colour_tokens=COLOUR_WORDS)
    wired.seed("commcalc.daily_sales_feed", COLOUR_MONTH)
    _rule_current(wired, "HANDSET A CPO", "HANDSET A PRO CPO")
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 2}).json()

    cable = next(c for c in body["consolidated"] if c["category"] == "cable")
    assert len(cable["lines"]) == 1, "both phones take the current standard, so one line"
    assert cable["lines"][0]["label"] == "USB-C to USB-C"
    assert cable["lines"][0]["spec_basis"] == "house_default"
    assert {m["model"] for m in cable["lines"][0]["models"]} == {"HANDSET A CPO", "HANDSET A PRO CPO"}
    assert cable["qty"] == body["projected_devices"]

    ear = next(c for c in body["consolidated"] if c["category"] == "earphones")
    assert ear["reason"] == "no_rate" and ear["lines"] == []


def test_a_declared_style_mix_drives_the_columns_instead_of_an_even_split(wired, api_client):
    _declare(wired, box_departments=HOUSE_DEPTS, device_colour_tokens=COLOUR_WORDS,
             accessory_order_option_mix={"screen_protector": [{"key": "clear", "share": 75},
                                                              {"key": "privacy", "share": 25}]})
    wired.seed("commcalc.daily_sales_feed", COLOUR_MONTH)
    _rule_current(wired, "HANDSET A CPO")
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 2}).json()
    assert body["option_mix_bases"]["screen_protector"] == "declared"
    assert body["option_mix_bases"]["case"] == "even_split"
    cell = body["model_orders"][0]["cells"]["screen_protector"]
    clear = next(o for o in cell["options"] if o["key"] == "clear")
    assert clear["qty"] == round(cell["qty"] * 0.75)


def test_a_phone_awaiting_a_ruling_has_dashes_in_the_grid_not_zeros(wired, api_client):
    _declare(wired, box_departments=HOUSE_DEPTS, device_colour_tokens=COLOUR_WORDS)
    wired.seed("commcalc.daily_sales_feed", COLOUR_MONTH)
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 2}).json()
    for r in body["model_orders"]:
        for cell in r["cells"].values():
            assert cell["qty"] is None
            assert cell["reason"] == "awaiting_declaration"


def test_the_catalog_and_the_colour_words_can_be_declared_through_the_config_route(wired, api_client):
    _declare(wired, box_departments=HOUSE_DEPTS)
    r = api_client.put(CONFIG_PATH, json={
        "colour_tokens": ["Pink", " black ", "pink"],
        "catalog": [{"key": "case", "dimensions": [{"key": "style", "options": [
            {"key": "wallet", "label": "Wallet", "image_url": "https://example.test/w.png"}]}]}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["colour_tokens"] == ["black", "pink"], "lower-cased, trimmed and de-duplicated"
    assert set(body["catalog"]) == {"case", "screen_protector", "cable", "charger", "earphones"}


def test_a_connector_the_catalog_does_not_offer_is_refused_rather_than_stored(wired, api_client):
    """A 200 from this route has to mean the sheet will really show what was chosen."""
    _declare(wired, box_departments=HOUSE_DEPTS)
    bad = api_client.put(PATH + "/model-spec", json={"specs": [
        {"model": "HANDSET A CPO", "category": "cable", "dimension": "connector",
         "option": "usb_z_to_usb_z"}]})
    assert bad.status_code == 400
    assert "catalog" in bad.text.lower()
    good = api_client.put(PATH + "/model-spec", json={"specs": [
        {"model": "HANDSET A CPO", "category": "cable", "dimension": "connector",
         "option": "usb_a_to_lightning"}]})
    assert good.status_code == 200, good.text
    assert good.json()["written"] == 1


def test_a_per_phone_connector_moves_that_phone_only(wired, api_client):
    _declare(wired, box_departments=HOUSE_DEPTS, device_colour_tokens=COLOUR_WORDS)
    wired.seed("commcalc.daily_sales_feed", COLOUR_MONTH)
    _rule_current(wired, "HANDSET A CPO", "HANDSET A PRO CPO")
    wired.seed("commcalc.model_accessory_spec", [
        {"org_id": ORG, "model": "HANDSET A PRO CPO", "category": "cable",
         "dimension": "connector", "option": "usb_c_to_lightning", "id": "s1"}])
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 2}).json()
    cable = next(c for c in body["consolidated"] if c["category"] == "cable")
    assert len(cable["lines"]) == 2
    by_label = {l["label"]: l for l in cable["lines"]}
    assert [m["model"] for m in by_label["USB-C to Lightning"]["models"]] == ["HANDSET A PRO CPO"]
    assert [m["model"] for m in by_label["USB-C to USB-C"]["models"]] == ["HANDSET A CPO"]
    assert sum(l["qty"] for l in cable["lines"]) == body["projected_devices"]


def test_the_report_still_works_before_the_new_migration_is_applied(wired, api_client):
    """Only `box_departments` exists on the config row and `model_accessory_spec` does not exist at
    all. The plan must come back on the house catalog with every phone on the standard connector —
    the same degradation rule migration 1104 was held to."""
    wired.seed("commcalc.accessory_config", [{"org_id": ORG, "box_departments": HOUSE_DEPTS,
                                              "id": "cfg"}])
    wired.seed("commcalc.daily_sales_feed", COLOUR_MONTH)
    r = api_client.get(PATH, params={"period": PERIOD, "months": 1, "horizon": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["catalog_basis"] == "house_default"
    assert body["colour_tokens_basis"] == "not_declared"
    assert body["model_specs_declared"] == 0
    assert [c["category"] for c in body["consolidated"]] == ["cable", "charger", "earphones"]


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A PRE-OWNED DEVICE IS A DIFFERENT DEVICE (owner 2026-10-31, index §POS-18)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Owner, verbatim: *"cpo is certified pre owned is adifferent device than new  , imnei is unique"*.
#
# The month below is the live Luxelink shape: the same handset sold both marked and unmarked, in
# several colours each, plus the glued SKU form its promo rows carry. Before the fix the two stayed
# apart only because `CPO` survived the parse — which is why the alias test underneath matters more
# than the happy path.
CONDITION_MONTH = [
    _sale("D-1", "HANDSET A 128GB Pink CPO", id="d1"),
    _sale("D-2", "HANDSET A 128GB Black CPO", id="d2"),
    _sale("D-3", "CERTIFIED_PREOWNED_HANDSET_A Midnight", id="d3"),
    _sale("D-4", "HANDSET A 128GB Pink", id="d4"),
    _sale("D-5", "HANDSET A 128GB Midnight", id="d5"),
]
CONDITION_WORDS = ["CPO", "CERTIFIED_PREOWNED"]


def test_a_marked_handset_is_ordered_separately_from_the_new_one(wired, api_client):
    """THE OWNER'S RULING, end to end: two rows for one model, each folding its own colours, and the
    marked row shows the ORG'S OWN word rather than the platform's."""
    _declare(wired, box_departments=HOUSE_DEPTS, device_colour_tokens=COLOUR_WORDS,
             device_condition_tokens=CONDITION_WORDS)
    wired.seed("commcalc.daily_sales_feed", CONDITION_MONTH)
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()
    assert body["condition_tokens_basis"] == "declared"
    rows = {m["model"]: m for m in body["models"]}
    assert len(rows) == 2, rows
    marked = next(m for m in body["models"] if m["condition"] == "refurbished")
    unmarked = next(m for m in body["models"] if m["condition"] == "new")
    assert marked["units_total"] == 3, "two worded CPO lines and the glued SKU form"
    assert unmarked["units_total"] == 2
    assert marked["condition_marker"] == "CPO" and marked["condition_basis"] == "declared"
    assert unmarked["condition_marker"] is None and unmarked["condition_basis"] == "unmarked"
    assert marked["model"].endswith("· CPO")


def test_a_curated_alias_cannot_put_a_pre_owned_handset_on_the_new_ones_row(wired, api_client):
    """The silent merge the fix exists to prevent. A human curating `device_model_alias` is saying
    what a model is CALLED, which is not a ruling on its condition — so the condition is applied
    after the alias and the two rows survive being given one name."""
    _declare(wired, box_departments=HOUSE_DEPTS, device_colour_tokens=COLOUR_WORDS,
             device_condition_tokens=CONDITION_WORDS)
    wired.seed("commcalc.daily_sales_feed", CONDITION_MONTH)
    wired.seed("commcalc.device_model_alias",
               [{"org_id": ORG, "raw_model": d["product_desc"], "canonical_model": "HANDSET A",
                 "lifecycle": "current", "id": f"al{i}"} for i, d in enumerate(CONDITION_MONTH)])
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()
    assert {m["condition"] for m in body["models"]} == {"refurbished", "new"}
    assert len(body["models"]) == 2, "one name, still two devices"
    assert sum(m["units_total"] for m in body["models"]) == 5


def test_without_a_declared_condition_vocabulary_no_line_is_identified(wired, api_client):
    """CLAUDE.md, *"every feature is for EVERY tenant"*: a condition WORD is a tenant's own spelling,
    so nothing declared identifies nothing and the surface says so. The rows are exactly what they
    were before the question existed — this is the property that makes the change safe for the
    tenant whose feed marks no condition at all."""
    _declare(wired, box_departments=HOUSE_DEPTS, device_colour_tokens=COLOUR_WORDS)
    wired.seed("commcalc.daily_sales_feed", CONDITION_MONTH)
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()
    assert body["condition_tokens_basis"] == "not_declared"
    assert body["condition_tokens"] == []
    assert {m["condition"] for m in body["models"]} == {None}
    assert {m["condition_basis"] for m in body["models"]} == {"no_vocabulary"}
    assert not any("·" in m["model"] for m in body["models"])


def test_one_tenants_condition_word_cannot_mark_another_tenants_feed(wired, api_client):
    """The cross-tenant half. This org declares a word its own feed does not use, so its marked-
    looking lines read as unmarked rather than borrowing the other tenant's reading."""
    _declare(wired, box_departments=HOUSE_DEPTS, device_colour_tokens=COLOUR_WORDS,
             device_condition_tokens=["refurb"])
    wired.seed("commcalc.daily_sales_feed", CONDITION_MONTH)
    body = api_client.get(PATH, params={"period": PERIOD, "months": 1}).json()
    assert {m["condition"] for m in body["models"]} == {"new"}
    assert {m["condition_basis"] for m in body["models"]} == {"unmarked"}


def test_the_condition_words_are_saved_and_read_back_through_the_one_home(wired, api_client):
    """The setup panel's round trip, and the platform's four conditions travelling with it so the
    screen never has to name one."""
    _declare(wired, box_departments=HOUSE_DEPTS)
    r = api_client.put(PATH + "/config", json={"condition_tokens": CONDITION_WORDS})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["condition_tokens_basis"] == "declared"
    assert saved["condition_tokens"] == ["certified preowned", "cpo"]
    assert [c["key"] for c in saved["device_conditions"]] == ["new", "refurbished", "used",
                                                              "damaged"]


def test_the_plan_still_works_before_the_condition_migration_is_applied(wired, api_client):
    """`device_condition_tokens` does not exist on the config row yet. The report comes back with no
    condition identified rather than a 500 — the same degradation rule migs 1104 and 1109 were held
    to, because code deploys on merge while a migration waits for the owner."""
    wired.seed("commcalc.accessory_config", [{"org_id": ORG, "box_departments": HOUSE_DEPTS,
                                              "id": "cfg"}])
    wired.seed("commcalc.daily_sales_feed", CONDITION_MONTH)
    r = api_client.get(PATH, params={"period": PERIOD, "months": 1})
    assert r.status_code == 200, r.text
    assert r.json()["condition_tokens_basis"] == "not_declared"
