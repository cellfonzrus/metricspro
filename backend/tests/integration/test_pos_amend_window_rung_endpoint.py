"""Integration: the edit window follows the CALLER's rung through the real endpoints (§POS-12.9).

Owner 2026-10-31: *"30 days nback for the admin and same day for teh sales rep or store manager"*.

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT. `window_for` is pure and already proved. What only the
wiring can get wrong is the three joins between it and a person:

1. **the rung is resolved from the caller's permissions**, not from a role name and not from a
   literal — an ordinary POS user and an administrator must get different answers on the SAME sale;
2. **both endpoints agree.** The plan offers the button and the save writes; a window read on one
   and not the other is §POS-12's whole argument for a one home, undone at the wiring;
3. **the refusal reaches the caller as a refusal**, not as a write — and the sentence a cashier
   reads names the administrator only when an administrator could actually still do it.

The rung is exercised by setting the caller's permission bag, which is what a Roles row produces, so
these tests fail if `_pos_grant`'s precedence stops being what resolves it.
"""
import pytest

ORG = "00000000-0000-0000-0000-000000000001"
SALE_ID = "11111111-1111-1111-1111-111111111111"
STORE = "B-1115"
PLAN_PATH = f"/api/v1/pos/sales/{SALE_ID}/amend-plan"
SAVE_PATH = f"/api/v1/pos/sales/{SALE_ID}/items"

PHONE = {"id": "i-1", "org_id": ORG, "sale_id": SALE_ID, "product_id": "p-1",
         "description": "Samsung A9 +", "product_type": "handset",
         "serial_number": "355123456789012", "qty": 1, "unit_price": 175.00, "list_price": 199.00,
         "cost": 120.00, "discount": 0, "tax_rate": 0.08875, "tax_value": 15.53,
         "extended_price": 190.53, "created_at": "2026-10-29T14:00:00Z"}


def _days_ago(n):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


@pytest.fixture
def wired(monkeypatch, fake_supabase, app_module):
    """The POS router over the double, with the caller's PERMISSION BAG settable per test. `ctx`
    starts as an ordinary POS user: a named scope (so `_pos_grant`'s full-scope default does not
    fire) and an explicit grant of `pos_amend` so the gate itself is never what refuses."""
    from app.modules.pos import router as pos_router
    monkeypatch.setattr("app.modules.pos.router.get_supabase", lambda: fake_supabase)
    app_module.dependency_overrides[pos_router._require_pos_access] = lambda: None
    monkeypatch.setattr("app.modules.pos.router._caller_employee", lambda *a, **k: "E240")
    monkeypatch.setattr("app.modules.pos.router._caller_store_keyset", lambda *a, **k: None)
    monkeypatch.setattr("app.modules.pos.router._span_filter", lambda rows, keyset: rows)

    ctx = {"super_admin": False, "role": "sales_rep",
           "perms": {"scope": "store", "pos_amend": True}}
    monkeypatch.setattr("app.modules.pos.router._caller_ctx", lambda *a, **k: ctx)

    fake_supabase.seed("pos.sale_items", [PHONE])
    fake_supabase.seed("pos.sale_amendments", [])
    fake_supabase.seed("pos.pos_settings", [])
    calls = []
    fake_supabase.seed_rpc("pos.amend_sale", lambda params: (calls.append(params) or
                                                             {"id": SALE_ID, "revision": 2}))
    fake_supabase.calls = calls
    fake_supabase.ctx = ctx

    def rung(elevated, **perms):
        """Make the caller elevated (or not) the way a Roles row would."""
        ctx["perms"] = {"scope": "store", "pos_amend": True,
                        "pos_amend_any_day": elevated, **perms}
    fake_supabase.rung = rung

    def sale(days_ago):
        fake_supabase.seed("pos.sales", [{
            "id": SALE_ID, "org_id": ORG, "store_code": STORE, "transaction_id": 4605,
            "employee_id": "E240", "status": "completed", "revision": 1,
            "subtotal": 175.00, "discount_total": 0, "tax_total": 15.53, "total": 190.53,
            "customer_id": "c-1", "is_activation_sale": False, "created_at": _days_ago(days_ago)}])
    fake_supabase.sale = sale
    sale(0)
    yield fake_supabase
    app_module.dependency_overrides.clear()


def _plan(api_client):
    res = api_client.get(PLAN_PATH, params={"org_id": ORG})
    assert res.status_code == 200, res.text
    return res.json()


def _save(api_client):
    charger = {"product_id": "p-3", "description": "Charger", "product_type": "accessory",
               "qty": 1, "unit_price": 20.00, "discount": 0, "tax_rate": 0.08875}
    return api_client.put(SAVE_PATH, params={"org_id": ORG},
                          json={"items": [PHONE, charger]})


# ── the grid the owner asked for, through the real endpoints ──────────────────────────────────────
@pytest.mark.parametrize("days,elevated,allowed", [
    (0, False, True), (1, False, False), (7, False, False), (30, False, False),
    (0, True, True), (1, True, True), (29, True, True), (31, True, False),
])
def test_the_owners_grid_end_to_end(wired, api_client, days, elevated, allowed):
    wired.sale(days)
    wired.rung(elevated)
    assert _plan(api_client)["allowed"] is allowed


def test_the_plan_says_which_window_it_offered_the_button_under(wired, api_client):
    wired.rung(False)
    assert _plan(api_client)["window_days"] == 0
    wired.rung(True)
    body = _plan(api_client)
    assert body["window_days"] == 30 and body["elevated"] is True


def test_an_ordinary_caller_is_told_they_are_not_elevated_rather_than_left_to_infer_it(
        wired, api_client):
    wired.rung(False)
    assert _plan(api_client)["elevated"] is False


# ── the two endpoints cannot disagree ─────────────────────────────────────────────────────────────
def test_the_save_refuses_what_the_plan_refused_and_writes_nothing(wired, api_client):
    wired.sale(3)
    wired.rung(False)
    assert _plan(api_client)["allowed"] is False
    res = _save(api_client)
    assert res.status_code == 400, res.text
    assert wired.calls == []


def test_the_save_allows_for_an_administrator_what_it_refused_for_a_rep(wired, api_client):
    """The SAME sale and the SAME request — only the permission bag differs."""
    wired.sale(3)
    wired.rung(False)
    assert _save(api_client).status_code == 400
    assert wired.calls == []
    wired.rung(True)
    assert _save(api_client).status_code == 200
    assert len(wired.calls) == 1


# ── the sentence a person reads ───────────────────────────────────────────────────────────────────
def test_a_refused_rep_is_pointed_at_an_administrator_when_one_could_still_do_it(wired, api_client):
    wired.sale(5)
    wired.rung(False)
    assert "administrator" in _plan(api_client)["reason"].lower()


def test_a_refused_rep_is_not_pointed_anywhere_when_nobody_could_do_it(wired, api_client):
    wired.sale(400)
    wired.rung(False)
    assert "administrator" not in _plan(api_client)["reason"].lower()


def test_an_administrator_beyond_their_own_window_is_not_sent_to_themselves(wired, api_client):
    wired.sale(400)
    wired.rung(True)
    said = _plan(api_client)["reason"]
    assert said is not None and "administrator" not in said.lower()


def test_the_same_day_refusal_never_reads_as_zero_days(wired, api_client):
    wired.sale(2)
    wired.rung(False)
    said = _plan(api_client)["reason"]
    assert "0 day" not in said and "same day" in said.lower()


# ── the declarations are read from config, both of them ───────────────────────────────────────────
def test_a_declared_ordinary_window_widens_the_rep(wired, api_client):
    wired.seed("pos.pos_settings", [{"org_id": ORG, "store_code": None,
                                     "key": "sale_reopen_window_days", "value": "10"}])
    wired.sale(8)
    wired.rung(False)
    body = _plan(api_client)
    assert body["allowed"] is True and body["window_days"] == 10


def test_a_declared_elevated_window_widens_only_the_administrator(wired, api_client):
    wired.seed("pos.pos_settings", [{"org_id": ORG, "store_code": None,
                                     "key": "sale_reopen_window_days_admin", "value": "90"}])
    wired.sale(60)
    wired.rung(False)
    assert _plan(api_client)["allowed"] is False
    wired.rung(True)
    body = _plan(api_client)
    assert body["allowed"] is True and body["window_days"] == 90


def test_a_wider_ordinary_declaration_never_leaves_an_administrator_reaching_less_far(
        wired, api_client):
    """45 ordinary days with the elevated one left at the house 30 must not refuse an admin a
    40-day-old sale that a cashier could edit."""
    wired.seed("pos.pos_settings", [{"org_id": ORG, "store_code": None,
                                     "key": "sale_reopen_window_days", "value": "45"}])
    wired.sale(40)
    wired.rung(True)
    body = _plan(api_client)
    assert body["allowed"] is True and body["window_days"] == 45


def test_an_unreadable_declaration_does_not_widen_anybody(wired, api_client):
    wired.seed("pos.pos_settings", [
        {"org_id": ORG, "store_code": None, "key": "sale_reopen_window_days", "value": "soon"},
        {"org_id": ORG, "store_code": None, "key": "sale_reopen_window_days_admin",
         "value": "whenever"}])
    wired.sale(1)
    wired.rung(False)
    assert _plan(api_client)["allowed"] is False
    wired.sale(31)
    wired.rung(True)
    assert _plan(api_client)["allowed"] is False


# ── multi-tenant: one org's declaration never scores another's sale ──────────────────────────────
def test_one_tenants_declared_window_never_reaches_another_tenants_sale(wired, api_client):
    """The owner directive of 2026-10-30: a feature works for every tenant, measured against each
    tenant's OWN declaration. A 90-day row belonging to another org must not widen this one."""
    other = "f4f1c16e-2acf-4221-854a-c29a605754a7"
    wired.seed("pos.pos_settings", [{"org_id": other, "store_code": None,
                                     "key": "sale_reopen_window_days", "value": "90"}])
    wired.sale(5)
    wired.rung(False)
    body = _plan(api_client)
    assert body["allowed"] is False and body["window_days"] == 0


# ── fails closed ─────────────────────────────────────────────────────────────────────────────────
def test_a_caller_whose_permissions_cannot_be_resolved_is_not_elevated(
        wired, api_client, monkeypatch):
    """`_pos_grant(None, …)` is False, so an unresolvable caller gets the SHORT window, never the
    long one. A gate that fails open on the reach of a rewrite is the wrong way round."""
    monkeypatch.setattr("app.modules.pos.router._caller_ctx", lambda *a, **k: None)
    from app.modules.pos import router as pos_router
    assert pos_router._amend_elevated("", ORG) is False


def test_a_super_admin_holds_the_longer_window(wired, api_client):
    """Not a special case in this feature — `_pos_grant`'s first rung, which every POS right shares.
    Asserted so a change to that precedence shows up here rather than in production."""
    wired.ctx["super_admin"] = True
    wired.ctx["perms"] = {"scope": "store"}
    wired.sale(20)
    assert _plan(api_client)["allowed"] is True


def test_an_explicit_denial_beats_a_full_scope_admin(wired, api_client):
    """`_pos_grant` lets an owner revoke one right from an otherwise-full role. That must hold for
    the longer window too, or the only way to limit an admin would be to stop them editing at all."""
    wired.sale(20)
    wired.ctx["super_admin"] = False
    wired.ctx["perms"] = {"scope": "all", "pos_amend": True, "pos_amend_any_day": False}
    assert _plan(api_client)["allowed"] is False
