"""Integration: editing a rung sale through the real endpoints (index §POS-12).

Real routing, the real router body, the real one home — over the in-memory database double, with
conftest blocking every outbound socket. There is no staging Supabase, so "integration" here means
the app, not a real database (docs/TESTING.md).

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT. The unit tests hand `sale_amendment.plan` its inputs
directly. They cannot see whether the ROUTER re-derives the figures or quietly forwards the body's,
which is the one thing that must not slip here: `pos.checkout` stores the totals it is handed, and
an edit copied from that habit would let a till send `total: 0.01` on a $200 sale. So these tests
send a deliberately false total and assert that what reaches `pos.amend_sale` is the server's own
arithmetic. They also cover the three things only the wiring can get wrong: the declared window
actually being read from config, the refusal reaching the caller as a 400 rather than a write, and
the endpoint refusing outright when migration 1103 is absent — because a database that cannot keep
the superseded version must not be allowed to overwrite one.
"""
import pytest

ORG = "00000000-0000-0000-0000-000000000001"
SALE_ID = "11111111-1111-1111-1111-111111111111"
STORE = "B-1115"
PLAN_PATH = f"/api/v1/pos/sales/{SALE_ID}/amend-plan"
SAVE_PATH = f"/api/v1/pos/sales/{SALE_ID}/items"
HIST_PATH = f"/api/v1/pos/sales/{SALE_ID}/amendments"

TODAY = None  # stamped by the fixture, so the sale is always inside the default 1-day window

PHONE = {"id": "i-1", "org_id": ORG, "sale_id": SALE_ID, "product_id": "p-1",
         "description": "Samsung A9 +", "product_type": "handset",
         "serial_number": "355123456789012", "qty": 1, "unit_price": 175.00, "list_price": 199.00,
         "cost": 120.00, "discount": 0, "tax_rate": 0.08875, "tax_value": 15.53,
         "extended_price": 190.53, "created_at": "2026-10-29T14:00:00Z"}
CASE = {"id": "i-2", "org_id": ORG, "sale_id": SALE_ID, "product_id": "p-2",
        "description": "Case", "product_type": "accessory",
        "serial_number": "", "qty": 2, "unit_price": 10.00, "list_price": 10.00,
        "cost": 3.00, "discount": 1.00, "tax_rate": 0.08875, "tax_value": 1.60,
        "extended_price": 19.60, "created_at": "2026-10-29T14:00:01Z"}


def _today():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def wired(monkeypatch, fake_supabase, app_module):
    """The POS router's client pointed at the double, its sign-in gate lifted, and a recorder in
    place of `pos.amend_sale` so the test can read exactly what the endpoint asked the database to
    write. The real function is plpgsql and is proved by the migration's own reasoning; what is
    under test here is the CALL."""
    from app.modules.pos import router as pos_router
    monkeypatch.setattr("app.modules.pos.router.get_supabase", lambda: fake_supabase)
    app_module.dependency_overrides[pos_router._require_pos_access] = lambda: None
    # A super-admin, so `_pos_grant` resolves `pos_amend` without seeding a role bag. What this file
    # tests is the edit, not the gate; the gate has its own assertion below.
    monkeypatch.setattr("app.modules.pos.router._caller_ctx",
                        lambda *a, **k: {"super_admin": True, "perms": {}, "role": "admin"})
    monkeypatch.setattr("app.modules.pos.router._caller_employee", lambda *a, **k: "E240")
    monkeypatch.setattr("app.modules.pos.router._caller_store_keyset", lambda *a, **k: None)
    monkeypatch.setattr("app.modules.pos.router._span_filter", lambda rows, keyset: rows)
    fake_supabase.seed("pos.sales", [{
        "id": SALE_ID, "org_id": ORG, "store_code": STORE, "transaction_id": 4605,
        "employee_id": "E240", "status": "completed", "revision": 1,
        "subtotal": 193.00, "discount_total": 2.00, "tax_total": 17.13, "total": 210.13,
        "customer_id": "c-1", "is_activation_sale": False, "created_at": _today()}])
    fake_supabase.seed("pos.sale_items", [PHONE, CASE])
    fake_supabase.seed("pos.sale_amendments", [])
    fake_supabase.seed("pos.pos_settings", [])
    calls = []
    fake_supabase.seed_rpc("pos.amend_sale", lambda params: (calls.append(params) or
                                                            {"id": SALE_ID, "revision": 2}))
    fake_supabase.calls = calls
    yield fake_supabase
    app_module.dependency_overrides.clear()


def test_the_routes_are_registered_at_their_documented_paths(app_module):
    from app.core.path_redact import route_templates
    paths = set(route_templates(app_module))
    for p in (PLAN_PATH, SAVE_PATH, HIST_PATH):
        assert p.replace(SALE_ID, "{sale_id}") in paths, p


# ── the plan ─────────────────────────────────────────────────────────────────────────────────────
def test_the_plan_loads_the_sales_own_lines_for_editing(wired, api_client):
    res = api_client.get(PLAN_PATH, params={"org_id": ORG})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["allowed"] is True and body["reason"] is None
    assert body["transaction_id"] == 4605
    assert [l["description"] for l in body["cart"]] == ["Samsung A9 +", "Case"]
    assert body["cart"][0]["serial_number"] == "355123456789012"
    assert body["cart"][1]["discount"] == 1.00


def test_the_plan_writes_nothing(wired, api_client):
    api_client.get(PLAN_PATH, params={"org_id": ORG})
    assert wired.calls == []
    assert wired.rows("pos.sale_amendments") == []


def test_a_voided_sale_is_refused_by_the_plan_with_a_sentence_for_the_till(wired, api_client):
    wired.seed("pos.sales", [{**wired.rows("pos.sales")[0], "status": "voided"}])
    body = api_client.get(PLAN_PATH, params={"org_id": ORG}).json()
    assert body["allowed"] is False
    assert "voided" in body["reason"].lower()
    assert body["cart"] == []


# ── the save ─────────────────────────────────────────────────────────────────────────────────────
def _save(api_client, items, **extra):
    return api_client.put(SAVE_PATH, params={"org_id": ORG}, json={"items": items, **extra})


def test_adding_an_item_keeps_the_number_and_bumps_the_revision(wired, api_client):
    """THE OWNER'S ASK, end to end: another item on the same receipt."""
    charger = {"product_id": "p-3", "description": "Charger", "product_type": "accessory",
               "qty": 1, "unit_price": 20.00, "discount": 0, "tax_rate": 0.08875}
    res = _save(api_client, [PHONE, CASE, charger])
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["revision"] == 2
    assert "1 line added" in body["changed"]
    params = wired.calls[-1]
    assert params["p_sale"] == SALE_ID          # the same sale row; no new transaction number
    assert len(params["p_items"]) == 3
    assert params["p_employee"] == "E240"


def test_the_total_that_reaches_the_database_is_the_servers_arithmetic_not_the_bodys(wired, api_client):
    """THE REGRESSION THIS FILE EXISTS FOR. `pos.checkout` stores the totals it is handed; an edit
    must not. A till sending a false total gets the true one written."""
    lying_case = dict(CASE, qty=5, extended_price=0.01, tax_value=0.00)
    res = _save(api_client, [PHONE, lying_case], notes="x")
    assert res.status_code == 200, res.text
    patch = wired.calls[-1]["p_sale_patch"]
    assert patch["subtotal"] == 220.00
    assert patch["tax_total"] == 19.52          # 15.53 + 3.99, each line rounded then summed
    assert patch["total"] == 239.52
    # and the LINE figures too, not just the header
    saved_case = [l for l in wired.calls[-1]["p_items"] if l["product_id"] == "p-2"][0]
    assert saved_case["extended_price"] == 48.99   # (9.00 + 0.79875) x 5, rounded once
    assert saved_case["tax_value"] == 3.99


def test_the_change_summary_and_detail_travel_with_the_write(wired, api_client):
    """The audit row's description of the edit is computed by the one home and handed over, so the
    history cannot be written with a different account of what happened."""
    patch = _save(api_client, [PHONE]) and wired.calls[-1]["p_sale_patch"]
    assert "1 removed" in patch["change_summary"]
    assert [l["description"] for l in patch["change_detail"]["removed"]] == ["Case"]


def test_an_edit_that_changes_nothing_is_refused_without_a_write(wired, api_client):
    res = _save(api_client, [PHONE, CASE])
    assert res.status_code == 400
    assert "no change" in res.text.lower()
    assert wired.calls == []


def test_emptying_a_sale_is_refused_and_points_at_the_void_path(wired, api_client):
    res = _save(api_client, [])
    assert res.status_code == 400
    assert "void" in res.text.lower()
    assert wired.calls == []


def test_a_sale_outside_the_declared_window_is_refused_without_a_write(wired, api_client):
    wired.seed("pos.sales", [{**wired.rows("pos.sales")[0],
                              "created_at": "2026-09-01T14:00:00Z"}])
    res = _save(api_client, [PHONE])
    assert res.status_code == 400
    assert "reported" in res.text.lower() or "within" in res.text.lower()
    assert wired.calls == []


def test_the_window_really_comes_from_the_declared_setting(wired, api_client):
    """Not a constant in the router. A store that declares a wider window gets it — the config read
    is wired, which no unit test can show."""
    wired.seed("pos.sales", [{**wired.rows("pos.sales")[0],
                              "created_at": "2026-09-01T14:00:00Z"}])
    wired.seed("pos.pos_settings", [{"org_id": ORG, "store_code": None,
                                     "key": "sale_reopen_window_days", "value": 3650}])
    assert _save(api_client, [PHONE]).status_code == 200, "a declared window must be honoured"


def test_a_voided_sale_is_refused_by_the_save_too(wired, api_client):
    """The plan refusing is not enough — a client can call the save directly."""
    wired.seed("pos.sales", [{**wired.rows("pos.sales")[0], "status": "voided"}])
    res = _save(api_client, [PHONE])
    assert res.status_code == 400
    assert wired.calls == []


# ── the history, and the migration gate ──────────────────────────────────────────────────────────
def test_without_the_migration_the_edit_is_refused_rather_than_done_unrecorded(monkeypatch, wired,
                                                                               api_client):
    """A database that cannot keep the superseded version must not be allowed to overwrite one. This
    is the whole bargain that made editing in place acceptable, so it fails closed."""
    monkeypatch.setattr("app.modules.pos.router._amend_available", lambda *a, **k: False)
    res = _save(api_client, [PHONE])
    assert res.status_code == 503
    # The platform rewrites a 503 detail into a user-facing sentence and logs the real one as a
    # `[setup-notice]`, so the status and the absence of a write are what this can assert.
    assert wired.calls == []


def test_the_plan_reports_the_migration_state_so_the_sale_log_can_hide_the_button(wired, api_client):
    assert api_client.get(PLAN_PATH, params={"org_id": ORG}).json()["available"] is True


def test_the_history_lists_every_superseded_version_newest_first(wired, api_client):
    wired.seed("pos.sale_amendments", [
        {"org_id": ORG, "sale_id": SALE_ID, "revision": 1, "change_summary": "1 line added",
         "total_before": 190.53, "total_after": 210.13, "amended_by": "E240",
         "created_at": "2026-10-29T15:00:00Z"},
        {"org_id": ORG, "sale_id": SALE_ID, "revision": 2, "change_summary": "1 changed",
         "total_before": 210.13, "total_after": 239.52, "amended_by": "E241",
         "created_at": "2026-10-29T16:00:00Z"},
    ])
    body = api_client.get(HIST_PATH, params={"org_id": ORG}).json()
    assert body["available"] is True
    assert [r["revision"] for r in body["revisions"]] == [2, 1]
    assert body["revisions"][0]["total_after"] == 239.52


def test_editing_needs_its_own_grant_and_not_the_void_one(monkeypatch, wired, api_client):
    """`pos_amend`, not `pos_void`: rewriting a tendered record is a larger right than cancelling
    one, so a role granted voids alone cannot edit."""
    monkeypatch.setattr("app.modules.pos.router._caller_ctx",
                        lambda *a, **k: {"super_admin": False, "role": "cashier",
                                         "perms": {"scope": "store", "data": {"pos_void": True}}})
    assert _save(api_client, [PHONE]).status_code == 403
    assert api_client.get(PLAN_PATH, params={"org_id": ORG}).status_code == 403
    assert wired.calls == []
