"""B2B / TCC (Verizon) 'Sale Receipt' format. Same engine, different declared columns/labels — the
proof that a new POS format is just a spec, not new geometry code."""
from __future__ import annotations

import re

from . import base, engine

POS_SOURCE = "b2b"
LABEL = "B2B (TCC / Verizon)"

COLUMNS = [
    {"key": "product_id", "label": "Product ID", "kind": base.KIND_CODE, "hdr": ["Product", "ID"]},
    {"key": "qty", "label": "Qty", "kind": base.KIND_QTY, "hdr": ["Qty"]},
    {"key": "description", "label": "Description", "kind": base.KIND_DESC, "hdr": ["Description"]},
    {"key": "serial", "label": "Serial", "kind": base.KIND_SERIAL, "hdr": ["Serial"]},
    {"key": "retail", "label": "Retail Price", "kind": base.KIND_MONEY, "hdr": ["Retail", "Price"]},
    {"key": "ext", "label": "Ext. Price", "kind": base.KIND_TOTAL, "hdr": ["Ext.", "Price"]},
]

TOTALS = [
    {"key": "pretax_subtotal", "label": "Pre-Tax Subtotal", "match": ["pre-tax", "subtotal"]},
    {"key": "subtotal", "label": "Sub Total", "match": ["sub", "total"]},
    {"key": "sales_tax_li", "label": "Sales tax LI", "match": ["sales", "tax", "li"], "editable": True},
    {"key": "sales_tax_city", "label": "Sales tax City", "match": ["sales", "tax", "city"], "editable": True},
    {"key": "total_due", "label": "Total Due", "match": ["total", "due"], "editable": True},
]

STOP_LABELS = ["Payment", "Sub Total", "Pre-Tax", "Total Due"]
FOOTER_ANCHOR = "Service Agreement"


def parse(pages_words) -> dict:
    rows = engine.group_rows(pages_words)
    doc = base.new_document(POS_SOURCE, LABEL)
    doc["title"] = engine.row_text(rows[0]).strip() if rows else "Sale Receipt"

    def meta(key, label, editable, *anchor):
        i = engine.find_row(rows, *anchor)
        val = engine.value_right_of(rows[i], *anchor) if i >= 0 else ""
        val = re.sub(r"^[:\s]+", "", val).strip()
        if val:
            doc["meta"].append({"key": key, "label": label, "value": val, "editable": editable})
        return val

    meta("transaction_id", "Transaction ID", False, "Transaction", "ID")
    sale_date = meta("sale_date", "Sale Date", True, "Sale", "Date")
    meta("salesperson", "Salesperson", True, "Salesperson")
    meta("created_by", "Created By", False, "Created", "By")
    meta("tendered_at", "Tendered At", False, "Tendered", "At")
    if sale_date:
        iso = base.parse_iso_date(sale_date)
        if iso:
            doc["meta"].append({"key": "sale_date_iso", "label": "Sale Date (ISO)", "value": iso, "editable": False})

    # store block sits top-left (x < 320); header fields are to the right
    top0 = rows[0]["top"] if rows else 0
    billrow = engine.find_row(rows, "Bill", "To")
    top_bill = rows[billrow]["top"] if billrow >= 0 else 1e9
    store_lines = engine.left_lines(rows, x_hi=320, top_lo=top0, top_hi=top_bill)
    doc["store"]["lines"] = store_lines
    for ln in store_lines:
        mp = re.search(r"PH[:\s]*([\d().\s-]{7,})", ln, re.I)
        if mp and not doc["store"]["phone"]:
            doc["store"]["phone"] = mp.group(1).strip()
        mf = re.search(r"FAX[:\s]*([\d().\s-]{7,})", ln, re.I)
        if mf and not doc["store"]["fax"]:
            doc["store"]["fax"] = mf.group(1).strip()

    # Bill To (left half) + Ship To (right half) share the same rows
    bt = engine.block_lines(rows, ["Bill", "To"], x_lo=-1e9, x_hi=320, max_lines=3, stop_tokens=["Product", "Serial"])
    st = engine.block_lines(rows, ["Ship", "To"], x_lo=320, x_hi=1e9, max_lines=3, stop_tokens=["Serial", "Retail"])
    doc["bill_to"]["lines"] = bt
    if bt:
        doc["bill_to"]["name"] = bt[0]
    if st:
        doc["ship_to"] = {"lines": st}

    cols, items, stop = engine.extract_table(rows, COLUMNS, STOP_LABELS)
    doc["columns"], doc["items"] = cols, items

    doc["totals"] = engine.extract_totals(rows, TOTALS, start=stop)

    # payment line e.g. "CREDIT CARD EXTERNAL (cash) $588.24"
    pi = engine.find_row(rows, "CREDIT", "CARD", start=stop)
    if pi < 0:
        pi = engine.find_row(rows, "Payment", start=stop)
    if pi >= 0:
        monies = base.find_money(engine.row_text(rows[pi]))
        label = re.sub(r"\s*\$[\d,]+\.\d{2}.*$", "", engine.row_text(rows[pi])).strip() or "Payment"
        if monies:
            doc["payments"].append({"label": label, "amount": base.money(monies[-1])})

    doc["footer_text"] = engine.footer_from(rows, FOOTER_ANCHOR)
    doc["derived"] = base.compute_derived(doc)
    return doc
