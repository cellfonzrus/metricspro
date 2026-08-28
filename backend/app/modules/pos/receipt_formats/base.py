"""Shared receipt-Document schema + pure tokenizers for the per-POS parsers.

WHY A STRUCTURED DOCUMENT (and not per-format code paths everywhere)
────────────────────────────────────────────────────────────────────
Different tenants upload from different POS systems (RQ / Wireless Zone, B2B / TCC, …) and every one
has a DIFFERENT layout — different item columns, different tax lines, different header labels, a
different legal footer. The owner's rule is "nothing hardcoded … different tenants might have a
different format". So a parser's ONLY job is to turn its own format into ONE common, fully-DATA
shape — `Document` — and a single generic renderer reprints ANY Document in its original layout.
Adding a new POS later = adding one parser that emits a Document; no renderer or endpoint changes.

The Document is also the EDITABLE record: item `description/qty/price` and the `tax`/price total rows
are flagged editable, saved back verbatim, and the reprint uses the current (possibly edited) values.

Document shape (all JSON-serializable):
  {
    "pos_source": "rq",                      # which format produced this
    "format_label": "RQ (Wireless Zone)",
    "title": "Sale",                          # receipt title, from the file
    "meta": [ {"key","label","value","editable"} ],          # header key/value fields, ordered
    "store": {"lines": [str,…], "phone": str|None, "fax": str|None},
    "bill_to": {"lines": [str,…]},
    "ship_to": {"lines": [str,…]} | None,
    "columns": [ {"key","label","kind","align"} ],           # item-table columns, FROM the receipt
    "items":  [ {"cells": {colkey: value}, "editable": [colkey,…]} ],
    "totals": [ {"key","label","amount","editable"} ],       # subtotal/tax/total/financed…, ordered
    "payments": [ {"label","amount"} ],
    "sections": [ {"title","kind","columns":[…],"rows":[[…]]} ],   # Contract Details / Service Agreement
    "comments": str|None,
    "footer_text": str|None,                  # legal/return policy, captured verbatim for the reprint
    "derived": { … denormalized search/summary fields … }
  }

`kind` on a column drives BOTH the editable flag and the search extraction (which cells are IMEIs,
which are money). It is a semantic tag, never a position — the label/order come from the file.
"""
from __future__ import annotations

import re
from typing import Any

# Column kinds — semantic, format-independent. `money`/`qty`/`desc` cells are editable.
KIND_CODE = "code"     # SKU / Product ID
KIND_DESC = "desc"     # product name / description   (editable)
KIND_SERIAL = "serial"  # IMEI / serial / tracking #  (search key)
KIND_QTY = "qty"       # quantity                     (editable)
KIND_MONEY = "money"   # unit price / retail price     (editable)
KIND_TOTAL = "money_total"  # extended / line total    (editable)
EDITABLE_KINDS = {KIND_DESC, KIND_QTY, KIND_MONEY, KIND_TOTAL}


def money(v: Any):
    """'$1,410.00' / '($1,410.00)' / '1210' / -1410 → float; blank/unparseable → None. Parentheses
    mean a NEGATIVE amount (accounting style, used by RQ for the financed offset lines)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = str(v).strip()
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^0-9.\-]", "", s)
    if s in ("", "-", ".", "-."):
        return None
    try:
        n = round(float(s), 2)
    except ValueError:
        return None
    return -n if (neg and n > 0) else n


def digits(v: Any):
    if v is None:
        return None
    d = re.sub(r"\D", "", str(v))
    return d or None


def is_serial(tok: str) -> bool:
    """A 14–16 digit device id (IMEI/serial). Kept strict so a price/qty is never mistaken for one."""
    d = re.sub(r"\D", "", tok or "")
    return 14 <= len(d) <= 16


_MONEY_RE = re.compile(r"\(?-?\$[\d,]+\.\d{2}\)?|\(?-?\$?\d[\d,]*\.\d{2}\)?")


def find_money(line: str) -> list[str]:
    """Every money token on a line, left→right (keeps the parens/sign so money() can read it)."""
    return _MONEY_RE.findall(line or "")


def new_document(pos_source: str, format_label: str) -> dict:
    return {
        "pos_source": pos_source, "format_label": format_label, "title": None,
        "meta": [], "store": {"lines": [], "phone": None, "fax": None},
        "bill_to": {"lines": []}, "ship_to": None,
        "columns": [], "items": [], "totals": [], "payments": [], "sections": [],
        "comments": None, "footer_text": None, "derived": {},
    }


def item(cells: dict, columns: list[dict]) -> dict:
    """Build one item row; editable = the cells whose column kind is editable."""
    editable = [c["key"] for c in columns if c["kind"] in EDITABLE_KINDS]
    return {"cells": cells, "editable": editable}


def compute_derived(doc: dict) -> dict:
    """Denormalized search/summary fields off the structured Document — used for the receipt_imports
    columns + the blind index. PURE. Recomputed on every save so edits stay searchable."""
    cols = doc.get("columns") or []
    desc_keys = [c["key"] for c in cols if c["kind"] == KIND_DESC]
    serial_keys = [c["key"] for c in cols if c["kind"] == KIND_SERIAL]
    imeis: list[str] = []
    device_name = None
    for it in (doc.get("items") or []):
        cells = it.get("cells") or {}
        for k in serial_keys:
            d = digits(cells.get(k))
            if d and is_serial(d):
                imeis.append(d)
        if device_name is None:
            for k in desc_keys:
                if str(cells.get(k) or "").strip():
                    device_name = str(cells[k]).strip()
                    break
    meta = {m["key"]: m.get("value") for m in (doc.get("meta") or [])}
    totals = {t.get("key") or (t.get("label") or "").lower(): t.get("amount") for t in (doc.get("totals") or [])}
    # "grand total" = the row the format flagged as the final total (key 'total'/'total_due'), else the max.
    grand = totals.get("total") or totals.get("total_due")
    return {
        "customer_name": (doc.get("bill_to") or {}).get("name") or _first_line(doc.get("bill_to")),
        "phone": None,  # customer phone is not on these receipts; store phone is the store's
        "imei": imeis[0] if imeis else None,
        "imeis": imeis,
        "device_name": device_name,
        "invoice_no": meta.get("invoice_no") or meta.get("ref_no") or meta.get("transaction_id"),
        "salesperson": meta.get("salesperson"),
        "sale_date": meta.get("sale_date_iso") or meta.get("sale_date"),
        "total": grand,
        "store_name": _first_line(doc.get("store")),
    }


def _first_line(block) -> str | None:
    lines = (block or {}).get("lines") or []
    return lines[0] if lines else None


# ── shared line helpers used by the format parsers ────────────────────────────────────────────────
def split_lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in (text or "").splitlines()]


def parse_iso_date(s: str) -> str | None:
    """Accept the date spellings these receipts use → ISO 'YYYY-MM-DD'. Returns None on no match, so a
    bad parse never corrupts the record. Formats: '28-Nov-2025', '5/5/2025', '2025-11-28'."""
    s = (s or "").strip()
    m = re.search(r"(\d{1,2})[-/](\w{3})[-/](\d{4})", s)  # 28-Nov-2025
    if m:
        months = {mo: i + 1 for i, mo in enumerate(
            ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
        mm = months.get(m.group(2)[:3].lower())
        if mm:
            return f"{int(m.group(3)):04d}-{mm:02d}-{int(m.group(1)):02d}"
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)  # 5/5/2025
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    return m.group(0) if m else None
