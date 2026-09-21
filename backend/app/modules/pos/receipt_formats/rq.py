"""RQ / Wireless Zone receipt format. Declares its columns/labels; the engine reads positions from the
actual file. Everything the reprint needs (header, items, totals, contract details, comments, legal
footer) is captured — nothing about the layout is hardcoded beyond the label vocabulary this POS uses.

The same declarations serve a document BUILT for this POS from the landed sales reports
(pos/sales_from_reports.py): TITLE, DATE_FORMAT, FINANCED_ITEMS (which item lines make the 'Financed:'
total), CONTRACT_SECTION (the tracking / contract table) and PRINT_LAYOUT (the geometry render_words
lays a document out on, so `parse` reads the rendering back — the round-trip the proof pins)."""
from __future__ import annotations

import re

from . import base, engine

POS_SOURCE = "rq"
LABEL = "RQ (Wireless Zone)"
TITLE = "Sale"                       # the receipt title this POS prints on a sale
DATE_FORMAT = "%d-%b-%Y"             # how it prints the tendered-on date ('28-Nov-2025')

# Item columns, in the order this POS prints them. `hdr` = the header words the engine locates.
COLUMNS = [
    {"key": "sku", "label": "Product SKU", "kind": base.KIND_CODE, "hdr": ["Product", "SKU"]},
    {"key": "name", "label": "Product Name", "kind": base.KIND_DESC, "hdr": ["Product", "Name"]},
    {"key": "tracking", "label": "Tracking #", "kind": base.KIND_SERIAL, "hdr": ["Tracking", "#"]},
    {"key": "qty", "label": "Qty", "kind": base.KIND_QTY, "hdr": ["Qty"]},
    {"key": "price", "label": "Your Price", "kind": base.KIND_MONEY, "hdr": ["Your", "Price"]},
    {"key": "total", "label": "Your Total", "kind": base.KIND_TOTAL, "hdr": ["Your", "Total"]},
]

TOTALS = [
    {"key": "subtotal", "label": "Subtotal", "match": ["subtotal"]},
    {"key": "sales_tax", "label": "Sales Tax", "match": ["sales", "tax"], "editable": True},
    {"key": "financed", "label": "Financed", "match": ["financed"], "editable": True},
    {"key": "total", "label": "Total", "match": ["total"], "editable": True},
    {"key": "change", "label": "Change", "match": ["change"]},
]

# The totals block begins with these; kept specific (with the colon) so an ITEM whose description
# contains "Financed"/"Total" (e.g. "Device Payment Agreement Financed Amount") never ends the table.
STOP_LABELS = ["Subtotal:", "Payment:"]
FOOTER_ANCHOR = "Terms and Conditions"
CHANGE_LABEL = "Change"              # the payment lines sit between 'Payment:' and this row

# A FINANCED line on this POS: an item whose description carries the same word its 'Financed:' total
# does, with a NEGATIVE quantity (the installment offset: '… Financed Amount  -1  $1,410.00  ($1,410.00)').
# The 'Financed:' total is minus the sum of their totals. The same vocabulary word as TOTALS — no
# product, carrier or tenant name.
FINANCED_ITEMS = {"match": ["financed"], "qty_negative": True}

# The 'Contract Details:' table under the totals — one row per (tracking #, contract #) pair.
CONTRACT_SECTION = {"title": "Contract Details", "anchor": ["Contract", "Details"],
                    "columns": [{"key": "tracking", "label": "Tracking #", "kind": base.KIND_SERIAL, "hdr": ["Tracking", "#"]},
                                {"key": "contract", "label": "Contract #", "kind": base.KIND_CODE, "hdr": ["Contract", "#"]}]}

# The print geometry of this POS's receipt (x positions measured on a real one) — what render_words
# lays a document out on, so the parser above reads the rendering back exactly (the round-trip pin).
# Left-aligned columns start at `x`; the money columns end at `right`.
PRINT_LAYOUT = {
    "title_x": 515, "store_x": 69, "meta_label_x": 380, "meta_value_x": 462, "left_x": 27, "bill_x": 98,
    "totals_label_x": 466, "totals_value_right": 594, "payment_value_right": 190,
    "columns": [{"key": "sku", "x": 27, "right": None}, {"key": "name", "x": 109, "right": None},
                {"key": "tracking", "x": 341, "right": None}, {"key": "qty", "x": 449, "right": None},
                {"key": "price", "x": 492, "right": 541}, {"key": "total", "x": 545, "right": 594}],
    "section_columns_x": [27, 149], "char_w": 5.0,
}


def parse(pages_words) -> dict:
    rows = engine.group_rows(pages_words)
    doc = base.new_document(POS_SOURCE, LABEL)
    doc["title"] = engine.row_text(rows[0]).strip() if rows else "Sale"

    bill_i = engine.find_row(rows, "Bill", "To")
    top_bill = rows[bill_i]["top"] if bill_i >= 0 else 1e9

    # meta (header key/values, right column)
    def meta(key, label, editable, *anchor):
        i = engine.find_row(rows, *anchor)
        val = engine.value_right_of(rows[i], *anchor) if i >= 0 else ""
        val = re.sub(r"^[:\s]+", "", val).strip()
        if val:
            doc["meta"].append({"key": key, "label": label, "value": val, "editable": editable})
        return val

    meta("invoice_no", "Invoice", False, "Invoice")
    tendered_on = meta("sale_date", "Tendered On", True, "Tendered", "On")
    meta("salesperson", "Sales Person", True, "Sales", "Person")
    meta("tendered_by", "Tendered By", False, "Tendered", "By")
    meta("tendered_at", "Tendered At", False, "Tendered", "At")
    if tendered_on:
        iso = base.parse_iso_date(tendered_on)
        if iso:
            doc["meta"].append({"key": "sale_date_iso", "label": "Sale Date (ISO)", "value": iso, "editable": False})

    # store (top-left block; header fields sit to the right of x≈330 on the same rows)
    top0 = rows[0]["top"] if rows else 0
    store_lines = engine.left_lines(rows, x_hi=330, top_lo=top0, top_hi=top_bill)
    doc["store"]["lines"] = store_lines
    for ln in store_lines:
        m = re.search(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", ln)
        if m and not doc["store"]["phone"]:
            doc["store"]["phone"] = m.group(0)

    # bill to
    bt = engine.block_lines(rows, ["Bill", "To"], max_lines=3,
                            stop_tokens=["Product", "SKU"])
    doc["bill_to"]["lines"] = bt
    if bt:
        doc["bill_to"]["name"] = bt[0]

    # items
    cols, items, stop = engine.extract_table(rows, COLUMNS, STOP_LABELS)
    doc["columns"], doc["items"] = cols, items

    # totals + payments (region after the items). The payment lines are whatever the register printed
    # between 'Payment:' and 'Change:' — cash, a card brand, a keyed-by-hand twin — never a fixed word.
    doc["totals"] = engine.extract_totals(rows, TOTALS, start=stop)
    doc["payments"] = engine.extract_payments(rows, stop, CHANGE_LABEL, TOTALS)

    # comments
    ci = engine.find_row(rows, "Comments")
    if ci >= 0 and ci + 1 < len(rows):
        doc["comments"] = engine.row_text(rows[ci]).split(":", 1)[-1].strip() or engine.row_text(rows[ci + 1]).strip()

    # contract details section: the (Tracking #, Contract #) table — read as PAIRS through the same
    # header-located column bounds the item table uses; a receipt without that header row (an older
    # print) falls back to the flat list of reference numbers it used to give.
    scols, srows = engine.extract_pairs(rows, CONTRACT_SECTION["anchor"], CONTRACT_SECTION["columns"],
                                        stop_tokens=("comments", FOOTER_ANCHOR))
    if srows:
        doc["sections"].append({"title": CONTRACT_SECTION["title"], "kind": "table", "columns": scols, "rows": srows})
    else:
        cd = engine.find_row(rows, *CONTRACT_SECTION["anchor"])
        if cd >= 0:
            pairs = []
            for j in range(cd + 1, min(cd + 60, len(rows))):
                t = engine.row_text(rows[j]).strip()
                if not t or "comments" in t.lower():
                    break
                for n in re.findall(r"\d{6,}", t):
                    pairs.append([n])
            if pairs:
                doc["sections"].append({"title": CONTRACT_SECTION["title"], "kind": "list",
                                        "columns": [{"key": "ref", "label": "Contract / Tracking #"}],
                                        "rows": pairs})

    doc["footer_text"] = engine.footer_from(rows, FOOTER_ANCHOR)
    doc["derived"] = base.compute_derived(doc)
    return doc
