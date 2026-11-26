"""A BULK PRICE IS DIVIDED FOR THE USER, AND AN UNKNOWN ONE IS NEVER CALLED "EACH".

Owner, 2026-11-25: *"if it shows bulk items with the number of items next to it it should divide to
get the cost per item, which the user can see and do by naked eye, the system should do it for them"*.

The class of defect, named: **the platform knows three different things about a price and stored only
two.** A vendor either states a per-item price ("$5.40 per EA (10/BDL)"), or states a pack and leaves
the division to us ("$76.20", pack of 15), or says nothing at all. The third was collapsed into the
first — `unit_price` returned the listed price unchanged and the surfaces printed the word "each"
beside it — so a case price sat against a single-unit price and the difference was reported as a
saving. Live on 2026-11-25: a 24x18x18 UPS box read as "$71.39 cheaper (94%)" when the two vendors
are 27 cents apart.

So the fix is not a rule about boxes. It is: **one home answers "what does ONE of these cost, and how
do we know", with three answers and never two**, and every surface dereferences it.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest

from app.modules.supply import pricing_core as core
from app.modules.supply import ordering_logic as L


# ── 1. THE THREE ANSWERS ────────────────────────────────────────────────────────────────────────
def test_a_stated_per_each_price_is_the_per_item_cost():
    """"$5.4000 per EA (10/BDL)" — the vendor already divided. One item costs $5.40, and we KNOW it."""
    p = {"price": 5.40, "pack_qty": None, "order_multiple": 10,
         "name": "CTN RSC 10X10X48 200K (10/BDL)",
         "description": "CTN RSC 10X10X48 200K (10/BDL) $5.4000 per EA Add to Cart"}
    per, basis = core.unit_basis(p)
    assert per == 5.40
    assert basis == core.UNIT_STATED


def test_a_pack_price_is_divided_so_the_user_does_not_have_to():
    """"$76.20" for a bundle of 15 — the system does the division, not the user's eye."""
    p = {"price": 76.20, "pack_qty": 15, "name": "24X18X18 200LB UPS BRANDED BOX"}
    per, basis = core.unit_basis(p)
    assert per == 5.08
    assert basis == core.UNIT_DIVIDED


def test_a_price_with_no_pack_anywhere_is_NOT_called_each():
    """The vendor said nothing. The per-item cost is UNKNOWN — never the listed price wearing the
    word "each", which is what let a case be compared against a single."""
    p = {"price": 4.81, "pack_qty": None, "name": "24X18X18 200LB UPS BRANDED MULTI DEPTH BOX",
         "description": "24X18X18 200LB UPS BRANDED MULTI DEPTH BOX"}
    per, basis = core.unit_basis(p)
    assert per is None
    assert basis == core.UNIT_UNKNOWN


def test_the_bundle_count_in_the_name_is_read_wherever_it_is_written():
    """(10/BDL), (100/CS), 25/bundle, 6/PK — the count sits in the product name, which is exactly
    where the owner said the user can see it by eye."""
    for text, want in [("CTN RSC 6X6X48 200K (10/BDL)", 10),
                       ("BUBBLE MAILER GOLDEN S/S 8.5 X12 #2 (100/CS)", 100),
                       ("Pack Rite - 2\"x40yds Tan Tape - 6/PK", 6),
                       ("CTN RSC 16X12X12 200K (25/BDL)", 25)]:
        assert core.parse_pack(text) == want, text


# ── 2. unit_price IS DERIVED, NOT A SECOND COPY ─────────────────────────────────────────────────
def test_unit_price_dereferences_unit_basis():
    """One fact, one home. `unit_price` keeps its old answer for callers that only rank, but it
    must come FROM `unit_basis` so the two can never drift apart."""
    for p in [{"price": 76.20, "pack_qty": 15},
              {"price": 5.40, "pack_qty": None, "description": "$5.4000 per EA"},
              {"price": 4.81, "pack_qty": None, "name": "plain box"}]:
        per, _ = core.unit_basis(p)
        assert core.unit_price(p) == (per if per is not None else p["price"])


# ── 3. A MIXED-BASIS ROW IS NOT A SAVING ────────────────────────────────────────────────────────
def _group(a, b, score=0.9):
    return [{"members": {"v1": dict(a, vendor="v1"), "v2": dict(b, vendor="v2")},
             "score": score, "method": "same size"}]


def test_a_case_against_a_single_is_refused_as_a_saving():
    """The live 2026-11-25 defect, as a regression. Supply One quotes a bundle of 15 at $76.20
    ($5.08 each); My Box Choice quotes $4.81 and says nothing about a pack. The honest answer is
    "we cannot compare these yet", NOT "$71.39 cheaper"."""
    rows, _ = core.comparison_rows(
        _group({"price": 76.20, "pack_qty": 15, "name": "24X18X18 200LB UPS BOX", "availability": "unknown"},
               {"price": 4.81, "pack_qty": None, "name": "24X18X18 200LB UPS BRANDED MULTI DEPTH BOX",
                "availability": "unknown"}),
        ["v1", "v2"])
    r = rows[0]
    assert r["comparable"] is False
    assert r["savings"] is None and r["savings_pct"] is None
    assert "pack" in (r["note"] or "").lower()


def test_two_stated_per_item_prices_DO_compare_per_item():
    """Both vendors state what one costs — one from a pack, one per each. That is comparable, and
    the saving is the per-item difference, not the listed one."""
    rows, _ = core.comparison_rows(
        _group({"price": 76.20, "pack_qty": 15, "name": "box", "availability": "unknown"},
               {"price": 60.00, "pack_qty": 10, "name": "box", "availability": "unknown"}),
        ["v1", "v2"])
    r = rows[0]
    assert r["comparable"] is True
    assert r["basis"] == core.UNIT_DIVIDED or "item" in r["basis"]
    assert r["best_price"] == 5.08 and r["savings"] == 0.92


def test_neither_vendor_states_a_pack_still_compares_but_says_so():
    """Two plain listed prices are the best available answer and are allowed to compare — but the
    row must say the basis is a listed price, so nobody reads it as a per-item saving."""
    rows, _ = core.comparison_rows(
        _group({"price": 12.71, "pack_qty": None, "name": "20x10x50 box", "availability": "unknown"},
               {"price": 20.61, "pack_qty": None, "name": "20x10x50 box", "availability": "unknown"}),
        ["v1", "v2"])
    r = rows[0]
    assert r["comparable"] is True
    assert r["savings"] == 7.90
    assert "listed" in r["basis"].lower()


# ── 4. THE OFFER CARRIES THE BASIS TO EVERY SURFACE ─────────────────────────────────────────────
def test_an_offer_carries_its_per_item_cost_and_how_it_was_reached():
    """A surface must not have to re-derive this (the Price Compare screen had its own copy, and
    the PDF had another). The offer states it."""
    o = L.offer_from_row({"vendor_id": "v1", "price": 5.40, "pack_qty": None,
                          "name": "CTN RSC 10X10X48 200K (10/BDL)",
                          "description": "CTN RSC 10X10X48 200K (10/BDL) $5.4000 per EA"})
    assert o["unit_price"] == 5.40
    assert o["unit_basis"] == core.UNIT_STATED
    assert o["order_multiple"] == 10          # the bundle is how it is SOLD, kept as the step


def test_an_unknown_offer_says_unknown_rather_than_each():
    o = L.offer_from_row({"vendor_id": "v2", "price": 4.81, "pack_qty": None,
                          "name": "24X18X18 200LB UPS BRANDED MULTI DEPTH BOX"})
    assert o["unit_price"] is None
    assert o["unit_basis"] == core.UNIT_UNKNOWN


# ── 5. OUR OWN ORDER HISTORY IS NOT A PRICE LIST ────────────────────────────────────────────────
# Owner, same message: *"teh pdf also shows our chagin invoices"*.
#
# Measured on the live 2026-11-25 snapshot: 81 of one vendor's 205 "catalog" rows (40%) were read off
# our OWN past orders — ordhist.htm (24), ordhistdet.htm (21), ordhistlist.htm (17),
# ordhistlistdetail.htm (11), cathistsub.htm (6), itmhistsub.htm (1), ordhistlistmth.htm (1). Only
# 124 came from the product page, itemdet.htm. An invoice line's price is what we PAID once, not
# what the vendor charges today, so those rows were quietly ageing the comparison.
#
# The class is not "this vendor wanders": it is that the reader had a rule for pages that could
# ACT (SAFE_SKIP — cart, checkout, logout) and no rule for pages that are simply NOT A PRICE LIST.
# That is a house POLICY about what a catalog is, identical for every vendor and every tenant, so it
# belongs beside SAFE_SKIP — not in one vendor's config, which is where a patch would have gone.
from app.modules.supply import catalog_scrape as scrape  # noqa: E402


def test_our_own_order_history_is_not_followed_as_a_catalog():
    for url in ["https://v.example/shopfront3.0/ordhist.htm",
                "https://v.example/shopfront3.0/ordhistdet.htm?ponbr=12",
                "https://v.example/shopfront3.0/ordhistlist.htm?itmnbr=X&begdate=01/01/26",
                "https://v.example/shopfront3.0/ordhistlistdetail.htm",
                "https://v.example/shopfront3.0/ordhistlistmth.htm",
                "https://v.example/shopfront3.0/itmhistsub.htm",
                "https://v.example/shopfront3.0/cathistsub.htm",
                "https://v.example/account/invoices",
                "https://v.example/my-orders",
                "https://v.example/index.php?main_page=account_history_info"]:
        assert scrape.is_not_a_price_list(url), url


def test_the_real_product_pages_are_still_followed():
    for url in ["https://v.example/shopfront3.0/itemdet.htm?mbritm=UPSCT10X10X48",
                "https://v.example/shopfront3.0/subcatlist4.htm?cat=8002",
                "https://v.example/index.php?main_page=product_info&products_id=818",
                "https://v.example/index.php?main_page=products_all",
                "https://v.example/catalog/boxes"]:
        assert not scrape.is_not_a_price_list(url), url


def test_a_vendor_may_declare_that_its_catalog_lives_there_anyway():
    """Config, never code: a vendor whose real listings sit under such a path says so in its own
    portal_config rather than anyone editing the house policy for it."""
    v = {"key": "k", "start_urls": ["https://v.example/shopfront3.0/subcatlist4.htm"]}
    s = scrape.VendorScraper.__new__(scrape.VendorScraper)
    s.v = v
    hosts = {"v.example"}
    assert s._allowed("https://v.example/shopfront3.0/ordhist.htm", hosts) is False
    s.v = dict(v, allow_account_pages=True)
    assert s._allowed("https://v.example/shopfront3.0/ordhist.htm", hosts) is True


# ── 6. THE COUNT WE DIVIDE BY IS A COUNT, NOT A PRICE OR A SKU OR A WEIGHT ──────────────────────
# Dividing automatically is only a kindness if the divisor is right. Found on the live 2026-11-25
# snapshot the moment the division became visible:
#
#   "… (12 RL/CS) $7.8900 per CS"              → read as a pack of 8,900 — the PRICE's own decimals
#   "Item #: 2436NEWS … $66.6400 per CS"       → read as a pack of 6,400 — same cause, no count at all
#   "Item #: UPS819 Carton Sealing Tape, …"    → read as a pack of 819 — digits inside the ITEM NUMBER
#   "24X36 NEWSPRINT PAPER SHEETS 25LB BAG"    → read as a pack of 25 — that is the bag's WEIGHT
#
# Three sources of a number that is not a count: a money amount, a part number, a unit of weight.
# None is vendor-specific and all three are in the one home, so every caller is wrong together and
# right together.
LIVE_PACKS = [
    ("Item #: TNBEC305 Cust Item #: SW-EXC-5-115 STRETCH FILM 5X1000FT 80GA [45058112BPQW] "
     "(12 RL/CS) $7.8900 per CS Add to Cart", 12),
    ('Item #: 2436NEWS Cust Item #: CU-24X36-NEWS Newsprint, 24"x36", Unprinted $66.6400 per CS', None),
    ('Item #: UPS819 Carton Sealing Tape, 2"x40YD, UPS, With Dispenser $20.0000 per CS', None),
    ("24X36 NEWSPRINT PAPER SHEETS 25LB BAG 25# BAG", None),
    ("Item #: JL2SS BUBBLE MAILER GOLDEN S/S 8.5 X12 #2 (100/CS) $31.0000 per CS", 100),
    ("CTN RSC 10X10X48 200K (10/BDL) $5.4000 per EA", 10),
    ('Pack Rite - 2"x40yds Tan Tape in a dispenser with cutter - 6/PK', 6),
    ("36 rolls/case", 36),
    ("pack of 25", 25),
    ("100 ct", 100),
]


@pytest.mark.parametrize("text,want", LIVE_PACKS)
def test_the_divisor_is_a_count_and_nothing_else(text, want):
    assert core.parse_pack(text) == want


def test_a_money_amount_never_supplies_the_pack():
    """"$7.8900 per CS" is a PRICE per case, not a case of 8,900."""
    assert core.parse_pack("$7.8900 per CS") is None
    assert core.parse_pack("$66.6400 per CS") is None
    # …and the same sentence with a real count still reads it
    assert core.parse_pack("(12 RL/CS) $7.8900 per CS") == 12


def test_digits_glued_to_letters_are_a_part_number_not_a_count():
    assert core.parse_pack("UPS819 Carton Sealing Tape") is None
    assert core.parse_pack("SKU12PK Widget") is None
    assert core.parse_pack("Widget 12 PK") == 12          # standing alone, it IS a count


def test_a_weight_is_not_a_count():
    for text in ["25LB BAG", "25# BAG", "50 lb case", "80GA roll"]:
        assert core.parse_pack(text) is None, text
