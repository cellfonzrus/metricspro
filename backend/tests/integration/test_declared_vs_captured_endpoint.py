"""Integration: GET /closing/declared-vs-captured through the REAL app (index §CLOSING-01).

Real routing, real middleware, the real endpoint body — over the in-memory database double, with
conftest blocking every outbound socket. There is no staging Supabase, so "integration" here means
the app, not a real database (docs/TESTING.md).

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT. The pure reconciler is proved in
`tests/unit/test_declared_feed_recon.py`. What only the app can prove is the WIRING, and every one
of these is a way the feature could be live and still wrong:

  * the route exists at its documented path
  * a tenant that has named no counterpart gets an honest "not configured" instead of an empty
    report that looks like a clean bill of health
  * the declared side is read at BOTH grains — the per-employee lines (mig 1072) and the store's own
    counts — rather than one being inferred from the other
  * the captured side comes from the tenant's financing VENDOR rows, so Edge and Acima reach this
    endpoint as config and nothing in the code spells either
  * a day the sales feed never loaded is `pending`, not a $0.00 variance
"""
import pytest

ORG = "00000000-0000-0000-0000-000000000001"
PATH = "/api/v1/closing/declared-vs-captured"
DAY = "2026-10-28"


def _sale(trans_id, serial, price, *, rep="Arora, Tanish", store="123 Main St",
          tender="TW Financing", day=DAY):
    return {"org_id": ORG, "period": day[:7], "trans_id": trans_id, "store": store,
            "salesperson": rep, "trans_date": day, "tender_type": tender,
            "department": "Handsets", "category": "Phones", "product_desc": "moto g play",
            "contract_type": "New", "ext_price": price, "voided": None, "trans_type": "Sale",
            "serial_1": serial, "mdn": "2155550143", "id": trans_id + serial}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    """The closing router's client getter pointed at the double, and the per-process memos cleared
    so one test's store index cannot answer another's."""
    from app.modules.closing import router as closing_router
    monkeypatch.setattr("app.modules.closing.router.get_supabase", lambda: fake_supabase,
                        raising=False)
    monkeypatch.setattr("app.modules.closing.router.sb", lambda: fake_supabase, raising=False)
    for name in ("_STORE_INDEX_MEMO",):
        memo = getattr(closing_router, name, None)
        if isinstance(memo, dict):
            memo.clear()
    # The sales read picks the pair side by whether the day is in the OPEN month; pin it so the test
    # does not change answer with the calendar.
    monkeypatch.setattr("app.modules.closing.router._biz_today_iso", lambda: DAY, raising=False)
    fake_supabase.seed("commcalc.store_mapping",
                       [{"org_id": ORG, "store_code": "B-1115", "store_address": "123 Main St"}])
    return fake_supabase


def _seed_edge_category(db, *, declared_by="employee", counterpart="financing:edge"):
    db.seed("commcalc.closing_count_field_def", [
        {"org_id": ORG, "field_key": "edge_count", "label": "Edge", "sort_order": 10,
         "is_standard": False, "is_active": True, "recon_class": "other",
         "value_kind": "both", "declared_by": declared_by, "unit_price": None,
         "in_revenue": True, "feed_counterpart": counterpart,
         "feed_units_tolerance": None, "feed_amount_tolerance": None},
    ])


def _seed_edge_vendor(db):
    """The tenant's OWN financing vendor. Edge reaches this endpoint as a config row — the point of
    the design, and the reason no test here asserts a carrier name in any source file."""
    db.seed("commcalc.financing_vendor", [
        {"org_id": ORG, "vendor_key": "edge", "label": "Edge financing", "enabled": True,
         "detection_source": "rules", "amount_basis": "unit_line", "sort_order": 10},
    ])
    db.seed("commcalc.financing_detection_rule", [
        {"org_id": ORG, "vendor_key": "edge", "match_field": "tender_type",
         "match_op": "word", "match_value": "financing"},
    ])


def test_the_route_is_registered_at_its_documented_path(app_module):
    from app.core.path_redact import route_templates
    assert PATH in set(route_templates(app_module))


def test_a_tenant_that_named_no_counterpart_is_told_so_not_shown_a_clean_report(wired, api_client):
    # An empty report would read as "every declaration agrees". It must say nothing is wired up.
    _seed_edge_category(wired, counterpart=None)
    r = api_client.get(PATH, params={"date_from": DAY})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is False
    assert body["rows"] == []
    assert "edge_count" in body["not_reconciled"]
    assert "Count Fields" in body["note"]


def test_a_declaration_matching_the_feed_comes_back_ok(wired, api_client):
    _seed_edge_category(wired)
    _seed_edge_vendor(wired)
    wired.seed("commcalc.daily_sales_feed", [_sale("T-1", "356938035643809", 400.0)])
    wired.seed("commcalc.closing_metric_line", [
        {"org_id": ORG, "store_code": "B-1115", "close_date": DAY, "metric_key": "edge_count",
         "employee_name": "Arora, Tanish", "units": 1, "amount": 400.0},
    ])
    body = api_client.get(PATH, params={"date_from": DAY}).json()
    assert body["configured"] is True
    row = next(r for r in body["rows"] if r["metric_key"] == "edge_count")
    assert row["verdict"] == "ok", body
    assert row["units_declared"] == 1 and row["units_captured"] == 1
    assert row["store_code"] == "B-1115"       # resolved from the sale's ADDRESS, as the report does


def test_a_rep_declaring_more_than_the_feed_captured_is_flagged_with_both_numbers(wired, api_client):
    _seed_edge_category(wired)
    _seed_edge_vendor(wired)
    wired.seed("commcalc.daily_sales_feed", [_sale("T-1", "356938035643809", 400.0)])
    wired.seed("commcalc.closing_metric_line", [
        {"org_id": ORG, "store_code": "B-1115", "close_date": DAY, "metric_key": "edge_count",
         "employee_name": "Arora, Tanish", "units": 3, "amount": 1200.0},
    ])
    body = api_client.get(PATH, params={"date_from": DAY}).json()
    row = next(r for r in body["rows"] if r["metric_key"] == "edge_count")
    assert row["verdict"] == "variance"
    assert row["units_declared"] == 3 and row["units_captured"] == 1
    assert row["units_variance"] == 2 and row["amount_variance"] == 800.0
    assert row["severity"] == "flagged"               # informational — never a close block
    assert body["totals"]["gaps"] == 1


def test_the_feed_spelling_and_the_closing_spelling_are_one_person(wired, api_client):
    _seed_edge_category(wired)
    _seed_edge_vendor(wired)
    wired.seed("commcalc.daily_sales_feed",
               [_sale("T-1", "356938035643809", 400.0, rep="Singh, Simarjyot")])
    wired.seed("commcalc.closing_metric_line", [
        {"org_id": ORG, "store_code": "B-1115", "close_date": DAY, "metric_key": "edge_count",
         "employee_name": "Simarjyot Singh", "units": 1, "amount": 400.0},
    ])
    body = api_client.get(PATH, params={"date_from": DAY}).json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["verdict"] == "ok"


def test_a_day_the_feed_never_loaded_is_pending_not_a_zero_variance(wired, api_client):
    _seed_edge_category(wired)
    _seed_edge_vendor(wired)
    wired.seed("commcalc.closing_metric_line", [
        {"org_id": ORG, "store_code": "B-1115", "close_date": DAY, "metric_key": "edge_count",
         "employee_name": "Arora, Tanish", "units": 2, "amount": 800.0},
    ])
    body = api_client.get(PATH, params={"date_from": DAY}).json()
    row = next(r for r in body["rows"] if r["metric_key"] == "edge_count")
    assert row["verdict"] == "pending"
    assert row["units_variance"] is None
    assert body["totals"]["gaps"] == 0 and body["totals"]["pending"] == 1
    assert body["feed_days"] == []


def test_a_vendor_with_no_detection_mapped_blames_us_not_the_rep(wired, api_client):
    _seed_edge_category(wired)
    wired.seed("commcalc.financing_vendor", [
        {"org_id": ORG, "vendor_key": "edge", "label": "Edge financing", "enabled": True,
         "detection_source": "rules", "amount_basis": "unit_line", "sort_order": 10},
    ])   # no detection rule at all
    wired.seed("commcalc.daily_sales_feed", [_sale("T-1", "356938035643809", 400.0)])
    wired.seed("commcalc.closing_metric_line", [
        {"org_id": ORG, "store_code": "B-1115", "close_date": DAY, "metric_key": "edge_count",
         "employee_name": "Arora, Tanish", "units": 2, "amount": 800.0},
    ])
    body = api_client.get(PATH, params={"date_from": DAY}).json()
    row = next(r for r in body["rows"] if r["metric_key"] == "edge_count")
    assert row["verdict"] == "pending"
    assert "financing:edge" in body["counterpart_status"]


def test_a_store_declared_category_reconciles_at_the_store_grain(wired, api_client):
    # `declared_by='store'` with money on it lands on closing_metric_line with employee_name NULL;
    # the row must be addressed to the STORE and never to whoever submitted the closing.
    _seed_edge_category(wired, declared_by="store")
    _seed_edge_vendor(wired)
    wired.seed("commcalc.daily_sales_feed", [_sale("T-1", "356938035643809", 400.0)])
    wired.seed("commcalc.daily_closing", [
        {"org_id": ORG, "store_code": "B-1115", "close_date": DAY,
         "employee_name": "Whoever Submitted", "counts": {}},
    ])
    wired.seed("commcalc.closing_metric_line", [
        {"org_id": ORG, "store_code": "B-1115", "close_date": DAY, "metric_key": "edge_count",
         "employee_name": None, "units": 1, "amount": 400.0},
    ])
    body = api_client.get(PATH, params={"date_from": DAY}).json()
    rows = [r for r in body["rows"] if r["metric_key"] == "edge_count"]
    assert len(rows) == 1
    assert rows[0]["rep"] == ""                       # the store's figure, not the submitter's
    assert rows[0]["verdict"] in ("ok", "variance")


def test_a_rep_filter_narrows_the_rows_and_the_totals_together(wired, api_client):
    _seed_edge_category(wired)
    _seed_edge_vendor(wired)
    wired.seed("commcalc.daily_sales_feed", [
        _sale("T-1", "356938035643809", 400.0),
        _sale("T-2", "356938035643810", 600.0, rep="Singh, Simarjyot"),
    ])
    wired.seed("commcalc.closing_metric_line", [
        {"org_id": ORG, "store_code": "B-1115", "close_date": DAY, "metric_key": "edge_count",
         "employee_name": "Arora, Tanish", "units": 1, "amount": 400.0},
        {"org_id": ORG, "store_code": "B-1115", "close_date": DAY, "metric_key": "edge_count",
         "employee_name": "Simarjyot Singh", "units": 4, "amount": 1600.0},
    ])
    body = api_client.get(PATH, params={"date_from": DAY, "rep": "Simarjyot Singh"}).json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["units_declared"] == 4
    assert body["totals"]["rows"] == 1


def test_an_inverted_range_is_refused_rather_than_answered_emptily(wired, api_client):
    _seed_edge_category(wired)
    _seed_edge_vendor(wired)
    r = api_client.get(PATH, params={"date_from": "2026-10-28", "date_to": "2026-10-01"})
    assert r.status_code == 400


def test_an_oversized_range_is_refused(wired, api_client):
    _seed_edge_category(wired)
    _seed_edge_vendor(wired)
    r = api_client.get(PATH, params={"date_from": "2026-01-01", "date_to": "2026-10-28"})
    assert r.status_code == 400


def test_the_catalog_the_report_names_is_the_one_home_s_catalog(wired, api_client):
    # The report must not publish a category list of its own: `metrics` is the catalog's entries,
    # each carrying the counterpart it named.
    _seed_edge_category(wired)
    _seed_edge_vendor(wired)
    body = api_client.get(PATH, params={"date_from": DAY}).json()
    assert body["metrics"] == [{"key": "edge_count", "label": "Edge",
                                "counterpart": "financing:edge", "value_kind": "both"}]
