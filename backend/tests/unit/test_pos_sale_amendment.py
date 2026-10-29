"""Unit: editing a sale that is already rung, keeping its transaction number (index §POS-12).

Owner, 2026-10-29: *"im not able to edit the receipt ot add more tiems to teh same rec3ipt"*, and
then, picking between re-ringing, a second sale and editing in place: **edit in place, same number**.

He was right that it could not be done, and it was never a bug: the register had exactly one write
path for a finished sale (`POST /pos/sales/{id}/void`), `pos.checkout` and `pos.void_sale` were the
only two sale functions in the database (mig 725), and the Sale Log's item table was display-only.

**WHAT MAKES AN IN-PLACE EDIT SAFE ENOUGH TO LIVE WITH.** Rewriting a tendered record is the risky
option — I said so and the owner chose it, so it ships with the thing that makes it answerable:
**nothing is overwritten.** Every edit snapshots the sale as it stood, so the version the customer's
printed receipt was struck from is still on file, and the sale carries a revision number that says
how many times it has moved. A figure that changed can always be traced to the edit that changed it.

**THE CLASS, NOT THE INSTANCE.** Not "the Sale Log needs an edit button". Three facts had no home,
and all three are needed by more than one caller:

1. **May this sale be edited at all** — asked by the Sale Log offering the button, by the endpoint
   allowing or refusing, and by the till before it saves.
2. **What a line's money comes to** — `extended_price` and `tax_value` were derived in the browser
   only, and `pos.checkout` stores whatever it is handed. An EDIT is precisely the case where those
   figures must be re-derived rather than trusted, because changing them is the point.
3. **What changed between two versions of a sale** — needed by the audit row, by the screen that
   confirms the edit, and by the reprint that says which revision it is.

**ONE CART VOCABULARY.** §POS-01 already owns what a cart line IS (`held_sale.LINE_FIELDS`). This
module spells no line field of its own, so a line loaded for editing and a line restored from a
suspended cart are the same shape arriving at one write.

**TAX: each line keeps its OWN rate.** Unlike a suspended cart (§POS-01.4, which re-derives the rate
on restore because the sale has not happened yet), an edit touches a sale that legally DID happen, on
its date, at the rate in force then. So an existing line keeps its stored rate and only a newly added
line takes the store's current one. Re-deriving every rate would restate the tax on a sale whose
tax was already struck and, for an older sale, remitted.
"""
import pytest

from app.modules.pos import held_sale as HS
from app.modules.pos import sale_amendment as SA

SALE = {"id": "s-1", "transaction_id": 4605, "status": "completed", "store_code": "B-1115",
        "customer_id": "c-1", "is_activation_sale": False, "revision": 1,
        "subtotal": 184.00, "discount_total": 2.00, "tax_total": 17.13, "total": 201.13,
        "created_at": "2026-10-29T14:00:00Z"}

# The two lines below are the real shape and the real figures mig 725 stores — the arithmetic in
# `line_money` was DERIVED from them (both reproduce to the cent) rather than invented.
PHONE = {"product_id": "p-1", "description": "Samsung A9 +", "product_type": "handset",
         "serial_number": "355123456789012", "qty": 1, "unit_price": 175.00, "list_price": 199.00,
         "cost": 120.00, "discount": 0, "tax_rate": 0.08875, "tax_value": 15.53,
         "extended_price": 190.53}
CASE = {"product_id": "p-2", "description": "Case", "product_type": "accessory",
        "serial_number": "", "qty": 2, "unit_price": 10.00, "list_price": 10.00,
        "cost": 3.00, "discount": 1.00, "tax_rate": 0.08875, "tax_value": 1.60,
        "extended_price": 19.60}
ITEMS = [PHONE, CASE]

NOW = "2026-10-29T19:30:00Z"


# ── 1. what may be edited ────────────────────────────────────────────────────────────────────────
def test_a_completed_sale_rung_today_may_be_edited():
    assert SA.refusal(SALE, now=NOW, window_days=1) is None


def test_a_voided_sale_may_not_be_edited():
    """Its items are already back on hand and its money already reversed. Editing it would take the
    stock a second time against a sale that no longer exists."""
    r = SA.refusal(dict(SALE, status="voided"), now=NOW, window_days=1)
    assert r and "voided" in r.lower()
    assert SA.refusal(dict(SALE, voided_at="2026-10-29T15:00:00Z"), now=NOW, window_days=1)


def test_a_sale_outside_the_window_is_refused_and_says_how_old_it_is():
    """An edit changes a day's takings. Doing that to a day somebody has already reported is the
    one thing a cashier must not be able to do by accident."""
    r = SA.refusal(dict(SALE, created_at="2026-10-20T14:00:00Z"), now=NOW, window_days=1)
    assert r, "a nine-day-old sale must be refused"
    assert "9" in r, r


def test_the_window_is_declared_not_decided_here():
    """RULE TWO: no tenant's answer lives in code. The endpoint reads the setting and passes it."""
    yesterday = dict(SALE, created_at="2026-10-28T14:00:00Z")
    assert SA.refusal(yesterday, now=NOW, window_days=0) is not None
    assert SA.refusal(yesterday, now=NOW, window_days=1) is None
    assert SA.refusal(yesterday, now=NOW, window_days=30) is None
    assert SA.declared_window(None) == SA.HOUSE_WINDOW_DAYS
    assert SA.declared_window("not a number") == SA.HOUSE_WINDOW_DAYS


def test_an_unreadable_date_is_refused_rather_than_assumed_recent():
    assert SA.refusal(dict(SALE, created_at=None), now=NOW, window_days=30) is not None
    assert SA.refusal(dict(SALE, created_at="not a date"), now=NOW, window_days=30) is not None


def test_a_missing_sale_is_refused_not_crashed():
    assert SA.refusal(None, now=NOW, window_days=1) is not None
    assert SA.refusal({}, now=NOW, window_days=1) is not None


# ── 2. what a line's money comes to (the one home) ───────────────────────────────────────────────
def test_a_lines_figures_reproduce_what_the_register_already_stored():
    """THE DERIVATION. Both live lines come back to the cent, which is what makes this a one home
    for the arithmetic rather than a second opinion on it."""
    assert SA.line_money(PHONE)["tax_value"] == 15.53
    assert SA.line_money(PHONE)["extended_price"] == 190.53
    assert SA.line_money(CASE)["tax_value"] == 1.60
    assert SA.line_money(CASE)["extended_price"] == 19.60


def test_the_figures_are_re_derived_and_never_taken_from_the_body():
    """The whole point of an edit is that the figures move. A client-supplied total is the one thing
    that must not survive it."""
    lied = dict(CASE, extended_price=0.01, tax_value=0.00)
    assert SA.line_money(lied)["extended_price"] == 19.60
    assert SA.line_money(lied)["tax_value"] == 1.60


def test_changing_a_quantity_moves_the_line_and_the_tax_with_it():
    assert SA.line_money(dict(CASE, qty=3))["extended_price"] == 29.40
    assert SA.line_money(dict(CASE, qty=3))["tax_value"] == 2.40


def test_a_line_with_no_tax_rate_is_priced_without_tax():
    out = SA.line_money(dict(CASE, tax_rate=0))
    assert out["tax_value"] == 0.00
    assert out["extended_price"] == 18.00


def test_the_sales_totals_are_the_sum_of_its_lines():
    t = SA.totals([PHONE, CASE])
    assert t["subtotal"] == 193.00          # (175-0)*1 + (10-1)*2, tax-exclusive, net of discount
    assert t["discount_total"] == 2.00      # 1.00 per unit x 2
    assert t["tax_total"] == 17.13          # 15.53 + 1.60
    assert t["total"] == 210.13             # subtotal + tax
    assert round(t["subtotal"] + t["tax_total"], 2) == t["total"]


def test_an_empty_sale_is_refused_rather_than_saved_as_zero():
    """Emptying a sale is a void, and the void path is the one that puts stock back and leaves the
    record. Letting an edit do it would be a second, silent way to cancel a sale."""
    with pytest.raises(SA.AmendmentError):
        SA.priced([])


# ── 3. the lines loaded for editing ──────────────────────────────────────────────────────────────
def test_every_line_of_the_sale_loads_for_editing():
    cart = SA.cart_from(ITEMS)
    assert [l["product_id"] for l in cart] == ["p-1", "p-2"]
    assert cart[1]["qty"] == 2


def test_the_money_the_cashier_agreed_loads_verbatim():
    cart = SA.cart_from(ITEMS)
    assert cart[0]["unit_price"] == 175.00
    assert cart[0]["list_price"] == 199.00      # the override stays visible as list <> unit
    assert cart[1]["discount"] == 1.00
    assert cart[0]["serial_number"] == "355123456789012"


def test_an_existing_line_keeps_the_tax_rate_the_sale_was_struck_at():
    """Unlike a suspended cart, this sale HAPPENED, on its date, at the rate in force then. Only a
    newly added line takes the store's current rate."""
    assert SA.cart_from(ITEMS)[0]["tax_rate"] == 0.08875


def test_the_cart_invents_no_field_the_register_does_not_know():
    cart = SA.cart_from(ITEMS)
    for line in cart:
        assert set(line) <= set(HS.LINE_FIELDS), set(line) - set(HS.LINE_FIELDS)
    assert HS.clean_cart(cart) == cart


# ── 4. what changed, for the audit row and the confirmation ──────────────────────────────────────
def test_an_added_line_is_reported_as_added():
    d = SA.diff(ITEMS, ITEMS + [dict(CASE, product_id="p-3", description="Charger")])
    assert [l["description"] for l in d["added"]] == ["Charger"]
    assert d["removed"] == [] and d["changed"] == []


def test_a_removed_line_is_reported_as_removed():
    d = SA.diff(ITEMS, [PHONE])
    assert [l["description"] for l in d["removed"]] == ["Case"]
    assert d["added"] == []


def test_a_quantity_change_is_reported_as_a_change_not_an_add_and_a_remove():
    d = SA.diff(ITEMS, [PHONE, dict(CASE, qty=5)])
    assert d["added"] == [] and d["removed"] == []
    assert len(d["changed"]) == 1
    assert d["changed"][0]["was"]["qty"] == 2
    assert d["changed"][0]["now"]["qty"] == 5


def test_a_serialised_unit_is_identified_by_its_serial_not_its_product():
    """Two handsets of one model are one product and two units. Keyed on the product they would
    cancel out, and the swapped IMEI would never reach the stock move."""
    swapped = [dict(PHONE, serial_number="999888777666555"), CASE]
    d = SA.diff(ITEMS, swapped)
    assert len(d["added"]) == 1 and len(d["removed"]) == 1
    assert d["added"][0]["serial_number"] == "999888777666555"
    assert d["removed"][0]["serial_number"] == "355123456789012"


def test_an_unchanged_sale_reports_no_change_at_all():
    d = SA.diff(ITEMS, ITEMS)
    assert (d["added"], d["removed"], d["changed"]) == ([], [], [])
    assert d["any"] is False


def test_the_change_is_summarised_in_one_line_for_the_audit_row():
    d = SA.diff(ITEMS, [PHONE, dict(CASE, qty=5), dict(CASE, product_id="p-3")])
    assert d["any"] is True
    assert "1 line added" in d["summary"]
    assert "1 changed" in d["summary"]


# ── 5. the snapshot that makes the edit answerable ───────────────────────────────────────────────
def test_the_snapshot_holds_the_sale_as_it_stood_with_its_own_figures():
    """This is what the customer's printed receipt was struck from. If it is not kept, an edited
    sale is indistinguishable from a sale that was always this way."""
    snap = SA.snapshot(SALE, ITEMS)
    assert snap["revision"] == 1
    assert snap["transaction_id"] == 4605
    assert snap["total"] == 201.13              # the figures AS STORED, not re-derived
    assert len(snap["items"]) == 2
    assert snap["items"][0]["extended_price"] == 190.53


def test_the_first_edit_of_an_older_sale_counts_as_revision_one():
    """`revision` arrives null on every sale rung before the column existed. Reading that as "no
    revisions" would make the first edit overwrite the original with nothing kept."""
    assert SA.snapshot(dict(SALE, revision=None), ITEMS)["revision"] == 1
    assert SA.next_revision(dict(SALE, revision=None)) == 2
    assert SA.next_revision(dict(SALE, revision=3)) == 4


# ── 6. the whole plan ────────────────────────────────────────────────────────────────────────────
def test_the_plan_prices_the_new_lines_and_says_what_moves():
    p = SA.plan(SALE, ITEMS, [PHONE, dict(CASE, qty=5)], now=NOW, window_days=1)
    assert p["allowed"] is True and p["reason"] is None
    # 220.00 net (175 + 9x5), tax 19.52. Tax rounds PER LINE and the rounded figures are summed —
    # 15.53 + 3.99 — because that is what `sale_items.tax_value` stores per line, and a total
    # computed from unrounded tax would not add up to the lines a customer can read off the receipt.
    assert p["totals"]["subtotal"] == 220.00
    assert p["totals"]["tax_total"] == 19.52
    assert p["totals"]["total"] == 239.52
    assert p["revision"] == 2
    assert p["diff"]["changed"]
    assert p["snapshot"]["total"] == 201.13
    assert "4605" in p["sentence"]


def test_the_plan_refuses_an_edit_that_changes_nothing():
    """Saving an unchanged sale would burn a revision and file a snapshot of nothing."""
    p = SA.plan(SALE, ITEMS, ITEMS, now=NOW, window_days=1)
    assert p["allowed"] is False
    assert "no change" in (p["reason"] or "").lower()


def test_a_refused_plan_carries_no_figures_to_write():
    p = SA.plan(dict(SALE, status="voided"), ITEMS, [PHONE], now=NOW, window_days=1)
    assert p["allowed"] is False
    assert p["items"] == [] and p["totals"] == {}
    assert p["reason"] in p["sentence"]


def test_the_plan_states_that_the_receipt_in_the_customers_hand_is_now_stale():
    """The owner asked for the same number, and gets it — which means the paper the customer already
    holds shows the old total. The cashier is the one who can hand them a new one, so the cashier is
    told."""
    s = SA.plan(SALE, ITEMS, [PHONE], now=NOW, window_days=1)["sentence"]
    assert "reprint" in s.lower() or "print" in s.lower(), s
