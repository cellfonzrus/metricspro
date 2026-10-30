"""THE receipt of a register sale — one home for what it says, how it looks, and who it is sent to
(owner ask 2026-10-17, index §POS-02). PURE: no IO, no app imports, no DB.

Owner, verbatim: *"admin should be able to edit the receipt, teh template for the receipt for vzone
ahoul be same as uplaoded from rq, the email option should show which email to send to and it should
be editable, the from email address should show the company name Vzone Wireless or whatever name is
set up during onboarding"*.

**THE CLASS THIS FIXES.** "What does this org's receipt look like?" had TWO answers:

  1. the register's own sale — built as HTML *in the browser* (`pos/sales/page.tsx::buildReceiptHtml`),
     the only place the template toggles, the brand block and the disclaimers were ever applied;
  2. a receipt IMPORTED or rebuilt from the landed reports — built as a structured Document and
     printed by `receipt_formats/render.render_html`, in the layout of the POS it came from.

Two paths, so an admin editing "the receipt template" changed one of them and the other kept its own
look — and nothing could render a receipt where there is no browser, which is why emailing a receipt
had never been built at all (`email_receipt_subject` has sat in the config registry marked *not yet
enforced* since the POS module landed). So this module answers it ONCE, for every caller: the print
view, the Sale Log reprint and the emailed copy all read `html()` here, and the format path hands the
SAME Document vocabulary the imported receipts already use to the SAME renderer. The register keeps
no second copy — it prints what the server returns.

**THE TWO LAYOUTS, both declared.** `LAYOUT_THERMAL` is the 58/80 mm till slip this POS has always
printed, which is what every tenant keeps unless it says otherwise. `LAYOUT_POS_FORMAT` prints the
sale in the tenant's own POS receipt layout — the one its uploaded receipts are parsed from and
reprinted in (`receipt_formats.registry`), which is what the owner's "same as uploaded from rq"
asks for. RULE TWO: the choice is a per-org config row (`pos.receipt_templates.layout` + the org's
declared POS), never a tenant name in code — nothing here knows what "vzone" or "rq" is; it is handed
a format module and a template row.

**WHAT IS NOT A RECEIPT DECISION.** Money is not recomputed here, ever. The figures printed are the
snapshot the checkout stored on `pos.sales.receipt` (its own subtotal, discount, tax, total, tendered
and change) — a reprint that re-derived a total would be a second answer to what the customer paid,
and the first one is already on the sale row. This module only chooses words and geometry.
"""
from __future__ import annotations

import html as _html

from . import receipt_store_block as _shop

LAYOUT_THERMAL = "thermal"
LAYOUT_POS_FORMAT = "pos_format"
LAYOUTS = (LAYOUT_THERMAL, LAYOUT_POS_FORMAT)

#: Paper widths mig 725 allows, and the pixel width each prints at (the browser's own mapping).
PAPER_PX = {58: 220, 80: 300}
DEFAULT_PAPER_MM = 80

#: How many characters of the printer's own font fit on each roll, for the renderer that writes to
#: the printer directly (§POS-04). It lives beside PAPER_PX because it is the same fact about the
#: same paper in the other medium's units, and `paper_options` still derives the offer from PAPER_PX.
PAPER_COLS = {58: 32, 80: 48}

#: THE PAGE A RECEIPT PRINTS ON — one rule, both layouts, every medium that goes through a browser
#: (§POS-11).
#:
#: Owner 2026-10-29: *"d not put hte reprint date and time on top woith the word Slae in the middle
#: of the screen"*. Neither was the receipt's. A browser stamps its OWN header and footer on anything
#: it prints — the date and time of the printing, the document's `<title>`, the URL, the page number
#: — unless the page declares its own `@page` margins. No stylesheet here had one, so every receipt
#: this platform has ever printed carried that stamp: a customer's receipt looking like a printout of
#: a web page, with a reprint timestamp on it that contradicts the sale's own date.
#:
#: `margin: 0` removes the PAGE's margin, which is what suppresses the stamp; each renderer then sets
#: its own padding so the ink does not run to the edge of a real sheet. One home, because the till
#: slip and the tenant's own POS layout are two layouts of ONE receipt and the browser must stamp
#: neither — and because the next layout gets it without anyone remembering.
PAGE_CSS = "@page { size: auto; margin: 0; }"

DEFAULT_FONT_PX = 12
DEFAULT_FOOTER = "Thank you for your business!"

#: The subject an emailed receipt carries when the org has declared none.
DEFAULT_SUBJECT = "Your receipt from {company}"
#: The one line of body an emailed receipt carries when the org has declared none. The receipt itself
#: follows it, so this says what the mail IS rather than restating the sale.
DEFAULT_BODY = "Thank you — your receipt is below."


class ReceiptError(ValueError):
    """A receipt that cannot be rendered says why. Never a blank page, never a partial slip."""


# ── the template, as the renderer needs it ────────────────────────────────────────────────────────
def clean_layout(value, *, format_available: bool = True) -> str:
    """Which layout applies. Blank or unknown → the till slip, because an unreadable setting must not
    change what a store prints. `pos_format` asked for with no registered format for the org's POS
    falls back to the till slip too — and `layout_note()` says so out loud, since a silent fallback
    is how a tenant ends up believing it prints a layout it does not.
    """
    want = str(value or "").strip().lower()
    if want == LAYOUT_POS_FORMAT and format_available:
        return LAYOUT_POS_FORMAT
    return LAYOUT_THERMAL


def layout_note(value, *, format_available: bool = True) -> str:
    """One sentence for the admin screen when the stored choice is not what will print."""
    want = str(value or "").strip().lower()
    if want == LAYOUT_POS_FORMAT and not format_available:
        return ("This tenant is set to print in its POS's own receipt layout, but no POS is declared "
                "for it yet (or its POS has no registered receipt format), so receipts print on the "
                "till slip until one is declared.")
    if want and want not in LAYOUTS:
        return f"'{want}' is not a receipt layout, so receipts print on the till slip."
    return ""


def template_view(row) -> dict:
    """The template row as the renderer reads it — every blank spelled once, so no caller has to
    remember that a null footer still prints the house line."""
    row = row or {}

    def _flag(key, default=True):
        v = row.get(key)
        return default if v is None else bool(v)

    paper = row.get("paper_width_mm")
    try:
        paper = int(paper)
    except (TypeError, ValueError):
        paper = DEFAULT_PAPER_MM
    if paper not in PAPER_PX:
        paper = DEFAULT_PAPER_MM
    try:
        font = int(row.get("font_size_px") or DEFAULT_FONT_PX)
    except (TypeError, ValueError):
        font = DEFAULT_FONT_PX
    font = min(16, max(9, font))
    footer = row.get("footer_text")
    return {
        "name": str(row.get("name") or "Default"),
        "header_text": str(row.get("header_text") or ""),
        "footer_text": DEFAULT_FOOTER if footer is None else str(footer),
        "show_store_name": _flag("show_store_name"),
        "show_customer": _flag("show_customer"),
        "show_employee": _flag("show_employee", False),
        "show_serials": _flag("show_serials"),
        "show_tax_breakdown": _flag("show_tax_breakdown"),
        "show_discounts": _flag("show_discounts"),
        "paper_width_mm": paper,
        "font_size_px": font,
        "layout": str(row.get("layout") or LAYOUT_THERMAL),
    }


# ── the snapshot, as the renderer needs it ────────────────────────────────────────────────────────
def _f(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _money(v) -> str:
    n = _f(v)
    return f"-${abs(n):,.2f}" if n < 0 else f"${n:,.2f}"


def _qty(v):
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return 0
    return int(n) if float(n).is_integer() else round(n, 2)


def snapshot_view(snapshot, sale=None) -> dict:
    """The checkout snapshot (`pos.sales.receipt`, written by the register) in the one shape every
    layout below reads. A sale with NO snapshot — an imported or pre-snapshot row — is refused rather
    than rendered from the sale's own totals, because a receipt with no lines is not a receipt.
    """
    snap = snapshot or {}
    items = snap.get("items")
    if not isinstance(items, list) or not items:
        raise ReceiptError("this sale has no stored receipt, so there is nothing to reprint "
                           "(only sales rung up on the register carry one)")
    sale = sale or {}
    lines = []
    for raw in items:
        it = raw or {}
        lines.append({
            "description": str(it.get("description") or "").strip(),
            "serial": str(it.get("serial") or "").strip(),
            "qty": _qty(it.get("qty")),
            "unit_price": _f(it.get("unit_price")),
            "discount": _f(it.get("discount")),
            "line_total": _f(it.get("line_total")),
        })
    txn = snap.get("transactionId")
    if txn in (None, "") and sale.get("transaction_id") not in (None, ""):
        txn = sale.get("transaction_id")
    return {
        "transaction_id": "" if txn in (None, "") else str(txn),
        "date": str(snap.get("date") or sale.get("created_at") or ""),
        "customer_name": str(snap.get("customerName") or "").strip(),
        "items": lines,
        "subtotal": _f(snap.get("subtotal")),
        "discount_total": _f(snap.get("discountTotal")),
        "tax_total": _f(snap.get("taxTotal")),
        "total": _f(snap.get("total")),
        "method": str(snap.get("method") or "").strip(),
        "tendered": None if snap.get("tendered") in (None, "") else _f(snap.get("tendered")),
        "change": _f(snap.get("change")),
        # §POS-15: a tender that does not settle at the till. Carried so the money line can say
        # what is OWED instead of claiming payment; blank on every sale that was paid.
        "terms_days": None if snap.get("termsDays") in (None, "") else int(snap.get("termsDays")),
        "due_date": str(snap.get("dueDate") or "").strip()[:10],
        "status": str(sale.get("status") or "completed"),
    }


# ── who the emailed copy goes to, and what it says ────────────────────────────────────────────────
def recipient(customer=None, typed=None) -> str:
    """THE address an emailed receipt goes to: what the cashier typed, else the customer's own email.
    Returned trimmed and never guessed — the screen SHOWS this, so a blank here is a blank there, and
    the cashier is asked instead of a receipt going somewhere nobody chose.
    """
    for candidate in (typed, (customer or {}).get("email")):
        addr = str(candidate or "").strip()
        if addr:
            return addr
    return ""


def is_address(value) -> bool:
    """Enough of a check to stop an obvious mistake reaching the mail provider — one @, something
    either side, a dot in the domain, no spaces. Deliberately not a validator: a deliverable address
    is the provider's answer, not ours."""
    addr = str(value or "").strip()
    if not addr or " " in addr or addr.count("@") != 1:
        return False
    local, _, domain = addr.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


def subject(company_name="", declared="", transaction_id="") -> str:
    """The emailed receipt's subject. The org's declared subject wins verbatim (it is config the admin
    typed); otherwise the house line names the company, and the transaction number rides along so a
    customer's inbox can tell two receipts apart."""
    typed = " ".join(str(declared or "").split())
    if typed:
        return typed
    company = " ".join(str(company_name or "").split()) or "your store"
    line = DEFAULT_SUBJECT.format(company=company)
    txn = str(transaction_id or "").strip()
    return f"{line} — #{txn}" if txn else line


def body_html(declared="", company_name="") -> str:
    """The words above the receipt in the email. The org's declared body wins; blank lines in it are
    kept as line breaks because an admin typing a paragraph means a paragraph."""
    typed = str(declared or "").strip()
    text = typed or DEFAULT_BODY
    paras = [p for p in text.replace("\r\n", "\n").split("\n") if p.strip()] or [DEFAULT_BODY]
    who = " ".join(str(company_name or "").split())
    out = "".join(f"<p>{_html.escape(p.strip())}</p>" for p in paras)
    return f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#111">{out}</div>' \
           + (f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#555">{_html.escape(who)}</div>'
              if who else "")


def email_view(snapshot_v, brand, template_v, settings_text, customer=None, typed=None,
               tenant_name="") -> dict:
    """Everything the email screen must SHOW before anything is sent: the address it will go to
    (editable), the name the customer will see it come from, the subject and the body. One home, so
    the screen cannot display one thing and the sender use another."""
    company = company_name(brand, tenant_name)
    addr = recipient(customer, typed)
    return {
        "to": addr,
        "to_source": ("typed" if str(typed or "").strip() else
                      ("customer" if addr else "none")),
        "valid": is_address(addr),
        "from_name": company,
        "subject": subject(company, (settings_text or {}).get("email_receipt_subject"),
                           (snapshot_v or {}).get("transaction_id")),
        "body_declared": str((settings_text or {}).get("email_receipt_body") or ""),
    }


#: What a WhatsApp number must have before it is worth sending to — a country code and a subscriber
#: number. Short of that the screen says so rather than Meta refusing it after the fact.
WHATSAPP_MIN_DIGITS = 11


def whatsapp_phone(customer=None, typed=None, *, digits=None) -> str:
    """THE number a receipt sent over WhatsApp goes to: what the cashier typed, else the number on
    the customer's record. Never guessed — the screen SHOWS this, so a blank here is a blank there.

    `digits` is INJECTED (`notify/whatsapp_window.digits`) for the same reason `html` injects its
    renderer: what counts as a phone number is WhatsApp's rule and it has one home, so this module
    does not keep a second copy of it (§POS-09). Without it the raw value is returned untouched and
    `whatsapp_view` reports the number as unusable rather than inventing a normalisation.
    """
    for candidate in (typed, (customer or {}).get("phone_primary")):
        raw = str(candidate or "").strip()
        if raw:
            return digits(raw) if digits else raw
    return ""


def whatsapp_view(snapshot_v, brand, template_v, settings_text, customer=None, typed=None,
                  tenant_name="", digits=None) -> dict:
    """Everything the send screen must SHOW before a receipt goes out over WhatsApp — the number it
    will go to (editable), whose it is, whether it can be sent at all, and the one line that rides
    with the file. The caption IS the email subject (`subject`), because a receipt arriving by two
    channels should announce itself the same way in both.
    """
    company = company_name(brand, tenant_name)
    num = whatsapp_phone(customer, typed, digits=digits)
    return {
        "to": num,
        "to_source": ("typed" if str(typed or "").strip() else ("customer" if num else "none")),
        "valid": bool(digits) and num.isdigit() and len(num) >= WHATSAPP_MIN_DIGITS,
        "from_name": company,
        "caption": subject(company, (settings_text or {}).get("email_receipt_subject"),
                           (snapshot_v or {}).get("transaction_id")),
    }


def company_name(brand, tenant_name="") -> str:
    """THE name a receipt and its email are FROM. The receipt-branding company name is the admin's own
    override and wins; otherwise it is the tenant's name as set up during onboarding
    (`storeops.tenants.name`). Never a hard-coded brand and never the platform's name — the customer
    is buying from the store, not from the software.
    """
    for candidate in ((brand or {}).get("company_name"), tenant_name):
        name = " ".join(str(candidate or "").split())
        if name:
            return name
    return ""


def customer_display(name, short=False) -> str:
    """The customer's name AS PRINTED. `receipt_customer_short_name` asks for "John S." instead of
    the full name; the rule lives here rather than at checkout, so switching it changes what every
    reprint shows instead of only what new sales record. A single word (a company) is never
    abbreviated — "Acme" has no last initial to take.
    """
    full = " ".join(str(name or "").split())
    if not short or not full:
        return full
    parts = full.split(" ")
    if len(parts) < 2:
        return full
    first, last = " ".join(parts[:-1]), parts[-1]
    return f"{first} {last[0].upper()}." if last else first


# ── the till slip ─────────────────────────────────────────────────────────────────────────────────
def _esc(v) -> str:
    return _html.escape("" if v is None else str(v))


def method_label(method) -> str:
    """How a payment method is written FOR A CUSTOMER.

    `pos.sale_payments.payment_method` stores a machine value (`credit_card`), and both receipt
    layouts used to dress it themselves — the slip with the raw value inside "Paid (credit_card)",
    the POS layout with `.title()`, which leaves the underscore and prints **Credit_Card** on a
    customer's receipt (owner, 2026-10-29). Two renderings of one fact, both wrong in their own way,
    so it is one function and neither spells it again.

    No method vocabulary is declared here (RULE TWO): whatever the tenant stores is made readable,
    never translated against a list of methods this file knows.
    """
    return " ".join(w for w in str(method or "").replace("_", " ").split()).title()


def _unpaid(snap) -> bool:
    """Did this sale's tender settle at the till? The vocabulary answers (§POS-15); a tender it does
    not know reads as settled, which is what every receipt printed before this existed."""
    from app.modules.pos import tender_vocabulary as _tv
    t = _tv.tender(snap.get("method"))
    return bool(t) and not t["settles_now"]


def slip_model(snap, template_v, brand=None, disclaimer_blocks=None, store_name="",
               fold_discount=False, tenant_name="", employee_name="", remit_block="",
               customer_lines=None, store_row=None) -> dict:
    """THE CONTENT of a till slip: which lines print, in what order, with what figures.

    WHY THIS EXISTS (owner 2026-10-20: *"the receipts will print to the thermal printer only if
    desired by the user, there cannot be any harcoded rule so we need have a helper"*). A receipt
    that goes to the printer as ESC/POS commands is a second MEDIUM, not a second receipt: the same
    toggles decide the same lines and the same arithmetic has to hold. Were the byte stream to read
    the snapshot and the template for itself, the two media would answer "what does this slip say"
    twice and drift — the duplicate defect the house rules forbid. So the CONTENT is resolved once,
    here, and `thermal_html` (paper and email, through a browser) and `receipt_escpos.payload` (the
    printer, through the till helper) are two RENDERERS of this one model.

    The arithmetic that keeps it adding up lives here too: `snap['subtotal']` is NET of line
    discounts, so when a separate Discounts row prints, the Subtotal line shows the GROSS figure or
    Subtotal − Discounts + Tax would not equal the total the customer paid.
    """
    t = template_v
    brand = brand or {}
    show_discount_row = (not fold_discount) and t["show_discounts"] and snap["discount_total"] > 0
    display_subtotal = snap["subtotal"] + snap["discount_total"] if show_discount_row else snap["subtotal"]
    company = company_name(brand, tenant_name)
    # What the shop IS, from the ONE home every layout reads (§POS-10) — never assembled here, or
    # the till slip and the tenant's own POS layout answer that question twice.
    shop = _shop.block(brand, store_row, tenant_name=tenant_name, store_code=store_name)
    head_name = shop["name"] or store_name or "POS System"

    items = []
    for it in snap["items"]:
        unit = it["unit_price"] - it["discount"] if fold_discount else it["unit_price"]
        items.append({
            "description": it["description"],
            "serial": it["serial"] if (t["show_serials"] and it["serial"]) else "",
            "qty": it["qty"],
            "unit_price": unit,
            "less": it["discount"] if ((not fold_discount) and t["show_discounts"]
                                       and it["discount"] > 0) else 0,
            "line_total": it["line_total"],
        })

    head_lines = []
    if t["show_store_name"] or company:
        head_lines.append({"text": head_name, "strong": True})
    head_lines += [{"text": ln} for ln in shop["address_lines"]]
    if shop["phone"]:
        head_lines.append({"text": shop["phone"]})
    if shop["license"]:
        head_lines.append({"text": shop["license"]})
    if t["header_text"]:
        head_lines.append({"text": t["header_text"], "pre": True})
    head_lines.append({"text": f'Transaction #{snap["transaction_id"]}'})
    head_lines.append({"text": snap["date"]})
    if t["show_customer"] and snap["customer_name"]:
        head_lines.append({"text": f'Customer: {snap["customer_name"]}'})
        # Their address as the record holds it (§POS-07) — the lines come from
        # `customer_bill_to.lines`, never assembled here, so the till slip, the printer's byte
        # stream and an imported copy of the same receipt word one address one way.
        head_lines += [{"text": ln} for ln in (customer_lines or []) if str(ln or "").strip()]
    if t["show_employee"] and employee_name:
        head_lines.append({"text": f"Served by: {employee_name}"})
    if snap["status"] == "voided":
        head_lines.append({"text": "** VOIDED **", "strong": True})

    totals = [{"label": "Subtotal", "value": display_subtotal}]
    if show_discount_row:
        totals.append({"label": "Discounts", "value": snap["discount_total"], "negative": True})
    if t["show_tax_breakdown"]:
        totals.append({"label": "Tax", "value": snap["tax_total"]})
    totals.append({"label": "TOTAL", "value": snap["total"], "grand": True})

    # WAS THIS SALE PAID? Asked of the tender vocabulary (§POS-15), never of a tender word here —
    # `settles_now` is declared once and this renderer dereferences it. A sale on account would
    # otherwise print "Paid (Terms)" with the full amount on an invoice nobody has paid.
    if _unpaid(snap):
        due = snap["due_date"]
        paid = [{"label": f"Balance due {due}" if due else "Balance due", "value": snap["total"]}]
    else:
        tendered = snap["tendered"] if snap["tendered"] is not None else snap["total"]
        paid = [{"label": f'Paid ({method_label(snap["method"])})' if snap["method"] else "Paid",
                 "value": tendered}]
        if snap["change"] > 0:
            paid.append({"label": "Change", "value": snap["change"]})

    return {
        "paper_width_mm": t["paper_width_mm"],
        "head_lines": head_lines,
        "items": items,
        "totals": totals,
        "paid": paid,
        "footer": template_v["footer_text"],
        # The pay-by-bank paragraph (§POS-06) is an ordinary footer block, LAST, after whatever the
        # tenant's legal blocks say. Deliberately not a section of its own: both renderers already
        # print `blocks`, so the paper copy, the emailed copy and the printer's byte stream carry it
        # with no renderer change — and no renderer can therefore carry it differently. It never
        # takes a signature line: the customer signs for what they bought, not for being told where
        # to pay.
        "blocks": ([{"text": d.get("text"), "signature": bool(d.get("signature"))}
                    for d in (disclaimer_blocks or [])]
                   + ([{"text": remit_block, "signature": False}] if remit_block else [])),
    }


def thermal_html(snap, template_v, brand=None, disclaimer_blocks=None, store_name="",
                 fold_discount=False, tenant_name="", employee_name="", remit_block="",
                 customer_lines=None, store_row=None) -> str:
    """The 58/80 mm till slip as HTML — ONE of the two renderers of `slip_model`, the one a browser
    prints and an email carries. Field for field what the register's own `buildReceiptHtml` used to
    print, every figure now read from the model rather than recomputed here."""
    t = template_v
    m = slip_model(snap, template_v, brand=brand, disclaimer_blocks=disclaimer_blocks,
                   store_name=store_name, fold_discount=fold_discount, tenant_name=tenant_name,
                   employee_name=employee_name, remit_block=remit_block,
                   customer_lines=customer_lines, store_row=store_row)
    width = PAPER_PX.get(t["paper_width_mm"], PAPER_PX[DEFAULT_PAPER_MM])
    font = t["font_size_px"]
    small = max(9, font - 2)

    rows = []
    for it in m["items"]:
        unit = it["unit_price"]
        serial = (f'<br><span class="serial">S/N: {_esc(it["serial"])}</span>'
                  if it["serial"] else "")
        less = f' &minus;{_money(it["less"])}' if it["less"] else ""
        rows.append(
            f'<tr><td colspan="3" class="desc">{_esc(it["description"])}{serial}</td></tr>'
            f'<tr class="nums"><td>{_esc(it["qty"])} &times; {_money(unit)}{less}</td>'
            f'<td></td><td class="r">{_money(it["line_total"])}</td></tr>')

    blocks = []
    for d in m["blocks"]:
        sign = ('<div style="margin-top:16px">X ______________________<br>'
                f'<span style="font-size:{small}px">Customer signature</span></div>'
                if d["signature"] else "")
        blocks.append(f'<hr><div class="pre" style="font-size:{small}px">{_esc(d["text"])}</div>{sign}')

    header_bits = []
    for ln in m["head_lines"]:
        cls = ("store" if ln.get("strong") and ln is m["head_lines"][0]
               else "void" if ln.get("strong") else "pre" if ln.get("pre") else "")
        attr = ' class="%s"' % cls if cls else ""
        header_bits.append(f'<div{attr}>{_esc(ln["text"])}</div>')

    total_rows = []
    for row in m["totals"]:
        sign = "&minus;" if row.get("negative") else ""
        klass = ' class="grand"' if row.get("grand") else ""
        total_rows.append(f'<tr{klass}><td>{_esc(row["label"])}</td>'
                          f'<td class="r">{sign}{_money(row["value"])}</td></tr>')
    paid_rows = [f'<tr><td>{_esc(row["label"])}</td><td class="r">{_money(row["value"])}</td></tr>'
                 for row in m["paid"]]

    return (
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<title>Receipt #{_esc(snap["transaction_id"])}</title><style>'
        "* { margin: 0; padding: 0; box-sizing: border-box; }"
        f"body {{ font-family: 'Courier New', monospace; font-size: {font}px; color: #000;"
        f" width: {width}px; padding: 12px; }}"
        ".center { text-align: center; }"
        f".store {{ font-size: {font + 2}px; font-weight: bold; }}"
        ".pre { white-space: pre-wrap; }"
        ".void { font-weight: bold; margin-top: 4px; }"
        "hr { border: none; border-top: 1px dashed #000; margin: 8px 0; }"
        "table { width: 100%; border-collapse: collapse; }"
        "td { padding: 1px 0; vertical-align: top; }"
        ".r { text-align: right; }"
        ".desc { font-weight: bold; }"
        f".serial {{ font-weight: normal; font-size: {small}px; }}"
        ".nums td { color: #333; }"
        f".grand {{ font-size: {font + 2}px; font-weight: bold; }}"
        "@media print { body { width: auto; } }"
        # The browser's own header and footer (§POS-11) — a receipt says what page it prints on.
        + PAGE_CSS +
        "</style></head><body>"
        f'<div class="center">{"".join(header_bits)}</div>'
        f'<hr><table>{"".join(rows)}</table><hr>'
        f'<table>{"".join(total_rows)}</table><hr>'
        f'<table>{"".join(paid_rows)}</table>'
        f'<hr><div class="center pre">{_esc(m["footer"])}</div>{"".join(blocks)}'
        "</body></html>")


# ── the tenant's own POS layout ───────────────────────────────────────────────────────────────────
def _footer_with_blocks(footer_text, disclaimer_blocks) -> str:
    """The legal text a POS-layout receipt ends with: the template's own footer, then the tenant's
    declared blocks in order.

    WHY HERE. The blocks reached the till slip and the printer's byte stream (`slip_model["blocks"]`)
    and NOT this layout, so the SAME sale emailed in the tenant's own POS format went out with no
    return policy on it — half a receipt, and the half a customer argues about. The Document already
    has one verbatim legal region (`footer_text`, which both of its renderers print), so the blocks
    ride THAT rather than a fourth place deciding what a receipt's terms say.
    """
    parts = [str(footer_text).strip()] if str(footer_text or "").strip() else []
    for d in (disclaimer_blocks or []):
        text = str((d or {}).get("text") or "").strip()
        if not text:
            continue
        if (d or {}).get("signature"):
            text += "\n\nX ______________________\nCustomer signature"
        parts.append(text)
    return "\n\n".join(parts)


def document(snap, fmt, template_v, brand=None, store_row=None, tenant_name="",
             employee_name="", remit_block="", customer_lines=None, store_name="",
             disclaimer_blocks=None) -> dict:
    """The sale as the SAME structured Document an imported receipt of this POS is parsed into — so
    `receipt_formats.render.render_html` prints it in that POS's layout with no renderer change, and
    the tenant's own uploaded receipts and its register's receipts finally look alike.

    `fmt` is the format MODULE the registry handed us (its `COLUMNS` / `TOTALS` / `TITLE` /
    `DATE_FORMAT`), never a name this module interprets.
    """
    base = getattr(fmt, "base", None)
    if base is None:  # pragma: no cover - the registry always hands a format built on base
        raise ReceiptError("that POS format cannot build a document")
    doc = base.new_document(getattr(fmt, "POS_SOURCE", ""), getattr(fmt, "LABEL", ""))
    doc["title"] = getattr(fmt, "TITLE", None) or "Sale"
    # THE SAME store block the till slip prints, from the one home (§POS-10). This path used to be
    # handed a `store_lines` list the router assembled for it alone, which is how a receipt in the
    # tenant's own POS layout came to carry the company name and no address at all.
    shop = _shop.block(brand, store_row, tenant_name=tenant_name, store_code=store_name)
    doc["store"]["lines"] = [ln for ln in ([shop["name"]] + shop["address_lines"]
                                           + [shop["license"]]) if ln]
    doc["store"]["phone"] = shop["phone"]

    doc["meta"] = [
        {"key": "invoice_no", "label": "Invoice #", "value": snap["transaction_id"]},
        {"key": "tendered_on", "label": "Tendered On", "value": snap["date"]},
    ]
    if employee_name:
        doc["meta"].append({"key": "sales_person", "label": "Sales Person", "value": employee_name})
    if (template_v or {}).get("show_customer") and snap["customer_name"]:
        # The same bill-to block an imported receipt of this POS carries: the name, then the address
        # the customer's record holds (§POS-07, `customer_bill_to`).
        doc["bill_to"]["lines"] = ([snap["customer_name"]]
                                   + [ln for ln in (customer_lines or []) if str(ln or "").strip()])
        doc["bill_to"]["name"] = snap["customer_name"]

    cols = list(getattr(fmt, "COLUMNS", []) or [])
    doc["columns"] = cols
    key_by_kind = {}
    for c in cols:
        key_by_kind.setdefault(c.get("kind"), c.get("key"))
    k_desc = key_by_kind.get(base.KIND_DESC)
    k_serial = key_by_kind.get(base.KIND_SERIAL)
    k_qty = key_by_kind.get(base.KIND_QTY)
    k_price = key_by_kind.get(base.KIND_MONEY)
    k_total = key_by_kind.get(base.KIND_TOTAL)
    show_serials = (template_v or {}).get("show_serials", True)
    for it in snap["items"]:
        cells = {}
        if k_desc:
            cells[k_desc] = it["description"]
        if k_serial and show_serials:
            cells[k_serial] = it["serial"]
        if k_qty:
            cells[k_qty] = it["qty"]
        if k_price:
            cells[k_price] = it["unit_price"]
        if k_total:
            cells[k_total] = it["line_total"]
        doc["items"].append(base.item(cells, cols))

    wanted = {"subtotal": snap["subtotal"], "sales_tax": snap["tax_total"],
              "total": snap["total"], "change": snap["change"]}
    for spec in (getattr(fmt, "TOTALS", []) or []):
        key = spec.get("key")
        if key in wanted:
            doc["totals"].append({"key": key, "label": spec.get("label") or key,
                                  "amount": wanted[key], "editable": bool(spec.get("editable"))})
    if snap["method"]:
        # The same question as the slip's money line, asked of the same home (§POS-15): a tender
        # that did not settle at the till is a BALANCE, and this layout must not label it a payment.
        if _unpaid(snap):
            due = snap["due_date"]
            doc["payments"] = [{"label": f"Balance due {due}" if due else "Balance due",
                                "amount": snap["total"]}]
        else:
            doc["payments"] = [{"label": method_label(snap["method"]),
                                "amount": snap["tendered"] if snap["tendered"] is not None else snap["total"]}]
    doc["footer_text"] = _footer_with_blocks((template_v or {}).get("footer_text") or "",
                                             disclaimer_blocks)
    # §POS-06 — a receipt printed in the tenant's own POS layout carries the pay-by-bank paragraph
    # too. `comments` is the Document's own free-text block and both of that format's renderers
    # already print it, so this is the same one paragraph in the third medium rather than a fourth
    # place that decides what the block says. A document BUILT from a sale has no comments of its
    # own (an UPLOADED one does, and this function never touches those), so nothing is overwritten.
    if remit_block:
        doc["comments"] = remit_block
    doc["derived"] = {"transaction_id": snap["transaction_id"], "voided": snap["status"] == "voided"}
    return doc


def for_render(snap, *, short_customer_name=False) -> dict:
    """The snapshot as every MEDIUM renders it — the one place the customer's printed name is decided
    (`receipt_customer_short_name`). The HTML body, the printer's byte stream and the PDF attachment
    all pass through here, so a receipt cannot be "John Smith" in the mail and "John S." in the file
    attached to it (§POS-08)."""
    out = dict(snap or {})
    out["customer_name"] = customer_display(out.get("customer_name"), short=short_customer_name)
    return out


def html(snap, template_v, *, render_document=None, fmt=None, brand=None, disclaimer_blocks=None,
         store_name="", store_row=None, fold_discount=False, tenant_name="",
         employee_name="", short_customer_name=False, remit_block="", customer_lines=None) -> str:
    """THE receipt of this sale, as print-ready HTML — the only answer, for print, reprint and email.

    `render_document` is injected (`receipt_formats.render.render_html`) so this module stays pure and
    the choice of layout is the only decision it makes.
    """
    layout = clean_layout((template_v or {}).get("layout"),
                          format_available=bool(fmt and render_document))
    snap = for_render(snap, short_customer_name=short_customer_name)
    if layout == LAYOUT_POS_FORMAT:
        return render_document(document(snap, fmt, template_v, brand=brand,
                                        store_row=store_row, tenant_name=tenant_name,
                                        employee_name=employee_name, remit_block=remit_block,
                                        customer_lines=customer_lines, store_name=store_name,
                                        disclaimer_blocks=disclaimer_blocks))
    return thermal_html(snap, template_v, brand=brand, disclaimer_blocks=disclaimer_blocks,
                        store_name=store_name, fold_discount=fold_discount,
                        tenant_name=tenant_name, employee_name=employee_name,
                        remit_block=remit_block, customer_lines=customer_lines,
                        store_row=store_row)


# ── WHAT THIS PRINT USES ──────────────────────────────────────────────────────────────────────────
# Owner 2026-10-19, verbatim: *"all stores should have the option to print the print in the required
# format from the print receipt screen as there are receipts from the thermal printer also"*.
#
# WHAT WAS WRONG. The layout was a per-ORG stored setting and nothing else. One counter prints the
# same sale on a thermal roll and, minutes later, on the office laser — and had no way to say so:
# whichever layout the org had stored was the only one it could produce. An admin changing the org
# setting to serve one counter changed it for every store.
#
# THE FIX IS ONE RESOLVER, not a second path. `print_choice` answers "which layout and which paper
# does THIS print use", from three inputs in order: what the screen ASKED for, else what this STORE
# declared, else what the ORG's template says. The asked-for value is validated against the SAME
# `LAYOUTS` / `PAPER_PX` vocabularies the stored one is, so a request cannot reach a layout a
# setting could not, and `source` names which of the three won so the screen can say it.


def paper_options() -> list[dict]:
    """The paper widths this renderer can produce — derived from PAPER_PX, never a second list."""
    return [{"value": mm, "label": f"{mm} mm roll" if mm != DEFAULT_PAPER_MM else f"{mm} mm roll (standard)"}
            for mm in sorted(PAPER_PX)]


def clean_paper(value, default=DEFAULT_PAPER_MM) -> int:
    """A requested paper width, or `default` when it is blank, unreadable or not one this renderer
    can set. Never raises: an unusable width must not stop a store printing a receipt."""
    try:
        mm = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return mm if mm in PAPER_PX else default


#: Where a printed copy GOES. `dialog` is the operating system's own print dialog, which every
#: store has had all along; `direct` is the till printer itself, through the helper on the counter
#: PC (§POS-04). Both are offered on every print — nothing here decides for a store.
DEST_DIALOG = "dialog"
DEST_DIRECT = "direct"
DESTINATIONS = (DEST_DIALOG, DEST_DIRECT)


def clean_destination(value, default=DEST_DIALOG) -> str:
    """A requested destination, or `default` when it is blank or not one we can send to. An unknown
    destination reads as the dialog, because a receipt the customer is waiting for must still come
    out of something."""
    v = str(value or "").strip().lower()
    return v if v in DESTINATIONS else default


def destination_options(printer_label="", helper_ready: bool = True) -> list:
    """What the print screen offers, with the store's own printer named in the direct one so the
    choice reads as "this counter's printer" rather than as a code word."""
    machine = " ".join(str(printer_label or "").split())
    direct = f"{machine} — straight to the printer" if machine else "Straight to the receipt printer"
    return [
        {"value": DEST_DIALOG, "label": "Print dialog (choose the printer)"},
        {"value": DEST_DIRECT,
         "label": direct if helper_ready else f"{direct} (no helper address set)"},
    ]


def _asked(value) -> bool:
    """Did the print screen tick the box? The query string carries "1" / "true" / "on"; anything
    else, including a missing parameter, is a no. One reader, so the register and a later caller
    cannot disagree about what a tick looks like on the wire."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def print_choice(template_v, *, want_layout=None, want_paper=None, store_layout=None,
                 store_paper=None, format_available: bool = True,
                 want_destination=None, store_direct=False,
                 want_remit=None, remit_available: bool = False) -> dict:
    """Which layout and paper THIS print uses, and where that came from.

    Precedence: the screen's request (one print, nothing stored) → this store's own declaration →
    the org's template. A request for something unusable does not fail and does not silently
    substitute something else either: it resolves the same way a stored setting would and
    `layout_note` carries the sentence saying what will actually come out of the printer.
    """
    layout_src = "request"
    raw_layout = want_layout
    if not str(raw_layout or "").strip():
        raw_layout, layout_src = store_layout, "store"
    if not str(raw_layout or "").strip():
        raw_layout, layout_src = template_v.get("layout"), "org"
    paper_src = "request"
    raw_paper = want_paper
    if not str(raw_paper or "").strip():
        raw_paper, paper_src = store_paper, "store"
    if not str(raw_paper or "").strip():
        raw_paper, paper_src = template_v.get("paper_width_mm"), "org"
    return {
        "layout": clean_layout(raw_layout, format_available=format_available),
        "layout_note": layout_note(raw_layout, format_available=format_available),
        "layout_source": layout_src,
        "paper_width_mm": clean_paper(raw_paper, template_v.get("paper_width_mm") or DEFAULT_PAPER_MM),
        "paper_source": paper_src,
        # The destination has only two answers: what this print asked for, else what the store set
        # as its default (and a store that has not turned direct printing on has the dialog, which
        # is what every store has today). There is no org-wide destination, because the helper runs
        # on one counter's PC and nowhere else.
        "destination": (clean_destination(want_destination)
                        if str(want_destination or "").strip()
                        else (DEST_DIRECT if store_direct else DEST_DIALOG)),
        "destination_source": "request" if str(want_destination or "").strip() else "store",
        # THE PAY-BY-BANK TICK (owner 2026-10-21, §POS-06). Off on every receipt until the cashier
        # ticks it — there is no stored default, because which customer is paying by bank transfer is
        # known at the counter and nowhere else. A tick cannot turn it on when the store has not
        # declared both numbers: `remit_available` gates it the same way `format_available` gates the
        # POS layout, so the block can never print with a number missing from it.
        "remit": bool(remit_available) and _asked(want_remit),
        "remit_source": "request" if _asked(want_remit) else "off",
    }


def with_choice(template_v, choice) -> dict:
    """The template as the renderer should read it for THIS print — the chosen layout and paper laid
    over the stored row. A copy, so one counter's choice cannot leak into the next print or into the
    emailed copy of a different sale."""
    out = dict(template_v)
    out["layout"] = choice["layout"]
    out["paper_width_mm"] = choice["paper_width_mm"]
    return out


def layout_options(format_label="") -> list[dict]:
    """What the admin screen offers, with the tenant's own POS named in the second one so the choice
    reads as "print like my POS does" rather than as a code word."""
    pos = " ".join(str(format_label or "").split())
    return [
        {"value": LAYOUT_THERMAL, "label": "Till slip (58 / 80 mm thermal)"},
        {"value": LAYOUT_POS_FORMAT,
         "label": f"{pos} layout — as this tenant's receipts are uploaded" if pos
                  else "My POS's own receipt layout (no POS declared yet)"},
    ]
