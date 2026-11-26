"""Integration: GET /supply/compare states what ONE item costs, through the REAL app (index §36).

Real routing, real middleware, the real endpoint body — over the in-memory database double, with
conftest blocking every outbound socket (docs/TESTING.md: "integration" here means the app, not a
real database — there is no staging Supabase).

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT: that the per-item cost and its BASIS actually survive
the HTTP boundary and reach the screen. The unit tests prove `pricing_core.unit_basis` is right; this
proves the route does not drop it, re-derive it, or hand the screen a listed price it will label
"each". That is the shape of the 2026-11-25 defect: the logic layer knew the answer and the surface
printed its own.

The live row shapes are reproduced from the UPS Store tenant's 2026-11-25 snapshot:
  · Supply One quotes "$5.4000 per EA (10/BDL)" — the vendor already divided.
  · Supply One quotes $76.20 for a bundle of 15 — WE divide, to $5.08.
  · My Box Choice quotes $4.81 and says nothing about a pack — the per-item cost is UNKNOWN.
The last two are the same product, and the platform used to call that "$71.39 cheaper (94%)".
"""
import pytest

ORG = "00000000-0000-0000-0000-000000000001"
PATH = "/api/v1/supply/compare"
V1, V2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"

VENDORS = [
    {"id": V1, "org_id": ORG, "name": "Vendor One", "is_active": True, "is_price_source": True,
     "portal_url": "", "catalog_urls": [], "portal_config": {}, "data_source_id": None,
     "free_shipping_threshold": 500, "shipping_fee_below_threshold": 150,
     "delivery_days_min": 2, "delivery_days_max": 3, "contact_name": None, "email": None,
     "phone": None, "terms": "COD", "notes": None, "created_at": None, "updated_at": None},
    {"id": V2, "org_id": ORG, "name": "Vendor Two", "is_active": True, "is_price_source": True,
     "portal_url": "", "catalog_urls": [], "portal_config": {}, "data_source_id": None,
     "free_shipping_threshold": 750, "shipping_fee_below_threshold": None,
     "delivery_days_min": 2, "delivery_days_max": 5, "contact_name": None, "email": None,
     "phone": None, "terms": None, "notes": None, "created_at": None, "updated_at": None},
]


def _row(id_, vendor, name, price, *, pack=None, desc="", seen="2026-11-25T11:00:00+00:00"):
    return {"id": id_, "org_id": ORG, "vendor_id": vendor, "run_id": "run-" + vendor[-1],
            "source": "portal", "item_key": "s:" + id_, "sku": id_, "name": name, "price": price,
            "list_price": None, "pack_qty": pack, "availability": "unknown", "stock_qty": None,
            "stock_text": None, "url": "", "description": desc or name, "seen_at": seen,
            "min_order_qty": None, "order_multiple": None}


CATALOG = [
    # the vendor states the per-item price itself; the bundle is only how it is SOLD
    _row("A1", V1, "CTN RSC 10X10X48 200K (10/BDL)", 5.40,
         desc="CTN RSC 10X10X48 200K (10/BDL) $5.4000 per EA Add to Cart"),
    # a pack price we divide, against a price with no pack stated at all
    _row("A2", V1, "24X18X18 200LB UPS BRANDED BOX", 76.20, pack=15),
    _row("B2", V2, "24X18X18 200LB UPS BRANDED MULTI DEPTH BOX", 4.81),
    # both state a pack: a clean per-item comparison
    _row("A3", V1, "BUBBLE MAILER GOLDEN S/S 8.5 X12 #2 (100/CS)", 31.00, pack=100),
    _row("B3", V2, "NO. 2 8.5X12 JIFFY PADDED MAILER (100/CS)", 63.31, pack=100),
]


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    fake_supabase.seed("commcalc.po_vendor", VENDORS)
    fake_supabase.seed("commcalc.vendor_catalog_price", CATALOG)
    fake_supabase.seed("commcalc.supply_favorite", [])
    monkeypatch.setattr("app.modules.supply.router.sb", lambda: fake_supabase)
    return fake_supabase


def _rows(api_client):
    r = api_client.get(PATH, params={"org_id": ORG})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["migrated"] is True, body
    return {row["product"]: row for row in body["rows"]}, body


def test_a_stated_per_each_price_arrives_as_a_known_per_item_cost(wired, api_client):
    rows, _ = _rows(api_client)
    row = next(r for k, r in rows.items() if "10X10X48" in k)
    offer = row["offers"][V1]
    assert offer["unit_price"] == 5.40
    assert "per item" in offer["unit_basis"]


def test_a_pack_price_reaches_the_screen_already_divided(wired, api_client):
    """The owner's ask, over HTTP: the user never has to divide $76.20 by 15 themselves."""
    rows, _ = _rows(api_client)
    row = next(r for k, r in rows.items() if "24X18X18" in k)
    assert row["offers"][V1]["unit_price"] == 5.08


def test_an_unstated_pack_is_sent_as_unknown_not_as_each(wired, api_client):
    rows, _ = _rows(api_client)
    row = next(r for k, r in rows.items() if "24X18X18" in k)
    offer = row["offers"][V2]
    assert offer["unit_price"] is None
    assert "not stated" in offer["unit_basis"]


def test_a_case_against_a_single_crosses_the_wire_as_a_gap_not_a_saving(wired, api_client):
    """The live 2026-11-25 regression: $5.08 vs $4.81 must never be served as "$71.39 cheaper"."""
    rows, _ = _rows(api_client)
    row = next(r for k, r in rows.items() if "24X18X18" in k)
    assert row["comparable"] is False
    assert row["savings"] is None and row["savings_pct"] is None
    assert row["best_vendor"] is None
    assert row["gap"] == 71.39 and "pack" in row["note"]
    assert row["cart_basis"] == "packs"


def test_two_pack_prices_compare_per_item_over_the_wire(wired, api_client):
    rows, _ = _rows(api_client)
    row = next(r for k, r in rows.items() if "MAILER" in k.upper())
    assert row["comparable"] is True
    assert row["cart_basis"] == "units"
    assert row["best_vendor"] == V1
    assert row["best_price"] == 0.31
    assert row["savings"] == pytest.approx(0.3231, abs=1e-6)
