"""Integration: a trading day of sales that never arrived reaches the attention surface — for EVERY
tenant (project rule 2026-10-30; index §COMMCALC-51).

Owner (2026-10-31): *"this is a big problem for management iff we wil miss trading days, we need to
get this fix right away"*.

WHY AN INTEGRATION TEST FOR A PROVIDER. The unit test proves `zero_sales.feed_gap_items` over rows a
human typed. Only the wiring proves the three things that actually broke:

  1. the provider is REGISTERED in `import_health.PROVIDERS`, so `GET /core/attention` runs it. The
     whole defect was a correct fact that no surface asked for; a provider nobody registered would
     reproduce it exactly.
  2. it is HEAVY, so it is deferred on the login popup and runs on the admin page's full check — a
     cheap provider running the zero-sales fold would make every login pay for a sales read.
  3. it reads the REAL `_zero_sales_core` fold over real seeded sales rows, with each tenant's own
     store set and its own config. A gap found for one tenant must never surface under another.

Real routing, real middleware, the real provider body, over the in-memory database double, with
conftest blocking every outbound socket (docs/TESTING.md).
"""
import datetime

import pytest

HOUSE = "00000000-0000-0000-0000-000000000001"
#: Three tenants, each with its OWN store and its OWN missing day, so a cross-tenant leak shows up
#: as the wrong store code and not merely as a wrong total.
TENANTS = (HOUSE,
           "00000000-0000-0000-0000-0000000000a2",
           "00000000-0000-0000-0000-0000000000a3")
OWN_STORE = {TENANTS[0]: "B-1115", TENANTS[1]: "B-2000", TENANTS[2]: "B-3000"}
#: Each tenant's store sold on these days of October 2026 and nothing landed on the rest.
SOLD_DAYS = {TENANTS[0]: ("2026-10-01", "2026-10-02"),
             TENANTS[1]: ("2026-10-01", "2026-10-03"),
             TENANTS[2]: ("2026-10-02", "2026-10-03")}
MISSING = {TENANTS[0]: ["2026-10-03"], TENANTS[1]: ["2026-10-02"], TENANTS[2]: ["2026-10-01"]}
#: "now" is fixed, and the window ends YESTERDAY, so the three days above are the closed days.
NOW = datetime.datetime(2026, 10, 4, 9, 0, tzinfo=datetime.timezone.utc)
PROVIDER_KEY = "commcalc_feed_gap_days"


def _line(org, trans_id, day, store):
    return {"org_id": org, "period": "October 2026", "trans_id": trans_id,
            "id": org[-2:] + trans_id + day, "store": store, "salesperson": "Arora, Tanish",
            "trans_date": day, "contract_type": "New Activation", "department": "Handsets",
            "category": "Phones", "product_desc": "moto g play", "ext_price": 49.99,
            "voided": None, "trans_type": "Sale", "serial_1": "S" + trans_id + day,
            "mdn": "2155550143", "tender_type": "Cash"}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    from app.modules.commcalc import router as commcalc_router
    monkeypatch.setattr("app.modules.commcalc.router.get_supabase", lambda: fake_supabase,
                        raising=False)
    for name in ("_ACFG_CACHE", "_CFG_CACHE"):
        cache = getattr(commcalc_router, name, None)
        if isinstance(cache, dict):
            cache.clear()
    # The window is config, set to exactly the three closed days so the assertions name those days
    # and not thirty days of an estate nobody seeded. It exercises the config column at the same time.
    fake_supabase.seed("commcalc.commission_org_config",
                       [{"org_id": o, "audit_feed_gap_window_days": 3} for o in TENANTS])
    fake_supabase.seed("commcalc.store_mapping",
                       [{"org_id": o, "store_code": OWN_STORE[o],
                         "store_address": f"{OWN_STORE[o]} Main St"} for o in TENANTS])
    fake_supabase.seed("commcalc.daily_sales_feed",
                       [_line(o, "T-1", d, OWN_STORE[o]) for o in TENANTS for d in SOLD_DAYS[o]])
    # `trading_day_source` defaults to the schedule and no tenant here has one, so every store's
    # calendar is 'unknown' and every day is evaluated — the honest fallback (§15z), which is what
    # makes a missing day visible rather than silently "closed".
    return fake_supabase


def _ctx(client):
    from app.modules.core import import_health as IH
    return {"now": NOW, "feed_health": {}, "route_policy": [], "connector_scope": None}


# ── 1. the provider is registered, so a surface actually asks ─────────────────────────────────────
def test_the_provider_is_registered_with_the_aggregator(app_module):
    """A correct fact no surface asks for is the defect this change exists to end."""
    from app.modules.core import import_health as IH
    assert PROVIDER_KEY in {p["key"] for p in IH.PROVIDERS}


def test_it_is_heavy_so_no_login_pays_for_a_sales_read(app_module):
    from app.modules.core import import_health as IH
    spec = next(p for p in IH.PROVIDERS if p["key"] == PROVIDER_KEY)
    assert spec["cost"] == "heavy" and spec["group"] == "import"


def test_the_attention_route_is_registered_at_its_documented_path(app_module):
    from app.core.path_redact import route_templates
    assert "/api/v1/core/attention" in set(route_templates(app_module))


# ── 2. the finding, per tenant, over the real fold ────────────────────────────────────────────────
@pytest.mark.parametrize("org", TENANTS, ids=[o[-2:] for o in TENANTS])
def test_each_tenant_gets_its_own_missing_day(wired, org):
    from app.modules.commcalc import import_audit as IA
    items = IA.p_sales_feed_days(wired, org, _ctx(wired))
    assert len(items) == 1, items
    item = items[0]
    assert item["severity"] == "warning"
    assert item["key"] == "commcalc:feed_gap:short_days"
    assert item["count"] == len(MISSING[org])
    assert OWN_STORE[org] in item["detail"]


@pytest.mark.parametrize("org", TENANTS, ids=[o[-2:] for o in TENANTS])
def test_no_tenant_can_see_another_tenants_store(wired, org):
    """The leak this guards: one tenant's store named in another tenant's finding."""
    from app.modules.commcalc import import_audit as IA
    detail = " ".join(i["detail"] for i in IA.p_sales_feed_days(wired, org, _ctx(wired)))
    for other in TENANTS:
        if other != org:
            assert OWN_STORE[other] not in detail


@pytest.mark.parametrize("org", TENANTS, ids=[o[-2:] for o in TENANTS])
def test_the_named_days_are_the_days_that_did_not_land(wired, org):
    """Through the real fold, not through a typed row: the dates the provider reports must be
    exactly the days that store sold nothing on and delivered nothing for."""
    from app.modules.commcalc import import_audit as IA
    from app.modules.commcalc import zero_sales as Z
    from app.modules.commcalc.router import _zero_sales_core
    lo, hi, _cfg = IA._feed_gap_window(wired, org, NOW)
    assert (lo, hi) == ("2026-10-01", "2026-10-03")
    report = _zero_sales_core(wired, org, lo, hi, as_of="2026-10-04")
    found = Z.feed_gap_items(report)
    assert [i["store_code"] for i in found] == [OWN_STORE[org]]
    assert found[0]["dates"] == MISSING[org]


# ── 3. the healthy answer is silence ─────────────────────────────────────────────────────────────
def test_a_tenant_with_no_sales_feed_at_all_raises_nothing(wired):
    """A tenant with no stores and no feed has nothing to be missing — never a false alarm."""
    from app.modules.commcalc import import_audit as IA
    assert IA.p_sales_feed_days(wired, "00000000-0000-0000-0000-0000000000ff",
                                _ctx(wired)) == []


def test_a_store_that_delivered_every_day_raises_nothing(wired):
    """The healthy answer is silence, not an item reading zero."""
    from app.modules.commcalc import import_audit as IA
    wired.seed("commcalc.daily_sales_feed",
               [_line(TENANTS[0], "T-9", d, OWN_STORE[TENANTS[0]])
                for d in ("2026-10-01", "2026-10-02", "2026-10-03")])
    assert IA.p_sales_feed_days(wired, TENANTS[0], _ctx(wired)) == []


# ── 4. the window ────────────────────────────────────────────────────────────────────────────────
def test_the_window_ends_yesterday_because_today_has_not_failed_to_deliver(wired):
    from app.modules.commcalc import import_audit as IA
    lo, hi, _ = IA._feed_gap_window(wired, HOUSE, NOW)
    assert hi == "2026-10-03"
    assert lo < hi


def test_the_window_length_is_clamped_to_what_the_fold_accepts(wired):
    """A config value past the fold's 186-day cap would become a 400 the admin page shows as a
    broken check, so it is clamped here instead of passed through."""
    from app.modules.commcalc import import_audit as IA
    wired.seed("commcalc.commission_org_config",
               [{"org_id": HOUSE, "audit_feed_gap_window_days": 9999}])
    lo, hi, _ = IA._feed_gap_window(wired, HOUSE, NOW)
    span = (datetime.date.fromisoformat(hi) - datetime.date.fromisoformat(lo)).days + 1
    assert span == IA.FEED_GAP_WINDOW_MAX
