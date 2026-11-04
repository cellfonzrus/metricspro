"""Integration: the two receivable reports through the real endpoints (§POS-19).

Real routing, the real router bodies, the real one homes — over the in-memory database double, with
conftest blocking every outbound socket. There is no staging Supabase, so "integration" here means
the app, not a real database (docs/TESTING.md).

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT. They hand `receivable.assess` its sale and payments
directly, so they cannot see the four things only the wiring can get wrong:

  · WHICH sales the report lists — a sale with no deferred leg must not reach the rows at all;
  · which ORG's customers and settings answer, so one tenant's wholesale list cannot appear on
    another's report (the multi-tenant rule, asserted rather than assumed);
  · that the store a caller asks for is refused BEFORE it reaches a read;
  · that both reports say `available: false` BY NAME when their migration is absent, rather than
    answering an empty table that reads as "nobody owes us anything".
"""
import datetime as dt

import pytest

ORG_HOUSE = "00000000-0000-0000-0000-000000000001"     # Cellfonz R Us
ORG_OTHER = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"     # Luxelink Wireless LLC
STORE = "B-1115"
BAL_PATH = "/api/v1/pos/reports/open-balances"
W9_PATH = "/api/v1/pos/reports/w9-status"

TODAY = "2026-10-31"
TERMS_SALE = "11111111-1111-1111-1111-111111111111"
CASH_SALE = "22222222-2222-2222-2222-222222222222"
BARE_SALE = "33333333-3333-3333-3333-333333333333"
OTHER_SALE = "44444444-4444-4444-4444-444444444444"

CUST_HOUSE = "aaaaaaaa-0000-0000-0000-000000000001"
CUST_OTHER = "bbbbbbbb-0000-0000-0000-000000000001"


def _sale(sid, org, total, balance, created="2026-09-15T14:00:00Z", **kw):
    return {"id": sid, "org_id": org, "store_code": STORE, "transaction_id": 4600,
            "employee_id": "E240", "status": "completed", "revision": 1,
            "total": total, "balance": balance, "customer_id": CUST_HOUSE,
            "voided_at": None, "created_at": created, **kw}


@pytest.fixture
def wired(monkeypatch, fake_supabase, app_module):
    from app.modules.pos import router as pos_router
    monkeypatch.setattr("app.modules.pos.router.get_supabase", lambda: fake_supabase)
    app_module.dependency_overrides[pos_router._require_pos_access] = lambda: None
    monkeypatch.setattr("app.modules.pos.router._caller_ctx",
                        lambda *a, **k: {"super_admin": True, "perms": {}, "role": "admin"})
    monkeypatch.setattr("app.modules.pos.router._caller_employee", lambda *a, **k: "E240")
    monkeypatch.setattr("app.modules.pos.router._caller_store_keyset", lambda *a, **k: None)
    monkeypatch.setattr("app.modules.pos.router._span_filter", lambda rows, keyset: rows)
    monkeypatch.setattr("app.modules.pos.router._employee_names", lambda *a, **k: {"E240": "Abid"})
    monkeypatch.setattr("app.modules.pos.router._customer_names",
                        lambda *a, **k: {CUST_HOUSE: {"name": "Dollar Mobile", "phone": "5160001111"}})
    fake_supabase.seed("pos.pos_settings", [])
    fake_supabase.seed("pos.sales", [
        _sale(TERMS_SALE, ORG_HOUSE, 500.00, 500.00),      # wholly on 30-day terms, now late
        _sale(CASH_SALE, ORG_HOUSE, 190.53, 0.00),         # paid at the till
        _sale(BARE_SALE, ORG_HOUSE, 212.30, 0.00),         # imported history: no payment row
        _sale(OTHER_SALE, ORG_OTHER, 900.00, 900.00),      # another tenant's terms sale
    ])
    fake_supabase.seed("pos.sale_payments", [
        {"org_id": ORG_HOUSE, "sale_id": TERMS_SALE, "payment_method": "terms", "amount": 500.00,
         "card_last_four": None, "terms_days": 30, "due_date": "2026-10-15"},
        {"org_id": ORG_HOUSE, "sale_id": CASH_SALE, "payment_method": "cash", "amount": 190.53,
         "card_last_four": None, "terms_days": None, "due_date": None},
        {"org_id": ORG_OTHER, "sale_id": OTHER_SALE, "payment_method": "terms", "amount": 900.00,
         "card_last_four": None, "terms_days": 60, "due_date": "2026-12-01"},
    ])
    fake_supabase.seed("pos.customers", [
        {"id": CUST_HOUSE, "org_id": ORG_HOUSE, "first_name": "Ravi", "last_name": "Shah",
         "company_name": "Dollar Mobile", "email": "ravi@example.com", "phone": "5160001111",
         "account_type": "Wholesale"},
        {"id": "aaaaaaaa-0000-0000-0000-000000000002", "org_id": ORG_HOUSE,
         "first_name": "Walk", "last_name": "In", "company_name": None,
         "email": None, "phone": None, "account_type": "Personal"},
        {"id": CUST_OTHER, "org_id": ORG_OTHER, "first_name": "Ann", "last_name": "Lee",
         "company_name": "Luxe Trading", "email": "ann@example.com", "phone": "7180002222",
         "account_type": "Wholesale"},
    ])
    fake_supabase.seed("pos.customer_tax_doc", [
        # The house tenant's certificate arrived and, on the house 36-month period, is current.
        {"id": "d-1", "org_id": ORG_HOUSE, "customer_id": CUST_HOUSE, "kind": "w9",
         "status": "received", "received_at": "2026-01-15", "confirmed_by": "E240",
         "expires_at": None, "requested_at": "2026-01-02T10:00:00Z",
         "requested_to_email": "ravi@example.com", "return_token": None},
        # The other tenant's lapsed long ago — it must never appear on the house report.
        {"id": "d-2", "org_id": ORG_OTHER, "customer_id": CUST_OTHER, "kind": "w9",
         "status": "received", "received_at": "2019-01-15", "confirmed_by": "E900",
         "expires_at": None, "requested_at": None, "requested_to_email": None,
         "return_token": None},
    ])
    yield fake_supabase
    app_module.dependency_overrides.clear()


def _bal(api_client, org=ORG_HOUSE, **params):
    return api_client.get(BAL_PATH, params={"org_id": org, "as_of": TODAY, **params})


def _w9(api_client, org=ORG_HOUSE, **params):
    return api_client.get(W9_PATH, params={"org_id": org, "as_of": TODAY, **params})


# ── the routes exist where they are documented ───────────────────────────────────────────────────
def test_both_routes_are_registered_at_their_documented_paths(app_module):
    from app.core.path_redact import route_templates
    have = {p.replace("/api/v1", "") for p in route_templates(app_module)}
    assert BAL_PATH.replace("/api/v1", "") in have
    assert W9_PATH.replace("/api/v1", "") in have


# ── which sales reach the report at all ──────────────────────────────────────────────────────────
def test_only_the_sale_with_a_deferred_leg_is_a_balance(wired, api_client):
    """THE OWNER'S ASK. The $500 sale on 30-day terms is 16 days past due; the cash sale and the
    imported sale with no payment row are not debts and are not listed."""
    body = _bal(api_client).json()
    assert body["available"] is True
    assert [r["sale_id"] for r in body["rows"]] == [TERMS_SALE]
    row = body["rows"][0]
    assert row["outstanding"] == 500.00
    assert row["terms_days"] == 30
    assert row["due_date"] == "2026-10-15"
    assert row["days_past_due"] == 16
    assert row["band"] == "8-30"
    assert row["state"] == "past_due"
    assert row["customer_name"] == "Dollar Mobile"
    assert "$500.00" in row["note"] and "16 days past due" in row["note"]


def test_the_header_totals_separate_overdue_from_merely_owed(wired, api_client):
    s = _bal(api_client).json()["summary"]
    assert s["open_total"] == 500.00
    assert s["past_due_total"] == 500.00
    assert s["not_due_total"] == 0.0
    assert s["past_due_count"] == 1
    assert s["oldest_days_past_due"] == 16
    assert s["bands"]["8-30"] == 500.00


def test_a_sale_inside_its_terms_is_owed_and_not_overdue(wired, api_client):
    """Measured as of a date BEFORE the due date — the report ages against `as_of`, which the
    caller resolves, so this is reproducible rather than a fact about the server's clock."""
    body = _bal(api_client, as_of="2026-10-01").json()
    row = body["rows"][0]
    assert row["state"] == "not_due"
    assert row["band"] is None
    assert body["summary"]["not_due_total"] == 500.00
    assert body["summary"]["past_due_total"] == 0.0


def test_the_views_narrow_without_changing_the_totals(wired, api_client):
    assert _bal(api_client, view="past_due").json()["rows"][0]["sale_id"] == TERMS_SALE
    assert _bal(api_client, view="not_due").json()["rows"] == []
    # the rollup is over every assessed sale, so narrowing the LIST must not move the header
    assert _bal(api_client, view="not_due").json()["summary"]["past_due_total"] == 500.00


def test_what_is_not_built_is_carried_in_the_payload(wired, api_client):
    """Taking a payment against an open balance does not exist, so the report says so rather than
    looking complete. It is not inferred from a later over-tender."""
    body = _bal(api_client).json()
    assert "not built yet" in body["collection_note"]
    assert body["as_of"] == TODAY


# ── one tenant's debts never appear on another's report ──────────────────────────────────────────
def test_another_tenants_terms_sale_is_not_on_this_tenants_report(wired, api_client):
    assert OTHER_SALE not in [r["sale_id"] for r in _bal(api_client).json()["rows"]]


def test_the_other_tenant_sees_its_own_and_only_its_own(wired, api_client):
    body = _bal(api_client, org=ORG_OTHER, as_of="2026-12-15").json()
    assert [r["sale_id"] for r in body["rows"]] == [OTHER_SALE]
    assert body["rows"][0]["outstanding"] == 900.00
    assert body["rows"][0]["terms_days"] == 60


def test_a_store_outside_the_callers_span_is_refused_before_anything_is_read(
        wired, api_client, monkeypatch):
    monkeypatch.setattr("app.modules.pos.router._caller_store_keyset", lambda *a, **k: {"B-9999"})
    monkeypatch.setattr("app.modules.pos.router._span_filter",
                        lambda rows, keyset: [r for r in rows
                                              if (r.get("store_code") or "") in (keyset or set())])
    res = _bal(api_client, store_code=STORE)
    assert res.status_code == 403
    assert "reporting span" in res.json()["detail"]


# ── the W-9 report ───────────────────────────────────────────────────────────────────────────────
def test_the_w9_report_lists_only_wholesale_customers_of_this_org(wired, api_client):
    body = _w9(api_client, view="all").json()
    assert body["available"] is True
    assert [r["customer_id"] for r in body["rows"]] == [CUST_HOUSE]
    assert body["summary"]["wholesale"] == 1


def test_the_lapse_date_is_the_one_the_register_is_judged_by(wired, api_client):
    row = _w9(api_client, view="all").json()["rows"][0]
    assert row["w9_status"] == "current"
    assert row["blocked"] is False
    assert row["tax_exempt"] is True
    assert row["expires_at"] == "2029-01-15"          # 36 house months from 2026-01-15
    assert row["expiry_basis"] == "declared"
    assert row["days_until_expiry"] == (dt.date(2029, 1, 15) - dt.date(2026, 10, 31)).days


def test_a_certificates_own_expiry_wins_and_drives_lapsing_soon(wired, api_client):
    wired.seed("pos.customer_tax_doc", [
        {"id": "d-1", "org_id": ORG_HOUSE, "customer_id": CUST_HOUSE, "kind": "w9",
         "status": "received", "received_at": "2026-01-15", "confirmed_by": "E240",
         "expires_at": "2026-11-20", "requested_at": None, "requested_to_email": None,
         "return_token": None}])
    row = _w9(api_client, view="expiring", within_days=30).json()["rows"][0]
    assert row["expiry_basis"] == "own"
    assert row["expires_at"] == "2026-11-20"
    assert row["days_until_expiry"] == 20
    assert row["expiring_soon"] is True
    # ...and outside a shorter window it is not "soon"
    assert _w9(api_client, view="expiring", within_days=10).json()["rows"] == []


def test_a_wholesale_customer_with_no_certificate_is_blocked_and_counted(wired, api_client):
    wired.seed("pos.customer_tax_doc", [])
    body = _w9(api_client).json()
    row = body["rows"][0]
    assert row["w9_status"] == "missing"
    assert row["blocked"] is True
    assert row["tax_exempt"] is False
    assert row["needs_w9_request"] is True
    assert body["summary"]["missing"] == 1 and body["summary"]["blocked"] == 1
    assert body["summary"]["never_requested"] == 1


def test_a_customers_own_upload_is_not_a_certificate_until_a_person_confirms_it(wired, api_client):
    """§POS-16's asymmetry, asserted from the report's side: `submitted` is not `received`, so a
    customer cannot grant themselves a tax exemption by uploading a file."""
    wired.seed("pos.customer_tax_doc", [
        {"id": "d-1", "org_id": ORG_HOUSE, "customer_id": CUST_HOUSE, "kind": "w9",
         "status": "submitted", "received_at": None, "confirmed_by": None, "expires_at": None,
         "requested_at": "2026-10-20T10:00:00Z", "requested_to_email": "ravi@example.com",
         "return_token": None}])
    row = _w9(api_client).json()["rows"][0]
    assert row["doc_status"] == "submitted"
    assert row["w9_status"] == "missing"
    assert row["tax_exempt"] is False
    assert row["expires_at"] is None
    assert "nothing to expire" in row["expiry_note"]


def test_another_tenants_lapsed_certificate_is_invisible_here(wired, api_client):
    body = _w9(api_client, view="all").json()
    assert CUST_OTHER not in [r["customer_id"] for r in body["rows"]]
    assert body["summary"]["expired"] == 0
    # ...and the other tenant sees its own, correctly expired
    other = _w9(api_client, org=ORG_OTHER, view="all").json()
    assert [r["customer_id"] for r in other["rows"]] == [CUST_OTHER]
    assert other["rows"][0]["w9_status"] == "expired"
    assert other["summary"]["expired"] == 1


def test_an_org_with_no_wholesale_customer_is_told_so_rather_than_shown_all_clear(
        wired, api_client):
    wired.seed("pos.customers", [
        {"id": "aaaaaaaa-0000-0000-0000-000000000002", "org_id": ORG_HOUSE,
         "first_name": "Walk", "last_name": "In", "company_name": None, "email": None,
         "phone": None, "account_type": "Personal"}])
    body = _w9(api_client, view="all").json()
    assert body["rows"] == []
    assert body["available"] is True              # available, and genuinely empty
    assert body["summary"]["wholesale"] == 0      # the figure the page turns into a sentence


def test_the_blank_form_link_is_offered_so_nobody_has_to_find_it(wired, api_client):
    assert _w9(api_client).json()["form_url"].startswith("https://")


# ── both reports degrade by name until their migration lands ─────────────────────────────────────
def test_the_balance_report_says_terms_are_not_switched_on_rather_than_showing_nothing(
        wired, api_client, monkeypatch):
    monkeypatch.setattr("app.modules.pos.router._terms_schema_ready", lambda client: False)
    body = _bal(api_client).json()
    assert body["available"] is False
    assert body["rows"] == []
    # §19.36: the migration number is NEUTRALISED for a tenant before the response leaves the app,
    # so what is pinned here is that neutralisation — the detail reaches the platform super admin
    # through `PlatformDetail` on the page, and a test asserting "1106" would be asserting a leak.
    assert "1106" not in body["setup_note"]
    assert "switched on" in body["setup_note"]
    assert body["summary"]["open_total"] == 0.0
    assert "not built yet" in body["collection_note"]


def test_the_w9_report_says_certificates_are_not_switched_on(wired, api_client, monkeypatch):
    monkeypatch.setattr("app.modules.pos.router._w9_docs_for", lambda *a, **k: ({}, False))
    body = _w9(api_client).json()
    assert body["available"] is False
    assert body["rows"] == []
    assert "1107" not in body["setup_note"] and "switched on" in body["setup_note"]
    assert body["summary"]["wholesale"] == 0
