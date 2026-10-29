"""Unit: the PAGE a receipt prints on, and what the Bill To says (index §POS-11).

Owner, 2026-10-29, on the Verizon RQ-format receipt: *"the receipt for verizon rq format printed
correctly , d not put hte reprint date and time on top woith the word Slae in the middle of the
screen , teh bil to should have the customer address printed"*.

TWO THINGS, and only one of them is in the receipt.

1. **The date/time across the top and the word "Sale" centred over it are the BROWSER's**, not the
   receipt's. Chrome, Edge and Safari stamp a header and footer on anything they print — the date
   and time of the PRINTING on the left, the document's `<title>` in the middle, the URL and the
   page number below — unless the page declares its own `@page` margins. No stylesheet in this
   repository had an `@page` rule, so every receipt this platform has ever printed carried that
   stamp. It makes a receipt look like a printout of a web page, which is exactly what the owner
   said about it. The class is not "the RQ layout has a stray title": it is that **a receipt never
   said what page it prints on**, and it is true of both layouts and every tenant.

2. **The Bill To already prints the customer's address** when their record holds one — that is
   §POS-07, and it works. What was missing is upstream: the till's own quick-add customer form asked
   for a name, a phone and an email and NOT for an address, so a customer created at the register
   could never have one to print. Measured live on 2026-10-29: 1 of 1,636 customer records on the
   reporting tenant holds an address. No renderer change can print a fact the platform never
   collected, so the fix is to collect it — and, until a record has one, to say so to the CASHIER,
   who can fix it, rather than on the customer's receipt, who cannot.
"""
from app.modules.pos import customer_bill_to as BT
from app.modules.pos import sale_receipt as SR
from app.modules.pos.receipt_formats import registry as REG
from app.modules.pos.receipt_formats import render as RENDER

RQ = REG.get("rq")["module"]

TEMPLATE = SR.template_view({"show_customer": True, "show_store_name": True,
                             "footer_text": "Thank you for your business!"})

SNAP = SR.snapshot_view({
    "transactionId": 4605, "date": "10/21/2026, 6:03:24 PM",
    "customerName": "Grand Market Intl Corporation",
    "items": [{"description": "Samsung A9 +", "serial": "", "qty": 1,
               "unit_price": 175.00, "discount": 0, "line_total": 175.00}],
    "subtotal": 175.00, "discountTotal": 0, "taxTotal": 15.53, "total": 190.53,
    "method": "credit_card", "tendered": 190.53, "change": 0,
})

WITH_ADDRESS = {"address_1": "2723 Example Ave", "address_2": "Suite 2",
                "city": "Brooklyn", "state": "NY", "zip": "11229"}
NO_ADDRESS = {"first_name": "Grand", "last_name": "Market Intl Corporation", "phone_primary": "5550100"}


def _pos_html(**kw):
    return SR.html(SNAP, dict(TEMPLATE, layout=SR.LAYOUT_POS_FORMAT),
                   render_document=RENDER.render_html, fmt=RQ, **kw)


# ── 1. the page a receipt prints on ──────────────────────────────────────────────────────────────
def test_a_receipt_declares_its_own_page_so_the_browser_stamps_nothing_on_it():
    """The date/time and the centred title the owner saw are the browser's print header. A page that
    sets its own `@page` margins gets neither."""
    assert "@page" in SR.PAGE_CSS
    assert "margin: 0" in SR.PAGE_CSS


def test_both_layouts_print_on_the_same_page():
    """One fact. The till slip and the tenant's own POS layout are two layouts of one receipt; the
    browser must not stamp either of them."""
    assert SR.PAGE_CSS in _pos_html()
    assert SR.PAGE_CSS in SR.html(SNAP, TEMPLATE)


def test_the_receipt_keeps_a_margin_of_its_own_on_paper():
    """`@page { margin: 0 }` means the PAGE has no margin, not that the ink runs to the paper's edge:
    the receipt must set its own, or the first and last lines print off a real sheet."""
    assert "@media print" in RENDER._CSS
    printed = RENDER._CSS.split("@media print", 1)[1].split("}", 2)[0]
    assert "padding" in printed and "padding: 0" not in printed, printed


def test_the_page_rule_is_written_in_one_place():
    """A second copy is a second answer to 'what page does a receipt print on'."""
    assert "@page" not in RENDER._CSS
    assert RENDER._CSS.count("@page") == 0


# ── 2. the Bill To ───────────────────────────────────────────────────────────────────────────────
def test_the_bill_to_prints_the_customers_address_when_the_record_holds_one():
    """REGRESSION, and the thing the owner asked for: it works, and must keep working."""
    html = _pos_html(customer_lines=BT.lines(WITH_ADDRESS))
    assert "2723 Example Ave Suite 2" in html
    assert "Brooklyn NY 11229" in html


def test_a_record_with_no_address_bills_to_the_name_alone():
    """And says nothing apologetic on the customer's own receipt — the customer cannot fix it."""
    html = _pos_html(customer_lines=BT.lines(NO_ADDRESS))
    assert "Grand Market Intl Corporation" in html
    assert "no address" not in html.lower()


def test_the_screen_is_told_when_the_record_holds_no_address():
    """The cashier CAN fix it, so they are the one told. `has_address` was written for this question
    and is dereferenced rather than re-derived."""
    empty = BT.view(NO_ADDRESS)
    assert empty["on_file"] is False
    assert empty["lines"] == []
    assert "address" in empty["note"].lower()


def test_a_record_that_holds_one_says_nothing_at_all():
    held = BT.view(WITH_ADDRESS)
    assert held["on_file"] is True
    assert held["lines"] == BT.lines(WITH_ADDRESS)
    assert held["note"] == ""


def test_a_walk_in_with_no_record_is_not_an_error():
    assert BT.view(None)["on_file"] is False
    assert BT.view({})["lines"] == []
