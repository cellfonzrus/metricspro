"""Integration: the store-DLAR KPI read and its catalog, through the REAL app (index §COMMCALC-57).

Real routing, real middleware, the real endpoint bodies, over the in-memory database double, with
conftest blocking every outbound socket. There is no staging Supabase, so "integration" here means
the app, not a real database (docs/TESTING.md).

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT. `tests/unit/test_dlar_metric_catalog.py` proves the
catalog is derived from one declaration. What only the app can prove is the WIRING, and every one of
these is a way this feature could be live and still wrong:

  * the newly declared columns actually come back from `GET /dlar-store/{period}` — the point of the
    owner's 2026-11-10 ask, and the thing four separate column lists used to be able to break;
  * `GET /dlar-store-catalog` exists at its documented path and offers exactly what the read returns,
    so a "filter by the KPI" picker cannot offer a dead option;
  * a column migration 1116 has not yet added is reported UNAVAILABLE WITH A REASON — not omitted
    (which reads as "this carrier does not publish it") and not 0 (which reads as "sold none");
  * EVERY TENANT (project rule 2026-10-30): a second org's rows never reach the first org's read,
    and a tenant with no store DLAR at all is TOLD its feed published nothing rather than shown an
    empty grid that looks like a month of zeros;
  * market is stamped through the canonical resolver, so the market filter on this report binds the
    same stores every other market filter binds.
"""
import pytest

ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "00000000-0000-0000-0000-0000000000ff"
PERIOD = "November 2026"
PATH = "/api/v1/commcalc/dlar-store"
CATALOG = "/api/v1/commcalc/dlar-store-catalog"

# The brand-new figures, named here so a test failure says WHICH column stopped arriving at the API.
NEW_COLUMNS = ("activations_total", "total_trade_ins", "value_trade_ins", "swaps_total",
               "starlink_activations", "byod_total", "protection_dop", "avg_platinum_points",
               "total_health_checks", "projection", "mom_percent_change", "yoy_percent_change")


def _store_row(org=ORG, *, address="123 Main St", location="Main Street", code="B-1115", **over):
    row = {"org_id": org, "period": PERIOD, "period_month": 11, "period_year": 2026,
           "store_code": code, "address": address, "location": location,
           "atu": 66.67, "protect_pct": 80.0, "byod_pct": 35.0, "family_plan_pct": 45.0,
           "tmr3": 62.5, "aal_conversion": 3.85, "conversion_rate": 12.0,
           "total_acts": None, "gross_adds": None, "total_upgrades": None,
           "zero_selling_days": 2, "activations_total": 134, "total_trade_ins": 11,
           "value_trade_ins": 7, "upgrade_value_trade_ins": 3, "offer_trade_ins": 1,
           "trade_in_unlocked_activations": 4, "swaps_total": 9, "rpl_count": 2,
           "starlink_activations": 5, "starlink_sales": 6, "auxiliary_device_acts": 8,
           "byod_total": 21, "byod_unlocked_activations": 12, "protection_dop": 40,
           "protection_upgrades": 15, "avg_platinum_points": 3.5, "total_health_checks": 77,
           "projection": 150, "psa_projected": 89.3, "sales_target": 160,
           "mom_percent_change": 12.5, "yoy_percent_change": -4.25,
           "acc_attach_rate": 51.88, "avg_first_mrc": 48.0, "port_pct": 30.77,
           "shopper_trak_conversion": 8.0, "pay_now_acts": None, "pay_later_acts": None,
           "id": f"{org}-{code}-{address}"}
    row.update(over)
    return row


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    """The commcalc router's client getters pointed at the double."""
    for name in ("sb", "get_supabase"):
        monkeypatch.setattr(f"app.modules.commcalc.router.{name}", lambda: fake_supabase,
                            raising=False)
    monkeypatch.setattr("app.core.scope.get_supabase", lambda: fake_supabase, raising=False)
    return fake_supabase


def _seed_stores(db):
    """The tenant's own store vocabulary — the canonical market index reads this, so the market a
    row is stamped with is the market store setup declares, never one this test invents."""
    db.seed("storeops.stores", [
        {"org_id": ORG, "store_code": "B-1115", "address": "123 Main St", "market": "LI",
         "is_active": True},
        {"org_id": OTHER_ORG, "store_code": "V-001", "address": "9 Other Ave", "market": "NJ",
         "is_active": True},
    ])


# ── THE ASK: the new columns reach the report ───────────────────────────────────────────────────

def test_the_newly_declared_columns_come_back_from_the_read(wired, api_client):
    _seed_stores(wired)
    wired.seed("commcalc.raw_dlar_store", [_store_row()])
    r = api_client.get(f"{PATH}/{PERIOD}", params={"org_id": ORG})
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    for col in NEW_COLUMNS:
        assert col in rows[0], f"{col} declared and landed but not served"
    assert rows[0]["activations_total"] == 134
    assert rows[0]["yoy_percent_change"] == -4.25


def test_a_column_the_source_stopped_sending_reads_absent_not_zero(wired, api_client):
    """`gross_adds` has been landing NULL since the source renamed its field. NULL must survive the
    read: a 0 here would say every store sold nothing, which is the defect that hid for a month."""
    _seed_stores(wired)
    wired.seed("commcalc.raw_dlar_store", [_store_row()])
    row = api_client.get(f"{PATH}/{PERIOD}", params={"org_id": ORG}).json()[0]
    assert row["gross_adds"] is None and row["total_acts"] is None


def test_market_is_stamped_from_the_canonical_vocabulary(wired, api_client):
    _seed_stores(wired)
    wired.seed("commcalc.raw_dlar_store", [_store_row()])
    row = api_client.get(f"{PATH}/{PERIOD}", params={"org_id": ORG}).json()[0]
    assert row["market"] == "LI"


# ── THE CATALOG a picker is built from ─────────────────────────────────────────────────────────

def test_catalog_offers_exactly_what_the_read_returns(wired, api_client):
    _seed_stores(wired)
    wired.seed("commcalc.raw_dlar_store", [_store_row()])
    cat = api_client.get(CATALOG, params={"org_id": ORG})
    assert cat.status_code == 200, cat.text
    body = cat.json()
    row = api_client.get(f"{PATH}/{PERIOD}", params={"org_id": ORG}).json()[0]
    for m in body["metrics"]:
        assert m["key"] in row, f"catalog offers {m['key']}, the read does not return it"
        assert m["label"] and m["group"] in body["groups"]
    assert body["has_rows"] is True and body["note"] is None
    assert "LI" in body["markets"]
    scored = {m["key"] for m in body["metrics"] if m["scored"]}
    assert {"atu", "protect_pct", "tmr3", "aal_conversion"} <= scored


def test_an_unapplied_column_is_unavailable_with_a_reason_never_a_zero(wired, api_client,
                                                                      monkeypatch):
    """Migration 1116 is applied by hand. Until it runs, the figures it adds do not exist — and the
    surface must say so. Omitting them would read as "this carrier publishes no trade-in figure"."""
    _seed_stores(wired)
    wired.seed("commcalc.raw_dlar_store", [_store_row()])
    monkeypatch.setattr("app.core.column_tolerant.present_columns",
                        lambda *a, **k: frozenset({"activations_total"}))
    body = api_client.get(CATALOG, params={"org_id": ORG}).json()
    by_key = {m["key"]: m for m in body["metrics"]}
    assert by_key["activations_total"]["available"] is True
    missing = by_key["total_trade_ins"]
    assert missing["available"] is False and "1116" in missing["unavailable_reason"]
    # a pre-existing column is never reported unavailable — it cannot be, migration 002 made it
    assert by_key["atu"]["available"] is True


def test_the_read_still_answers_when_the_new_columns_do_not_exist_yet(wired, api_client,
                                                                     monkeypatch):
    """One unapplied column must not 400 the whole read. Before this, a single missing column in a
    `select(...)` literal would have left the page showing nothing at all (§4b.1)."""
    _seed_stores(wired)
    wired.seed("commcalc.raw_dlar_store", [_store_row()])
    monkeypatch.setattr("app.core.column_tolerant.present_columns", lambda *a, **k: frozenset())
    r = api_client.get(f"{PATH}/{PERIOD}", params={"org_id": ORG})
    assert r.status_code == 200, r.text
    assert r.json()[0]["atu"] == 66.67


# ── EVERY TENANT (project rule 2026-10-30) ─────────────────────────────────────────────────────

def test_one_tenants_rows_never_reach_another_tenants_read(wired, api_client):
    """A tenant-scoped feed read is where cross-tenant leakage lives. Both orgs have a row for the
    same period; each read returns only its own."""
    _seed_stores(wired)
    wired.seed("commcalc.raw_dlar_store", [
        _store_row(),
        _store_row(OTHER_ORG, address="9 Other Ave", location="Other Ave", code="V-001",
                   activations_total=999),
    ])
    mine = api_client.get(f"{PATH}/{PERIOD}", params={"org_id": ORG}).json()
    theirs = api_client.get(f"{PATH}/{PERIOD}", params={"org_id": OTHER_ORG}).json()
    assert [r["store_code"] for r in mine] == ["B-1115"]
    assert [r["store_code"] for r in theirs] == ["V-001"]
    assert all(r["activations_total"] != 999 for r in mine)
    assert theirs[0]["market"] == "NJ", "each tenant's market comes from its OWN vocabulary"


def test_a_tenant_with_no_store_dlar_is_told_so_rather_than_shown_zeros(wired, api_client):
    """§COMMCALC-49.10: nothing measured means nothing identified, and the surface says so. An empty
    grid with a zero in every cell is a plausible-looking answer, which is why this class hides."""
    _seed_stores(wired)
    wired.seed("commcalc.raw_dlar_store", [_store_row()])
    body = api_client.get(CATALOG, params={"org_id": OTHER_ORG}).json()
    assert body["has_rows"] is False
    assert body["note"] and "not zero" in body["note"]
    # and the catalog is still the SAME shape — it is the feed's shape, not a tenant's vocabulary
    mine = api_client.get(CATALOG, params={"org_id": ORG}).json()
    assert [m["key"] for m in body["metrics"]] == [m["key"] for m in mine["metrics"]]
    assert api_client.get(f"{PATH}/{PERIOD}", params={"org_id": OTHER_ORG}).json() == []


# ── SECURITY: a filter list is a disclosure surface ─────────────────────────────────────────────

def test_market_options_are_scoped_to_the_caller_not_just_the_org(wired, api_client, monkeypatch):
    """A market-restricted manager must not read the estate's market names off a filter dropdown,
    and an option they cannot bind is a dead filter besides. The row read was already keyset-filtered
    (`in_keyset`); the OPTION LIST is the part a reviewer has to remember, so it is pinned here."""
    wired.seed("storeops.stores", [
        {"org_id": ORG, "store_code": "B-1115", "address": "123 Main St", "market": "LI",
         "is_active": True},
        {"org_id": ORG, "store_code": "B-2000", "address": "7 Queens Blvd", "market": "QNS",
         "is_active": True},
    ])
    wired.seed("commcalc.raw_dlar_store", [_store_row()])
    unrestricted = api_client.get(CATALOG, params={"org_id": ORG}).json()
    assert {"LI", "QNS"} <= set(unrestricted["markets"])
    # the same call from a caller who may see only the LI store
    monkeypatch.setattr("app.modules.storeops.router.scope_keyset",
                        lambda *a, **k: {"B-1115", "123 MAIN ST"})
    scoped = api_client.get(CATALOG, params={"org_id": ORG}).json()
    assert scoped["markets"] == ["LI"], scoped["markets"]
