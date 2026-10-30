"""Integration: the working store, and the tax rate the till charges (index §POS-14).

Real routing, the real router bodies, the real one homes — over the in-memory database double,
with conftest blocking every outbound socket. There is no staging Supabase, so "integration" here
means the app, not a real database (docs/TESTING.md).

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT. The unit tests hand `working_store.span` a roster and
a keyset directly. They cannot see whether the ENDPOINTS actually reach for them, which is the
whole defect: the rule for "which store is this sale for" was never missing from anybody's head,
it was missing from the money path. So these send a sale to a store the caller is not granted, and
a sale with no store at all, and assert that NOTHING reaches `pos.checkout` — a 403 instead of a
row. They also pin the two cross-tenant facts the wiring alone can get wrong: one tenant's stores
never widen another's span, and one tenant's declared tax rate never answers another's till.

MULTI-TENANT (project rule, 2026-10-30). Every case below runs for THREE orgs with different
shapes: a tenant with several stores and a market rate, a tenant with one store and a store rate,
and a tenant that has declared no rate at all. The last is not an edge case — it is what a tenant
looks like on its first day, and a till that silently charges $0 there is the unrecoverable one.
"""
import pytest

HOUSE = "00000000-0000-0000-0000-000000000001"     # several stores, a MARKET rate
SOLO = "11111111-2222-3333-4444-555555555555"      # one store, a STORE rate
BARE = "99999999-8888-7777-6666-555555555555"      # stores, NO rate declared anywhere
WS_PATH = "/api/v1/pos/working-store"
CHECKOUT = "/api/v1/pos/sales/checkout"
RESOLVE = "/api/v1/pos/tax-codes/resolve"

STORES = [
    {"id": "s1", "org_id": HOUSE, "store_code": "B-1115", "address": "2723 Example Ave",
     "market": "Brooklyn", "is_active": True},
    {"id": "s2", "org_id": HOUSE, "store_code": "B-1598", "address": "88 Utica Ave",
     "market": "Brooklyn", "is_active": True},
    {"id": "s3", "org_id": HOUSE, "store_code": "B-2001", "address": "14 Main St",
     "market": "Long Island", "is_active": True},
    {"id": "s4", "org_id": HOUSE, "store_code": "B-DEAD", "address": "9 Closed Rd",
     "market": "Brooklyn", "is_active": False},
    {"id": "s5", "org_id": SOLO, "store_code": "L-01", "address": "1 Solo Way",
     "market": "Queens", "is_active": True},
    {"id": "s6", "org_id": BARE, "store_code": "V-01", "address": "5 Bare St",
     "market": "Bronx", "is_active": True},
    {"id": "s7", "org_id": BARE, "store_code": "V-02", "address": "6 Bare St",
     "market": "Bronx", "is_active": True},
]
TAX_CODES = [
    # HOUSE: a rate on ONE store and a rate on the market the other Brooklyn store sits in. This is
    # the exact shape the register used to get wrong — B-1598 inherits 8.5 and used to charge 4.0.
    {"id": "t1", "org_id": HOUSE, "name": "Ave U", "rate": 8.875, "store_code": "B-1115",
     "market": None, "is_active": True},
    {"id": "t2", "org_id": HOUSE, "name": "Brooklyn", "rate": 8.5, "store_code": None,
     "market": "Brooklyn", "is_active": True},
    {"id": "t3", "org_id": HOUSE, "name": "Default", "rate": 4.0, "store_code": None,
     "market": None, "is_active": True},
    {"id": "t4", "org_id": SOLO, "name": "Solo", "rate": 7.0, "store_code": "L-01",
     "market": None, "is_active": True},
]


@pytest.fixture
def wired(monkeypatch, fake_supabase, app_module):
    """Every POS read pointed at the double and the sign-in gate lifted, with a recorder in place of
    `pos.checkout` so a test can see whether a sale reached the database at all. The RPC is plpgsql
    and is proved by migration 725; what is under test here is whether it is ever CALLED."""
    from app.modules.pos import router as pos_router
    monkeypatch.setattr("app.modules.pos.router.get_supabase", lambda: fake_supabase)
    app_module.dependency_overrides[pos_router._require_pos_access] = lambda: None
    monkeypatch.setattr("app.modules.pos.router._caller_ctx",
                        lambda *a, **k: {"super_admin": True, "perms": {}, "role": "admin"})
    monkeypatch.setattr("app.modules.pos.router._caller_employee", lambda *a, **k: "E240")
    monkeypatch.setattr("app.modules.pos.router._caller_login_store", lambda *a, **k: "")
    # Unrestricted by default; a test that cares about scoping overrides this per case.
    monkeypatch.setattr("app.modules.pos.router._caller_store_keyset", lambda *a, **k: None)
    fake_supabase.seed("storeops.stores", STORES)
    fake_supabase.seed("pos.tax_codes", TAX_CODES)
    calls = []
    fake_supabase.seed_rpc("pos.checkout", lambda params: (calls.append(params) or
                                                           {"id": "sale-1", "transaction_id": 9001}))
    fake_supabase.calls = calls
    yield fake_supabase
    app_module.dependency_overrides.clear()


def _scoped(monkeypatch, keys):
    """Pin the caller's reporting span to a keyset, the way `storeops.scope_keyset` returns one
    (UPPER keys, codes AND addresses; None would mean unrestricted)."""
    monkeypatch.setattr("app.modules.pos.router._caller_store_keyset", lambda *a, **k: set(keys))


ITEM = {"product_id": "p-1", "description": "Case", "qty": 1, "unit_price": 10.0,
        "discount": 0, "tax_rate": 0.085}


def _checkout(api_client, org, store, **extra):
    sale = {"subtotal": 10.0, "tax_total": 0.85, "total": 10.85, **extra}
    if store is not None:
        sale["store_code"] = store
    return api_client.post(CHECKOUT, params={"org_id": org},
                           json={"sale": sale, "items": [ITEM], "payments": []})


# ── the route exists where it is documented ──────────────────────────────────────────────────────
def test_the_working_store_route_is_registered(app_module):
    from app.core.path_redact import route_templates
    assert WS_PATH in set(route_templates(app_module))


# ── §A. the span the register asks for ───────────────────────────────────────────────────────────
def test_A1_an_unrestricted_caller_gets_every_ACTIVE_store_and_is_told_to_confirm(wired, api_client):
    body = api_client.get(WS_PATH, params={"org_id": HOUSE}).json()
    assert body["stores"] == ["B-1115", "B-1598", "B-2001"]
    assert "B-DEAD" not in body["stores"], "a closed store is never where a sale happened"
    assert body["must_confirm"] is True
    assert body["basis"] == "unconfirmed" and body["store_code"] is None


def test_A2_a_cashier_granted_one_store_is_asked_nothing(wired, api_client, monkeypatch):
    _scoped(monkeypatch, {"B-1598"})
    body = api_client.get(WS_PATH, params={"org_id": HOUSE}).json()
    assert body["stores"] == ["B-1598"]
    assert body["must_confirm"] is False
    assert (body["store_code"], body["basis"]) == ("B-1598", "sole_store")


def test_A3_a_confirmed_pick_comes_back_with_its_basis_and_its_rate(wired, api_client):
    body = api_client.get(WS_PATH, params={"org_id": HOUSE, "store_code": "B-1115"}).json()
    assert (body["store_code"], body["basis"]) == ("B-1115", "confirmed")
    assert body["tax"]["rate"] == 8.875 and body["tax"]["scope"] == "store"


def test_A4_a_pick_outside_the_span_is_refused_and_names_no_store(wired, api_client, monkeypatch):
    _scoped(monkeypatch, {"B-1598"})
    body = api_client.get(WS_PATH, params={"org_id": HOUSE, "store_code": "B-2001"}).json()
    assert body["store_code"] is None and body["basis"] == "unconfirmed"
    assert "B-2001" in body["refusal"]


def test_A5_the_span_never_reaches_across_tenants(wired, api_client):
    """THE multi-tenant assertion. The same unrestricted caller, two orgs: neither sees the other's
    stores. A span that leaked would let a sale be logged into another tenant's books."""
    assert api_client.get(WS_PATH, params={"org_id": SOLO}).json()["stores"] == ["L-01"]
    assert api_client.get(WS_PATH, params={"org_id": BARE}).json()["stores"] == ["V-01", "V-02"]
    assert api_client.get(WS_PATH, params={"org_id": HOUSE}).json()["stores"] == [
        "B-1115", "B-1598", "B-2001"]


# ── §B. the money path refuses rather than guessing ──────────────────────────────────────────────
def test_B1_a_sale_at_a_granted_store_is_written(wired, api_client, monkeypatch):
    _scoped(monkeypatch, {"B-1598"})
    res = _checkout(api_client, HOUSE, "B-1598")
    assert res.status_code == 200, res.text
    assert wired.calls and wired.calls[0]["p_sale"]["store_code"] == "B-1598"


def test_B2_a_sale_at_a_store_the_caller_is_not_granted_NEVER_reaches_the_database(
        wired, api_client, monkeypatch):
    _scoped(monkeypatch, {"B-1598"})
    res = _checkout(api_client, HOUSE, "B-2001")
    assert res.status_code == 403, res.text
    assert wired.calls == [], "the refusal must happen before the sale, not after it"


def test_B3_a_sale_with_NO_store_is_refused(wired, api_client):
    res = _checkout(api_client, HOUSE, None)
    assert res.status_code == 403
    assert "Confirm which store" in res.json()["detail"]
    assert wired.calls == []


def test_B4_a_sale_with_no_store_is_refused_even_for_a_single_store_cashier(
        wired, api_client, monkeypatch):
    """The server knows which store it must be and still refuses: the receipt, the day's closing and
    the P&L all read the STORED column, so the sale has to carry it."""
    _scoped(monkeypatch, {"B-1598"})
    assert _checkout(api_client, HOUSE, None).status_code == 403
    assert wired.calls == []


def test_B5_the_store_is_stored_in_the_rosters_own_spelling(wired, api_client):
    """'b-1115' is the same store to a human and was not to this platform — one such mismatch once
    created a phantom store holding $289 of a day's money."""
    assert _checkout(api_client, HOUSE, "b-1115").status_code == 200
    assert wired.calls[0]["p_sale"]["store_code"] == "B-1115"


def test_B6_a_sale_cannot_be_posted_into_another_tenants_store(wired, api_client):
    """Acting in SOLO, naming a HOUSE store. The org filter on the roster read is what stops it."""
    res = _checkout(api_client, SOLO, "B-1115")
    assert res.status_code == 403, res.text
    assert wired.calls == []


def test_B7_an_activation_is_gated_the_same_way_as_the_sale(wired, api_client, monkeypatch):
    """THE SIBLING. An activation carries the same store_code into the commission feed and had the
    same absence of a check."""
    _scoped(monkeypatch, {"B-1598"})
    res = api_client.post("/api/v1/pos/activations", params={"org_id": HOUSE},
                          json={"store_code": "B-2001", "status": "active"})
    assert res.status_code == 403, res.text
    assert wired.rows("pos.activations") == []


# ── §C. the rate the till charges comes from the server's rule ───────────────────────────────────
def test_C1_the_market_rung_applies_at_a_store_with_no_rate_of_its_own(wired, api_client):
    """THE REGISTER DEFECT, through the endpoint the register now asks. B-1598 has no rate of its
    own and sits in Brooklyn, which has 8.5. The register's own copy skipped the market rung and
    charged the org-wide 4.0."""
    body = api_client.get(RESOLVE, params={"org_id": HOUSE, "store_code": "B-1598"}).json()
    assert (body["rate"], body["scope"]) == (8.5, "market")
    assert body["rate"] != 4.0


def test_C2_a_store_rate_still_wins_over_its_market(wired, api_client):
    body = api_client.get(RESOLVE, params={"org_id": HOUSE, "store_code": "B-1115"}).json()
    assert (body["rate"], body["scope"]) == (8.875, "store")


def test_C3_the_org_wide_rate_is_the_last_rung(wired, api_client):
    body = api_client.get(RESOLVE, params={"org_id": HOUSE, "store_code": "B-2001"}).json()
    assert (body["rate"], body["scope"]) == (4.0, "org")


def test_C4_a_tenant_that_has_declared_nothing_is_told_so_not_handed_a_zero(wired, api_client):
    """A tenant's first day. `none` must be explicit — a taxable sale that charged $0 is not
    recoverable once the customer has left."""
    body = api_client.get(RESOLVE, params={"org_id": BARE, "store_code": "V-01"}).json()
    assert body["rate"] is None and body["scope"] == "none"
    assert body["warning"]


def test_C5_one_tenants_rate_never_answers_anothers_till(wired, api_client):
    """SOLO declares 7.0 on L-01. Asking BARE for V-01 must not reach it, and asking SOLO for a
    HOUSE store code must not reach HOUSE's rate either."""
    assert api_client.get(RESOLVE, params={"org_id": BARE, "store_code": "V-01"}).json()["rate"] is None
    assert api_client.get(RESOLVE, params={"org_id": SOLO, "store_code": "L-01"}).json()["rate"] == 7.0
    assert api_client.get(RESOLVE, params={"org_id": SOLO, "store_code": "B-1115"}).json()["rate"] is None


def test_C6_the_store_grid_and_the_till_agree_rung_for_rung(wired, api_client):
    """The two surfaces that disagreed. The grid's `effective_scope`/`effective_rate` and the till's
    resolve must come from one rule, so they are compared store by store here."""
    grid = api_client.get("/api/v1/pos/tax-codes/store-grid", params={"org_id": HOUSE}).json()
    for row in grid["stores"]:
        till = api_client.get(RESOLVE, params={"org_id": HOUSE,
                                              "store_code": row["store_code"]}).json()
        assert (till["rate"], till["scope"]) == (row["effective_rate"], row["effective_scope"]), \
            row["store_code"]


# ── §T. THE CROSS-TENANT PASS (project rule, 2026-10-30) ─────────────────────────────────────────
#
# "Every feature must work for ALL tenants, never just one. After a feature is built, a testing pass
# must verify it works for every tenant." There is no staging database and production reads are not
# available from this container, so this pass is STRUCTURAL: every case below runs for EVERY seeded
# tenant, and each one also asserts the NEGATIVE — that the same rows under another tenant's
# declaration produce nothing. Asserting only the positive is what lets a feature pass for the house
# org and return zeros for the next tenant.

ALL_ORGS = [(HOUSE, "B-1115"), (SOLO, "L-01"), (BARE, "V-01")]


@pytest.mark.parametrize("org,store", ALL_ORGS)
def test_T1_every_tenant_gets_its_own_span_and_nobody_elses(wired, api_client, org, store):
    body = api_client.get(WS_PATH, params={"org_id": org}).json()
    assert store in body["stores"], f"{org} cannot see its own store"
    foreign = [s["store_code"] for s in STORES if s["org_id"] != org]
    assert not (set(body["stores"]) & set(foreign)), "another tenant's stores are in this span"


@pytest.mark.parametrize("org,store", ALL_ORGS)
def test_T2_every_tenant_can_ring_a_sale_at_its_own_store(wired, api_client, org, store):
    assert _checkout(api_client, org, store).status_code == 200, f"{org} cannot ring at {store}"
    assert wired.calls[-1]["p_sale"]["store_code"] == store


@pytest.mark.parametrize("org,store", ALL_ORGS)
def test_T3_no_tenant_can_ring_a_sale_at_another_tenants_store(wired, api_client, org, store):
    for other_org, other_store in ALL_ORGS:
        if other_org == org:
            continue
        res = _checkout(api_client, org, other_store)
        assert res.status_code == 403, f"{org} was allowed to post a sale to {other_store}"
    assert wired.calls == []


@pytest.mark.parametrize("org,store", ALL_ORGS)
def test_T4_no_tenant_can_ring_a_sale_with_no_store(wired, api_client, org, store):
    assert _checkout(api_client, org, None).status_code == 403
    assert wired.calls == []


@pytest.mark.parametrize("org,store", ALL_ORGS)
def test_T5_every_tenant_gets_an_ANSWER_about_tax_even_when_it_has_declared_none(
        wired, api_client, org, store):
    """The silent-zero case, per tenant. A tenant with nothing declared must come back `none` with a
    warning — not 0, and not another tenant's rate."""
    body = api_client.get(RESOLVE, params={"org_id": org, "store_code": store}).json()
    assert body["scope"] in ("store", "market", "org", "none")
    if body["scope"] == "none":
        assert body["rate"] is None and body["warning"]
    else:
        assert body["rate"] is not None and not body["warning"]


@pytest.mark.parametrize("org,store", ALL_ORGS)
def test_T6_a_tenants_rate_is_never_answered_from_another_tenants_rows(wired, api_client, org, store):
    """The NEGATIVE that pins the mechanism rather than the symptom: BARE declares no rate at all
    while HOUSE and SOLO declare several, so if the read were not org-scoped, BARE would inherit."""
    declared = {c["org_id"] for c in TAX_CODES}
    body = api_client.get(RESOLVE, params={"org_id": org, "store_code": store}).json()
    if org not in declared:
        assert body["rate"] is None, "a tenant with no declaration was handed somebody else's rate"
    foreign = [c["rate"] for c in TAX_CODES if c["org_id"] != org]
    assert body["rate"] is None or body["rate"] not in [f for f in foreign
                                                        if f not in [c["rate"] for c in TAX_CODES
                                                                     if c["org_id"] == org]]


@pytest.mark.parametrize("org,store", ALL_ORGS)
def test_T7_the_activation_gate_holds_for_every_tenant(wired, api_client, org, store):
    for other_org, other_store in ALL_ORGS:
        if other_org == org:
            continue
        res = api_client.post("/api/v1/pos/activations", params={"org_id": org},
                              json={"store_code": other_store, "status": "active"})
        assert res.status_code == 403, f"{org} filed an activation at {other_store}"
    assert wired.rows("pos.activations") == []


def test_T8_no_tenant_or_carrier_name_appears_in_either_one_home():
    """RULE TWO, on the modules this PR adds: behaviour is per-org config and the caller's span, so
    no tenant, carrier or store name may be spelled in the code."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    banned = ("boost", "luxelink", "cellfonz", "vzone", "total wireless", "b-1115", "metro")
    for rel in ("app/modules/pos/working_store.py", "app/modules/pos/tax_precedence.py"):
        src = (root / rel).read_text().lower()
        hits = [b for b in banned if b in src]
        assert not hits, f"{rel} spells {hits}"
