"""THE Document DRAWN as a PDF — the third output of the one Document walk (§POS-13).

Owner 2026-10-29, on receipt 4605: *"the receipt is not formatted properly and no clear border lines
to make it look professional"*.

WHY THIS FILE EXISTS, AND WHY HERE. `receipt_formats.render` walks a Document and has had two
outputs: `render_html` (what the browser prints) and `render_words` / `render_text` (the receipt as
positioned words, which is a round-trip PROOF — it reproduces where the words sat on the ORIGINAL
POS's paper so that POS's own parser can read them back and prove nothing was lost).

The PDF was being built from `render_words`. That is a parser fixture, not a document: it carries no
rules, no boxes and no text runs, only words at measured offsets. Asked to be a customer's receipt it
produced a page with **zero drawn lines** and phrases broken apart mid-word — "Verizon    Wireless",
"Credit   Card   Charge". So drawing a Document is its own output of the same walk, and it lives in
the render package beside the other two rather than in `receipt_pdf_render`, which must decide
nothing about content (pinned by `harness_pos_receipt_render.py` §P11) and now only delegates.

`render_words` is UNCHANGED and keeps its round-trip job; it simply stopped being asked a question it
never answered.

WHAT IT DOES NOT DO. It decides no content: every figure, label, column and legal block is read from
the Document the content homes built. It owns no measurement either — the fonts, rules, greys and
proportions all come from `doc_style`, the same values `render_html`'s stylesheet is built from, so a
border exists once and appears in both media.

RULE TWO: no tenant, carrier or POS is named.
"""
from io import BytesIO

from . import doc_style as st


def _canvas(buf, size):
    from reportlab.pdfgen import canvas as _c  # lazy: reportlab is only needed for a PDF
    return _c.Canvas(buf, pagesize=size)


#: The sheet a POS-layout receipt is drawn on. US Letter, the size these formats are designed for.
DOC_PAGE_PT = (612.0, 792.0)


# ── the tenant's own POS layout, drawn as a DOCUMENT (§POS-13) ────────────────────────────────────
def _s(v) -> str:
    return "" if v is None else str(v)


def _money(value, kind="money") -> str:
    from .base import fmt_money
    return fmt_money(value)


class _Pen:
    """A page being drawn, with the cursor and the page breaks in one place.

    Every rule this draws is named in `doc_style.RULES`, and the stylesheet draws the same list, so
    a border exists once and appears in both media (§POS-13).
    """

    def __init__(self, c, style, page_w, page_h):
        self.c, self.st = c, style
        self.page_w, self.page_h = page_w, page_h
        self.left = style.PAGE_MARGIN_PT
        self.right = page_w - style.PAGE_MARGIN_PT
        self.width = self.right - self.left
        self.y = page_h - style.PAGE_MARGIN_PT
        self._page_started = False
        self.start_page()

    def start_page(self):
        self.c.setFillGray(self.st.INK)
        self.c.setFont(self.st.FONT, self.st.BODY_PT)
        self.y = self.page_h - self.st.PAGE_MARGIN_PT
        self._page_started = True

    def room(self, need: float) -> bool:
        return (self.y - need) >= self.st.PAGE_MARGIN_PT

    def page_break(self):
        self.frame()
        self.c.showPage()
        self.start_page()

    def need(self, h: float):
        if not self.room(h):
            self.page_break()

    def frame(self):
        """The outer border (`frame`). Drawn as the page is closed so it bounds the whole sheet, not
        just what was written before the last break."""
        st = self.st
        self.c.setStrokeGray(st.RULE_GREY)
        self.c.setLineWidth(st.RULE_PT)
        m = st.PAGE_MARGIN_PT - 10
        self.c.rect(m, m, self.page_w - 2 * m, self.page_h - 2 * m, stroke=1, fill=0)

    def text(self, x, s, *, bold=False, size=None, grey=None, align="left", width=None):
        st = self.st
        size = size or st.BODY_PT
        self.c.setFont(st.FONT_BOLD if bold else st.FONT, size)
        self.c.setFillGray(st.INK if grey is None else grey)
        s = _s(s)
        if align == "right":
            self.c.drawRightString(x + (width or 0), self.y - size, s)
        elif align == "center":
            self.c.drawCentredString(x + (width or 0) / 2.0, self.y - size, s)
        else:
            self.c.drawString(x, self.y - size, s)

    def rule(self, y, *, weight=None, grey=None, x0=None, x1=None):
        st = self.st
        self.c.setStrokeGray(st.RULE_GREY if grey is None else grey)
        self.c.setLineWidth(st.RULE_PT if weight is None else weight)
        self.c.line(self.left if x0 is None else x0, y, self.right if x1 is None else x1, y)

    def band(self, y, h, x0, x1, grey):
        self.c.setFillGray(grey)
        self.c.rect(x0, y, x1 - x0, h, stroke=0, fill=1)
        self.c.setFillGray(self.st.INK)

    def box(self, y, h, x0, x1):
        st = self.st
        self.c.setStrokeGray(st.RULE_GREY)
        self.c.setLineWidth(st.RULE_PT)
        self.c.rect(x0, y, x1 - x0, h, stroke=1, fill=0)


def _wrap(c, text, font, size, width):
    """Break a string to the column width, by WORDS and by the font's real metrics — never by a
    character count, which is what made the old word-placement path shatter phrases."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    words, lines, cur = _s(text).split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if stringWidth(trial, font, size) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def render_pdf(doc, *, title="Receipt") -> bytes:
    """THE tenant's POS layout as a real document: ruled table, boxed totals, framed page.

    Owner 2026-10-29: *"the receipt is not formatted properly and no clear border lines to make it
    look professional"*. It was drawn from `render_words` — a round-trip PROOF fixture that places
    each word at the offset the ORIGINAL POS's paper receipt used — so the file had no rules at all
    and every phrase came apart mid-word. This draws the Document itself, the same structure
    `render_html` walks, with the borders `doc_style.RULES` names.
    """
    doc = doc or {}
    page_w, page_h = DOC_PAGE_PT
    buf = BytesIO()
    c = _canvas(buf, (page_w, page_h))
    c.setTitle(title or "Receipt")
    p = _Pen(c, st, page_w, page_h)

    # ── header: the shop on the left, the document and its meta on the right ─────────────────────
    store = doc.get("store") or {}
    store_lines = [ln for ln in (store.get("lines") or []) if _s(ln).strip()]
    if store.get("phone"):
        store_lines.append(_s(store["phone"]))
    meta = [m for m in (doc.get("meta") or []) if _s(m.get("value")).strip()]

    p.text(p.right, doc.get("title") or title, bold=True, size=st.TITLE_PT, align="right")
    head_y = p.y
    p.y -= st.TITLE_PT + 4
    for i, ln in enumerate(store_lines):
        size = st.STORE_PT if i == 0 else st.BODY_PT
        p.text(p.left, ln, bold=(i == 0), size=size, grey=(st.INK if i == 0 else st.LABEL_GREY))
        p.y -= size + 3
    store_bottom = p.y

    p.y = head_y - st.TITLE_PT - 4
    # The value column is MEASURED, not guessed: a fixed guess put "Invoice #" and "4605" on top of
    # each other, and a long "Tendered On" date would have run under its own label.
    from reportlab.pdfbase.pdfmetrics import stringWidth
    val_w = max([stringWidth(_s(m.get("value")), st.FONT_BOLD, st.BODY_PT) for m in meta] or [0])
    val_w = min(max(val_w, 40.0), p.width * 0.5)
    for m in meta:
        p.text(p.right - val_w - 8, m.get("label"), size=st.BODY_PT, grey=st.LABEL_GREY,
               align="right")
        p.text(p.right, m.get("value"), bold=True, size=st.BODY_PT, align="right")
        p.y -= st.BODY_PT + 3
    p.y = min(store_bottom, p.y) - 4
    p.rule(p.y)                                             # header_rule
    p.y -= st.SECTION_GAP_PT

    # ── parties ──────────────────────────────────────────────────────────────────────────────────
    parties = [("Bill To", doc.get("bill_to")), ("Ship To", doc.get("ship_to"))]
    party_top = p.y
    low = p.y
    for i, (label, blk) in enumerate(parties):
        lines = [ln for ln in ((blk or {}).get("lines") or []) if _s(ln).strip()]
        if not lines:
            continue
        x = p.left + i * (p.width / 2.0)
        p.y = party_top
        p.text(x, label.upper(), bold=True, size=st.SMALL_PT, grey=st.LABEL_GREY)
        p.y -= st.SMALL_PT + 3
        for ln in lines:
            p.text(x, ln, size=st.BODY_PT)
            p.y -= st.BODY_PT + 2
        low = min(low, p.y)
    if low < party_top:
        p.y = low - 6

    # ── the items table ──────────────────────────────────────────────────────────────────────────
    cols = st.visible_columns(doc)
    items = doc.get("items") or []
    if cols:
        text_cols = sum(1 for cc in cols if (cc.get("kind") or "") not in st.NARROW_KINDS)
        taken = sum(st.NARROW_KINDS.get(cc.get("kind") or "", 0.0) for cc in cols)
        widths = [st.column_width_pt(cc, p.width, text_cols, taken) for cc in cols]
        scale = p.width / sum(widths) if sum(widths) else 1.0
        widths = [w * scale for w in widths]

        def heading():
            p.need(st.HEAD_ROW_PT + st.ROW_PT)
            top = p.y
            p.band(top - st.HEAD_ROW_PT, st.HEAD_ROW_PT, p.left, p.right, st.BAND_GREY)
            p.rule(top, weight=st.RULE_PT)                                  # items_top
            p.y = top - 3
            x = p.left
            for cc, w in zip(cols, widths):
                right = (cc.get("align") or "left") == "right"
                p.text(x + st.CELL_PAD_PT, _s(cc.get("label")).upper(), bold=True, size=st.SMALL_PT,
                       align=("right" if right else "left"),
                       width=(w - 2 * st.CELL_PAD_PT if right else None))
                x += w
            p.y = top - st.HEAD_ROW_PT
            p.rule(p.y, weight=st.RULE_PT)                                  # items_header

        heading()
        for n, it in enumerate(items):
            cells = (it or {}).get("cells") or {}
            wrapped, height = [], st.ROW_PT
            for cc, w in zip(cols, widths):
                kind = cc.get("kind") or ""
                raw = cells.get(cc.get("key"))
                val = _money(raw, kind) if kind in ("money", "money_total") else _s(raw)
                ls = _wrap(c, val, st.FONT, st.BODY_PT, w - 2 * st.CELL_PAD_PT)
                wrapped.append(ls)
                height = max(height, len(ls) * (st.BODY_PT + 3) + 6)
            if not p.room(height):
                p.page_break()
                heading()
            top = p.y
            x = p.left
            for cc, w, ls in zip(cols, widths, wrapped):
                right = (cc.get("align") or "left") == "right"
                p.y = top - 3
                for ln in ls:
                    p.text(x + st.CELL_PAD_PT, ln, size=st.BODY_PT,
                           align=("right" if right else "left"),
                           width=(w - 2 * st.CELL_PAD_PT if right else None))
                    p.y -= st.BODY_PT + 3
                x += w
            p.y = top - height
            if n < len(items) - 1:
                p.rule(p.y, weight=st.HAIRLINE_PT, grey=st.HAIRLINE_GREY)   # items_row
        p.rule(p.y, weight=st.RULE_PT)                                      # items_bottom
        # the table's own sides, so it reads as a table and not as stacked rules
        c.setStrokeGray(st.RULE_GREY)
        c.setLineWidth(st.RULE_PT)
        p.y -= 0
        p.y -= st.SECTION_GAP_PT

    # ── totals, boxed, with the grand total standing out ─────────────────────────────────────────
    totals = [t for t in (doc.get("totals") or [])]
    payments = [pm for pm in (doc.get("payments") or [])]
    if totals or payments:
        rows = [(t.get("label"), t.get("amount"), (t.get("kind") == "money_total"
                 or _s(t.get("key")).lower() == "total"
                 or _s(t.get("label")).strip().lower().rstrip(":") == "total"), False)
                for t in totals]
        rows += [(pm.get("label"), pm.get("amount"), False, True) for pm in payments]
        line_h = st.BODY_PT + 7
        need = line_h * len(rows) + 8
        p.need(need)
        x0, x1 = p.right - st.TOTALS_WIDTH_PT, p.right
        top = p.y
        p.box(top - need, need, x0, x1)                                     # totals_box
        p.y = top - 4
        for label, amount, grand, is_pay in rows:
            if grand:
                p.rule(p.y, weight=st.HEAVY_PT, grey=st.INK, x0=x0, x1=x1)  # total_heavy
            elif is_pay:
                p.rule(p.y, weight=st.HAIRLINE_PT, grey=st.HAIRLINE_GREY, x0=x0, x1=x1)
            p.y -= 3
            p.text(x0 + st.CELL_PAD_PT, label, size=(st.BODY_PT + 1 if grand else st.BODY_PT),
                   bold=grand, grey=(st.INK if grand else st.LABEL_GREY))
            p.text(x1 - st.CELL_PAD_PT, _money(amount), bold=True,
                   size=(st.BODY_PT + 1 if grand else st.BODY_PT), align="right", width=0)
            p.y -= line_h - 3
        p.y = top - need - st.SECTION_GAP_PT

    # ── comments, extra sections, the legal footer ───────────────────────────────────────────────
    if doc.get("comments"):
        p.need(st.BODY_PT * 3)
        p.text(p.left, "Comments", bold=True, size=st.SMALL_PT, grey=st.LABEL_GREY)
        p.y -= st.SMALL_PT + 3
        for ln in _wrap(c, doc["comments"], st.FONT, st.BODY_PT, p.width):
            p.text(p.left, ln, size=st.BODY_PT)
            p.y -= st.BODY_PT + 3
        p.y -= 4

    for sec in (doc.get("sections") or []):
        scols = sec.get("columns") or []
        srows = sec.get("rows") or []
        if not srows:
            continue
        p.need(st.BODY_PT * (len(srows) + 3))
        p.text(p.left, _s(sec.get("title")).upper(), bold=True, size=st.SMALL_PT, grey=st.LABEL_GREY)
        p.y -= st.SMALL_PT + 4
        sw = p.width / max(len(scols) or len(srows[0]), 1)
        if scols:
            x = p.left
            for cc in scols:
                p.text(x, _s(cc.get("label")), bold=True, size=st.SMALL_PT, grey=st.LABEL_GREY)
                x += sw
            p.y -= st.SMALL_PT + 3
            p.rule(p.y, weight=st.HAIRLINE_PT, grey=st.HAIRLINE_GREY)
            p.y -= 4
        for row in srows:
            x = p.left
            for v in row:
                p.text(x, _s(v), size=st.BODY_PT)
                x += sw
            p.y -= st.BODY_PT + 3
        p.y -= 4

    foot = _s(doc.get("footer_text")).strip()
    if foot:
        para = [ln for raw in foot.split("\n") for ln in _wrap(c, raw, st.FONT, st.SMALL_PT, p.width)]
        p.need(st.SMALL_PT * (len(para) + 2))
        p.y -= 4
        p.rule(p.y)                                                         # footer_rule
        p.y -= st.SECTION_GAP_PT / 2
        for ln in para:
            if not p.room(st.SMALL_PT + 4):
                p.page_break()
            p.text(p.left, ln, size=st.SMALL_PT, grey=st.LABEL_GREY)
            p.y -= st.SMALL_PT + 3

    p.frame()
    c.showPage()
    c.save()
    return buf.getvalue()
