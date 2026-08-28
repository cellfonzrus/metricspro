"""RQ / Wireless Zone receipt format. Declares its columns/labels; the engine reads positions from the
actual file. Everything the reprint needs (header, items, totals, contract details, comments, legal
footer) is captured — nothing about the layout is hardcoded beyond the label vocabulary this POS uses."""
from __future__ import annotations

import re

from . import base, engine

POS_SOURCE = "rq"
LABEL = "RQ (Wireless Zone)"

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

    # totals + payments (region after the items)
    doc["totals"] = engine.extract_totals(rows, TOTALS, start=stop)
    pay_i = engine.find_row(rows, "Cash", start=stop)
    if pay_i >= 0:
        monies = base.find_money(engine.row_text(rows[pay_i]))
        if monies:
            doc["payments"].append({"label": "Cash", "amount": base.money(monies[-1])})

    # comments
    ci = engine.find_row(rows, "Comments")
    if ci >= 0 and ci + 1 < len(rows):
        doc["comments"] = engine.row_text(rows[ci]).split(":", 1)[-1].strip() or engine.row_text(rows[ci + 1]).strip()

    # contract details section (Contract # / Tracking #)
    cd = engine.find_row(rows, "Contract", "Details")
    if cd >= 0:
        pairs = []
        for j in range(cd + 1, min(cd + 60, len(rows))):
            t = engine.row_text(rows[j]).strip()
            if not t or "comments" in t.lower():
                break
            nums = re.findall(r"\d{6,}", t)
            for n in nums:
                pairs.append([n])
        if pairs:
            doc["sections"].append({"title": "Contract Details", "kind": "list",
                                    "columns": [{"key": "ref", "label": "Contract / Tracking #"}],
                                    "rows": pairs})

    doc["footer_text"] = engine.footer_from(rows, FOOTER_ANCHOR)
    doc["derived"] = base.compute_derived(doc)
    return doc
