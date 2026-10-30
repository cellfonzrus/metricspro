"""Integration: ringing a sale on Terms through the real endpoints (index §POS-15).

Real routing, the real router body, the real one home, over the in-memory database double with
every outbound socket blocked by conftest. There is no staging Supabase, so "integration" here
means the app, not a real database (docs/TESTING.md).

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT. `tender_vocabulary` is pure and its tests hand it
inputs directly. They cannot see whether the ROUTER asks it at all — and that is the whole risk
here, because `pos.checkout` stores the figures it is handed (mig 725). A till that sent
`payment_method: "terms"` with no customer, or with a day count the org never declared, or with a
`balance` of 0 on an unpaid invoice, would be obeyed. So these tests send exactly those bodies and
assert on what reached `pos.checkout`.

THE TENANT TESTS ARE THE POINT, not a postscript. The same body is sent for a second org that has
declared its own day counts and a third that has switched the tender off, and the assertion is that
each gets ITS answer — never the house one, and never another tenant's (CLAUDE.md: every feature is
for every tenant).
"""
import pytest

HOUSE = "00000000-0000-0000-0000-000000000001"
ORG_B = "22222222-2222-2222-2222-222222222222"
ORG_C = "33333333-3333-3333-3333-333333333333"
STORE = "B-1115"
CUSTOMER = "cccccccc-cccc-cccc-cccc-cccccccccccc"
TYPES_PATH = "/api/v1/pos/tender-types"
CHECKOUT_PATH = "/api/v1/pos/sales/checkout"

ITEM = {"product_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "description": "Case",
        "qty": 1, "unit_price": 100.00, "tax_rate": 0.08875, "tax_value": 8.88,
        "extended_price": 108.88}


@pytest.fixture
def wired(monkeypatch, fake_supabase, app_module):
    """The POS router pointed at the double, its sign-in gate lifted, and a recorder in place of
    `pos.checkout` so the test can read exactly what the endpoint asked the database to write."""
    from app.modules.pos import router as pos_router

    calls = []

    def _checkout(params):
        calls.append(params)
        return {"id": "s-1", "transaction_id": 9001, "org_id": params.get("p_org")}

    fake_supabase.seed_rpc("pos.checkout", _checkout)
    # WHICH STORE IS THIS SALE FOR (§POS-14) runs before the tender gate and refuses a store the
    # caller cannot ring at, so every org under test needs its own store on the roster. Seeded per
    # org so a tenant case cannot pass by borrowing another tenant's store.
    fake_supabase.seed("storeops.stores", [
        {"id": f"s-{i}", "org_id": org, "store_code": STORE, "address": "1 Example Ave",
         "market": "Brooklyn", "is_active": True}
        for i, org in enumerate((HOUSE, ORG_B, ORG_C))])
    monkeypatch.setattr("app.modules.pos.router._caller_login_store", lambda *a, **k: "")
    monkeypatch.setattr("app.modules.pos.router._caller_store_keyset", lambda *a, **k: None)
    # ORG_B declares its own day counts; ORG_C does not sell on account at all.
    fake_supabase.seed("pos.pos_settings", [
        {"org_id": ORG_B, "store_code": None, "key": "tender_terms_day_options", "value": "7,45"},
        {"org_id": ORG_C, "store_code": None, "key": "tender_terms_enabled", "value": False},
    ])
    monkeypatch.setattr("app.modules.pos.router.get_supabase", lambda: fake_supabase)
    app_module.dependency_overrides[pos_router._require_pos_access] = lambda: None
    monkeypatch.setattr("app.modules.pos.router._caller_employee", lambda *a, **k: "E240")
    monkeypatch.setattr("app.modules.pos.router._caller_ctx",
                        lambda *a, **k: {"super_admin": True, "perms": {}, "role": "admin"})
    yield calls
    app_module.dependency_overrides.clear()


def _body(org, **over):
    sale = {"store_code": STORE, "customer_id": CUSTOMER, "total": 108.88, "subtotal": 100.00,
            "tax_total": 8.88, "date": "2026-10-30"}
    sale.update(over.pop("sale", {}))
    pay = {"payment_method": "terms", "amount": 108.88, "terms_days": 30}
    pay.update(over.pop("payment", {}))
    return {"sale": sale, "items": [ITEM], "payments": [pay]}


# ── what the register is offered ─────────────────────────────────────────────────────────────────
def test_the_register_is_told_which_tenders_it_may_offer(api_client, wired):
    r = api_client.get(TYPES_PATH, params={"org_id": HOUSE, "store_code": STORE})
    assert r.status_code == 200
    keys = [t["key"] for t in r.json()["tenders"]]
    assert keys == ["cash", "credit_card", "debit_card", "check", "terms"]


def test_the_terms_entry_carries_the_day_counts_the_screen_must_show(api_client, wired):
    r = api_client.get(TYPES_PATH, params={"org_id": HOUSE})
    terms = next(t for t in r.json()["tenders"] if t["key"] == "terms")
    assert terms["day_options"] == [15, 30, 60]
    assert terms["settles_now"] is False


def test_each_tenant_is_offered_its_own_declaration_and_not_the_houses(api_client, wired):
    b = api_client.get(TYPES_PATH, params={"org_id": ORG_B}).json()
    assert next(t for t in b["tenders"] if t["key"] == "terms")["day_options"] == [7, 45]
    c = api_client.get(TYPES_PATH, params={"org_id": ORG_C}).json()
    assert "terms" not in [t["key"] for t in c["tenders"]]
    assert "cash" in [t["key"] for t in c["tenders"]]


# ── the checkout ────────────────────────────────────────────────────────────────────────────────
def test_a_terms_sale_reaches_the_database_with_its_due_date_and_the_whole_total_owed(api_client, wired):
    r = api_client.post(CHECKOUT_PATH, params={"org_id": HOUSE}, json=_body(HOUSE))
    assert r.status_code == 200, r.text
    params = wired[-1]
    assert params["p_payments"][0]["terms_days"] == 30
    assert params["p_payments"][0]["due_date"] == "2026-11-29"
    # the money is OWED, not taken: the sale's balance is the server's arithmetic, not the body's
    assert float(params["p_sale"]["balance"]) == pytest.approx(108.88)


def test_a_till_that_claims_an_unpaid_sale_is_settled_is_overruled_not_obeyed(api_client, wired):
    api_client.post(CHECKOUT_PATH, params={"org_id": HOUSE},
                    json=_body(HOUSE, sale={"balance": 0}))
    assert float(wired[-1]["p_sale"]["balance"]) == pytest.approx(108.88)


def test_an_immediate_tender_is_unchanged_and_owes_nothing(api_client, wired):
    api_client.post(CHECKOUT_PATH, params={"org_id": HOUSE},
                    json=_body(HOUSE, payment={"payment_method": "cash", "terms_days": None}))
    pay = wired[-1]["p_payments"][0]
    assert pay["payment_method"] == "cash"
    assert pay["terms_days"] is None and pay["due_date"] is None
    assert float(wired[-1]["p_sale"]["balance"]) == 0


def test_a_terms_sale_with_nobody_to_owe_it_is_refused_and_nothing_is_written(api_client, wired):
    before = len(wired)
    r = api_client.post(CHECKOUT_PATH, params={"org_id": HOUSE},
                        json=_body(HOUSE, sale={"customer_id": ""}))
    assert r.status_code == 400
    assert "customer" in r.json()["detail"].lower()
    assert len(wired) == before


def test_a_day_count_the_org_never_declared_is_refused(api_client, wired):
    before = len(wired)
    r = api_client.post(CHECKOUT_PATH, params={"org_id": ORG_B},
                        json=_body(ORG_B))          # 30 days, but ORG_B declared 7 / 45
    assert r.status_code == 400 and "45" in r.json()["detail"]
    assert len(wired) == before


def test_a_tenant_that_switched_terms_off_cannot_have_one_rung_through_the_api(api_client, wired):
    before = len(wired)
    r = api_client.post(CHECKOUT_PATH, params={"org_id": ORG_C}, json=_body(ORG_C))
    assert r.status_code == 400 and "not offered" in r.json()["detail"].lower()
    assert len(wired) == before


def test_a_tender_the_platform_does_not_know_is_refused_before_the_constraint_sees_it(api_client, wired):
    before = len(wired)
    r = api_client.post(CHECKOUT_PATH, params={"org_id": HOUSE},
                        json=_body(HOUSE, payment={"payment_method": "iou", "terms_days": None}))
    assert r.status_code == 400
    assert len(wired) == before


def test_the_refusal_is_the_one_homes_own_sentence_never_a_second_wording(api_client, wired):
    from app.modules.pos import tender_vocabulary as tv
    expected = tv.plan_payment("terms", {}, customer_id=None, terms_days=30,
                               sale_date="2026-10-30", total=108.88)["refusal"]
    r = api_client.post(CHECKOUT_PATH, params={"org_id": HOUSE},
                        json=_body(HOUSE, sale={"customer_id": ""}))
    assert r.json()["detail"] == expected


def test_a_split_sale_owes_only_the_leg_that_did_not_settle(api_client, wired):
    """A card leg and an account leg on one sale: the balance is the account leg alone. Validating
    the first payment and trusting the rest is how a split sale writes the wrong amount owed."""
    body = _body(HOUSE)
    body["payments"] = [{"payment_method": "credit_card", "amount": 50.00},
                        {"payment_method": "terms", "amount": 58.88, "terms_days": 15}]
    r = api_client.post(CHECKOUT_PATH, params={"org_id": HOUSE}, json=body)
    assert r.status_code == 200, r.text
    assert float(wired[-1]["p_sale"]["balance"]) == pytest.approx(58.88)
    assert wired[-1]["p_payments"][0]["terms_days"] is None
    assert wired[-1]["p_payments"][1]["due_date"] == "2026-11-14"


def test_a_bad_second_leg_refuses_the_whole_sale(api_client, wired):
    before = len(wired)
    body = _body(HOUSE)
    body["payments"] = [{"payment_method": "cash", "amount": 50.00},
                        {"payment_method": "terms", "amount": 58.88, "terms_days": 99}]
    r = api_client.post(CHECKOUT_PATH, params={"org_id": HOUSE}, json=body)
    assert r.status_code == 400 and "99" in r.json()["detail"]
    assert len(wired) == before
