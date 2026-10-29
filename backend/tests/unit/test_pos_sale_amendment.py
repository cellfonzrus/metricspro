"""Unit: adding items to a sale that is already rung (index §POS-12).

Owner, 2026-10-29: *"im not able to edit the receipt ot add more tiems to teh same rec3ipt"*.

He is right, and it was never a bug: the register has exactly one write path for a finished sale,
`POST /pos/sales/{id}/void`, and `pos.checkout` / `pos.void_sale` are the only two sale functions in
the database. The Sale Log's item table is display-only. So nothing was broken — the capability did
not exist.

**WHAT THIS IS NOT.** It is not an edit. A tendered sale is a financial record: the customer is
holding a printed receipt for a total, and a tax figure was struck against that total. Rewriting the
row would leave the paper and the record disagreeing, with nothing saying which one happened. So the
original is VOIDED and a NEW transaction replaces it, carrying every original line plus the new
ones — which is what RQ itself does, and what the void path already supports (a void puts serials
back to `in_stock` and non-serialised quantities back on hand, mig 725).

**THE CLASS, not the instance.** Not "the Sale Log needs an edit button". The missing fact is
**"what would it take to ring this sale again"**, and it has to be one answer because three screens
ask it: the Sale Log's button, the endpoint that refuses or allows, and the till that says what it
is about to do. A second copy would be a second opinion on whether a week-old sale may be re-rung.

**ONE CART VOCABULARY.** A re-ring hands the register a cart, and §POS-01 already owns what a cart
line IS (`held_sale.LINE_FIELDS` / `clean_cart`). This module DEREFERENCES that and spells no field
of its own, or a line restored from a sale and a line restored from a suspended cart would be two
different shapes arriving at one checkout.

**Money verbatim, tax re-derived** — the same bargain §POS-01.4 struck, for the same reason. The
agreed unit price, the override it overrode and the discount are what the cashier agreed with the
customer and are restored exactly; the tax RATE is a legal fact about the store, so the register
re-derives it on load rather than carrying last week's rate into today's sale.
"""
import pytest

from app.modules.pos import held_sale as HS
from app.modules.pos import sale_amendment as SA

SALE = {"id": "s-1", "transaction_id": 4605, "status": "completed", "store_code": "B-1115",
        "customer_id": "c-1", "is_activation_sale": False, "total": 190.53,
        "created_at": "2026-10-29T14:00:00Z"}

ITEMS = [
    {"product_id": "p-1", "description": "Samsung A9 +", "product_type": "handset",
     "serial_number": "355123456789012", "qty": 1, "unit_price": 175.00, "list_price": 199.00,
     "cost": 120.00, "discount": 0, "tax_rate": 0.08875, "tax_value": 15.53,
     "extended_price": 190.53},
    {"product_id": "p-2", "description": "Case", "product_type": "accessory",
     "serial_number": "", "qty": 2, "unit_price": 10.00, "list_price": 10.00,
     "cost": 3.00, "discount": 1.00, "tax_rate": 0.08875, "tax_value": 1.60,
     "extended_price": 19.60},
]

NOW = "2026-10-29T19:30:00Z"


# ── 1. what may be re-rung ───────────────────────────────────────────────────────────────────────
def test_a_completed_sale_rung_today_may_be_re_rung():
    assert SA.refusal(SALE, now=NOW, window_days=1) is None


def test_an_already_voided_sale_may_not_be_re_rung_again():
    """Its lines are already back on hand and its money is already reversed. Re-ringing it would
    take the stock a second time."""
    r = SA.refusal(dict(SALE, status="voided"), now=NOW, window_days=1)
    assert r and "voided" in r.lower()
    assert SA.refusal(dict(SALE, voided_at="2026-10-29T15:00:00Z"), now=NOW, window_days=1)


def test_a_sale_outside_the_window_is_refused_and_says_how_old_it_is():
    """A void is stamped when it happens, so voiding a sale from last week removes its money from
    LAST WEEK's POS day and puts the replacement in today's. A figure somebody has already read
    changes underneath them, which is why there is a window at all."""
    old = dict(SALE, created_at="2026-10-20T14:00:00Z")
    r = SA.refusal(old, now=NOW, window_days=1)
    assert r, "a nine-day-old sale must be refused"
    assert "9" in r or "nine" in r.lower(), r


def test_the_window_is_the_callers_to_declare_and_zero_means_same_day_only():
    """RULE TWO: no tenant decides this in code. The endpoint reads the declared setting and passes
    it; this module holds no default of its own beyond the house one."""
    yesterday = dict(SALE, created_at="2026-10-28T14:00:00Z")
    assert SA.refusal(yesterday, now=NOW, window_days=0) is not None
    assert SA.refusal(yesterday, now=NOW, window_days=1) is None
    assert SA.refusal(yesterday, now=NOW, window_days=30) is None


def test_a_sale_with_no_date_is_refused_rather_than_assumed_recent():
    """An unreadable date is not evidence that the sale is young."""
    assert SA.refusal(dict(SALE, created_at=None), now=NOW, window_days=30) is not None
    assert SA.refusal(dict(SALE, created_at="not a date"), now=NOW, window_days=30) is not None


def test_a_missing_sale_is_refused_not_crashed():
    assert SA.refusal(None, now=NOW, window_days=1) is not None
    assert SA.refusal({}, now=NOW, window_days=1) is not None


# ── 2. the cart it hands back ────────────────────────────────────────────────────────────────────
def test_every_line_of_the_sale_comes_back_as_a_cart_line():
    cart = SA.cart_from(ITEMS)
    assert len(cart) == 2
    assert [l["product_id"] for l in cart] == ["p-1", "p-2"]
    assert cart[1]["qty"] == 2


def test_the_money_the_cashier_agreed_comes_back_verbatim():
    """The override, the discount and the price the customer was charged. Re-deriving these is how a
    re-ring quietly becomes a price increase."""
    cart = SA.cart_from(ITEMS)
    assert cart[0]["unit_price"] == 175.00
    assert cart[0]["list_price"] == 199.00      # the override stays visible as list <> unit
    assert cart[1]["discount"] == 1.00
    assert cart[0]["serial_number"] == "355123456789012"


def test_the_tax_rate_is_not_carried_into_the_new_sale():
    """§POS-01.4's bargain, for the same reason: a tax rate is a legal fact about the store, so the
    register re-derives it. Carrying it would re-ring last week's rate today."""
    cart = SA.cart_from(ITEMS)
    for line in cart:
        assert "tax_rate" not in line
        assert "tax_value" not in line


def test_the_cart_is_the_one_cart_vocabulary_and_invents_no_field_of_its_own():
    """§POS-01 owns what a cart line is. A field here that `held_sale` does not know would arrive at
    checkout as a shape only one of the two restore paths produces."""
    cart = SA.cart_from(ITEMS)
    for line in cart:
        assert set(line) <= set(HS.LINE_FIELDS), set(line) - set(HS.LINE_FIELDS)
    # and it really is that module doing the cleaning, not a copy of its rules
    assert HS.clean_cart(cart) == cart


def test_a_sale_with_no_items_is_refused_rather_than_re_rung_empty():
    with pytest.raises(SA.AmendmentError):
        SA.cart_from([])


# ── 3. the context, and what the till is told ────────────────────────────────────────────────────
def test_the_customer_and_the_activation_flag_travel_with_the_cart():
    """Re-ringing without the customer would bill the replacement to a walk-in, and the §POS-07
    address would vanish off the new receipt."""
    ctx = SA.context_from(SALE)
    assert ctx["customer_id"] == "c-1"
    assert ctx["is_activation_sale"] is False
    assert set(ctx) <= set(HS.CONTEXT_FIELDS)


def test_the_plan_says_what_is_about_to_happen_naming_the_transaction():
    p = SA.plan(SALE, ITEMS, now=NOW, window_days=1)
    assert p["allowed"] is True
    assert p["reason"] is None
    assert len(p["cart"]) == 2
    assert "4605" in p["sentence"]
    assert "void" in p["sentence"].lower()      # the cashier is told the old one dies
    assert "new" in p["sentence"].lower()       # and that the number changes


def test_a_refused_plan_carries_the_reason_and_no_cart():
    p = SA.plan(dict(SALE, status="voided"), ITEMS, now=NOW, window_days=1)
    assert p["allowed"] is False
    assert p["reason"]
    assert p["cart"] == []
    assert p["reason"] in p["sentence"]


def test_the_sentence_never_promises_the_same_receipt_number():
    """The owner asked for "the same receipt". He cannot have it — the old number's receipt is
    already printed and in the customer's hand. Saying so at the till is the whole point."""
    s = SA.plan(SALE, ITEMS, now=NOW, window_days=1)["sentence"]
    assert "same" not in s.lower() or "same number" not in s.lower()
