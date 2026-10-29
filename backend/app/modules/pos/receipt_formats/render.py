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

from . import doc_style as _style


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
    # The columns at least one row FILLS — §POS-13's one home, shared with the PDF.
    # Receipt 4605 printed a "Product SKU" heading over a column of nothing on every row.
    # NOT applied to `render_words` / `default_layout` below: those reproduce the ORIGINAL
    # receipt's geometry for the parser round trip, where a column that was on the paper
    # must stay on the paper even when empty.
    cols = _style.visible_columns(doc)
    title = _esc(doc.get("title") or "Receipt")
    store = doc.get("store") or {}
    meta = doc.get("meta") or []

    # header: store block left, meta key/values right
    meta_rows = "".join(
        f'<tr><td class="ml">{_esc(m.get("label"))}</td>'
        f'<td class="mv"{_edit_attr(editable, "meta", m.get("key"), m.get("editable"))}>{_esc(m.get("value"))}</td></tr>'
        for m in meta
    )
    # The shop's NAME leads in bold and the rest of the block is set lighter beneath it — the same
    # weighting the PDF draws (§POS-13). Setting the whole block bold, as this did, is a difference
    # the two media had no reason to have.
    _slines = [ln for ln in (store.get("lines") or []) if str(ln or "").strip()]
    if store.get("phone"):
        _slines.append(str(store["phone"]))
    if store.get("fax"):
        _slines.append(f'FAX {store["fax"]}')
    store_html = '<div class="store">'
    if _slines:
        store_html += _esc(_slines[0])
        if len(_slines) > 1:
            store_html += ('<div class="store-sub">'
                           + "<br>".join(_esc(ln) for ln in _slines[1:]) + "</div>")
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
    def _is_grand(t) -> bool:
        """The grand total, which the stylesheet and the PDF both weight. One test, so the heavy
        rule cannot land on a different row in the two media."""
        return (t.get("kind") == "money_total"
                or str(t.get("key") or "").lower() == "total"
                or str(t.get("label") or "").strip().lower().rstrip(":") == "total")

    total_rows = "".join(
        f'<tr class="{"grand" if _is_grand(t) else ""}"><td class="tl">{_esc(t.get("label"))}</td>'
        f'<td class="tv"{_edit_attr(editable, "total", t.get("key"), t.get("editable"))}>'
        f'{_fmt_cell(t.get("amount"), "money")}</td></tr>'
        for t in (doc.get("totals") or [])
    )
    pay_rows = "".join(
        f'<tr class="pay"><td class="tl">{_esc(p.get("label"))}</td><td class="tv">{_fmt_cell(p.get("amount"), "money")}</td></tr>'
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
<style>{_page_css()}{_doc_css()}</style></head><body>
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


def _page_css() -> str:
    """THE page a receipt prints on (§POS-11) — dereferenced, never spelled here. Without it a
    browser stamps its own header and footer over the receipt: the date and time of the PRINTING,
    the document title centred above it, the URL and the page number. The till slip and this layout
    are two layouts of one receipt, so the rule is one rule. The import is local because
    `sale_receipt` injects THIS module's renderer, and the two must not import each other at module
    level."""
    from app.modules.pos.sale_receipt import PAGE_CSS
    return PAGE_CSS


def _doc_css() -> str:
    """THE document stylesheet (§POS-13) — dereferenced, never spelled here. It used to be a
    private `_CSS` string in this file, which is exactly how the screen came to carry hairlines the
    PDF had never heard of. Both media now read one description of the borders."""
    return _style.css()


def _edit_attr(editable: bool, scope: str, key, allowed) -> str:
    if not (editable and allowed):
        return ""
    return f' contenteditable="true" data-edit="{scope}" data-key="{_esc(key)}"'


# The stylesheet lives in `doc_style.css()` (§POS-13). A `_CSS` string here was the SECOND
# description of the document's borders, and the one the PDF never saw.



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
