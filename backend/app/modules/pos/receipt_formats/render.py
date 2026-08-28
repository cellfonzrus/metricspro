"""Generic reprint renderer — a Document → print-ready HTML in the SAME layout it was uploaded in.

ONE renderer for every format. It walks `doc["columns"]` and `doc["items"]` (whatever they are for
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
        from .base import money
        n = money(value)
        if n is None:
            return _esc(value)
        return ("(" + f"${abs(n):,.2f}" + ")") if n < 0 else f"${n:,.2f}"
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
