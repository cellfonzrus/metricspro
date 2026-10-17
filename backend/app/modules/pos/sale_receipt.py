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

LAYOUT_THERMAL = "thermal"
LAYOUT_POS_FORMAT = "pos_format"
LAYOUTS = (LAYOUT_THERMAL, LAYOUT_POS_FORMAT)

#: Paper widths mig 725 allows, and the pixel width each prints at (the browser's own mapping).
PAPER_PX = {58: 220, 80: 300}
DEFAULT_PAPER_MM = 80
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


def thermal_html(snap, template_v, brand=None, disclaimer_blocks=None, store_name="",
                 fold_discount=False, tenant_name="", employee_name="") -> str:
    """The 58/80 mm till slip — the layout this POS has always printed, now rendered in ONE place.

    Ported field for field from the register's own `buildReceiptHtml`, including the arithmetic that
    keeps it adding up: `snap['subtotal']` is NET of line discounts, so when a separate Discounts row
    prints, the Subtotal line shows the GROSS figure or Subtotal − Discounts + Tax would not equal the
    total the customer paid.
    """
    t = template_v
    brand = brand or {}
    width = PAPER_PX.get(t["paper_width_mm"], PAPER_PX[DEFAULT_PAPER_MM])
    font = t["font_size_px"]
    small = max(9, font - 2)
    show_discount_row = (not fold_discount) and t["show_discounts"] and snap["discount_total"] > 0
    display_subtotal = snap["subtotal"] + snap["discount_total"] if show_discount_row else snap["subtotal"]
    company = company_name(brand, tenant_name)
    head_name = company or store_name or "POS System"

    rows = []
    for it in snap["items"]:
        unit = it["unit_price"] - it["discount"] if fold_discount else it["unit_price"]
        serial = (f'<br><span class="serial">S/N: {_esc(it["serial"])}</span>'
                  if t["show_serials"] and it["serial"] else "")
        less = (f' &minus;{_money(it["discount"])}'
                if (not fold_discount) and t["show_discounts"] and it["discount"] > 0 else "")
        rows.append(
            f'<tr><td colspan="3" class="desc">{_esc(it["description"])}{serial}</td></tr>'
            f'<tr class="nums"><td>{_esc(it["qty"])} &times; {_money(unit)}{less}</td>'
            f'<td></td><td class="r">{_money(it["line_total"])}</td></tr>')

    blocks = []
    for d in (disclaimer_blocks or []):
        sign = ('<div style="margin-top:16px">X ______________________<br>'
                f'<span style="font-size:{small}px">Customer signature</span></div>'
                if d.get("signature") else "")
        blocks.append(f'<hr><div class="pre" style="font-size:{small}px">{_esc(d.get("text"))}</div>{sign}')

    header_bits = []
    if t["show_store_name"] or company:
        header_bits.append(f'<div class="store">{_esc(head_name)}</div>')
    header_bits += [f"<div>{_esc(ln)}</div>" for ln in (brand.get("address_lines") or [])]
    if brand.get("license_tax_ids"):
        header_bits.append(f'<div>{_esc(brand["license_tax_ids"])}</div>')
    if t["header_text"]:
        header_bits.append(f'<div class="pre">{_esc(t["header_text"])}</div>')
    header_bits.append(f'<div>Transaction #{_esc(snap["transaction_id"])}</div>')
    header_bits.append(f'<div>{_esc(snap["date"])}</div>')
    if t["show_customer"] and snap["customer_name"]:
        header_bits.append(f'<div>Customer: {_esc(snap["customer_name"])}</div>')
    if t["show_employee"] and employee_name:
        header_bits.append(f'<div>Served by: {_esc(employee_name)}</div>')
    if snap["status"] == "voided":
        header_bits.append('<div class="void">** VOIDED **</div>')

    paid_label = f'Paid ({_esc(snap["method"])})' if snap["method"] else "Paid"
    tendered = snap["tendered"] if snap["tendered"] is not None else snap["total"]
    change_row = (f'<tr><td>Change</td><td class="r">{_money(snap["change"])}</td></tr>'
                  if snap["change"] > 0 else "")
    tax_row = (f'<tr><td>Tax</td><td class="r">{_money(snap["tax_total"])}</td></tr>'
               if t["show_tax_breakdown"] else "")
    discount_row = (f'<tr><td>Discounts</td><td class="r">&minus;{_money(snap["discount_total"])}</td></tr>'
                    if show_discount_row else "")

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
        "</style></head><body>"
        f'<div class="center">{"".join(header_bits)}</div>'
        f'<hr><table>{"".join(rows)}</table><hr>'
        "<table>"
        f'<tr><td>Subtotal</td><td class="r">{_money(display_subtotal)}</td></tr>'
        f"{discount_row}{tax_row}"
        f'<tr class="grand"><td>TOTAL</td><td class="r">{_money(snap["total"])}</td></tr>'
        "</table><hr><table>"
        f'<tr><td>{paid_label}</td><td class="r">{_money(tendered)}</td></tr>{change_row}'
        "</table>"
        f'<hr><div class="center pre">{_esc(template_v["footer_text"])}</div>{"".join(blocks)}'
        "</body></html>")


# ── the tenant's own POS layout ───────────────────────────────────────────────────────────────────
def document(snap, fmt, template_v, brand=None, store_lines=None, tenant_name="",
             employee_name="") -> dict:
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
    company = company_name(brand, tenant_name)
    lines = [ln for ln in ([company] if company else []) + list(store_lines or []) if ln]
    if not lines and (template_v or {}).get("show_store_name"):
        lines = [l for l in (store_lines or []) if l]
    doc["store"]["lines"] = lines
    if (brand or {}).get("license_tax_ids"):
        doc["store"]["lines"].append(brand["license_tax_ids"])

    doc["meta"] = [
        {"key": "invoice_no", "label": "Invoice #", "value": snap["transaction_id"]},
        {"key": "tendered_on", "label": "Tendered On", "value": snap["date"]},
    ]
    if employee_name:
        doc["meta"].append({"key": "sales_person", "label": "Sales Person", "value": employee_name})
    if (template_v or {}).get("show_customer") and snap["customer_name"]:
        doc["bill_to"]["lines"] = [snap["customer_name"]]
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
        doc["payments"] = [{"label": snap["method"].title(),
                            "amount": snap["tendered"] if snap["tendered"] is not None else snap["total"]}]
    doc["footer_text"] = (template_v or {}).get("footer_text") or ""
    doc["derived"] = {"transaction_id": snap["transaction_id"], "voided": snap["status"] == "voided"}
    return doc


def html(snap, template_v, *, render_document=None, fmt=None, brand=None, disclaimer_blocks=None,
         store_name="", store_lines=None, fold_discount=False, tenant_name="",
         employee_name="", short_customer_name=False) -> str:
    """THE receipt of this sale, as print-ready HTML — the only answer, for print, reprint and email.

    `render_document` is injected (`receipt_formats.render.render_html`) so this module stays pure and
    the choice of layout is the only decision it makes.
    """
    layout = clean_layout((template_v or {}).get("layout"),
                          format_available=bool(fmt and render_document))
    snap = dict(snap)
    snap["customer_name"] = customer_display(snap.get("customer_name"), short=short_customer_name)
    if layout == LAYOUT_POS_FORMAT:
        return render_document(document(snap, fmt, template_v, brand=brand,
                                        store_lines=store_lines, tenant_name=tenant_name,
                                        employee_name=employee_name))
    return thermal_html(snap, template_v, brand=brand, disclaimer_blocks=disclaimer_blocks,
                        store_name=store_name, fold_discount=fold_discount,
                        tenant_name=tenant_name, employee_name=employee_name)


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
