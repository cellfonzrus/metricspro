"""THE one home for what a receipt DOCUMENT looks like (§POS-13).

Owner 2026-10-29, on receipt 4605: *"the receipt is not formatted properly and no clear border lines
to make it look professional"*.

WHAT WAS WRONG, STATED AS A CLASS. Not "the PDF has no borders" — that is the instance. The class is
that **a receipt's APPEARANCE had no home**, so the two media that print one receipt described it
twice and differently:

* `render.render_html` held a private `_CSS` string with a few hairlines in it.
* the PDF was not described at all. It was reconstructed from `render_words`, whose actual job is a
  round-trip PROOF — it reproduces the positions of the words on the ORIGINAL POS's paper receipt so
  that POS's own parser can read them back and prove nothing was lost (see `render`'s docstring).
  A parser fixture has no rules, no boxes and no text runs, only words at measured offsets. Asked to
  be a customer's document it produced exactly that: a page with zero drawn lines, and phrases blown
  apart mid-word — "Example    Wireless", "Credit   Card   Charge" — because every word was placed
  at an offset computed for a different medium's metrics.

So "what does this document look like" lives HERE, once, as numbers and as a list of the rules that
are drawn. `render.render_html` builds its stylesheet from these values and the PDF draws from the
same ones, which is what makes a border added once appear in both. A second description of the same
rule fails the build (`harness_pos_receipt_document.py` §A).

NOT the till slip. The 80 mm thermal slip is a different layout with its own content home
(`sale_receipt.slip_model` → `receipt_escpos.lines` → `receipt_pdf_render.slip_pdf`), it is printed
on a roll with no columns to rule, and the owner said it comes out correctly. Nothing here touches
it.

NOT `render_words`. It keeps its round-trip job and its proof unchanged; it simply stops being asked
a question it was never answering.

RULE TWO: no tenant, carrier or product is named. Every value here is a measurement, and a tenant
that wants a different look changes its receipt template, never this file.
"""

# ── the page ─────────────────────────────────────────────────────────────────────────────────────
#: Point sizes. The PDF works in points natively; the stylesheet converts (1 pt = 1/72 in, and the
#: browser's px are converted at 96 dpi so the screen and the sheet agree).
PAGE_MARGIN_PT = 36.0          # 0.5 in — the frame sits inside this
BODY_PT = 9.0
SMALL_PT = 7.5
TITLE_PT = 17.0
STORE_PT = 10.0

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
#: The browser's equivalent. Named here so the two media are not two font decisions.
CSS_FONT_STACK = "Arial, Helvetica, sans-serif"

# ── the rules that are drawn ─────────────────────────────────────────────────────────────────────
#: Line weights in points. `HAIRLINE` separates rows inside a table; `RULE` bounds a table or a
#: band; `HEAVY` is the one line under the grand total.
HAIRLINE_PT = 0.5
RULE_PT = 0.9
HEAVY_PT = 1.4

#: Greys, as 0–1 for reportlab. The stylesheet derives its hex from these, so a lightened rule
#: lightens in both media.
INK = 0.07
RULE_GREY = 0.45
HAIRLINE_GREY = 0.80
BAND_GREY = 0.93           # the items header band
LABEL_GREY = 0.35

#: WHICH rules this document draws. The list is the contract: every entry is drawn by BOTH media,
#: and §A fails the build if one of them stops carrying one. Naming them is what stops "the screen
#: has a line the PDF has not" from recurring, which is the defect this module exists for.
RULES = (
    "frame",            # the outer border around the whole receipt
    "header_rule",      # under the shop block / invoice meta
    "items_top",        # above the column headings
    "items_header",     # under the column headings
    "items_row",        # between item rows (hairline)
    "items_bottom",     # under the last item
    "totals_box",       # the box around the totals
    "total_heavy",      # the heavy line above the grand total
    "footer_rule",      # above the legal footer
)

# ── proportions ──────────────────────────────────────────────────────────────────────────────────
CELL_PAD_PT = 4.0
ROW_PT = 15.0              # one item row
HEAD_ROW_PT = 16.0
SECTION_GAP_PT = 12.0
TOTALS_WIDTH_PT = 215.0    # the totals box, right-aligned under the table

#: A column's share of the table when nothing better is known. The money and quantity columns are
#: narrow and fixed; whatever is left is split between the describing columns, so a receipt with one
#: description column does not leave half the sheet blank.
NARROW_KINDS = {"money": 68.0, "money_total": 72.0, "qty": 34.0}
MIN_TEXT_COL_PT = 56.0


def column_width_pt(col, table_width_pt: float, text_cols: int, taken_pt: float) -> float:
    """How wide this column is drawn. Money and quantity take a fixed, legible width; the describing
    columns share what is left. Shared by both media so a column cannot be one width on screen and
    another on paper."""
    fixed = NARROW_KINDS.get((col or {}).get("kind") or "")
    if fixed:
        return fixed
    if text_cols <= 0:
        return max(table_width_pt - taken_pt, MIN_TEXT_COL_PT)
    return max((table_width_pt - taken_pt) / text_cols, MIN_TEXT_COL_PT)


def visible_columns(doc) -> list:
    """The columns this receipt actually prints: the ones at least one row fills.

    THE OWNER'S "Product SKU". Every row of receipt 4605 had an empty SKU, so the column printed as a
    heading over a column of nothing and pushed the real content right. A column no row fills is not
    data, it is a gap with a title.

    One home, called by both media: dropping it in each renderer separately is how the PDF and the
    screen come to disagree about how many columns there are. A column whose kind is money or
    quantity is KEPT even when empty — a receipt that silently drops its price column because a
    free-of-charge sale has no figures would be worse than a blank cell.
    """
    cols = (doc or {}).get("columns") or []
    items = (doc or {}).get("items") or []
    if not items:
        # No rows at all is not evidence that a column is unused — it is a document with nothing in
        # it yet. Print the headings it declares rather than a table of two columns.
        return list(cols)
    out = []
    for c in cols:
        kind = c.get("kind") or ""
        if kind in NARROW_KINDS:
            out.append(c)
            continue
        key = c.get("key")
        if any(str(((it or {}).get("cells") or {}).get(key) or "").strip() for it in items):
            out.append(c)
    # Never return nothing: a document with no filled column still prints its headings rather than
    # an empty page the cashier cannot explain.
    return out or list(cols)


# ── the stylesheet, derived from the values above ────────────────────────────────────────────────
def _grey(v: float) -> str:
    n = max(0, min(255, round(v * 255)))
    return f"#{n:02x}{n:02x}{n:02x}"


def _px(pt: float) -> float:
    """Points to CSS pixels (96 dpi), so a 0.9 pt rule is the same thickness in both media."""
    return round(pt * 96.0 / 72.0, 2)


def css() -> str:
    """The document stylesheet, BUILT from the constants above rather than written beside them.

    `render.render_html` dereferences this. It used to hold its own `_CSS` string, which is how the
    screen came to have hairlines the PDF never heard about.
    """
    return f"""
* {{ box-sizing: border-box; }}
body {{ font-family: {CSS_FONT_STACK}; color: {_grey(INK)}; margin: 0; padding: 16px;
        font-size: {_px(BODY_PT)}px; background: #fff; }}
.receipt {{ max-width: 760px; margin: 0 auto; border: {_px(RULE_PT)}px solid {_grey(RULE_GREY)};
            padding: {_px(PAGE_MARGIN_PT) / 2}px; }}
.head {{ display: flex; justify-content: space-between; gap: 24px;
         padding-bottom: {_px(SECTION_GAP_PT) / 2}px;
         border-bottom: {_px(RULE_PT)}px solid {_grey(RULE_GREY)}; margin-bottom: {_px(SECTION_GAP_PT)}px; }}
.rtitle {{ font-size: {_px(TITLE_PT)}px; font-weight: 700; text-align: right; margin-bottom: 4px;
           letter-spacing: 0.02em; }}
.store {{ font-weight: 700; font-size: {_px(STORE_PT)}px; line-height: 1.45; }}
.store .store-sub {{ font-weight: 400; font-size: {_px(BODY_PT)}px; color: {_grey(LABEL_GREY)}; }}
.meta td {{ padding: 1px 4px; }}
.meta .ml {{ color: {_grey(LABEL_GREY)}; text-align: right; }}
.meta .mv {{ font-weight: 700; }}
.parties {{ display: flex; gap: 40px; margin: 0 0 {_px(SECTION_GAP_PT)}px; }}
.plabel {{ color: {_grey(LABEL_GREY)}; font-weight: 700; margin-bottom: 2px;
           font-size: {_px(SMALL_PT)}px; text-transform: uppercase; letter-spacing: 0.06em; }}
table.items {{ width: 100%; border-collapse: collapse; margin: 0 0 {_px(SECTION_GAP_PT)}px;
               border: {_px(RULE_PT)}px solid {_grey(RULE_GREY)}; }}
table.items th {{ background: {_grey(BAND_GREY)}; text-align: left;
                  padding: {_px(CELL_PAD_PT)}px; font-size: {_px(SMALL_PT)}px;
                  text-transform: uppercase; letter-spacing: 0.04em;
                  border-bottom: {_px(RULE_PT)}px solid {_grey(RULE_GREY)}; }}
table.items td {{ padding: {_px(CELL_PAD_PT)}px; vertical-align: top;
                  border-bottom: {_px(HAIRLINE_PT)}px solid {_grey(HAIRLINE_GREY)}; }}
table.items tr:last-child td {{ border-bottom: none; }}
.right {{ text-align: right; }}
.totals-wrap {{ display: flex; justify-content: flex-end; margin-bottom: {_px(SECTION_GAP_PT)}px; }}
table.totals {{ width: {_px(TOTALS_WIDTH_PT)}px; border-collapse: collapse;
                border: {_px(RULE_PT)}px solid {_grey(RULE_GREY)}; }}
table.totals td {{ padding: {_px(CELL_PAD_PT) - 1}px {_px(CELL_PAD_PT)}px; }}
table.totals .tl {{ color: {_grey(LABEL_GREY)}; }}
table.totals .tv {{ text-align: right; font-weight: 700; }}
table.totals tr.grand td {{ border-top: {_px(HEAVY_PT)}px solid {_grey(INK)};
                            font-size: {_px(BODY_PT) + 1}px; font-weight: 700;
                            color: {_grey(INK)}; }}
table.totals tr.pay td {{ border-top: {_px(HAIRLINE_PT)}px solid {_grey(HAIRLINE_GREY)};
                          color: {_grey(LABEL_GREY)}; }}
.comments {{ margin: {_px(SECTION_GAP_PT)}px 0; }}
.section {{ margin: {_px(SECTION_GAP_PT)}px 0; }}
.stitle {{ font-weight: 700; margin-bottom: 4px; font-size: {_px(SMALL_PT)}px;
           text-transform: uppercase; letter-spacing: 0.04em; }}
table.sub {{ border-collapse: collapse; }}
table.sub th, table.sub td {{ border: {_px(HAIRLINE_PT)}px solid {_grey(HAIRLINE_GREY)};
                              padding: 2px 8px; text-align: left; }}
.footer {{ margin-top: {_px(SECTION_GAP_PT)}px; padding-top: {_px(SECTION_GAP_PT) / 2}px;
           border-top: {_px(RULE_PT)}px solid {_grey(RULE_GREY)}; color: {_grey(LABEL_GREY)};
           font-size: {_px(SMALL_PT)}px; line-height: 1.5; white-space: pre-wrap; }}
[contenteditable="true"] {{ outline: 1px dashed #4c8bf5; background: #f5f9ff; }}
@media print {{ body {{ padding: 10mm 8mm; }}
                [contenteditable] {{ outline: none; background: none; }} }}
"""
