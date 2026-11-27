"""Integration: a daily-incentive rule paid on an ITEM the tenant uploaded, through the REAL app.

Real routing, real middleware, the real endpoint bodies — over the in-memory database double, with
conftest blocking every outbound socket. There is no staging Supabase, so "integration" here means
the app, not a real database (docs/TESTING.md).

WHAT ONLY THE APP CAN PROVE. `harness_payable_attributes.py` proves the pure pieces: the catalog
grows item entries, items are never revenue, the feed projects an item line. Every one of the things
below is a way this feature could be live and still wrong:

  * `GET /commcalc/incentive-rules` actually SERVES the uploaded items, read org-scoped from the
    tenant's own data sources — the picker builds no list of its own, so a list the endpoint does not
    serve is a dropdown that stays empty.
  * `PUT /commcalc/incentive-rules` ACCEPTS a rule naming an item. This is the defect itself: the
    guard read categories only, so a rule on an item the tenant had already declared in their feed
    mapping was refused with "add them on Count Fields first" — telling the owner to retype by hand
    a list they had uploaded. The guard and the picker must read ONE set.
  * an item rule PAYS, which needs the catalog and the feed projection wired to each other in the
    endpoint — a registry whose callers do not dereference it is not a fix at all (CLAUDE.md §19.18).
  * paying per item does NOT move the day's revenue or what the category rules already paid. An item
    is a finer view of money a category counts, so counting it as revenue would overstate every day.

MULTI-TENANT (project rule 2026-10-30): asserted for THREE orgs. ORG_B uploaded nothing and must be
offered nothing — never the house org's product names. ORG_C uploads the SAME item spelling on the
SAME store code and day as the house org, so only the org scope separates them; a dropped
`.eq("org_id", ...)` fails these tests rather than hiding behind an unrelated filter.
"""
import pytest

RULES = "/api/v1/commcalc/incentive-rules"
INCENTIVE = "/api/v1/commcalc/daily-incentive/2026-10-21"
HOUSE = "00000000-0000-0000-0000-000000000001"
ORG_B = "00000000-0000-0000-0000-0000000000b2"
ORG_C = "00000000-0000-0000-0000-0000000000b3"
ORGS = (HOUSE, ORG_B, ORG_C)
DAY = "2026-10-21"

#: Each tenant's own category vocabulary. ORG_C shares the house org's key deliberately (see above).
CATS = {HOUSE: ("overnight", "Next Day Air"),
        ORG_B: ("postpaid_count", "Activations"),
        ORG_C: ("overnight", "Next Day Air")}
#: The ITEM vocabulary each tenant uploaded with its sales report. ORG_B uploaded none at all.
ITEMS = {HOUSE: {"NDA Early AM": "overnight", "NDA Saver": "overnight"},
         ORG_B: {},
         ORG_C: {"NDA Early AM": "overnight"}}
#: ORG_C shares the house org's store code: store codes are not globally unique, and a test where
#: every tenant owns a distinct one proves nothing about org scoping.
STORE = {HOUSE: "7710", ORG_B: "B-1115", ORG_C: "7710"}
WHO = {HOUSE: "Hajra", ORG_B: "Ana", ORG_C: "Bo"}
#: Recognisably different units per tenant, so a leak reads as the wrong NUMBER as well as the wrong
#: tenant: 3 Early AM + 2 Saver for the house org, 7 Early AM for ORG_C.
FEED = {HOUSE: [("NDA Early AM", 3), ("NDA Saver", 2)],
        ORG_B: [("Activations", 4)],
        ORG_C: [("NDA Early AM", 7)]}
ITEM_KEY = "item:nda early am"


def _portal_row(org, label, units):
    return {"org_id": org, "source_id": "src-" + org[-2:], "portal_key": "pos_report",
            "report_key": "item_sales", "business_date": DAY, "store_code": STORE[org],
            "store_key": STORE[org].lower(), "store_label": STORE[org],
            "employee_name": WHO[org], "employee_key": WHO[org].lower(),
            "metric_key": CATS[org][0], "metric_basis": "line_map", "line_label": label,
            "units": units, "amount": 0.0, "source_line": 2}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    from app.modules.closing import router as closing_router
    from app.modules.commcalc import router as commcalc_router
    for mod in (commcalc_router, closing_router):
        for attr in ("get_supabase", "sb"):
            monkeypatch.setattr(f"{mod.__name__}.{attr}", lambda: fake_supabase, raising=False)
    #: The money posture is proved by its own tests; here it must not stand between the test and the
    #: guard it is testing, so the admin check passes and the GUARD's own refusal is what is asserted.
    monkeypatch.setattr("app.modules.commcalc.router._require_commission_admin",
                        lambda *a, **k: True, raising=False)
    fake_supabase.seed("commcalc.closing_count_field_def", [
        {"org_id": org, "field_key": CATS[org][0], "label": CATS[org][1], "value_kind": "count",
         "declared_by": "employee", "is_active": True, "in_revenue": True, "sort_order": 0}
        for org in ORGS])
    #: The ITEM vocabulary lives where the tenant uploaded it: the data source's calibration. Nothing
    #: in this test writes a second copy of it, because there is no second home for it.
    fake_supabase.seed("commcalc.data_source", [
        {"org_id": org, "id": "src-" + org[-2:], "processor": "pos_report", "label": "Portal",
         "portal_reports": [{"key": "item_sales", "label": "Item Sales"}],
         "portal_calibration": ({"item_sales": {"line_map": ITEMS[org]}} if ITEMS[org] else {})}
        for org in ORGS])
    fake_supabase.seed("commcalc.portal_metric_day",
                       [_portal_row(org, label, n) for org in ORGS for label, n in FEED[org]])
    #: Every tenant reads its own sales report, which is what makes an item measurable at all.
    fake_supabase.seed("commcalc.commission_org_config",
                       [{"org_id": org, "declared_metric_authority": "feed"} for org in ORGS])
    return fake_supabase


def _item_rule(org, *, rate=2.0, key="pay_per_early_am", metric=ITEM_KEY):
    return {"rule_key": key, "label": "Early AM bonus", "rule_kind": "per_unit",
            "qualifier": {"scope": "none"},
            "payout": {"basis": "units", "metric_keys": [metric], "rate": rate},
            "is_active": True}


# ═════ the endpoint serves what the picker is meant to offer ══════════════════════════════════════

def test_the_rules_endpoint_serves_the_items_the_tenant_uploaded(wired, api_client):
    body = api_client.get(RULES, params={"org_id": HOUSE}).json()
    served = {m["key"]: m for m in body["metrics"] if m.get("source") == "feed_item"}
    assert set(served) == {"item:nda early am", "item:nda saver"}
    assert served[ITEM_KEY]["label"] == "NDA Early AM", "the tenant's own spelling"
    assert served[ITEM_KEY]["parent_key"] == "overnight", "and the category it counts towards"


def test_the_categories_are_still_served_beside_them(wired, api_client):
    body = api_client.get(RULES, params={"org_id": HOUSE}).json()
    assert "overnight" in {m["key"] for m in body["metrics"]}, \
        "an item list that REPLACED the categories would break every existing rule"


# ═════ the defect: a rule on an uploaded item is ACCEPTED ════════════════════════════════════════

def test_a_rule_paid_on_an_uploaded_item_is_accepted(wired, api_client):
    r = api_client.put(RULES, params={"org_id": HOUSE}, json=_item_rule(HOUSE))
    assert r.status_code == 200, f"the guard refused an item the tenant declared: {r.text}"
    saved = r.json()
    assert saved["payout"]["metric_keys"] == [ITEM_KEY]
    assert saved["payout"]["rate"] == 2.0, "the amount is the admin's, not a built-in"


def test_an_item_the_tenant_never_uploaded_is_refused_and_named(wired, api_client):
    r = api_client.put(RULES, params={"org_id": HOUSE},
                       json=_item_rule(HOUSE, metric="item:ground residential"))
    assert r.status_code == 400
    assert "ground residential" in r.text.lower(), "the refusal names the item, not a key"
    assert "Count Fields" not in r.text, \
        "an unmapped ITEM is fixed on the report mapping, not by retyping a category"


def test_a_category_the_tenant_never_declared_is_still_refused_as_before(wired, api_client):
    r = api_client.put(RULES, params={"org_id": HOUSE},
                       json=_item_rule(HOUSE, metric="made_up_category"))
    assert r.status_code == 400 and "Count Fields" in r.text, \
        "the existing refusal for a category is unchanged"


# ═════ it pays, and it moves nothing that already worked ═════════════════════════════════════════

def test_an_item_rule_pays_on_the_items_own_units(wired, api_client):
    api_client.put(RULES, params={"org_id": HOUSE}, json=_item_rule(HOUSE))
    body = api_client.get(INCENTIVE, params={"org_id": HOUSE, "store_code": STORE[HOUSE]}).json()
    assert body["metric_source"] == "feed"
    #: 3 Early AM at $2.00 — and NOT the 5 units of the parent category, which is the whole point.
    assert body["total"] == 6.00, f"want $6.00 for 3 Early AM at $2.00, got {body['total']}"


def test_two_items_of_one_category_can_pay_different_amounts(wired, api_client):
    api_client.put(RULES, params={"org_id": HOUSE}, json=_item_rule(HOUSE, rate=2.0))
    api_client.put(RULES, params={"org_id": HOUSE},
                   json=_item_rule(HOUSE, rate=0.5, key="pay_per_saver",
                                   metric="item:nda saver"))
    body = api_client.get(INCENTIVE, params={"org_id": HOUSE, "store_code": STORE[HOUSE]}).json()
    #: 3 x $2.00 + 2 x $0.50 = $7.00. This is what the categories could never express: one category,
    #: two items, two rates.
    assert body["total"] == 7.00, f"want $7.00, got {body['total']}"


def test_paying_per_item_does_not_move_the_days_revenue(wired, api_client):
    before = api_client.get(INCENTIVE, params={"org_id": HOUSE,
                                               "store_code": STORE[HOUSE]}).json()
    api_client.put(RULES, params={"org_id": HOUSE}, json=_item_rule(HOUSE))
    after = api_client.get(INCENTIVE, params={"org_id": HOUSE,
                                              "store_code": STORE[HOUSE]}).json()
    assert after["store_revenue"] == before["store_revenue"], \
        "an item is a finer view of money the category already counts, never extra revenue"
    assert after["employee_revenue"] == before["employee_revenue"], \
        "nor any one person's revenue, which is the weight a threshold pool splits on"


def test_the_parent_category_still_reads_its_own_full_figure(wired, api_client):
    api_client.put(RULES, params={"org_id": HOUSE}, json=_item_rule(HOUSE))
    body = api_client.get(INCENTIVE, params={"org_id": HOUSE, "store_code": STORE[HOUSE]}).json()
    assert body["declared"]["overnight"]["units"] == 5, \
        "3 Early AM + 2 Saver — splitting a category into items may not shrink it"


# ═════ MULTI-TENANT — nothing declared means nothing identified ══════════════════════════════════

def test_a_tenant_that_uploaded_no_items_is_offered_none(wired, api_client):
    body = api_client.get(RULES, params={"org_id": ORG_B}).json()
    assert not [m for m in body["metrics"] if m.get("source") == "feed_item"]
    assert not [m for m in body["metrics"] if "nda" in m["key"].lower()], \
        "and specifically none of another tenant's product names"


def test_that_tenant_may_not_pay_on_another_tenants_item(wired, api_client):
    r = api_client.put(RULES, params={"org_id": ORG_B}, json=_item_rule(ORG_B))
    assert r.status_code == 400, "ORG_B never uploaded 'NDA Early AM'"


@pytest.mark.parametrize("org", ORGS, ids=[o[-2:] for o in ORGS])
def test_each_tenants_item_list_is_its_own(wired, api_client, org):
    body = api_client.get(RULES, params={"org_id": org}).json()
    got = {m["label"] for m in body["metrics"] if m.get("source") == "feed_item"}
    assert got == set(ITEMS[org]), f"{org[-2:]} was served {got}"


def test_two_tenants_sharing_an_item_spelling_and_a_store_pay_their_own_units(wired, api_client):
    """ORG_C uploaded the SAME item on the SAME store code and day. Only the org scope separates
    them, so a dropped org filter shows up here as the wrong number."""
    for org in (HOUSE, ORG_C):
        api_client.put(RULES, params={"org_id": org}, json=_item_rule(org, rate=1.0))
    house = api_client.get(INCENTIVE, params={"org_id": HOUSE,
                                              "store_code": STORE[HOUSE]}).json()
    other = api_client.get(INCENTIVE, params={"org_id": ORG_C,
                                              "store_code": STORE[ORG_C]}).json()
    assert house["total"] == 3.00, f"the house org sold 3 Early AM, got {house['total']}"
    assert other["total"] == 7.00, f"ORG_C sold 7, got {other['total']}"


# ═════ the existing behaviour of the endpoint is untouched ═══════════════════════════════════════

def test_a_tenant_on_its_reps_declarations_is_unmoved(wired, api_client):
    """An item only has a figure on days read from the sales report: a rep declares per CATEGORY.
    That must read as an honest zero for the item rule, never as the category's units."""
    wired.seed("commcalc.closing_metric_line", [
        {"org_id": HOUSE, "store_code": STORE[HOUSE], "close_date": DAY,
         "metric_key": "overnight", "employee_name": WHO[HOUSE], "units": 5, "amount": 0.0}])
    api_client.put(RULES, params={"org_id": HOUSE}, json=_item_rule(HOUSE))
    body = api_client.get(INCENTIVE, params={"org_id": HOUSE, "store_code": STORE[HOUSE],
                                             "metric_source": "declaration"}).json()
    assert body["declared"]["overnight"]["units"] == 5, "the rep's own category figure stands"
    assert body["total"] == 0.00, \
        "the item rule pays nothing on a declaration — it may NOT inherit the category's units"


# ═════ SECURITY PASS 2026-11-27 — an item must be strictly finer than its category ═══════════════

def test_an_item_spelled_like_its_category_is_not_offered(wired, api_client):
    """`line_map` keys are report LINE LABELS, and `pos_report_feed`'s synonyms for that column
    begin "product category" / "category" — ahead of "product" and "item". So a tenant's export can
    carry a CATEGORY where the mapping is keyed, and `item:<label>` would then carry the category's
    WHOLE figure while the editor says "pay on that item alone"."""
    #: `seed` REPLACES a table, so the tenant's real item rows are re-seeded alongside the
    #: category-grained one. Without them `served` would be empty and the two assertions below
    #: would pass for the wrong reason — the trivially-true pass this whole review was about.
    wired.seed("commcalc.data_source", [
        {"org_id": org, "id": "src-" + org[-2:], "processor": "pos_report", "label": "Portal",
         "portal_reports": [{"key": "item_sales", "label": "Item Sales"}],
         "portal_calibration": ({"item_sales": {"line_map": ITEMS[org]}} if ITEMS[org] else {})}
        for org in ORGS] + [
        {"org_id": HOUSE, "id": "src-dup", "processor": "pos_report", "label": "Portal 2",
         "portal_reports": [{"key": "by_category", "label": "By Category"}],
         "portal_calibration": {"by_category": {"line_map": {"Next Day Air": "overnight"}}}}])
    body = api_client.get(RULES, params={"org_id": HOUSE}).json()
    served = {m["key"] for m in body["metrics"] if m.get("source") == "feed_item"}
    assert "item:next day air" not in served, \
        "an 'item' spelled like its own category is the category wearing a hat"
    assert "overnight" in {m["key"] for m in body["metrics"]}, \
        "...and the category itself is still offered — it is the right thing to pay on"
    assert "item:nda early am" in served, "a genuinely finer item is unaffected"


def test_a_rule_naming_an_item_and_its_category_is_refused(wired, api_client):
    """Whatever the label's grain, `daily_incentive.measure` sums per NAMED key with no notion of
    parentage, so measuring a parent and its own descendant counts one row's money twice."""
    r = api_client.put(RULES, params={"org_id": HOUSE},
                       json={"rule_key": "double", "label": "Doubled", "rule_kind": "per_unit",
                             "qualifier": {"scope": "none"},
                             "payout": {"basis": "units", "rate": 2.0,
                                        "metric_keys": [ITEM_KEY, "overnight"]}})
    assert r.status_code == 400, "a parent and its own item in one rule is a double count"
    assert "twice" in r.text.lower()


def test_two_sibling_items_in_one_rule_stay_legal(wired, api_client):
    """Disjoint lines, and paying different rates for two items of one category is the whole ask."""
    r = api_client.put(RULES, params={"org_id": HOUSE},
                       json={"rule_key": "both_items", "label": "Both", "rule_kind": "per_unit",
                             "qualifier": {"scope": "none"},
                             "payout": {"basis": "units", "rate": 1.0,
                                        "metric_keys": [ITEM_KEY, "item:nda saver"]}})
    assert r.status_code == 200, r.text
    body = api_client.get(INCENTIVE, params={"org_id": HOUSE, "store_code": STORE[HOUSE]}).json()
    assert body["total"] == 5.00, f"3 Early AM + 2 Saver at $1.00 = $5.00, got {body['total']}"


def test_the_qualifier_cannot_smuggle_the_overlap_in(wired, api_client):
    """The guard reads the qualifier's keys as well as the payout's — `_gate` measures the same way."""
    r = api_client.put(RULES, params={"org_id": HOUSE},
                       json={"rule_key": "sneaky", "label": "Sneaky", "rule_kind": "per_unit",
                             "qualifier": {"scope": "store", "basis": "units", "op": "gte",
                                           "threshold": 1, "metric_keys": ["overnight"]},
                             "payout": {"basis": "units", "rate": 2.0,
                                        "metric_keys": [ITEM_KEY]}})
    assert r.status_code == 400, "a gate on the parent plus a payout on its item is the same overlap"
