"""Integration: GET /pos/sales/{id}/receipt in the tenant's own POS layout (index §POS-10).

Real routing, the real router body, the real renderer — over the in-memory database double, with
conftest blocking every outbound socket. There is no staging Supabase, so "integration" here means
the app, not a real database (docs/TESTING.md).

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT. The unit tests hand `sale_receipt.document` a store
row directly; they cannot see that the ROUTER never gave it one. That is where the owner's defect
actually lived: the router read `storeops.stores` for a `name` column that table does not have,
PostgREST answered 42703, the `except Exception: pass` swallowed it, and the emailed receipt went
out carrying the company name and nothing else. So this test seeds a store row with the columns
that table REALLY has — no `name` — and asserts the rendered receipt still carries the address, the
licence and the return policy, end to end.
"""
import pytest

ORG = "00000000-0000-0000-0000-000000000001"
SALE_ID = "11111111-1111-1111-1111-111111111111"
STORE = "Ave U"
PATH = f"/api/v1/pos/sales/{SALE_ID}/receipt"

RECEIPT_SNAPSHOT = {
    "transactionId": 4605,
    "date": "10/21/2026, 6:03:24 PM",
    "customerName": "Grand Market Intl Corporation",
    "items": [{"description": "Samsung A9 +", "serial": "358291470012345", "qty": 1,
               "unit_price": 175.00, "discount": 0, "line_total": 175.00}],
    "subtotal": 175.00, "discountTotal": 0, "taxTotal": 15.53, "total": 190.53,
    "method": "credit_card", "tendered": 190.53, "change": 0,
}

#: Exactly the columns `storeops.stores` has. A `name` here would hide the defect this file is about.
STORE_ROW = {"org_id": ORG, "store_code": STORE, "address": "2723 Example Ave Brooklyn",
             "market": "NY", "phone": "718-555-0144", "is_active": True}

SETTINGS = [
    {"org_id": ORG, "store_code": None, "key": "brand_company_name", "value": "Example Wireless"},
    {"org_id": ORG, "store_code": None, "key": "brand_address_line1", "value": "2723 Example Ave"},
    {"org_id": ORG, "store_code": None, "key": "brand_address_line2", "value": "Brooklyn NY 11229"},
    {"org_id": ORG, "store_code": None, "key": "brand_license_tax_ids", "value": "2126405-DCWP"},
    {"org_id": ORG, "store_code": None, "key": "disclaimer1_text",
     "value": "Return Policy-\nNo Cash refunds, Store Credit only."},
    {"org_id": ORG, "store_code": None, "key": "disclaimer1_target", "value": "pos"},
]


@pytest.fixture
def wired(monkeypatch, fake_supabase, app_module):
    """The POS router's client pointed at the double, and its sign-in gate lifted for the test.

    The gate is overridden rather than faked with a token: what is under test is the receipt the
    endpoint renders, and a double of Supabase Auth would be a second answer to "who is signed in".
    """
    from app.modules.pos import router as pos_router
    monkeypatch.setattr("app.modules.pos.router.get_supabase", lambda: fake_supabase)
    app_module.dependency_overrides[pos_router._require_pos_access] = lambda: None
    fake_supabase.seed("pos.sales", [{"id": SALE_ID, "org_id": ORG, "store_code": STORE,
                                      "employee_id": "E240", "status": "completed",
                                      "receipt": RECEIPT_SNAPSHOT}])
    fake_supabase.seed("storeops.stores", [STORE_ROW])
    fake_supabase.seed("storeops.tenants", [{"org_id": ORG, "name": "Example Wireless Inc"}])
    fake_supabase.seed("pos.pos_settings", SETTINGS)
    fake_supabase.seed("pos.receipt_templates", [{"id": "t1", "org_id": ORG, "name": "Default", "is_default": True,
                                                  "layout": "pos_format", "show_store_name": True,
                                                  "show_customer": True, "show_serials": True,
                                                  "footer_text": "Thank you for your business!"}])
    fake_supabase.seed("commcalc.pos_profile", [{"org_id": ORG, "pos_key": "rq", "is_active": True}])
    yield fake_supabase
    app_module.dependency_overrides.clear()


def _html(api_client):
    res = api_client.get(PATH, params={"org_id": ORG})
    assert res.status_code == 200, res.text
    body = res.json()["receipt"]
    return body["layout"], body["html"]


def test_the_route_is_registered_at_its_documented_path(app_module):
    from app.core.path_redact import route_templates
    assert PATH.replace(SALE_ID, "{sale_id}") in set(route_templates(app_module))


def test_the_receipt_prints_in_the_tenants_own_pos_layout(wired, api_client):
    layout, html = _html(api_client)
    assert layout == "pos_format"
    assert "Samsung A9 +" in html


def test_the_emailed_receipt_carries_the_shops_address_and_licence(wired, api_client):
    """THE REGRESSION (owner, 2026-10-28, on receipt 4605): it carried the company name and stopped,
    which is what made a real receipt look fake."""
    _, html = _html(api_client)
    for expected in ("Example Wireless", "2723 Example Ave", "Brooklyn NY 11229", "2126405-DCWP"):
        assert expected in html, expected


def test_the_return_policy_rides_this_layout_too(wired, api_client):
    _, html = _html(api_client)
    assert "No Cash refunds" in html
    assert "Thank you for your business!" in html


def test_the_till_slip_of_the_same_sale_says_the_same_things(wired, api_client):
    """One fact, two layouts. Asking for the other layout FOR THIS PRINT (§POS-03) must not change
    what the receipt says the shop is — that divergence is the whole defect."""
    res = api_client.get(PATH, params={"org_id": ORG, "layout": "thermal"})
    assert res.status_code == 200, res.text
    html = res.json()["receipt"]["html"]
    for expected in ("Example Wireless", "2723 Example Ave", "Brooklyn NY 11229", "2126405-DCWP",
                     "No Cash refunds"):
        assert expected in html, expected


def test_a_store_row_that_cannot_be_read_still_prints_the_branding(wired, api_client):
    """The read failing is what nobody noticed for as long as it did. It must cost the receipt
    nothing the admin filled in — the branding rows are the answer, that row is the fallback."""
    wired.seed("storeops.stores", [])
    _, html = _html(api_client)
    assert "2723 Example Ave" in html and "2126405-DCWP" in html


# ── §POS-11: the page it prints on, and what the screen is told about the Bill To ────────────────
def test_the_receipt_declares_its_own_page_so_the_browser_stamps_nothing(wired, api_client):
    """The owner's "reprint date and time on top with the word Sale in the middle" was Chrome's own
    print header, over a page that had never said what page it prints on."""
    from app.modules.pos import sale_receipt as SR
    _, html = _html(api_client)
    assert SR.PAGE_CSS in html


def test_the_screen_is_told_the_customer_record_holds_no_address(wired, api_client):
    res = api_client.get(PATH, params={"org_id": ORG})
    bill_to = res.json()["receipt"]["bill_to"]
    assert bill_to["on_file"] is False
    assert bill_to["lines"] == []
    assert "address" in bill_to["note"].lower()


def test_a_customer_with_an_address_bills_to_it_and_the_screen_says_nothing(wired, api_client):
    wired.seed("pos.customers", [{"id": "cust-1", "org_id": ORG, "first_name": "Grand",
                                  "last_name": "Market Intl Corporation",
                                  "address_1": "41 Example Road", "address_2": "Unit 7",
                                  "city": "Brooklyn", "state": "NY", "zip": "11229"}])
    wired.seed("pos.sales", [{"id": SALE_ID, "org_id": ORG, "store_code": STORE,
                              "employee_id": "E240", "status": "completed",
                              "customer_id": "cust-1", "receipt": RECEIPT_SNAPSHOT}])
    res = api_client.get(PATH, params={"org_id": ORG})
    body = res.json()["receipt"]
    assert body["bill_to"]["on_file"] is True and body["bill_to"]["note"] == ""
    assert "41 Example Road Unit 7" in body["html"]
    assert "Brooklyn NY 11229" in body["html"]
