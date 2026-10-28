"""Unit: the store block a receipt prints — ONE home for every layout (index §POS-10).

THE DEFECT THIS WAS WRITTEN RED FOR (owner, 2026-10-28, on a real emailed receipt):
*"see the rq receipt email to the customer, it does not show the address and looks fake … the till
thermal printer receipt came out ok"*.

THE CLASS, not the instance. "What does a receipt say the shop IS?" had TWO answers:

  · the till slip read the receipt-branding rows — `pos_config_effective.brand`: the company name,
    the address lines, the licence/tax ids — and printed all three;
  · the tenant's own POS layout read a DIFFERENT thing, a `store_lines` list the router assembled
    for it alone out of the `storeops.stores` row.

Two paths answering one question, so of course they drifted: the second one selected a `name`
column that does not exist on `storeops.stores`, the read raised, the router swallowed it, and the
POS-format receipt went out carrying the company name and nothing else — no street, no city, no
licence. The till slip was untouched because it never read that row. The same split hid a second
hole: the legal blocks (the return policy) were handed to the slip and to the printer's byte stream
and NOT to the POS layout, so the emailed copy had no terms on it either.

So the fix is not "select the right columns". It is: one home resolves what the shop IS, every
layout and every medium dereferences it, and the legal blocks reach all of them.
"""
import pytest

from app.modules.pos import receipt_store_block as SB
from app.modules.pos import sale_receipt as SR
from app.modules.pos.receipt_formats import registry as REG

BRAND = {"company_name": "Example Wireless",
         "address_lines": ["2723 Example Ave", "Brooklyn NY 11229"],
         "license_tax_ids": "2126405-DCWP"}

#: A row shaped like the live `storeops.stores` — the columns that table ACTUALLY has. There is no
#: `name` column on it, which is the whole point: a reader that asks for one gets nothing back.
STORE_ROW = {"store_code": "Ave U", "address": "2723 Example Ave Brooklyn", "market": "NY",
             "phone": "718-555-0144"}

TEMPLATE = SR.template_view({"show_store_name": True, "show_customer": True, "show_employee": True,
                             "show_serials": True, "show_discounts": True,
                             "show_tax_breakdown": True, "footer_text": "Thank you for your business!",
                             "layout": SR.LAYOUT_POS_FORMAT})

SNAP = SR.snapshot_view({
    "transactionId": 4605,
    "date": "10/21/2026, 6:03:24 PM",
    "customerName": "Grand Market Intl Corporation",
    "items": [{"description": "Smartphone 128GB", "serial": "358291470012345", "qty": 1,
               "unit_price": 175.00, "discount": 0, "line_total": 175.00}],
    "subtotal": 175.00, "discountTotal": 0, "taxTotal": 15.53, "total": 190.53,
    "method": "credit_card", "tendered": 190.53, "change": 0,
})

RETURN_POLICY = [{"text": "Return Policy-\nNo Cash refunds, Store Credit only.", "signature": False}]

RQ = REG.get("rq")["module"]


def _doc(**kw):
    kw.setdefault("brand", BRAND)
    kw.setdefault("store_row", STORE_ROW)
    return SR.document(SNAP, RQ, TEMPLATE, **kw)


# ── the one home ─────────────────────────────────────────────────────────────────────────────────
def test_the_store_row_is_read_through_columns_that_exist():
    """The read that silently failed. `storeops.stores` has no `name`; asking for one is a 42703 and
    a receipt with no address. The columns are declared ONCE here and the router dereferences them."""
    asked = set(SB.STORE_COLUMNS.split(","))
    assert "name" not in asked
    assert asked <= set(STORE_ROW)


def test_the_block_is_the_branding_the_admin_typed():
    b = SB.block(BRAND, STORE_ROW, tenant_name="Example Wireless Inc")
    assert b["name"] == "Example Wireless"
    assert b["address_lines"] == ["2723 Example Ave", "Brooklyn NY 11229"]
    assert b["license"] == "2126405-DCWP"
    assert b["phone"] == "718-555-0144"


def test_a_store_that_declared_no_branding_address_falls_back_to_its_own_row():
    b = SB.block({"company_name": "Example Wireless"}, STORE_ROW)
    assert b["address_lines"] == ["2723 Example Ave Brooklyn"]


def test_the_market_column_is_not_folded_into_the_address():
    """It is a market NAME, not reliably a state, and an address that already ends in one then reads
    '1 Test St Testville NY 10000 NY'. A receipt prints what is known, never a guess."""
    b = SB.block({}, {"address": "1 Test St Testville NY 10000", "market": "NY"})
    assert b["address_lines"] == ["1 Test St Testville NY 10000"]


def test_nothing_is_invented_for_a_shop_that_declared_nothing():
    b = SB.block({}, None, tenant_name="", store_code="Ave U")
    assert b["name"] == "Ave U"
    assert b["address_lines"] == [] and b["phone"] == "" and b["license"] == ""
    assert b["lines"] == ["Ave U"]


# ── both layouts dereference it ──────────────────────────────────────────────────────────────────
def test_the_pos_format_receipt_carries_the_address_the_till_slip_carries():
    """THE REGRESSION. This is receipt 4605: the emailed copy said 'Example Wireless' and stopped."""
    doc = _doc()
    printed = "\n".join(doc["store"]["lines"])
    assert "2723 Example Ave" in printed
    assert "Brooklyn NY 11229" in printed
    assert "2126405-DCWP" in printed


def test_the_two_layouts_say_the_same_thing_about_the_shop():
    """One fact, one home: whatever the slip prints at the top, the POS layout prints too."""
    slip = SR.slip_model(SNAP, TEMPLATE, brand=BRAND, store_row=STORE_ROW)
    head = [ln["text"] for ln in slip["head_lines"]]
    doc = _doc()
    for line in SB.block(BRAND, STORE_ROW)["lines"]:
        assert line in head
        assert line in doc["store"]["lines"] + [doc["store"].get("phone") or ""]


def test_a_broken_store_read_never_costs_the_receipt_its_branding():
    """Even with NO store row at all — the read failed, the row is gone — the address still prints,
    because it comes from the branding rows the admin typed, not from that read."""
    doc = _doc(store_row=None)
    assert "2723 Example Ave" in "\n".join(doc["store"]["lines"])


# ── the legal blocks reach every layout ──────────────────────────────────────────────────────────
def test_the_return_policy_prints_on_the_pos_layout_too():
    doc = _doc(disclaimer_blocks=RETURN_POLICY)
    assert "No Cash refunds" in (doc["footer_text"] or "")
    assert TEMPLATE["footer_text"] in (doc["footer_text"] or "")


def test_a_block_that_asks_for_a_signature_gets_a_signature_line():
    doc = _doc(disclaimer_blocks=[{"text": "I agree.", "signature": True}])
    assert "Customer signature" in (doc["footer_text"] or "")


def test_a_receipt_with_no_blocks_grows_no_footer_of_its_own():
    assert _doc(disclaimer_blocks=[])["footer_text"] == TEMPLATE["footer_text"]


def test_the_rendered_pos_receipt_shows_all_of_it():
    from app.modules.pos.receipt_formats import render as RENDER
    html = SR.html(SNAP, TEMPLATE, render_document=RENDER.render_html, fmt=RQ,
                   brand=BRAND, store_row=STORE_ROW, disclaimer_blocks=RETURN_POLICY)
    for expected in ("Example Wireless", "2723 Example Ave", "Brooklyn NY 11229", "2126405-DCWP",
                     "4605", "No Cash refunds"):
        assert expected in html, expected


# ── the till slip's PDF is not clipped ───────────────────────────────────────────────────────────
def test_every_figure_on_the_slip_survives_into_the_pdf():
    """The second thing the customer got: a thermal PDF whose right-hand column was cut off at the
    page edge, so 'Subtotal 808.75' read 'Subtotal 8'. A receipt with no amounts on it is as unusable
    as one with no address."""
    pdfplumber = pytest.importorskip("pdfplumber")
    from io import BytesIO

    from app.modules.pos import receipt_pdf_render as RPDF
    thermal = dict(TEMPLATE, layout=SR.LAYOUT_THERMAL)
    pdf = RPDF.receipt_pdf(SNAP, thermal, brand=BRAND, store_row=STORE_ROW)
    with pdfplumber.open(BytesIO(pdf)) as doc:
        page = doc.pages[0]
        text = page.extract_text() or ""
        widest = max((w["x1"] for w in page.extract_words()), default=0)
        assert widest <= page.width + 0.5, "a word runs off the edge of the page"
    assert "190.53" in text and "15.53" in text and "175.00" in text
