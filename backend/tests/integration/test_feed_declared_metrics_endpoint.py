"""Integration: a store-day paid from the store's OWN sales report, through the REAL app.

Real routing, real middleware, the real endpoint bodies — over the in-memory database double, with
conftest blocking every outbound socket. There is no staging Supabase, so "integration" here means
the app, not a real database (docs/TESTING.md).

WHY THIS EXISTS BEYOND THE UNIT TESTS. The unit tests prove the four pure pieces. Only the app proves
the three things that actually broke a tenant: that the uploaded file reaches the SAME ingest the
portal pull uses (so a delivered file and a pulled one are indistinguishable downstream), that the
daily-incentive endpoint reads the declared authority rather than the declaration table, and that the
read is ORG-SCOPED — a feed is where cross-tenant leakage lives, and this one is keyed on a store-day
that two tenants can both have.

MULTI-TENANT (project rule 2026-10-30): every assertion is made for THREE orgs, each with its own
export shape and its own declared vocabulary, and each org's figures are checked to be its own.
"""
import io

import pytest

INCENTIVE = "/api/v1/commcalc/daily-incentive/2026-10-21"
UPLOAD = "/api/v1/commcalc/pos-report/upload"
HOUSE = "00000000-0000-0000-0000-000000000001"
ORG_B = "00000000-0000-0000-0000-0000000000b2"
ORG_C = "00000000-0000-0000-0000-0000000000b3"
DAY = "2026-10-21"

#: Three tenants. Each declares its OWN categories, its OWN store and its OWN people — so a leak
#: shows up as another tenant's category or rep, not merely as a wrong total.
CATS = {
    HOUSE: [("overnight", "Next Day Air", "both"), ("printing", "Printing", "amount")],
    ORG_B: [("postpaid_count", "Activations", "both")],
    #: ORG_C declares the SAME category key as the house org, on the SAME store code and the same
    #: day. Two tenants may both sell overnight parcels from a store numbered 7710, and if a test
    #: gives every tenant its own key then a dropped `.eq("org_id", ...)` is invisible — the catalog
    #: would filter the stranger's row out for an unrelated reason. Here only the org scope separates
    #: them, which is what makes the leak test ARMED (verified by removing the filter).
    ORG_C: [("overnight", "Next Day Air", "both")],
}
#: ORG_C deliberately uses the SAME store code as the house org. Store codes are not globally
#: unique, and a test where every tenant owns a distinct code proves nothing about org scoping — the
#: store filter alone would separate them and a dropped `.eq("org_id", ...)` would still pass. With
#: a shared code, ONLY the org scope stands between two tenants' store-days (verified by arming:
#: removing the org filter fails these tests).
STORE = {HOUSE: "7710", ORG_B: "B-1115", ORG_C: "7710"}
WHO = {HOUSE: "295533", ORG_B: "Ana", ORG_C: "Bo"}
#: Each tenant's money is a different, recognisable number.
MONEY = {HOUSE: 300.0, ORG_B: 450.0, ORG_C: 600.0}
ORGS = (HOUSE, ORG_B, ORG_C)


def _portal_row(org, metric, *, basis="line_map", units=2, amount=None):
    return {"org_id": org, "source_id": "src-" + org[-2:], "portal_key": "pos_report",
            "report_key": "item_sales", "business_date": DAY, "store_code": STORE[org],
            "store_key": STORE[org].lower(), "store_label": STORE[org],
            "employee_name": WHO[org], "employee_key": WHO[org].lower(),
            "metric_key": metric, "metric_basis": basis, "line_label": metric,
            "units": units, "amount": MONEY[org] if amount is None else amount, "source_line": 2}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    from app.modules.closing import router as closing_router
    from app.modules.commcalc import router as commcalc_router
    for mod in (commcalc_router, closing_router):
        for attr in ("get_supabase", "sb"):
            monkeypatch.setattr(f"{mod.__name__}.{attr}", lambda: fake_supabase, raising=False)
    fake_supabase.seed("commcalc.closing_count_field_def", [
        {"org_id": org, "field_key": k, "label": label, "value_kind": vk,
         "declared_by": "employee", "is_active": True, "in_revenue": True, "sort_order": i}
        for org in ORGS for i, (k, label, vk) in enumerate(CATS[org])])
    fake_supabase.seed("commcalc.incentive_rule", [
        {"org_id": org, "id": "rule-" + org[-2:], "rule_key": "pool", "label": "Store pool",
         "rule_kind": "threshold_pool", "is_active": True,
         "qualifier": {"scope": "store", "threshold": 100},
         "payout": {"rate": 0.01, "basis": "revenue", "split": "individual_sales",
                    "metric_keys": []}}
        for org in ORGS])
    #: The FEED side: every tenant's own store-day, all on the same date, which is the shape a
    #: cross-tenant leak would hide in.
    fake_supabase.seed("commcalc.portal_metric_day",
                       [_portal_row(org, CATS[org][0][0]) for org in ORGS])
    #: The DECLARATION side, deliberately DIFFERENT, so a test can tell which one answered.
    fake_supabase.seed("commcalc.closing_metric_line", [
        {"org_id": org, "store_code": STORE[org], "close_date": DAY,
         "metric_key": CATS[org][0][0], "employee_name": WHO[org], "units": 1, "amount": 11.0}
        for org in ORGS])
    fake_supabase.seed("commcalc.data_source", [
        {"org_id": org, "id": "src-" + org[-2:], "processor": "pos_report", "label": "Portal",
         "portal_reports": [{"key": "item_sales", "label": "Item Sales"}],
         "portal_calibration": {}}
        for org in ORGS])
    return fake_supabase


# ═════ the route exists where it is documented ═══════════════════════════════════════════════════

def test_the_upload_door_is_registered_at_its_documented_path(app_module):
    from app.core.path_redact import route_templates
    assert UPLOAD in set(route_templates(app_module))


# ═════ which source answered, and the answer always says so ══════════════════════════════════════

@pytest.mark.parametrize("org", ORGS, ids=[o[-2:] for o in ORGS])
def test_declaring_nothing_reads_the_reps_declarations_exactly_as_today(wired, api_client, org):
    body = api_client.get(INCENTIVE, params={"org_id": org, "store_code": STORE[org]}).json()
    assert body["metric_source"] == "declaration"
    assert body["declared"][CATS[org][0][0]]["amount"] == 11.0, "the rep's own figure, not the feed's"
    assert "declaration" in body["metric_source_note"]


@pytest.mark.parametrize("org", ORGS, ids=[o[-2:] for o in ORGS])
def test_the_feed_answers_when_it_is_the_declared_authority(wired, api_client, org):
    wired.seed("commcalc.commission_org_config",
               [{"org_id": o, "declared_metric_authority": "feed"} for o in ORGS])
    body = api_client.get(INCENTIVE, params={"org_id": org, "store_code": STORE[org]}).json()
    assert body["metric_source"] == "feed"
    assert body["declared"][CATS[org][0][0]]["amount"] == MONEY[org]
    assert body["metric_source_declaration_pending"] is False


@pytest.mark.parametrize("org", ORGS, ids=[o[-2:] for o in ORGS])
def test_an_override_answers_this_call_only_and_says_it_was_overridden(wired, api_client, org):
    body = api_client.get(INCENTIVE, params={"org_id": org, "store_code": STORE[org],
                                             "metric_source": "feed"}).json()
    assert body["metric_source"] == "feed" and body["metric_source_overridden"] is True
    assert body["metric_source_declared"] == "", "nothing was persisted by asking"


@pytest.mark.parametrize("org", ORGS, ids=[o[-2:] for o in ORGS])
def test_a_day_the_feed_covers_pays_what_the_declaration_alone_could_not(wired, api_client, org):
    declared = api_client.get(INCENTIVE, params={"org_id": org, "store_code": STORE[org]}).json()
    fed = api_client.get(INCENTIVE, params={"org_id": org, "store_code": STORE[org],
                                            "metric_source": "feed"}).json()
    assert declared["total"] == 0.0, "an $11 day misses a $100 gate — correctly"
    assert fed["total"] > 0.0, "the store's own report shows the day it really had"


# ═════ org scoping — a feed is where cross-tenant leakage lives ══════════════════════════════════

@pytest.mark.parametrize("org", ORGS, ids=[o[-2:] for o in ORGS])
def test_one_tenants_store_day_is_never_reachable_from_anothers_read(wired, api_client, org):
    """ARMED: the house org and ORG_C share a store code, a day and a category key, so the ONLY
    thing separating their figures is the org scope. Drop it and this tenant's money doubles."""
    body = api_client.get(INCENTIVE, params={"org_id": org, "store_code": STORE[org],
                                             "metric_source": "feed"}).json()
    mine = {c[0] for c in CATS[org]}
    assert set(body["declared"]) <= mine, "a category this tenant never declared is another's"
    got = body["declared"][CATS[org][0][0]]["amount"]
    assert got == MONEY[org], f"expected only this tenant's own money, got {got}"
    strangers = {o for o in ORGS if o != org and STORE[o] == STORE[org]}
    for other in strangers:
        assert got != MONEY[org] + MONEY[other], "a tenant sharing this store code was folded in"


@pytest.mark.parametrize("org", ORGS, ids=[o[-2:] for o in ORGS])
def test_only_this_tenants_people_are_paid(wired, api_client, org):
    body = api_client.get(INCENTIVE, params={"org_id": org, "store_code": STORE[org],
                                             "metric_source": "feed"}).json()
    others = {WHO[o] for o in ORGS if o != org} - {WHO[org]}
    assert not (set(body.get("by_employee") or {}) & others)


# ═════ a guess never pays ════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("org", ORGS, ids=[o[-2:] for o in ORGS])
def test_a_line_matched_only_by_wording_is_reported_and_not_counted(wired, api_client, org):
    wired.seed("commcalc.portal_metric_day",
               [_portal_row(org, CATS[org][0][0], basis="label_part")
                for org in ORGS])
    body = api_client.get(INCENTIVE, params={"org_id": org, "store_code": STORE[org],
                                             "metric_source": "feed"}).json()
    assert body["declared"][CATS[org][0][0]]["declared"] is False
    assert body["metric_source_not_counted"], "it is reported, never silently dropped"
    assert "not counted" in body["metric_source_note"]


# ═════ the upload door ═══════════════════════════════════════════════════════════════════════════

def _csv(rows):
    return io.BytesIO("\n".join(",".join(str(c) for c in r) for r in rows).encode())


@pytest.mark.parametrize("org", ORGS, ids=[o[-2:] for o in ORGS])
def test_a_file_lands_through_the_same_ingest_the_pull_uses(wired, api_client, org):
    key = CATS[org][0][0]
    label = CATS[org][0][1]
    body = _csv([["Date", "Employee", "Category", "Quantity", "Net Sales"],
                 [DAY, WHO[org], label, 3, 150.0]])
    r = api_client.post(UPLOAD, data={"source_id": "src-" + org[-2:], "report_key": "item_sales",
                                      "org_id": org},
                        files={"file": ("export.csv", body, "text/csv")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["delivered"] is True and out["rows_ingested"] == 1
    assert out["delivered_as"] == "upload"
    landed = [row for row in wired.rows("commcalc.portal_metric_day")
              if row.get("org_id") == org and row.get("metric_key") == key
              and row.get("units") == 3]
    assert landed, "the row did not land under this tenant"


@pytest.mark.parametrize("org", ORGS, ids=[o[-2:] for o in ORGS])
def test_a_file_that_is_not_this_report_lands_nothing_and_names_what_it_looked_for(
        wired, api_client, org):
    body = _csv([["Widget", "Sprocket"], ["a", "b"]])
    r = api_client.post(UPLOAD, data={"source_id": "src-" + org[-2:], "report_key": "item_sales",
                                      "org_id": org},
                        files={"file": ("wrong.csv", body, "text/csv")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["delivered"] is False and out.get("rows_ingested", 0) == 0
    assert out["warnings"], "an empty parse is never a green tick"


def test_an_empty_file_is_refused_in_words(wired, api_client):
    r = api_client.post(UPLOAD, data={"source_id": "src-01", "report_key": "item_sales",
                                      "org_id": HOUSE},
                        files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")})
    assert r.status_code == 400 and "empty" in r.text.lower()


def test_a_source_belonging_to_another_tenant_is_not_found(wired, api_client):
    r = api_client.post(UPLOAD, data={"source_id": "src-b2", "report_key": "item_sales",
                                      "org_id": HOUSE},
                        files={"file": ("x.csv", _csv([["Category"], ["a"]]), "text/csv")})
    assert r.status_code == 404


def test_a_source_that_is_not_a_pos_report_login_is_refused_with_the_reason(wired, api_client):
    wired.seed("commcalc.data_source",
               [{"org_id": HOUSE, "id": "src-other", "processor": "supply_vendor",
                 "label": "Vendor", "portal_reports": [], "portal_calibration": {}}])
    r = api_client.post(UPLOAD, data={"source_id": "src-other", "org_id": HOUSE},
                        files={"file": ("x.csv", _csv([["Category"], ["a"]]), "text/csv")})
    assert r.status_code == 400 and "POS report" in r.text
