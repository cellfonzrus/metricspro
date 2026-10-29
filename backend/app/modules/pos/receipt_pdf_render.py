"""THE receipt as a PDF — a THIRD medium of the two content homes, never a third receipt (§POS-08).

Owner 2026-10-21, verbatim: *"the receipt is going the body of the meial it should as a pdf
attachment aswell"*.

WHY A PDF AT ALL. An emailed receipt in the body of the mail is a receipt the customer cannot keep:
it cannot be forwarded to an insurer, attached to an expense claim or opened on a phone a week later
without the mail client re-flowing it. So the same receipt also leaves as a file.

WHAT THIS IS NOT. It is NOT a second receipt, and it does not read the sale, the template or the
customer for itself. The receipt has two content homes already and this module renders THEM:

  • the till slip — `sale_receipt.slip_model` turned into the exact rows of characters that print by
    `receipt_escpos.lines`, the SAME rows the printer cuts, laid onto a page as wide as the roll;
  • the tenant's own POS layout — `sale_receipt.document` turned into positioned words by
    `receipt_formats.render.render_words` in the geometry that format declares, the same words its
    own parser reads back.

So a figure that is right on paper is right in the file, and nothing here decides what a receipt
says. The layout decision is not taken here either: `sale_receipt.clean_layout` is the one rule, the
same call the HTML body makes, so the attachment can never be a different layout from the mail it
rides on.

NOT `receipt_pdf.py` — that READS a PDF someone uploaded (pdfplumber, for the format parsers). This
module WRITES one. The two never meet; the round trip they would make (write a receipt, parse it
back) is exercised by `render_words` / `engine.group_rows` already.

RULE TWO: no tenant, POS or printer is named. The roll width comes from the store's declared paper
setting and the page geometry from the format's own `PRINT_LAYOUT`.
"""
from io import BytesIO

#: PDF points per millimetre — a roll is declared in mm and a page is measured in points.
PT_PER_MM = 72.0 / 25.4

#: A till slip is columns of characters (`receipt_escpos.two_col` right-aligns a total by COUNTING
#: them), so the file has to be set in a monospace face or every total walks off its edge.
SLIP_FONT = "Courier"
SLIP_FONT_BOLD = "Courier-Bold"
SLIP_FONT_PT = 8.5
SLIP_LEADING_PT = 10.5
SLIP_MARGIN_PT = 8.0

#: The letter page the POS layouts are drawn in — `render_words` x-coordinates run 27…594, which is
#: this page's own width in points, because that geometry came from the uploaded receipts themselves.
DOC_PAGE_PT = (612.0, 792.0)
DOC_FONT = "Helvetica"
DOC_FONT_PT = 7.5
DOC_MARGIN_PT = 36.0


def _canvas(buf, size):
    from reportlab.pdfgen import canvas as _c  # lazy: reportlab is only needed when a PDF is asked for
    return _c.Canvas(buf, pagesize=size)


def filename(transaction_id="", prefix="receipt") -> str:
    """What the attachment is called in the customer's inbox. A transaction number when the sale has
    one, because a customer with two receipts from one shop needs to tell them apart.

    A SLUGIFIER, not an identity fold: anything that is not alphanumeric is SUBSTITUTED with `_`
    rather than dropped, which is the house's spelling for an attachment name (see
    `notify/report_registry`, `notify/finance_reports`). Dropping would let two different
    transaction references collapse onto one file name, and a dropped fold is also the shape of the
    accent-folding defect `harness_payroll_hours_basis_lock.py` §E guards against — nothing is ever
    MATCHED on this value, so it must not look like a match key.
    """
    txn = "".join(ch if ch.isalnum() else "_" for ch in str(transaction_id or "")).strip("_")
    return f"{prefix}-{txn}.pdf" if txn else f"{prefix}.pdf"


def _fit_scale(rows, usable: float) -> float:
    """How much the slip's type has to shrink for its WIDEST row to fit the roll — never above 1.0,
    so a short receipt is set at the ordinary size. Measured through reportlab's own metrics rather
    than a character-width constant, because a constant is a second answer to "how wide is this
    text" and the drawing is the first one."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    need = 0.0
    for r in (rows or []):
        text = str((r or {}).get("text") or "")
        if not text:
            continue
        size = SLIP_FONT_PT * (1.35 if (r or {}).get("big") else 1.0)
        need = max(need, stringWidth(text, SLIP_FONT, size))
    if need <= 0 or usable <= 0:
        return 1.0
    return min(1.0, usable / need)


def slip_pdf(rows, *, paper_width_mm=80, title="Receipt") -> bytes:
    """The till slip as ONE continuous page as wide as the roll and as long as the slip — a roll has
    no page break, and a receipt split across two A4 sheets is not what came out of the printer.

    `rows` is `receipt_escpos.lines(model)`: `{"text", "align", "bold", "big"}`, already wrapped to
    the roll's character width by the one home that knows it.
    """
    rows = [r for r in (rows or [])]
    width = max(120.0, float(paper_width_mm or 80) * PT_PER_MM)
    usable = width - SLIP_MARGIN_PT * 2
    # THE TYPE IS SET TO FIT THE ROLL, which is what a thermal printer does: it fits its whole
    # character grid (`sale_receipt.PAPER_COLS`) across the paper, and `receipt_escpos.two_col`
    # right-aligns every figure by COUNTING those characters. At a fixed 8.5 pt, 48 Courier columns
    # measure ~245 pt and an 80 mm roll is ~227 pt, so the last six characters of every right-hand
    # column fell off the page: a customer's receipt that read "Subtotal 8" where the slip says
    # "Subtotal 808.75". Scaling here rather than widening the page keeps the file the size of the
    # roll (what `neg-P2` pins) and keeps the arithmetic the printer's own.
    scale = _fit_scale(rows, usable)
    leading = SLIP_LEADING_PT * scale
    height = max(80.0, SLIP_MARGIN_PT * 2
                 + sum(leading * (1.35 if r.get("big") else 1.0) for r in rows or [{}]))
    buf = BytesIO()
    c = _canvas(buf, (width, height))
    c.setTitle(title or "Receipt")
    y = height - SLIP_MARGIN_PT - SLIP_FONT_PT * scale
    for r in rows:
        text = str(r.get("text") or "")
        big = bool(r.get("big"))
        size = SLIP_FONT_PT * scale * (1.35 if big else 1.0)
        c.setFont(SLIP_FONT_BOLD if (r.get("bold") or big) else SLIP_FONT, size)
        w = c.stringWidth(text, SLIP_FONT, size)
        align = r.get("align") or "left"
        if align == "center":
            x = SLIP_MARGIN_PT + max(0.0, (usable - w) / 2)
        elif align == "right":
            x = SLIP_MARGIN_PT + max(0.0, usable - w)
        else:
            x = SLIP_MARGIN_PT
        c.drawString(x, y, text)
        y -= leading * (1.35 if big else 1.0)
    c.showPage()
    c.save()
    return buf.getvalue()


def words_pdf(words, *, title="Receipt") -> bytes:
    """The tenant's own POS layout as a PDF: the positioned words `render_words` produced, drawn
    where that format says they go. A receipt longer than the page continues onto the next one —
    paginated by the words' own `top`, so no line is ever dropped to make it fit."""
    words = [w for w in (words or []) if str(w.get("text") or "").strip()]
    page_w, page_h = DOC_PAGE_PT
    usable = page_h - DOC_MARGIN_PT * 2
    buf = BytesIO()
    c = _canvas(buf, (page_w, page_h))
    c.setTitle(title or "Receipt")
    if not words:
        c.setFont(DOC_FONT, DOC_FONT_PT)
        c.showPage()
        c.save()
        return buf.getvalue()
    top0 = min(float(w.get("top") or 0) for w in words)
    pages = {}
    for w in words:
        offset = float(w.get("top") or 0) - top0
        pages.setdefault(int(offset // usable), []).append((offset, w))
    # The font is set at the START of each page, never after `showPage` — touching the canvas after
    # a page is closed opens the next one, and reportlab then writes that empty page into the file.
    # A blank second sheet in a customer's receipt is a defect they can see.
    for pi in sorted(pages):
        c.setFont(DOC_FONT, DOC_FONT_PT)
        for offset, w in sorted(pages[pi], key=lambda t: (t[0], float(t[1].get("x0") or 0))):
            y = page_h - DOC_MARGIN_PT - (offset - pi * usable) - DOC_FONT_PT
            c.drawString(float(w.get("x0") or 0), y, str(w.get("text") or ""))
        c.showPage()
    c.save()
    return buf.getvalue()


def receipt_pdf(snap, template_v, *, fmt=None, render_words=None, brand=None,
                disclaimer_blocks=None, store_name="", store_row=None, fold_discount=False,
                tenant_name="", employee_name="", short_customer_name=False, remit_block="",
                customer_lines=None) -> bytes:
    """THE receipt of this sale as a PDF, from the same inputs `sale_receipt.html` is given — and the
    same layout decision, taken by `sale_receipt.clean_layout` rather than again here, so the file
    and the mail it rides on can never be two different receipts.

    `render_words` is injected (`receipt_formats.render.render_words`) for the same reason
    `sale_receipt.html` injects `render_document`: this module draws, it does not choose.
    """
    from app.modules.pos import sale_receipt as _sr
    from app.modules.pos import receipt_escpos as _escpos
    layout = _sr.clean_layout((template_v or {}).get("layout"),
                              format_available=bool(fmt and render_words))
    snap = _sr.for_render(snap, short_customer_name=short_customer_name)
    title = f'Receipt {snap["transaction_id"]}'.strip()
    if layout == _sr.LAYOUT_POS_FORMAT:
        doc = _sr.document(snap, fmt, template_v, brand=brand, store_row=store_row,
                           tenant_name=tenant_name, employee_name=employee_name,
                           remit_block=remit_block, customer_lines=customer_lines,
                           store_name=store_name, disclaimer_blocks=disclaimer_blocks)
        return words_pdf(render_words(doc, getattr(fmt, "PRINT_LAYOUT", None)), title=title)
    model = _sr.slip_model(snap, template_v, brand=brand, disclaimer_blocks=disclaimer_blocks,
                           store_name=store_name, fold_discount=fold_discount,
                           tenant_name=tenant_name, employee_name=employee_name,
                           remit_block=remit_block, customer_lines=customer_lines,
                           store_row=store_row)
    return slip_pdf(_escpos.lines(model),
                    paper_width_mm=(template_v or {}).get("paper_width_mm"), title=title)
