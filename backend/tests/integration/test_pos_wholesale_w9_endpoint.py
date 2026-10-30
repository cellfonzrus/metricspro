"""Integration: the wholesale W-9 gate, the request email and the customer's return link (§POS-15).

Real routing, the real router bodies, the real one home, over the in-memory database double with
conftest blocking every outbound socket.

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT.

  * `pos.checkout` stores the figures it is handed (mig 725). So a till could send
    `tax_total: 0` on a customer with no certificate, or omit the `tax_exempt_basis` on a sale it
    charged no tax for, and be obeyed. These tests send exactly those bodies.
  * The admin override must come from the CALLER's own rights, never from a body field claiming
    them — the one thing only the wiring can get wrong.
  * The public return endpoints are the only anonymous surface this feature adds. The assertions
    that matter are the ones about what they DON'T answer: a dead token and a live one for another
    customer must be indistinguishable, and nothing about the customer may leak to a caller holding
    a token that is not theirs.
  * Three tenants, each with its own declaration, and the negative: one org's policy must not reach
    another's till.
"""
import datetime as dt

import pytest

HOUSE = "00000000-0000-0000-0000-000000000001"
ORG_B = "22222222-2222-2222-2222-222222222222"
ORG_C = "33333333-3333-3333-3333-333333333333"
STORE = "B-1115"
WHOLESALE_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
RETAIL_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
CHECKOUT = "/api/v1/pos/sales/checkout"
TOKEN = "a-live-token-value-nobody-can-guess"

ITEM = {"product_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "description": "Case",
        "qty": 1, "unit_price": 100.00, "tax_rate": 0.08875, "tax_value": 8.88,
        "extended_price": 108.88}


def _customers(org):
    return [
        {"id": WHOLESALE_ID, "org_id": org, "account_type": "Wholesale",
         "company_name": "A Shop", "email": "buyer@example.test", "first_name": "Dana"},
        {"id": RETAIL_ID, "org_id": org, "account_type": "Personal",
         "first_name": "A", "last_name": "Customer", "email": "a@example.test"},
    ]


@pytest.fixture
def wired(monkeypatch, fake_supabase, app_module):
    from app.modules.pos import router as pos_router

    calls = []

    def _checkout(params):
        calls.append(params)
        return {"id": "s-1", "transaction_id": 9001, "org_id": params.get("p_org")}

    fake_supabase.seed_rpc("pos.checkout", _checkout)
    fake_supabase.seed("pos.customers", _customers(HOUSE) + _customers(ORG_B) + _customers(ORG_C))
    fake_supabase.seed("pos.customer_tax_doc", [])
    # ORG_B refreshes a W-9 every 12 months; ORG_C does not block a sale without one.
    fake_supabase.seed("pos.pos_settings", [
        {"org_id": ORG_B, "store_code": None, "key": "w9_valid_months", "value": 12},
        {"org_id": ORG_C, "store_code": None, "key": "w9_block_sale", "value": False},
    ])
    monkeypatch.setattr("app.modules.pos.router.get_supabase", lambda: fake_supabase)
    app_module.dependency_overrides[pos_router._require_pos_access] = lambda: None
    monkeypatch.setattr("app.modules.pos.router._caller_employee", lambda *a, **k: "E240")
    monkeypatch.setattr("app.modules.pos.router._caller_ctx",
                        lambda *a, **k: {"super_admin": False, "perms": {}, "role": "cashier"})
    yield fake_supabase, calls
    app_module.dependency_overrides.clear()


def _admin(monkeypatch):
    monkeypatch.setattr("app.modules.pos.router._caller_ctx",
                        lambda *a, **k: {"super_admin": True, "perms": {}, "role": "admin"})


def _received(org, received="2026-05-01", **over):
    row = {"id": "d-1", "org_id": org, "customer_id": WHOLESALE_ID, "kind": "w9",
           "status": "received", "received_at": received, "expires_at": None,
           "return_token": None, "token_expires_at": None}
    row.update(over)
    return row


def _body(customer=WHOLESALE_ID, **over):
    sale = {"store_code": STORE, "customer_id": customer, "total": 108.88, "subtotal": 100.00,
            "tax_total": 8.88, "date": "2026-10-30"}
    sale.update(over.pop("sale", {}))
    return {"sale": sale, "items": [dict(ITEM, **over.pop("item", {}))],
            "payments": [{"payment_method": "cash", "amount": sale["total"]}]}


# ── what the screen is told ─────────────────────────────────────────────────────────────────────
def test_the_register_is_told_the_customers_w9_state_and_where_the_blank_form_is(api_client, wired):
    r = api_client.get(f"/api/v1/pos/customers/{WHOLESALE_ID}/w9", params={"org_id": HOUSE})
    assert r.status_code == 200
    j = r.json()
    assert j["wholesale"] is True
    assert j["w9_status"] == "missing"
    assert j["blocked"] is True
    assert j["form_url"].startswith("https://")
    assert j["override_available"] is False          # a cashier


def test_an_admin_is_told_an_override_is_available_to_them(api_client, wired, monkeypatch):
    _admin(monkeypatch)
    j = api_client.get(f"/api/v1/pos/customers/{WHOLESALE_ID}/w9", params={"org_id": HOUSE}).json()
    assert j["override_available"] is True and j["blocked"] is True


def test_a_retail_customer_is_not_dragged_into_any_of_this(api_client, wired):
    j = api_client.get(f"/api/v1/pos/customers/{RETAIL_ID}/w9", params={"org_id": HOUSE}).json()
    assert j["wholesale"] is False and j["blocked"] is False and j["tax_exempt"] is False


# ── the gate at the till ────────────────────────────────────────────────────────────────────────
def test_a_wholesale_sale_without_a_w9_is_refused_and_nothing_is_written(api_client, wired):
    db, calls = wired
    r = api_client.post(CHECKOUT, params={"org_id": HOUSE}, json=_body())
    assert r.status_code == 400 and "W-9" in r.json()["detail"]
    assert calls == []


def test_a_cashier_cannot_override_by_claiming_it_in_the_body(api_client, wired):
    db, calls = wired
    r = api_client.post(CHECKOUT, params={"org_id": HOUSE},
                        json=_body(sale={"w9_override": True, "w9_override_by": "E240"}))
    assert r.status_code == 400
    assert calls == []


def test_an_admin_override_lets_the_sale_through_as_a_TAXABLE_sale_and_names_the_admin(
        api_client, wired, monkeypatch):
    db, calls = wired
    _admin(monkeypatch)
    r = api_client.post(CHECKOUT, params={"org_id": HOUSE}, json=_body(sale={"w9_override": True}))
    assert r.status_code == 200, r.text
    sale = calls[-1]["p_sale"]
    assert sale["w9_override_by"] == "E240"          # the CALLER, never a body field
    assert sale["tax_exempt_basis"] in (None, "")
    assert float(sale["tax_total"]) == pytest.approx(8.88)


def test_a_current_w9_sells_tax_free_and_the_sale_records_why(api_client, wired):
    db, calls = wired
    db.seed("pos.customer_tax_doc", [_received(HOUSE)])
    body = _body(sale={"tax_total": 0}, item={"tax_rate": 0, "tax_value": 0, "extended_price": 100.0})
    body["sale"]["total"] = 100.0
    body["payments"][0]["amount"] = 100.0
    r = api_client.post(CHECKOUT, params={"org_id": HOUSE}, json=body)
    assert r.status_code == 200, r.text
    assert calls[-1]["p_sale"]["tax_exempt_basis"] == "w9_on_file"


def test_a_till_that_charges_tax_on_an_exempt_sale_is_refused_rather_than_obeyed(api_client, wired):
    """The exemption and the rate are one answer (§POS-15). A sale that claims the exemption and
    charges tax anyway is a disagreement the server must not store either side of."""
    db, calls = wired
    db.seed("pos.customer_tax_doc", [_received(HOUSE)])
    r = api_client.post(CHECKOUT, params={"org_id": HOUSE}, json=_body())   # tax_total 8.88
    assert r.status_code == 400 and "tax" in r.json()["detail"].lower()
    assert calls == []


def test_a_till_whose_tax_total_does_not_match_its_own_lines_is_refused(api_client, wired):
    """The other direction, and the one that costs the house money: a tax figure that does not add
    up to the lines it came from. The server will not store a sale that contradicts itself. (It
    does NOT demand tax on every sale — a non-taxable product is a real thing, declared on the
    product, so a zero that the LINES agree with is accepted.)"""
    db, calls = wired
    body = _body(customer=RETAIL_ID, sale={"tax_total": 0})      # lines still carry 8.88
    r = api_client.post(CHECKOUT, params={"org_id": HOUSE}, json=body)
    assert r.status_code == 400 and "tax" in r.json()["detail"].lower()
    assert calls == []


def test_a_genuinely_non_taxable_sale_is_accepted(api_client, wired):
    db, calls = wired
    body = _body(customer=RETAIL_ID, sale={"tax_total": 0, "total": 100.0},
                 item={"tax_rate": 0, "tax_value": 0, "extended_price": 100.0})
    body["payments"][0]["amount"] = 100.0
    r = api_client.post(CHECKOUT, params={"org_id": HOUSE}, json=body)
    assert r.status_code == 200, r.text
    assert calls[-1]["p_sale"]["tax_exempt_basis"] in (None, "")


def test_a_retail_sale_with_tax_goes_through_exactly_as_before(api_client, wired):
    db, calls = wired
    r = api_client.post(CHECKOUT, params={"org_id": HOUSE}, json=_body(customer=RETAIL_ID))
    assert r.status_code == 200, r.text
    assert calls[-1]["p_sale"]["tax_exempt_basis"] in (None, "")
    assert calls[-1]["p_sale"]["w9_override_by"] in (None, "")


def test_a_sale_with_no_customer_at_all_is_untouched_by_this(api_client, wired):
    db, calls = wired
    body = _body(customer=None)
    body["sale"]["customer_id"] = None
    r = api_client.post(CHECKOUT, params={"org_id": HOUSE}, json=body)
    assert r.status_code == 200, r.text


# ── each tenant's own policy ────────────────────────────────────────────────────────────────────
def test_each_tenant_judges_its_own_w9_by_its_own_refresh_period(api_client, wired):
    db, _calls = wired
    db.seed("pos.customer_tax_doc", [_received(HOUSE, received="2024-06-01"),
                                     _received(ORG_B, received="2024-06-01")])
    house = api_client.get(f"/api/v1/pos/customers/{WHOLESALE_ID}/w9",
                           params={"org_id": HOUSE}).json()
    orgb = api_client.get(f"/api/v1/pos/customers/{WHOLESALE_ID}/w9",
                          params={"org_id": ORG_B}).json()
    assert house["w9_status"] == "current" and house["blocked"] is False
    assert orgb["w9_status"] == "expired" and orgb["blocked"] is True


def test_a_tenant_that_does_not_block_still_charges_tax(api_client, wired):
    db, calls = wired
    j = api_client.get(f"/api/v1/pos/customers/{WHOLESALE_ID}/w9", params={"org_id": ORG_C}).json()
    assert j["blocked"] is False and j["tax_exempt"] is False
    r = api_client.post(CHECKOUT, params={"org_id": ORG_C}, json=_body())
    assert r.status_code == 200, r.text
    assert calls[-1]["p_sale"]["tax_exempt_basis"] in (None, "")


def test_one_tenants_certificate_does_not_make_anothers_sale_tax_free(api_client, wired):
    """The negative control that pins the mechanism: the SAME customer id, a certificate filed
    under the house org, and ORG_B's till must still be blocked."""
    db, calls = wired
    db.seed("pos.customer_tax_doc", [_received(HOUSE)])
    j = api_client.get(f"/api/v1/pos/customers/{WHOLESALE_ID}/w9", params={"org_id": ORG_B}).json()
    assert j["w9_status"] == "missing" and j["blocked"] is True
    assert api_client.post(CHECKOUT, params={"org_id": ORG_B}, json=_body()).status_code == 400


# ── confirming receipt, and asking for one ──────────────────────────────────────────────────────
def test_confirming_receipt_records_who_confirmed_it_and_unblocks_the_sale(api_client, wired):
    db, _calls = wired
    r = api_client.post(f"/api/v1/pos/customers/{WHOLESALE_ID}/w9/confirm",
                        params={"org_id": HOUSE}, json={})
    assert r.status_code == 200, r.text
    row = [d for d in db.rows("pos.customer_tax_doc") if d["org_id"] == HOUSE][0]
    assert row["status"] == "received" and row["confirmed_by"] == "E240"
    assert row["received_at"]
    assert r.json()["decision"]["blocked"] is False
    assert r.json()["decision"]["tax_exempt"] is True


def test_asking_for_a_w9_mints_a_single_use_link_and_emails_the_form(api_client, wired, monkeypatch):
    db, _calls = wired
    sent = {}

    async def _send(to, subject, html, attachments=None, from_name=None, **kw):
        sent.update({"to": to, "subject": subject, "html": html})
        return "mid-1"

    monkeypatch.setattr("app.modules.notify.channels.email_resend.send_email", _send)
    monkeypatch.setattr("app.modules.notify.channels.email_resend.is_configured", lambda: True)
    r = api_client.post(f"/api/v1/pos/customers/{WHOLESALE_ID}/w9/request",
                        params={"org_id": HOUSE}, json={})
    assert r.status_code == 200, r.text
    assert sent["to"] == "buyer@example.test"
    row = [d for d in db.rows("pos.customer_tax_doc") if d["org_id"] == HOUSE][0]
    assert row["status"] == "requested" and len(row["return_token"]) > 20
    assert row["return_token"] in sent["html"]
    assert "irs.gov" in sent["html"] or "https://" in sent["html"]
    # the token must not come back to the screen that asked — the link does, the raw token's
    # value is the credential and belongs only in the customer's mailbox
    assert row["return_token"] not in r.text.replace(r.json().get("link", ""), "")


def test_a_request_with_no_address_asks_for_one_rather_than_minting_a_dead_link(api_client, wired):
    db, _calls = wired
    db.seed("pos.customers", [{"id": WHOLESALE_ID, "org_id": HOUSE, "account_type": "Wholesale",
                               "company_name": "A Shop", "email": ""}])
    r = api_client.post(f"/api/v1/pos/customers/{WHOLESALE_ID}/w9/request",
                        params={"org_id": HOUSE}, json={})
    assert r.status_code == 400 and "email" in r.json()["detail"].lower()
    assert db.rows("pos.customer_tax_doc") == []


# ── the public return link (the only anonymous surface) ─────────────────────────────────────────
def _live_doc(org=HOUSE, token=TOKEN, expires="2026-12-01T00:00:00"):
    return {"id": "d-1", "org_id": org, "customer_id": WHOLESALE_ID, "kind": "w9",
            "status": "requested", "received_at": None, "expires_at": None,
            "return_token": token, "token_expires_at": expires}


def test_the_customer_sees_only_who_is_asking_and_what_to_do(api_client, wired):
    db, _calls = wired
    db.seed("pos.customer_tax_doc", [_live_doc()])
    r = api_client.get(f"/api/v1/pos/public/w9/{TOKEN}")
    assert r.status_code == 200
    j = r.json()
    assert j["form_url"].startswith("https://")
    assert "pdf" in [f.lower() for f in j["accepted_formats"]]
    # nothing about the customer's record travels to an anonymous caller
    for leak in ("org_id", "customer_id", "email", "id"):
        assert leak not in j


def test_a_dead_token_and_an_unknown_one_are_indistinguishable(api_client, wired):
    db, _calls = wired
    db.seed("pos.customer_tax_doc", [_live_doc(expires="2026-01-01T00:00:00")])
    expired = api_client.get(f"/api/v1/pos/public/w9/{TOKEN}")
    unknown = api_client.get("/api/v1/pos/public/w9/not-a-token-at-all")
    assert expired.status_code == unknown.status_code == 404
    assert expired.json() == unknown.json()


def test_the_customer_can_send_the_form_back_and_it_lands_as_submitted_not_received(
        api_client, wired, monkeypatch):
    """A customer's own upload must NOT buy the exemption: the owner's wording is that tax-free
    follows *the user confirming they received the filled out W-9*."""
    db, _calls = wired
    db.seed("pos.customer_tax_doc", [_live_doc()])
    monkeypatch.setattr("app.modules.pos.router._store_tax_doc_file",
                        lambda *a, **k: {"path": "p/1.pdf", "format": "pdf"})
    r = api_client.post(f"/api/v1/pos/public/w9/{TOKEN}/upload",
                        files={"file": ("w9.pdf", b"%PDF-1.7\nsigned", "application/pdf")})
    assert r.status_code == 200, r.text
    row = db.rows("pos.customer_tax_doc")[0]
    assert row["status"] == "submitted"
    assert row["submitted_at"]
    assert row["return_token"] is None              # one use, then the door closes
    assert row["file_path"] == "p/1.pdf"
    j = api_client.get(f"/api/v1/pos/customers/{WHOLESALE_ID}/w9",
                       params={"org_id": HOUSE}).json()
    assert j["w9_status"] == "missing" and j["tax_exempt"] is False


def test_a_file_that_is_not_what_it_claims_is_refused(api_client, wired):
    db, _calls = wired
    db.seed("pos.customer_tax_doc", [_live_doc()])
    r = api_client.post(f"/api/v1/pos/public/w9/{TOKEN}/upload",
                        files={"file": ("w9.pdf", b"#!/bin/sh\nrm -rf /", "application/pdf")})
    assert r.status_code == 400
    assert db.rows("pos.customer_tax_doc")[0]["status"] == "requested"


def test_only_the_customers_return_link_is_anonymous():
    from app.core import tenant_middleware as tm
    assert tm._is_public("/api/v1/pos/public/w9/abc") is True
    # and nothing else under /pos is
    for guarded in ("/api/v1/pos/sales/checkout", "/api/v1/pos/customers", "/api/v1/pos/public",
                    f"/api/v1/pos/customers/{WHOLESALE_ID}/w9"):
        assert tm._is_public(guarded) is False, guarded
