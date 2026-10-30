"""Integration: the cash / credit mismatch an edit leaves behind, through the real endpoints (§POS-13).

Real routing, the real router body, the real one home — over the in-memory database double, with
conftest blocking every outbound socket. There is no staging Supabase, so "integration" here means
the app, not a real database (docs/TESTING.md).

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT. The unit tests hand `sale_settlement.outstanding` its
sale and its payments directly. They cannot see the four things only the WIRING can get wrong, and
each of them is the kind of thing that would make the log quietly useless:

  · the mismatch is measured against the sale's RESTATED total, not the one it had before the edit;
  · the record actually travels to `pos.amend_sale` inside `p_sale_patch` under `tender`;
  · the dry run returns the same figure the save writes, and writes nothing;
  · the band comes from the ORG's own `pos_settings`, so one tenant's declaration cannot score
    another tenant's sale — the multi-tenant rule, asserted rather than assumed.
"""
import pytest

ORG_HOUSE = "00000000-0000-0000-0000-000000000001"     # Cellfonz R Us
ORG_OTHER = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"     # Luxelink Wireless LLC
SALE_ID = "11111111-1111-1111-1111-111111111111"
STORE = "B-1115"
SAVE_PATH = f"/api/v1/pos/sales/{SALE_ID}/items"
PLAN_PATH = f"/api/v1/pos/sales/{SALE_ID}/amend-plan"
REPORT_PATH = "/api/v1/pos/reports/tender-mismatch"

# One handset: $175.00 at 0.08875 → $15.53 tax, $190.53 extended. The figures in the §POS-12 proof,
# reproduced from live transaction 4605, so both files are scoring the same arithmetic.
PHONE = {"id": "i-1", "org_id": ORG_HOUSE, "sale_id": SALE_ID, "product_id": "p-1",
         "description": "Samsung A9 +", "product_type": "handset",
         "serial_number": "355123456789012", "qty": 1, "unit_price": 175.00, "list_price": 199.00,
         "cost": 120.00, "discount": 0, "tax_rate": 0.08875, "tax_value": 15.53,
         "extended_price": 190.53, "created_at": "2026-10-29T14:00:00Z"}
CHARGER = {"product_id": "p-3", "description": "Charger", "product_type": "accessory",
           "qty": 1, "unit_price": 20.00, "discount": 0, "tax_rate": 0.08875}


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def wired(monkeypatch, fake_supabase, app_module):
    """The POS router pointed at the double, with a recorder in place of `pos.amend_sale`. The sale
    is rung TODAY so it is inside the declared window; the window itself is §POS-12's business and
    is proved there."""
    from app.modules.pos import router as pos_router
    monkeypatch.setattr("app.modules.pos.router.get_supabase", lambda: fake_supabase)
    app_module.dependency_overrides[pos_router._require_pos_access] = lambda: None
    monkeypatch.setattr("app.modules.pos.router._caller_ctx",
                        lambda *a, **k: {"super_admin": True, "perms": {}, "role": "admin"})
    monkeypatch.setattr("app.modules.pos.router._caller_employee", lambda *a, **k: "E240")
    monkeypatch.setattr("app.modules.pos.router._caller_store_keyset", lambda *a, **k: None)
    monkeypatch.setattr("app.modules.pos.router._span_filter", lambda rows, keyset: rows)
    monkeypatch.setattr("app.modules.pos.router._employee_names", lambda *a, **k: {"E240": "Abid"})
    monkeypatch.setattr("app.modules.pos.router._customer_names", lambda *a, **k: {})
    fake_supabase.seed("pos.sales", [{
        "id": SALE_ID, "org_id": ORG_HOUSE, "store_code": STORE, "transaction_id": 4605,
        "employee_id": "E240", "status": "completed", "revision": 1,
        "subtotal": 175.00, "discount_total": 0.00, "tax_total": 15.53, "total": 190.53,
        "balance": 0.00, "customer_id": None, "is_activation_sale": False, "created_at": _now()}])
    fake_supabase.seed("pos.sale_items", [PHONE])
    fake_supabase.seed("pos.sale_amendments", [])
    fake_supabase.seed("pos.pos_settings", [])
    # Paid in full, in cash, at the time it was rung — so any mismatch below is the EDIT's doing.
    fake_supabase.seed("pos.sale_payments", [
        {"org_id": ORG_HOUSE, "sale_id": SALE_ID, "payment_method": "cash", "amount": 190.53,
         "card_last_four": None, "created_at": "2026-10-29T14:00:00Z"}])
    calls = []
    fake_supabase.seed_rpc("pos.amend_sale", lambda params: (calls.append(params) or
                                                            {"id": SALE_ID, "revision": 2}))
    fake_supabase.calls = calls
    yield fake_supabase
    app_module.dependency_overrides.clear()


def _save(api_client, items, org=ORG_HOUSE, **extra):
    return api_client.put(SAVE_PATH, params={"org_id": org}, json={"items": items, **extra})


# ── the route exists where it is documented ──────────────────────────────────────────────────────
def test_the_report_route_is_registered_at_its_documented_path(app_module):
    from app.core.path_redact import route_templates
    assert REPORT_PATH.replace("/api/v1", "") in {
        p.replace("/api/v1", "") for p in route_templates(app_module)}


# ── the mismatch is measured against the RESTATED total ──────────────────────────────────────────
def test_adding_an_item_leaves_the_difference_to_collect_in_cash(wired, api_client):
    """THE OWNER'S ASK. $190.53 was taken in cash; the charger takes the sale to $212.30; the log
    says $21.77 is owed, and says it is the cash it was tendered on."""
    res = _save(api_client, [PHONE, CHARGER])
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["totals"]["total"] == 212.30
    t = body["tender"]
    assert t["measured"] is True
    assert t["tendered"]["total"] == 190.53
    assert t["expected"]["amount"] == 212.30          # the RESTATED total, not the old 190.53
    assert t["variance"] == -21.77
    assert t["verdict"] == "short" and t["direction"] == "collect"
    assert t["attribution"]["method"] == "cash"
    assert "21.77" in body["tender_message"] and "collect" in body["tender_message"].lower()


def test_the_record_reaches_the_database_inside_the_patch(wired, api_client):
    """It travels under `tender` in `p_sale_patch`, which is why mig 1106 needs no new parameter and
    creates no overload on `pos.amend_sale`."""
    _save(api_client, [PHONE, CHARGER])
    tender = wired.calls[-1]["p_sale_patch"]["tender"]
    assert tender["tender_variance"] == -21.77
    assert tender["tender_expected"] == 212.30
    assert tender["tender_total"] == 190.53
    assert tender["tender_verdict"] == "short"
    assert tender["tender_direction"] == "collect"
    assert tender["tender_measured"] is True
    assert tender["tender_methods"] == ["cash"]
    assert tender["tender_reason"] is None


def test_removing_value_leaves_a_refund_on_the_method_it_was_taken_on(wired, api_client):
    cheaper = dict(PHONE, unit_price=100.00)
    body = _save(api_client, [cheaper]).json()
    t = body["tender"]
    assert t["direction"] == "refund" and t["variance"] == round(190.53 - 108.88, 2)
    assert t["attribution"]["method"] == "cash"
    assert "refund" in body["tender_message"].lower()


def test_an_edit_that_does_not_move_the_money_logs_no_mismatch(wired, api_client):
    """Changing a description moves no figure, so there is nothing to collect or refund and the log
    row records a settled comparison rather than a finding."""
    body = _save(api_client, [dict(PHONE, description="Samsung A9 Plus")]).json()
    assert body["tender"]["measured"] is True
    assert body["tender"]["variance"] == 0.0
    assert body["tender"]["mismatch"] is False
    assert wired.calls[-1]["p_sale_patch"]["tender"]["tender_verdict"] == "ok"


def test_a_sale_with_no_recorded_payment_is_not_called_short(wired, api_client):
    """The 4,603-row finding, through the wiring: an unrecorded tender files its REASON and no
    figures, so imported receipt history can be edited without being accused of a shortage."""
    wired.seed("pos.sale_payments", [])
    body = _save(api_client, [PHONE, CHARGER]).json()
    assert body["tender"]["measured"] is False
    assert body["tender"]["mismatch"] is False
    assert body["tender"]["variance"] is None
    tender = wired.calls[-1]["p_sale_patch"]["tender"]
    assert tender["tender_reason"] == "tender_unrecorded"
    assert tender["tender_variance"] is None and tender["tender_verdict"] is None
    assert "$" not in body["tender_message"]


# ── the dry run: the preview and the save are one computation ────────────────────────────────────
def test_the_dry_run_returns_the_same_figure_the_save_writes_and_writes_nothing(wired, api_client):
    preview = _save(api_client, [PHONE, CHARGER], dry_run=True)
    assert preview.status_code == 200, preview.text
    pre = preview.json()
    assert pre["dry_run"] is True
    assert wired.calls == []                       # nothing was written
    saved = _save(api_client, [PHONE, CHARGER]).json()
    assert pre["tender"]["variance"] == saved["tender"]["variance"]
    assert pre["tender_message"] == saved["tender_message"]
    assert pre["totals"] == saved["totals"]


def test_a_dry_run_of_a_refused_edit_still_refuses(wired, api_client):
    """The preview must not be a way to see figures for an edit that may not happen — §POS-12's
    `plan` carries no items alongside a refusal, and the dry run runs after that same gate."""
    wired.seed("pos.sales", [{**wired.rows("pos.sales")[0],
                              "created_at": "2026-01-01T12:00:00+00:00"}])
    res = _save(api_client, [PHONE, CHARGER], dry_run=True)
    assert res.status_code == 400
    assert wired.calls == []


# ── it fails closed when the mismatch could not be logged ────────────────────────────────────────
def test_an_edit_that_would_create_unexplained_money_is_refused_when_it_cannot_be_logged(
        monkeypatch, wired, api_client):
    """§POS-12 refuses to overwrite a sale whose superseded version cannot be kept. The same
    argument, applied to the second record that makes an edit answerable."""
    monkeypatch.setattr("app.modules.pos.router._tender_log_available", lambda *a, **k: False)
    res = _save(api_client, [PHONE, CHARGER])
    assert res.status_code == 503
    assert wired.calls == []


def test_a_settled_edit_is_not_held_up_by_a_migration_it_does_not_use(monkeypatch, wired, api_client):
    """An edit that moves no money needs no log row, so the refusal above must not become a blanket
    block on editing — that would be §POS-12 regressed in the name of §POS-13."""
    monkeypatch.setattr("app.modules.pos.router._tender_log_available", lambda *a, **k: False)
    res = _save(api_client, [dict(PHONE, description="Samsung A9 Plus")])
    assert res.status_code == 200, res.text
    assert len(wired.calls) == 1


# ── the plan shows what stands tendered, and writes nothing ──────────────────────────────────────
def test_the_plan_reports_what_stands_tendered_without_writing(wired, api_client):
    body = api_client.get(PLAN_PATH, params={"org_id": ORG_HOUSE}).json()
    assert body["tender"]["tendered"]["total"] == 190.53
    assert body["tender"]["tendered"]["methods"] == ["cash"]
    assert body["tender"]["mismatch"] is False     # as it stands, before any edit, it adds up
    assert wired.calls == []
    assert wired.rows("pos.sale_amendments") == []


# ── the band is the ORG's own (the multi-tenant rule) ────────────────────────────────────────────
def test_the_declared_band_is_read_from_the_orgs_own_settings(wired, api_client):
    """A tenant that declares a $25 band sees a $21.77 difference as settled. If this read were
    hardcoded the assertion below could not move."""
    wired.seed("pos.pos_settings", [
        {"org_id": ORG_HOUSE, "store_code": None, "key": "tender_mismatch_band", "value": 25.00}])
    body = _save(api_client, [PHONE, CHARGER]).json()
    assert body["tender"]["band"] == 25.00
    assert body["tender"]["variance"] == -21.77   # the money is unchanged...
    assert body["tender"]["mismatch"] is False     # ...only whether it counts as a finding
    assert body["tender"]["verdict"] == "ok"


def test_one_tenants_declared_band_never_scores_another_tenants_sale(wired, api_client):
    """THE MULTI-TENANT ASSERTION. Luxelink declares a $100 band; Cellfonz declares none. The same
    $21.77 difference on a Cellfonz sale must stay a mismatch — a declaration is read for the org
    that owns the row, never for whoever declared one most recently."""
    wired.seed("pos.pos_settings", [
        {"org_id": ORG_OTHER, "store_code": None, "key": "tender_mismatch_band", "value": 100.00}])
    body = _save(api_client, [PHONE, CHARGER]).json()
    assert body["tender"]["band"] == 0.0           # the HOUSE band, not Luxelink's
    assert body["tender"]["mismatch"] is True


def test_a_store_level_band_beats_the_org_default_for_that_store_only(wired, api_client):
    wired.seed("pos.pos_settings", [
        {"org_id": ORG_HOUSE, "store_code": None, "key": "tender_mismatch_band", "value": 1.00},
        {"org_id": ORG_HOUSE, "store_code": STORE, "key": "tender_mismatch_band", "value": 50.00}])
    assert _save(api_client, [PHONE, CHARGER]).json()["tender"]["band"] == 50.00


# ── the log report ───────────────────────────────────────────────────────────────────────────────
def _report(api_client, org=ORG_HOUSE, **params):
    return api_client.get(REPORT_PATH, params={"org_id": org, **params})


def test_the_report_lists_only_measured_mismatches_by_default(wired, api_client):
    """A sale short by $21.77, a sale that adds up, and a sale nobody recorded a payment for. Only
    the first is a finding; the third is counted and named, never listed as one."""
    wired.seed("pos.sales", [
        {"id": "s-short", "org_id": ORG_HOUSE, "store_code": STORE, "transaction_id": 1,
         "status": "completed", "total": 212.30, "balance": 0, "revision": 2,
         "employee_id": "E240", "created_at": _now()},
        {"id": "s-ok", "org_id": ORG_HOUSE, "store_code": STORE, "transaction_id": 2,
         "status": "completed", "total": 190.53, "balance": 0, "revision": 1,
         "employee_id": "E240", "created_at": _now()},
        {"id": "s-none", "org_id": ORG_HOUSE, "store_code": STORE, "transaction_id": 3,
         "status": "completed", "total": 32.66, "balance": 0, "revision": None,
         "employee_id": "E240", "created_at": _now()}])
    wired.seed("pos.sale_payments", [
        {"org_id": ORG_HOUSE, "sale_id": "s-short", "payment_method": "cash", "amount": 190.53},
        {"org_id": ORG_HOUSE, "sale_id": "s-ok", "payment_method": "cash", "amount": 190.53}])
    body = _report(api_client).json()
    assert [r["transaction_id"] for r in body["rows"]] == [1]
    assert body["rows"][0]["variance"] == -21.77
    assert body["rows"][0]["direction"] == "collect"
    assert body["rows"][0]["was_edited"] is True
    assert body["summary"] == {**body["summary"], "sales": 3, "mismatches": 1, "settled": 1,
                               "not_measurable": 1, "to_collect": 21.77, "to_refund": 0.0}
    assert body["blockers"] and body["blockers"][0]["count"] == 1


def test_the_report_covers_a_voided_sale_whose_tender_is_still_recorded(wired, api_client):
    """THE SIBLING. `pos.void_sale` puts the stock back and leaves the payment rows standing, and
    files nothing. Deriving the comparison live is what makes the void path visible on this screen
    without reopening a function that handles money correctly today."""
    wired.seed("pos.sales", [
        {"id": "s-void", "org_id": ORG_HOUSE, "store_code": STORE, "transaction_id": 9,
         "status": "voided", "voided_at": _now(), "total": 190.53, "balance": 0,
         "employee_id": "E240", "created_at": _now()}])
    wired.seed("pos.sale_payments", [
        {"org_id": ORG_HOUSE, "sale_id": "s-void", "payment_method": "credit_card",
         "amount": 190.53}])
    body = _report(api_client).json()
    row = next(r for r in body["rows"] if r["transaction_id"] == 9)
    assert row["was_voided"] is True
    assert row["expected"] == 0.0                  # a voided sale is owed nothing...
    assert row["variance"] == 190.53               # ...so the whole tender goes back
    assert row["direction"] == "refund"
    assert body["summary"]["voided_with_tender"] == 1


def test_the_report_can_be_asked_for_what_it_could_not_measure(wired, api_client):
    wired.seed("pos.sales", [
        {"id": "s-none", "org_id": ORG_HOUSE, "store_code": STORE, "transaction_id": 3,
         "status": "completed", "total": 32.66, "balance": 0, "employee_id": "E240",
         "created_at": _now()}])
    wired.seed("pos.sale_payments", [])
    body = _report(api_client, kind="unmeasured").json()
    assert [r["transaction_id"] for r in body["rows"]] == [3]
    assert body["rows"][0]["measured"] is False
    assert body["rows"][0]["variance"] is None
    assert body["rows"][0]["reason"]


def test_the_report_filters_by_store_and_writes_nothing(wired, api_client):
    wired.seed("pos.sales", [
        {"id": "s-a", "org_id": ORG_HOUSE, "store_code": STORE, "transaction_id": 1,
         "status": "completed", "total": 212.30, "balance": 0, "employee_id": "E240",
         "created_at": _now()},
        {"id": "s-b", "org_id": ORG_HOUSE, "store_code": "B-1598", "transaction_id": 2,
         "status": "completed", "total": 212.30, "balance": 0, "employee_id": "E240",
         "created_at": _now()}])
    wired.seed("pos.sale_payments", [
        {"org_id": ORG_HOUSE, "sale_id": "s-a", "payment_method": "cash", "amount": 190.53},
        {"org_id": ORG_HOUSE, "sale_id": "s-b", "payment_method": "cash", "amount": 190.53}])
    body = _report(api_client, store_code="B-1598").json()
    assert [r["transaction_id"] for r in body["rows"]] == [2]
    assert wired.calls == []


def test_the_report_of_a_tenant_with_no_register_sales_is_empty_not_an_error(wired, api_client):
    """Three of the four tenants have no `pos.sales` rows at all (measured 2026-10-30). An empty
    answer with a zeroed summary is the correct one; an exception or a borrowed figure is not."""
    body = _report(api_client, org=ORG_OTHER).json()
    assert body["rows"] == []
    assert body["summary"]["sales"] == 0 and body["summary"]["net_variance"] == 0.0
    assert body["blockers"] == []
