"""Generic reprint renderer — a Document → print-ready HTML in the SAME layout it was uploaded in.

ONE renderer for every format — with TWO outputs of the same walk: `render_html` (the reprint the
browser prints) and `render_words` / `render_text` (the receipt as positioned words / plain text
lines, laid out on the format's declared PRINT_LAYOUT). The word output is what the format's own
parser reads back: document → render_words → parse → the same document, which is how the proof
harness pins that a sale REBUILT from the landed reports (pos/sales_from_reports.py) prints exactly
what the parser of that POS would read from a real receipt. It walks `doc["columns"]` and `doc["items"]` (whatever they are for
this POS), the ordered `totals`, the parties, the sections and the verbatim legal `footer_text`. There
is no per-format HTML: the layout is reproduced from the DATA the parser captured, so a new POS format
reprints correctly with zero renderer changes. The reprint uses the CURRENT (possibly edited) values,
so an edited description/qty/tax/price prints as edited. Print CSS keeps the same look on paper.
"""
from __future__ import annotations

import html


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _lines(block) -> str:
    return "<br>".join(_esc(ln) for ln in ((block or {}).get("lines") or []))


def _fmt_cell(value, kind) -> str:
    if kind in ("money", "money_total"):
        from .base import fmt_money
        return _esc(fmt_money(value))
    return _esc(value)


def render_html(doc: dict, *, editable: bool = False) -> str:
    """Full standalone HTML document for printing. `editable=True` adds contenteditable hooks +
    data-attributes so a UI can turn the same markup into an inline editor (kept optional so the print
    view stays clean)."""
    cols = doc.get("columns") or []
    title = _esc(doc.get("title") or "Receipt")
    store = doc.get("store") or {}
    meta = doc.get("meta") or []

    # header: store block left, meta key/values right
    meta_rows = "".join(
        f'<tr><td class="ml">{_esc(m.get("label"))}</td>'
        f'<td class="mv"{_edit_attr(editable, "meta", m.get("key"), m.get("editable"))}>{_esc(m.get("value"))}</td></tr>'
        for m in meta
    )
    store_html = f'<div class="store">{_lines(store)}'
    if store.get("phone"):
        store_html += f'<br>{_esc(store["phone"])}'
    if store.get("fax"):
        store_html += f' &nbsp; FAX {_esc(store["fax"])}'
    store_html += "</div>"

    # parties
    parties = f'<div class="party"><div class="plabel">Bill To</div>{_lines(doc.get("bill_to"))}</div>'
    if doc.get("ship_to"):
        parties += f'<div class="party"><div class="plabel">Ship To</div>{_lines(doc.get("ship_to"))}</div>'

    # items table
    thead = "".join(f'<th class="{_esc(c.get("align") or "left")}">{_esc(c["label"])}</th>' for c in cols)
    body_rows = []
    for idx, it in enumerate(doc.get("items") or []):
        cells = it.get("cells") or {}
        editable_keys = set(it.get("editable") or [])
        tds = []
        for c in cols:
            k, kind = c["key"], c["kind"]
            cell_editable = editable and (k in editable_keys)
            tds.append(
                f'<td class="{_esc(c.get("align") or "left")}"'
                f'{_edit_attr(cell_editable, "item", f"{idx}.{k}", True)}>'
                f'{_fmt_cell(cells.get(k), kind)}</td>'
            )
        body_rows.append("<tr>" + "".join(tds) + "</tr>")
    items_table = (
        f'<table class="items"><thead><tr>{thead}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody></table>'
    )

    # totals + payments
    total_rows = "".join(
        f'<tr><td class="tl">{_esc(t.get("label"))}</td>'
        f'<td class="tv"{_edit_attr(editable, "total", t.get("key"), t.get("editable"))}>'
        f'{_fmt_cell(t.get("amount"), "money")}</td></tr>'
        for t in (doc.get("totals") or [])
    )
    pay_rows = "".join(
        f'<tr><td class="tl">{_esc(p.get("label"))}</td><td class="tv">{_fmt_cell(p.get("amount"), "money")}</td></tr>'
        for p in (doc.get("payments") or [])
    )
    totals_block = f'<table class="totals">{total_rows}{pay_rows}</table>'

    # extra sections (Contract Details / Service Agreement rows)
    sections = ""
    for s in (doc.get("sections") or []):
        scols = s.get("columns") or []
        sh = "".join(f"<th>{_esc(c.get('label'))}</th>" for c in scols)
        sr = "".join("<tr>" + "".join(f"<td>{_esc(v)}</td>" for v in row) + "</tr>" for row in (s.get("rows") or []))
        sections += f'<div class="section"><div class="stitle">{_esc(s.get("title"))}</div><table class="sub"><thead><tr>{sh}</tr></thead><tbody>{sr}</tbody></table></div>'

    comments = f'<div class="comments"><b>Comments:</b> {_esc(doc.get("comments"))}</div>' if doc.get("comments") else ""
    footer = f'<div class="footer">{_esc(doc.get("footer_text")).replace(chr(10), "<br>")}</div>' if doc.get("footer_text") else ""

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title>
<style>{_CSS}</style></head><body>
<div class="receipt" data-pos-source="{_esc(doc.get('pos_source'))}">
  <div class="head">
    <div class="head-left">{store_html}</div>
    <div class="head-right"><div class="rtitle">{title}</div><table class="meta">{meta_rows}</table></div>
  </div>
  <div class="parties">{parties}</div>
  {items_table}
  <div class="totals-wrap">{totals_block}</div>
  {comments}
  {sections}
  {footer}
</div></body></html>"""


def _edit_attr(editable: bool, scope: str, key, allowed) -> str:
    if not (editable and allowed):
        return ""
    return f' contenteditable="true" data-edit="{scope}" data-key="{_esc(key)}"'


_CSS = """
* { box-sizing: border-box; }
body { font-family: Arial, Helvetica, sans-serif; color: #111; margin: 0; padding: 16px; font-size: 12px; }
.receipt { max-width: 760px; margin: 0 auto; }
.head { display: flex; justify-content: space-between; gap: 24px; margin-bottom: 12px; }
.rtitle { font-size: 20px; font-weight: 700; text-align: right; margin-bottom: 4px; }
.store { font-weight: 600; line-height: 1.4; }
.meta td { padding: 1px 4px; }
.meta .ml { color: #555; text-align: right; }
.meta .mv { font-weight: 600; }
.parties { display: flex; gap: 40px; margin: 8px 0 14px; }
.plabel { color: #555; font-weight: 700; margin-bottom: 2px; }
table.items { width: 100%; border-collapse: collapse; margin: 6px 0; }
table.items th { border-bottom: 1.5px solid #333; text-align: left; padding: 4px 6px; font-size: 11px; }
table.items td { padding: 3px 6px; border-bottom: 1px solid #eee; vertical-align: top; }
.right { text-align: right; }
.totals-wrap { display: flex; justify-content: flex-end; margin-top: 8px; }
table.totals { min-width: 260px; }
table.totals .tl { color: #444; padding: 2px 10px 2px 0; }
table.totals .tv { text-align: right; font-weight: 700; }
.comments { margin: 12px 0; }
.section { margin: 12px 0; }
.stitle { font-weight: 700; margin-bottom: 4px; }
table.sub { border-collapse: collapse; }
table.sub th, table.sub td { border: 1px solid #ddd; padding: 2px 8px; text-align: left; }
.footer { margin-top: 18px; padding-top: 10px; border-top: 1px solid #ccc; color: #444; font-size: 10px; line-height: 1.4; white-space: pre-wrap; }
[contenteditable="true"] { outline: 1px dashed #4c8bf5; background: #f5f9ff; }
@media print { body { padding: 0; } [contenteditable] { outline: none; background: none; } }
"""


# ── The word / text output (the round-trip side of the ONE renderer) ────────────────────────────────
def default_layout(doc: dict) -> dict:
    """A print layout for a format that declares none: the item columns share the page width in the
    order the document lists them; the header fields sit on the right. Enough for the parser to read
    the table back; a format that cares about its exact geometry declares PRINT_LAYOUT."""
    cols = doc.get("columns") or []
    n = max(1, len(cols))
    left, right, step = 27, 594, (594 - 27) / n
    layout_cols = []
    for i, c in enumerate(cols):
        x = left + i * step
        is_right = (c.get("align") == "right")
        layout_cols.append({"key": c["key"], "x": round(x), "right": round(x + step - 6) if is_right else None})
    return {"title_x": 515, "store_x": 69, "meta_label_x": 380, "meta_value_x": 462, "left_x": 27,
            "bill_x": 98, "totals_label_x": 466, "totals_value_right": right, "payment_value_right": 190,
            "columns": layout_cols, "section_columns_x": [27, 149], "char_w": 5.0}


def render_words(doc: dict, layout: dict | None = None) -> list[dict]:
    """The Document as positioned words ({text,x0,x1,top,bottom}) in the geometry `layout` declares —
    the same shape receipt_pdf.extract_pages_words gives a parser. Money is spelled through
    base.fmt_money; every block (title, store, meta, bill to, the item table, totals, payments, the
    sections, comments, footer) is laid out in the order the receipt prints it."""
    from .base import fmt_money
    L = layout or default_layout(doc)
    w = float(L.get("char_w", 5.0))
    out: list[dict] = []

    def put(text, x0, y, right=None):
        """Emit `text` as words starting at x0 (or ending at `right` when given)."""
        text = "" if text is None else str(text)
        toks = text.split()
        if not toks:
            return
        total_w = sum(len(t) for t in toks) * w + (len(toks) - 1) * w
        x = (right - total_w) if right is not None else float(x0)
        for t in toks:
            x1 = x + len(t) * w
            out.append({"text": t, "x0": round(x, 2), "x1": round(x1, 2), "top": float(y), "bottom": float(y) + 10})
            x = x1 + w

    y = 37
    put(doc.get("title") or "Receipt", L["title_x"], y)
    # store block (left) and the header fields (right) share the rows below the title
    store = doc.get("store") or {}
    lines = list(store.get("lines") or [])
    if store.get("phone") and not any(store["phone"] in ln for ln in lines):
        lines.append(store["phone"])
    ys = 98
    for ln in lines:
        put(ln, L["store_x"], ys)
        ys += 15
    ym = 107
    for m in (doc.get("meta") or []):
        if m.get("key") == "sale_date_iso":
            continue                                   # derived from the printed date, never printed itself
        put(f'{m.get("label")}:', L["meta_label_x"], ym)
        put(m.get("value"), L["meta_value_x"], ym)
        ym += 15
    y = max(ys, ym) + 8
    put("Bill To:", L["left_x"], y)
    for ln in ((doc.get("bill_to") or {}).get("lines") or []):
        y += 15
        put(ln, L["bill_x"], y)
    if doc.get("ship_to"):
        put("Ship To:", L["left_x"] + 300, y - 15 * len((doc.get("bill_to") or {}).get("lines") or []))
    # the item table
    y += 30
    cols = doc.get("columns") or []
    lx = {c["key"]: c for c in L.get("columns") or []}
    for c in cols:
        lc = lx.get(c["key"]) or {"x": L["left_x"], "right": None}
        put(c["label"], lc["x"], y)
    for it in (doc.get("items") or []):
        y += 18
        cells = it.get("cells") or {}
        for c in cols:
            lc = lx.get(c["key"]) or {"x": L["left_x"], "right": None}
            v = cells.get(c["key"])
            if c.get("kind") in ("money", "money_total"):
                v = fmt_money(v)
            put(v, lc["x"], y, right=lc.get("right"))
    # totals (right column) with the payment lines (left column) between "Payment:" and "Change:"
    y += 27
    totals = list(doc.get("totals") or [])
    payments = list(doc.get("payments") or [])
    first = totals[:1]
    rest = totals[1:]
    for t in first:
        put(f'{t.get("label")}:', L["totals_label_x"], y)
        put(fmt_money(t.get("amount")), None, y, right=L["totals_value_right"])
        y += 14
    put("Payment:", L["left_x"], y)
    y += 14
    for p in payments:
        put(p.get("label"), L["left_x"], y)
        put(fmt_money(p.get("amount")), None, y, right=L["payment_value_right"])
        y += 14
    for t in rest:
        if t.get("key") == "change":
            continue
        put(f'{t.get("label")}:', L["totals_label_x"], y)
        put(fmt_money(t.get("amount")), None, y, right=L["totals_value_right"])
        y += 14
    ch = next((t for t in totals if t.get("key") == "change"), None)
    if ch is not None:
        put("Change:", L["left_x"], y)
        put(fmt_money(ch.get("amount")), None, y, right=L["payment_value_right"])
        y += 14
    # the sections (Contract Details …)
    sx = L.get("section_columns_x") or [27, 149]
    for s in (doc.get("sections") or []):
        y += 18
        put(f'{s.get("title")}:', L["left_x"], y)
        y += 17
        scols = s.get("columns") or []
        for i, c in enumerate(scols):
            put(c.get("label"), sx[i] if i < len(sx) else sx[-1] + 120 * (i - len(sx) + 1), y)
        for row in (s.get("rows") or []):
            y += 10
            for i, v in enumerate(row):
                put(v, sx[i] if i < len(sx) else sx[-1] + 120 * (i - len(sx) + 1), y)
    if doc.get("comments"):
        y += 18
        put(f'Comments: {doc["comments"]}', L["left_x"], y)
    if doc.get("footer_text"):
        y += 24
        for ln in str(doc["footer_text"]).splitlines():
            if ln.strip():
                put(ln, L["left_x"], y)
                y += 10
    return out


def render_text(doc: dict, layout: dict | None = None) -> str:
    """The receipt as plain text, one printed row per line, in the order it prints — the words of
    render_words grouped into rows exactly as the engine groups a PDF's words."""
    from . import engine
    rows = engine.group_rows(render_words(doc, layout))
    return "\n".join(engine.row_text(r) for r in rows)
