"""PROOF — a receipt has ONE renderer, the layout is a declared per-org choice, and the emailed copy
shows who it goes to and who it is from before anything is sent (owner 2026-10-17, index §POS-02).
stdlib only, DB-free.

    python3 harness_pos_receipt_render.py        (from backend/)

  §A THE TILL SLIP — every template toggle and every brand field reaches the HTML, and the one piece
     of arithmetic the slip does (a separate Discounts row means the Subtotal line shows the GROSS
     figure) still adds up: Subtotal − Discounts + Tax = the total the customer paid.
  §B THE TWO CONFIG COPIES ARE PINNED — the server's own defaults for the POS settings it reads are
     compared, key by key, against the browser registry's `default:` literals. A default changed on
     one side only fails the build naming the key. This is the lock that lets the rule live in two
     languages without becoming two answers.
  §C THE LAYOUT IS DECLARED, NEVER GUESSED — unknown and blank read as the till slip; "my POS's
     layout" asked for with no registered format falls back to the till slip AND SAYS SO.
  §D THE TENANT'S OWN POS LAYOUT — the sale becomes the SAME structured Document an imported receipt
     of that POS is parsed into, so the existing renderer prints it with no renderer change.
  §E WHO IT IS FROM — the receipt-branding override, else the name onboarding set; never a
     hard-coded brand, and never the platform's name on a tenant's mail.
  §F WHO IT GOES TO — the typed address beats the customer's, nothing is invented, and an obviously
     broken address is refused before the provider sees it.
  §G NOT A RE-PRICING — a reprint prints the snapshot's figures and never recomputes a total.
  §H ONE HOME — the register builds no receipt HTML of its own and the admin screen draws no
     imitation of one; both dereference the server. The `From:` header is assembled in one place.
  §I ENDPOINT WIRING + REGISTRATION.
  §N PAY BY BANK TRANSFER (§POS-06) — the account a receipt tells a customer to pay into: validated
     before it is stored (a routing number has a check digit), sealed at rest and masked everywhere a
     screen can see it, settable only with the settings right and tickable by anyone who prints — and
     ONE paragraph that four media render without any of them deciding what it says.
  §O THE CUSTOMER'S OWN ADDRESS (§POS-07) — "what does this customer's record say, written out" had
     no home, so the receipt carried their name alone while an imported copy of the same receipt
     carried their address. One home, every medium, and a lock on a second copy.
  §P THE RECEIPT AS A FILE (§POS-08) — the emailed receipt also leaves as a PDF: a third MEDIUM of
     the same two content homes (the slip's own rows, the POS layout's own words), read back with
     pdfplumber so the checks are on what the customer's reader shows.
  §Q SENDING A RECEIPT (§POS-09) — email and WhatsApp from ONE panel, mounted by the till and the
     Sale Log alike, with the number rule dereferenced from the channel that owns it.
  §M THE DECLARED FORMAT HAS ONE SHAPE (§POS-05, the 2026-10-20 print crash, ref 357a4772) — "which
     receipt format has this tenant declared" had three derivations in two shapes (the registry ROW
     and the format MODULE), so the day one's `.get("label")` was copied onto the other every receipt
     of the one tenant WITH a declared POS died with an unhandled 500. One home, four dereferences,
     and a regression that reproduces the crash.
  Negative controls prove each rule goes red.
"""
import io
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.pos import pos_config_effective as pc  # noqa: E402
from app.modules.pos import sale_receipt as sr  # noqa: E402
from app.modules.core import tenant_identity as ti  # noqa: E402

_pass = _fail = 0
ROUTER = "backend/app/modules/pos/router.py"
REGISTER = "frontend/src/app/(platform)/pos/sales/page.tsx"
PANEL_SEND = "frontend/src/components/pos/SendReceiptPanel.tsx"
EDITOR = "frontend/src/components/pos/ReceiptTemplateSection.tsx"
CLIENT = "frontend/src/lib/pos-receipt.ts"
POS_CONFIG_TS = "frontend/src/lib/pos-config.ts"
CHANNEL = "backend/app/modules/notify/channels/email_resend.py"
MIGRATION = "database/migrations/1089_pos_receipt_layout.sql"
INDEX = "docs/SYSTEM_DATA_FLOW_INDEX.md"


def code_only(text: str) -> str:
    """The source with its docstrings and comments removed.

    RULE TWO bans a tenant/carrier/product name in BEHAVIOUR. A module that quotes the owner's own
    sentence, or names the tenant that surfaced a defect while explaining it, is documentation and is
    worth keeping — so the RULE TWO scans below read what the interpreter reads, not the prose.
    """
    out = re.sub(r'(?s)""".*?"""', '""', text)
    out = re.sub(r"(?s)'''.*?'''", "''", out)
    return "\n".join(ln.split("#", 1)[0] if "#" in ln else ln for ln in out.splitlines())


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name} {extra}")


def read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def refuses(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except sr.ReceiptError:
        return True


SNAP_IN = {
    "transactionId": 77, "date": "08/06/2026 2:41 PM", "customerName": "John Smith",
    "items": [{"description": "Smartphone 128GB", "serial": "358291470012345", "qty": 1,
               "unit_price": 999.00, "discount": 50.00, "line_total": 949.00},
              {"description": "Clear case", "serial": "", "qty": 2,
               "unit_price": 19.99, "discount": 0, "line_total": 39.98}],
    "subtotal": 988.98, "discountTotal": 50.00, "taxTotal": 87.77, "total": 1076.75,
    "method": "cash", "tendered": 1100.00, "change": 23.25,
}
SNAP = sr.snapshot_view(SNAP_IN, {"status": "completed"})
ALL_ON = sr.template_view({"header_text": "Authorised retailer", "footer_text": "No refunds after 14 days",
                           "show_store_name": True, "show_customer": True, "show_employee": True,
                           "show_serials": True, "show_tax_breakdown": True, "show_discounts": True,
                           "paper_width_mm": 80, "font_size_px": 12})
BRAND = {"company_name": "Example Wireless", "address_lines": ["1 Example St", "Queens NY"],
         "license_tax_ids": "Lic 123456"}

#: A row shaped like the live `storeops.stores` — its REAL columns (§POS-10). There is no `name`
#: column on that table, which is how a receipt in the tenant's own POS layout came to print the
#: company name and no address at all.
STORE_ROW = {"store_code": "STORE 1", "address": "1 Example St", "market": "NY",
             "phone": "(555) 010-0100"}

print("\n§A  the till slip — every toggle reaches the paper, and it adds up")
html = sr.thermal_html(SNAP, ALL_ON, brand=BRAND, store_name="STORE 1",
                       disclaimer_blocks=[{"text": "Return policy", "signature": True}],
                       employee_name="A. Rep")
check("A1 the company name, address and licence ids print",
      "Example Wireless" in html and "1 Example St" in html and "Lic 123456" in html)
check("A2 the header and footer print verbatim",
      "Authorised retailer" in html and "No refunds after 14 days" in html)
check("A3 the customer and the serving rep print when their toggles are on",
      "Customer: John Smith" in html and "A. Rep" in html)
check("A4 a serial prints under its line", "S/N: 358291470012345" in html)
check("A5 the disclaimer prints with its signature line",
      "Return policy" in html and "Customer signature" in html)
check("A6 the transaction number and date print", "#77" in html and "08/06/2026" in html)
check("A7 cash tendered and change print", "$1,100.00" in html and "$23.25" in html)
# Subtotal − Discounts + Tax = Total. The slip prints the GROSS subtotal when a Discounts row shows.
gross = SNAP["subtotal"] + SNAP["discount_total"]
check("A8 with a Discounts row, the Subtotal line is the GROSS figure so the three add to the total",
      f"${gross:,.2f}" in html and round(gross - SNAP["discount_total"] + SNAP["tax_total"], 2) == SNAP["total"])
folded = sr.thermal_html(SNAP, ALL_ON, brand=BRAND, fold_discount=True)
check("A9 folding the coupon into the price prints NO Discounts row and the NET subtotal",
      "Discounts" not in folded and f"${SNAP['subtotal']:,.2f}" in folded)
check("A10 folding prints the discounted unit price, not the list price",
      "$949.00" in folded and "$999.00" not in folded)
off = sr.template_view({"show_store_name": False, "show_customer": False, "show_employee": False,
                        "show_serials": False, "show_tax_breakdown": False, "show_discounts": False,
                        "footer_text": ""})
html_off = sr.thermal_html(SNAP, off, brand={}, store_name="STORE 1")
check("A11 every toggle OFF removes exactly its own line",
      "Customer:" not in html_off and "S/N:" not in html_off
      and "Tax" not in html_off and "Discounts" not in html_off and "STORE 1" not in html_off)
check("A12 a toggle cannot remove the TOTAL — a receipt always says what was paid",
      "TOTAL" in html_off and f"${SNAP['total']:,.2f}" in html_off)
check("A13 58 mm prints narrower than 80 mm",
      "width: 220px" in sr.thermal_html(SNAP, sr.template_view({"paper_width_mm": 58}))
      and "width: 300px" in html)
check("A14 a voided sale says so on its face",
      "** VOIDED **" in sr.thermal_html(sr.snapshot_view(SNAP_IN, {"status": "voided"}), ALL_ON))
check("A15 a customer name with HTML in it is escaped, not rendered",
      "&lt;b&gt;" in sr.thermal_html(sr.snapshot_view(dict(SNAP_IN, customerName="<b>x</b>"),
                                                      {"status": "completed"}), ALL_ON))
check("A16 the short-name rule is a RENDER rule and prints 'John S.'",
      sr.customer_display("John Smith", short=True) == "John S."
      and sr.customer_display("John Smith") == "John Smith")
check("A17 a one-word name (a company) is never abbreviated",
      sr.customer_display("Acme", short=True) == "Acme")

print("\n§B  the two config copies are pinned, key by key")
ts = read(POS_CONFIG_TS)


def ts_default(key):
    """The `default:` literal the browser registry declares for one key."""
    m = re.search(r"key:\s*'" + re.escape(key) + r"'[^}]*?default:\s*(true|false|''|\"\"|'[^']*'|\"[^\"]*\"|[0-9.]+)",
                  ts, re.S)
    if not m:
        return ("missing", None)
    raw = m.group(1)
    if raw in ("true", "false"):
        return ("ok", raw == "true")
    if raw in ("''", '""'):
        return ("ok", "")
    if raw[0] in "'\"":
        return ("ok", raw[1:-1])
    return ("ok", float(raw) if "." in raw else int(raw))


missing, mismatched = [], []
for key, server_default in pc.SERVER_DEFAULTS.items():
    state, value = ts_default(key)
    if state == "missing":
        missing.append(key)
    elif value != server_default:
        mismatched.append(f"{key}: server={server_default!r} browser={value!r}")
check("B1 every key the server declares EXISTS in the browser registry", not missing, f"missing: {missing}")
check("B2 and carries the SAME default on both sides", not mismatched, f"{mismatched}")
check("B3 the server declares only what it reads (not a second copy of the whole registry)",
      len(pc.SERVER_DEFAULTS) < 25 and "require_customer" not in pc.SERVER_DEFAULTS)
check("B4 the ts_default reader actually works (a known key resolves)",
      ts_default("brand_company_name") == ("ok", "") and ts_default("receipt_auto_print") == ("ok", False))

print("\n§B2 the resolution rule — store override, then org default, then declared")
ROWS = [{"store_code": None, "key": "brand_company_name", "value": "Org Co"},
        {"store_code": "S1", "key": "brand_company_name", "value": "Store Co"}]
check("B5 a store override wins at that store", pc.effective(ROWS, "brand_company_name", "S1") == "Store Co")
check("B6 another store inherits the org default", pc.effective(ROWS, "brand_company_name", "S2") == "Org Co")
check("B7 no row at all reads the declared default", pc.effective([], "receipt_coupon_in_retail_price") is False)
check("B8 an undeclared key answers None rather than inventing a blank",
      pc.effective([], "no_such_setting") is None)
check("B9 a stored string 'true' counts as on (the column is jsonb and older rows hold either)",
      pc.flag([{"store_code": None, "key": "receipt_coupon_in_retail_price", "value": "true"}],
              "receipt_coupon_in_retail_price") is True)
check("B10 only POS-targeted disclaimers print",
      [d["text"] for d in pc.disclaimers(
          [{"store_code": None, "key": "disclaimer1_text", "value": "A"},
           {"store_code": None, "key": "disclaimer1_target", "value": "pos"},
           {"store_code": None, "key": "disclaimer2_text", "value": "B"},
           {"store_code": None, "key": "disclaimer2_target", "value": "invoice"}])] == ["A"])
check("B11 a blank brand address line is dropped, not printed as an empty line",
      pc.brand([{"store_code": None, "key": "brand_address_line1", "value": "1 St"},
                {"store_code": None, "key": "brand_address_line2", "value": "  "}])["address_lines"] == ["1 St"])

print("\n§C  the layout is declared, never guessed")
check("C1 blank is the till slip", sr.clean_layout("") == sr.LAYOUT_THERMAL)
check("C2 an unknown value is the till slip", sr.clean_layout("rq") == sr.LAYOUT_THERMAL)
check("C3 the POS layout is honoured when a format is available",
      sr.clean_layout(sr.LAYOUT_POS_FORMAT, format_available=True) == sr.LAYOUT_POS_FORMAT)
check("C4 and falls back to the till slip when there is no format",
      sr.clean_layout(sr.LAYOUT_POS_FORMAT, format_available=False) == sr.LAYOUT_THERMAL)
check("C5 that fallback is SAID OUT LOUD (a silent one is how a tenant believes a layout it has not got)",
      "no POS is declared" in sr.layout_note(sr.LAYOUT_POS_FORMAT, format_available=False))
check("C6 a typo is reported too", "is not a receipt layout" in sr.layout_note("therml"))
check("C7 a correct choice says nothing", sr.layout_note(sr.LAYOUT_THERMAL) == ""
      and sr.layout_note(sr.LAYOUT_POS_FORMAT, format_available=True) == "")
check("C8 the admin screen is offered exactly the two layouts, the POS one named after its own POS",
      [o["value"] for o in sr.layout_options("RQ (Wireless Zone)")] == list(sr.LAYOUTS)
      and "RQ (Wireless Zone)" in sr.layout_options("RQ (Wireless Zone)")[1]["label"])
check("C9 with no POS declared the second option says so instead of naming nothing",
      "no POS declared" in sr.layout_options("")[1]["label"])
check("C10 a template row with no layout column at all reads as the till slip (adaptive, pre-mig-1089)",
      sr.template_view({"name": "Default"})["layout"] == sr.LAYOUT_THERMAL)

print("\n§D  the tenant's own POS layout — the same Document an import is parsed into")
from app.modules.pos.receipt_formats import rq as _rq  # noqa: E402
from app.modules.pos.receipt_formats import render as _render  # noqa: E402

doc = sr.document(SNAP, _rq, ALL_ON, brand=BRAND, store_row=STORE_ROW,
                  employee_name="A. Rep")
check("D1 it is that POS's own document (source and columns come from the format, not from here)",
      doc["pos_source"] == _rq.POS_SOURCE and doc["columns"] == _rq.COLUMNS)
check("D2 every cart line becomes an item", len(doc["items"]) == len(SNAP["items"]))
check("D3 the item cells are keyed by the FORMAT's column keys",
      set(doc["items"][0]["cells"]) <= {c["key"] for c in _rq.COLUMNS}
      and doc["items"][0]["cells"]["name"] == "Smartphone 128GB"
      and doc["items"][0]["cells"]["tracking"] == "358291470012345")
check("D4 the totals are the snapshot's own figures, in the format's declared order",
      [(t["key"], t["amount"]) for t in doc["totals"]][:2]
      == [("subtotal", SNAP["subtotal"]), ("sales_tax", SNAP["tax_total"])])
check("D5 the invoice number and the date ride in the meta block",
      {m["key"] for m in doc["meta"]} >= {"invoice_no", "tendered_on"})
check("D6 the customer is the Bill To party", doc["bill_to"]["lines"] == ["John Smith"])
check("D7 the template's footer travels with it", doc["footer_text"] == ALL_ON["footer_text"])
check("D8 serials are left out when that toggle is off",
      "tracking" not in sr.document(SNAP, _rq, sr.template_view({"show_serials": False}))["items"][0]["cells"])
rendered = _render.render_html(doc)
check("D9 the EXISTING renderer prints it with no renderer change",
      "Smartphone 128GB" in rendered and "358291470012345" in rendered and "<html" in rendered)
check("D10 html() routes to that layout when it is chosen, and to the slip when it is not",
      "<table" in sr.html(SNAP, sr.template_view({"layout": sr.LAYOUT_POS_FORMAT}),
                          render_document=_render.render_html, fmt=_rq)
      and "Courier New" in sr.html(SNAP, ALL_ON, render_document=_render.render_html, fmt=_rq))
check("D11 asking for the POS layout with NO format still prints a receipt (the slip), never nothing",
      "TOTAL" in sr.html(SNAP, sr.template_view({"layout": sr.LAYOUT_POS_FORMAT}),
                         render_document=None, fmt=None))

print("\n§E  who the receipt is from")
check("E1 the receipt-branding company name wins (it is the admin's own override)",
      sr.company_name({"company_name": "Brand Co"}, "Onboarded Co") == "Brand Co")
check("E2 otherwise the name set up at onboarding", sr.company_name({}, "Onboarded Co") == "Onboarded Co")
check("E3 neither → blank, never a placeholder printed as the business's name",
      sr.company_name({}, "") == "")
check("E4 the tenant name has ONE reader, and a blank row is blank not a stand-in",
      ti.display_name_from({"name": "  Vz  Co "}) == "Vz Co" and ti.display_name_from({}) == ""
      and ti.display_name_from(None) == "")
src = read("backend/app/modules/core/tenant_identity.py")
check("E5 that one home names no tenant in its BEHAVIOUR (RULE TWO)",
      not re.search(r"(?i)\b(vzone|luxelink|cellfonz|boost|verizon)\b", code_only(src)))
# A query that selects NOTHING BUT the name is a second answer to "what is this tenant called" —
# the fact core/tenant_identity.py now owns. A read that pulls the name alongside other columns of
# the same row (a letter's config, a slug, an active flag) is that row being read, not this fact
# being re-derived, so it is not a sibling and is not flagged.
others = []
for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "backend/app")):
    for fn in sorted(files):
        if not fn.endswith(".py"):
            continue
        rel = os.path.relpath(os.path.join(dirpath, fn), ROOT).replace(os.sep, "/")
        if rel.endswith("core/tenant_identity.py"):
            continue
        for m in re.finditer(r'table\("tenants"\)\.select\(\s*"([^"]*)"', code_only(read(rel))):
            cols = [c.strip() for c in m.group(1).split(",") if c.strip()]
            if [c for c in cols if c not in ("org_id", "id")] == ["name"]:
                others.append(rel)
check("E6 nothing else re-derives the tenant's NAME on its own (the siblings dereference the one home)",
      not others, f"{sorted(set(others))}")
channel = read(CHANNEL)
check("E7 the From header is assembled in ONE place",
      channel.count('f"{name} <{addr}>"') == 1 and "def from_header" in channel)
check("E8 a tenant name cannot forge a second header (quotes / brackets stripped, odd names quoted)",
      '.replace("<", "")' in channel and 'f\'"{name}" <{addr}>\'' in channel)
check("E9 the channel resolves a tenant's name through the one home, not its own query",
      "tenant_identity" in channel and 'table("tenants")' not in channel)

print("\n§F  who it goes to")
CUST = {"email": "cust@example.com"}
check("F1 the typed address wins over the customer's", sr.recipient(CUST, "typed@example.com") == "typed@example.com")
check("F2 the customer's own email is the default", sr.recipient(CUST) == "cust@example.com")
check("F3 neither → blank, and nothing is invented", sr.recipient({}, "") == "" and sr.recipient(None) == "")
check("F4 an obviously broken address is refused before the provider sees it",
      not sr.is_address("nope") and not sr.is_address("a b@c.com") and not sr.is_address("a@b")
      and not sr.is_address("a@@b.com") and not sr.is_address(""))
check("F5 a normal address passes", sr.is_address("cust@example.com") and sr.is_address("a.b+c@sub.example.co.uk"))
view = sr.email_view(SNAP, BRAND, ALL_ON, {"email_receipt_subject": "", "email_receipt_body": ""},
                     customer=CUST, tenant_name="Onboarded Co")
check("F6 the screen is told the address, where it came from, and whether it is usable",
      view["to"] == "cust@example.com" and view["to_source"] == "customer" and view["valid"] is True)
check("F7 and the name the customer will see it from", view["from_name"] == "Example Wireless")
check("F8 the house subject names the company and the transaction",
      "Example Wireless" in view["subject"] and "#77" in view["subject"])
check("F9 a declared subject wins verbatim",
      sr.subject("Co", "Here is your receipt", "77") == "Here is your receipt")
check("F10 a declared body wins, and a blank one still says what the mail is",
      "Thank you for shopping" in sr.body_html("Thank you for shopping")
      and sr.DEFAULT_BODY in sr.body_html(""))
check("F11 the body is escaped (an admin's text is text, not markup)",
      "&lt;script&gt;" in sr.body_html("<script>x</script>"))
check("F12 with no address the screen is told 'none' rather than shown a guess",
      sr.email_view(SNAP, BRAND, ALL_ON, {}, customer={}, tenant_name="")["to_source"] == "none")

print("\n§G  a receipt restates a sale, it does not re-price it")
check("G1 the printed figures are the snapshot's own",
      SNAP["total"] == 1076.75 and SNAP["subtotal"] == 988.98 and SNAP["tax_total"] == 87.77)
check("G2 nothing in the renderer multiplies a price by a quantity or sums the lines",
      not re.search(r"(qty|quantity)\s*\*\s*", read("backend/app/modules/pos/sale_receipt.py"))
      and "sum(" not in read("backend/app/modules/pos/sale_receipt.py"))
check("G3 a sale with no stored receipt is REFUSED, never rendered from the sale's own totals",
      refuses(sr.snapshot_view, None, {"total": 10}) and refuses(sr.snapshot_view, {"items": []}, {}))
check("G4 the transaction number falls back to the sale row when the snapshot was stored without one",
      sr.snapshot_view(dict(SNAP_IN, transactionId=None), {"transaction_id": 99})["transaction_id"] == "99")
check("G5 an unreadable money field reads as 0.00 rather than crashing a till",
      sr.snapshot_view(dict(SNAP_IN, total="oops"), {})["total"] == 0.0)

print("\n§H  one home — nothing builds a second receipt")
reg = read(REGISTER)
check("H1 the register builds NO receipt html of its own",
      "buildReceiptHtml" not in reg and "<!doctype html" not in reg.lower())
check("H2 it asks the one client home instead",
      "fetchSaleReceipt(" in reg and "from '@/lib/pos-receipt'" in reg)
check("H3 the till copy, the reprint and the auto-print all go through that one call, declared once",
      reg.count("const printSaleReceipt = useCallback(") == 1
      and reg.count("printSaleReceipt(") >= 3)
check("H4 the emailed copy is sent through the one client home too",
      "emailSaleReceipt(" in read(PANEL_SEND))
check("H5 the register still SHOWS the address and lets it be edited",
      "Email the receipt to" in read(PANEL_SEND) and "setEmail(" in read(PANEL_SEND))
check("H6 and says who it will come from", "sees it from" in read(PANEL_SEND))
editor = read(EDITOR)
check("H7 the admin screen no longer draws its own imitation of a receipt",
      "VERIZON" not in editor and "iPhone 15 Pro" not in editor)
check("H8 it previews the REAL renderer's output",
      "receipt-template/preview" in editor and "srcDoc={previewHtml}" in editor)
check("H9 the editor offers the layout choice and shows the From name",
      "Receipt layout" in editor and "Emailed receipts come from" in editor)
check("H10 and when the layout column is not there yet it says so through the ONE setup notice, so the "
      "migration name reaches the platform team and never a tenant (§19.36)",
      "SetupNotice" in editor and 'detail="1089_pos_receipt_layout.sql"' in editor
      and not [ln for ln in editor.splitlines()
               if "migration" in ln.lower() and not ln.strip().startswith("//")
               and "detail=" not in ln])
client = read(CLIENT)
check("H11 the client home spells the receipt URLs once each (the print choice rides the SAME url, "
      "it does not get a second endpoint)",
      client.count("/sales/${saleId}/receipt") == 1 and client.count("/sales/${saleId}/email-receipt") == 1)

print("\n§I  endpoint wiring + registration")
router = read(ROUTER)
ep = router.split('@router.get("/sales/{sale_id}/receipt")')[1].split("@router.")[0]
check("I1 GET /pos/sales/{id}/receipt renders through the one home",
      "_sr.html(" in router and "_receipt_bundle(" in ep)
check("I2 it is org-scoped", '.eq("org_id", org_id)' in router.split("def _load_sale")[1][:400])
mail = router.split('@router.post("/sales/{sale_id}/email-receipt")')[1].split("@router.")[0]
check("I3 the email endpoint refuses to send with no address, and says what to do",
      "no email address" in mail)
check("I4 it refuses a broken address instead of handing it to the provider",
      'view["valid"]' in mail)
check("I5 it passes the company name as the From name",
      'from_name=bundle["company_name"]' in mail)
check("I6 it reports what it sent and to whom", '"to": view["to"]' in mail and '"sent": True' in mail)
check("I7 an unconfigured provider is a plain sentence, not a 500",
      "isn't switched on" in mail and "503" in mail)
check("I8 the layout is a writable template field, and only the declared layouts are storable",
      '"layout")' in router and "_sr.LAYOUTS" in router)
check("I9 a layout save before migration 1089 names the file to run",
      "1089_pos_receipt_layout.sql" in router)
check("I10 the admin screen is handed the layouts, the note and the From name",
      '"layout_options"' in router and '"from_name"' in router and '"layout_note"' in router)
check("I11 the preview renders the same way as a real receipt (one renderer, sample sale)",
      "PREVIEW_SNAPSHOT" in router and "_sr.html(" in router.split("def preview_receipt_template")[1][:1500])
check("I12 the preview sample names no tenant, carrier or store (RULE TWO)",
      not re.search(r"(?i)\b(vzone|luxelink|cellfonz|verizon|boost|iphone)\b",
                    router.split("PREVIEW_SNAPSHOT = {")[1].split("}\n")[0]))
mig = read(MIGRATION)
check("I13 the migration is additive, idempotent and carries its REVERT",
      "ADD COLUMN IF NOT EXISTS layout" in mig and "-- REVERT" in mig and "DROP COLUMN IF EXISTS layout" in mig)
check("I14 it declares the same two layouts the renderer does",
      all(f"'{v}'" in mig for v in sr.LAYOUTS) and "CHECK (layout IN" in mig)
check("I15 it states the money impact", "MONEY IMPACT: NONE" in mig)
idx = read(INDEX)
check("I16 the endpoints are in the index",
      "GET /pos/sales/{sale_id}/receipt" in idx and "POST /pos/sales/{sale_id}/email-receipt" in idx)
check("I17 the one homes are registered",
      "sale_receipt" in idx and "pos_config_effective" in idx and "tenant_identity" in idx)

print("\n§K  what THIS print uses — the format chosen at the counter (§POS-03)")
# Owner 2026-10-19: "all stores should have the option to print the print in the required format
# from the print receipt screen as there are receipts from the thermal printer also". The class that
# was wrong: the layout was ONE stored per-org value, so a counter printing on a thermal roll and on
# an office printer could only ever produce whichever the org had stored.
T_ORG = sr.template_view({"paper_width_mm": 80, "layout": sr.LAYOUT_THERMAL})
check("K1 what the screen asked for wins over the store and the org",
      (lambda c: [c[k] for k in ("layout", "layout_note", "layout_source", "paper_width_mm",
                                 "paper_source")]
       == [sr.LAYOUT_POS_FORMAT, "", "request", 58, "request"])(
          sr.print_choice(T_ORG, want_layout=sr.LAYOUT_POS_FORMAT, want_paper="58",
                          store_layout=sr.LAYOUT_THERMAL, store_paper="80")))
check("K2 no request falls to what THIS STORE declared, not to the org",
      (lambda c: c["paper_width_mm"] == 58 and c["paper_source"] == "store")(
          sr.print_choice(T_ORG, store_paper="58")))
check("K3 a store that declared nothing reads the company template",
      (lambda c: c["paper_width_mm"] == 80 and c["paper_source"] == "org"
       and c["layout_source"] == "org")(sr.print_choice(T_ORG, store_paper="", store_layout="")))
check("K4 a request for a layout the tenant cannot print resolves AND SAYS SO (never silently)",
      (lambda c: c["layout"] == sr.LAYOUT_THERMAL and "no POS is declared" in c["layout_note"])(
          sr.print_choice(T_ORG, want_layout=sr.LAYOUT_POS_FORMAT, format_available=False)))
check("K5 a nonsense width does not stop the store printing — it reads the template's own width",
      sr.clean_paper("112", 80) == 80 and sr.clean_paper("", 58) == 58 and sr.clean_paper(None) == sr.DEFAULT_PAPER_MM
      and sr.print_choice(T_ORG, want_paper="112")["paper_width_mm"] == 80)
check("K6 the widths offered are DERIVED from the renderer's own, never a second list",
      [o["value"] for o in sr.paper_options()] == sorted(sr.PAPER_PX))
check("K7 one print's choice is a copy — it cannot leak into the next receipt",
      (lambda t, c: sr.with_choice(t, c)["layout"] == sr.LAYOUT_POS_FORMAT
       and t["layout"] == sr.LAYOUT_THERMAL)(
          dict(T_ORG), sr.print_choice(T_ORG, want_layout=sr.LAYOUT_POS_FORMAT)))
check("K8 the per-store printer declaration is three keys, each blank = accept the company default",
      all(pc.SERVER_DEFAULTS.get(k) == "" for k in
          ("receipt_printer_label", "receipt_paper_width_mm", "receipt_layout")))
check("K9 a store that said nothing reads BLANK, not a guessed width (the difference between "
      "honouring a declaration and inventing one)",
      [pc.printer([], "S1")[k] for k in ("label", "paper_width_mm", "layout")] == ["", "", ""]
      and pc.printer([{"store_code": "S1", "key": "receipt_paper_width_mm", "value": "58"}],
                     "S1")["paper_width_mm"] == "58")
check("K10 RULE TWO — no printer make is branched on anywhere in code",
      not re.search(r"(?i)\b(epson|citizen|star\s*micronics|bixolon)\b",
                    code_only(read("backend/app/modules/pos/pos_config_effective.py"))
                    + code_only(read("backend/app/modules/pos/sale_receipt.py"))
                    + code_only(router)))
check("K11 the GET takes the choice and resolves it through the one resolver",
      "def sale_receipt(sale_id: str" in router and "layout: str" in ep and "paper_width_mm: str" in ep
      and "want_layout=layout" in ep and "_sr.print_choice(" in router)
check("K12 the answer carries what this store may print, what it resolved to and its printer",
      all(k in router for k in ('"layout_options"', '"paper_options"', '"printer_label"',
                                '"layout_source"', '"paper_source"'))) 
check("K13 the emailed copy is the copy that was PRINTED (the same choice rides it)",
      "want_layout=body.get" in mail and "want_paper=body.get" in mail)
cl = read(CLIENT)
check("K14 the client spells the choice ONCE for both the print and the email path",
      cl.count("function choiceParams") == 1 and cl.count("choiceParams(choice)") == 3)
check("K15 the till and the Sale Log reprint share ONE picker, which offers only the server's own "
      "formats (a second copy is how one counter gets a format the other does not)",
      read("frontend/src/components/pos/PrintFormatPicker.tsx").count("view.layout_options") == 1
      and reg.count("<PrintFormatPicker") == 2
      and not re.search(r"['\"](thermal|pos_format)['\"]",
                        read("frontend/src/components/pos/PrintFormatPicker.tsx")))
check("K16 §POS-03 is registered in the index with its keys",
      "POS-03" in idx and "receipt_printer_label" in idx and "print_choice" in idx)

print("\n§L  straight to the printer — one slip, two media (§POS-04)")
# Owner 2026-10-20: "the receipts will print to the thermal printer only if desired by the user,
# there cannot be any harcoded rule so we need have a helper". A byte stream for the printer is a
# second MEDIUM, never a second receipt: both render sale_receipt.slip_model.
from app.modules.pos import receipt_escpos as esc  # noqa: E402

SR_SRC = read("backend/app/modules/pos/sale_receipt.py")
ESC_SRC = read("backend/app/modules/pos/receipt_escpos.py")
HELPER = read("tools/pos-print-helper/receipt-helper.js")
MODEL = sr.slip_model(SNAP, ALL_ON, brand=BRAND, store_name="STORE 1",
                      disclaimer_blocks=[{"text": "Return policy", "signature": True}],
                      tenant_name="Tenant", employee_name="A. Rep")
ESC_LINES = [l["text"] for l in esc.lines(MODEL)]

check("L1 the slip's CONTENT has ONE home, and the router hands BOTH media the same inputs",
      "def slip_model(" in SR_SRC and SR_SRC.count("def slip_model(") == 1
      and "_sr.slip_model(snap, template, brand=brand, disclaimer_blocks=blocks" in router
      and "disclaimer_blocks=blocks" in router and router.count("blocks = _poscfg.disclaimers(") == 1)
check("L1b and it renders the model, not the snapshot: the bytes module never reads a template "
      "toggle or a snapshot total for itself",
      not re.search(r'model\[.(show_|subtotal|tax_total|discount_total)', ESC_SRC)
      and not re.search(r'(snap|template_v)\[', code_only(ESC_SRC)))
check("L1c the HTML renderer reads the same model (the discount arithmetic is computed once)",
      SR_SRC.count("display_subtotal") == 2
      and "m = slip_model(" in SR_SRC and 'm["totals"]' in SR_SRC)
check("L2 the printed slip still adds up: Subtotal − Discounts + Tax = the total paid",
      round(MODEL["totals"][0]["value"] - MODEL["totals"][1]["value"]
            + MODEL["totals"][2]["value"], 2) == SNAP["total"])
check("L3 every item, the grand total and the footer reach the paper",
      all(any(it["description"][:20] in ln for ln in ESC_LINES) for it in MODEL["items"])
      and any(ln.startswith("TOTAL") and "1,076.75" in ln for ln in ESC_LINES)
      and any("No refunds" in ln for ln in ESC_LINES))
check("L4 no line is wider than the roll, not even a word longer than the roll itself",
      max(len(ln) for ln in ESC_LINES) <= sr.PAPER_COLS[80]
      and esc.wrap("A" * 99, 32) == ["A" * 32, "A" * 32, "A" * 32, "A" * 3]
      and max(len(ln) for ln in esc.wrap("word " * 40, 32)) <= 32)
check("L5 a figure sits hard against the right edge, and the label gives way before it does",
      esc.two_col("Total", "9.99", 20) == "Total" + " " * 11 + "9.99"
      and len(esc.two_col("Total", "9.99", 20)) == 20
      and esc.two_col("A" * 30, "9.99", 10) == "AAAAA 9.99")
check("L6 the stream resets the printer first and cuts the slip last (a slip nobody can tear off is "
      "not printed)",
      esc.payload(MODEL).startswith(esc.INIT) and esc.payload(MODEL).endswith(esc.FEED_CUT))
check("L7 the roll's width decides the line length, from ONE declared map",
      len(esc.lines(sr.slip_model(SNAP, sr.template_view({"paper_width_mm": 58})))[-1]["text"])
      <= sr.PAPER_COLS[58] and sorted(sr.PAPER_COLS) == sorted(sr.PAPER_PX))
OFF = sr.template_view({"show_serials": False, "show_tax_breakdown": False, "show_discounts": False,
                        "paper_width_mm": 80})
OFF_LINES = [l["text"] for l in esc.lines(sr.slip_model(SNAP, OFF))]
check("L8 an admin's toggles reach the PRINTED slip too, not only the paper one",
      not any("S/N" in ln for ln in OFF_LINES) and not any(ln.startswith("Tax") for ln in OFF_LINES)
      and any("S/N" in ln for ln in ESC_LINES) and any(ln.startswith("Tax") for ln in ESC_LINES))
check("L9 direct printing is OFF until a store turns it on, the cashier can still choose either way, "
      "and an unknown destination reads as the dialog (never a silent no-print)",
      sr.print_choice(T_ORG)["destination"] == sr.DEST_DIALOG
      and sr.print_choice(T_ORG, store_direct=True)["destination"] == sr.DEST_DIRECT
      and sr.print_choice(T_ORG, want_destination=sr.DEST_DIALOG,
                          store_direct=True)["destination"] == sr.DEST_DIALOG
      and sr.print_choice(T_ORG, want_destination="usb")["destination"] == sr.DEST_DIALOG
      and sr.print_choice(T_ORG, want_destination=sr.DEST_DIRECT)["destination_source"] == "request")
check("L10 the opt-in and the helper address are per-store SETTINGS, not constants in code",
      pc.SERVER_DEFAULTS["receipt_direct_print"] is False
      and pc.SERVER_DEFAULTS["receipt_helper_url"].startswith("http://127.0.0.1")
      and pc.printer([{"store_code": "S1", "key": "receipt_direct_print", "value": True}],
                     "S1")["direct"] is True
      and pc.printer([], "S1")["direct"] is False)
check("L11 the screen is offered BOTH destinations every time, the store's printer named in the one "
      "that goes to it",
      [o["value"] for o in sr.destination_options("EXAMPLE TM-1")] == list(sr.DESTINATIONS)
      and "EXAMPLE TM-1" in sr.destination_options("EXAMPLE TM-1")[1]["label"]
      and "no helper address" in sr.destination_options("", helper_ready=False)[1]["label"])
check("L12 the bytes are built ONLY for a print that is going to the printer",
      '_sr.DEST_DIRECT else ""' in router and "_escpos.payload_base64(" in router
      and router.count("_escpos.") == 1)
check("L13 the GET carries the destination and answers with it, the helper address and the bytes",
      "destination: str" in router and "want_destination=destination" in router
      and all(k in router for k in ('"destination"', '"destination_source"',
                                    '"destination_options"', '"helper_url"', '"escpos_base64"')))
check("L14 the register is the helper's COURIER, not a second renderer: only the client home spells "
      "the helper call, and it builds no receipt of its own",
      cl.count("fetch(helperUrl") == 1 and "printViaHelper" in reg
      and "escpos" not in reg.lower().replace("escpos_base64", "x")
      and reg.count("helper_url") == 1)
check("L15 a helper that is not running costs nobody their receipt — the dialog is the fallback and "
      "the cashier is told",
      "Printing through the print dialog instead" in reg
      and "Could not reach the receipt printer helper" in cl)
check("L16 the helper listens on this PC ONLY, runs no command its caller names, and refuses to "
      "start without being told which printer",
      "'127.0.0.1'" in HELPER and "0.0.0.0" not in HELPER
      and "execFile(" in HELPER and "exec(" not in HELPER.replace("execFile(", "")
      and "Which printer?" in HELPER and "process.exit(1)" in HELPER)
check("L17 RULE TWO — no printer make is branched on in any of it",
      not re.search(r"(?i)\b(epson|citizen|star\s*micronics|bixolon)\b",
                    code_only(ESC_SRC) + code_only(SR_SRC) + code_only(router)
                    + read("frontend/src/components/pos/PrintFormatPicker.tsx") + cl))
check("L18 §POS-04 is registered in the index with its one homes",
      "POS-04" in idx and "receipt_escpos" in idx and "slip_model" in idx
      and "pos-print-helper" in idx)

print("\n§M  THE DECLARED FORMAT HAS ONE SHAPE (the 2026-10-20 print crash, ref 357a4772)")
# WHAT WENT WRONG. `sales_from_reports.resolve_format` answers {"ok","pos","format","reason"} where
# `format` is the REGISTRY ROW ({source,label,parse,module}). Three callers in pos/router.py each
# unpacked that themselves: the admin screen kept the ROW and read `.get("label")` off it, the print
# path narrowed it to the MODULE — and §POS-03 copied the row's `.get("label")` onto the module. A
# module has no `.get`, so every receipt of the one tenant that HAS a declared POS died with an
# unhandled 500, while the tenants with no POS declared (format None) printed fine. The fact had
# three derivations; §M pins its ONE home and the four call sites that dereference it.
from app.modules.pos import sales_from_reports as sfr          # noqa: E402
from app.modules.pos.receipt_formats import registry as rfmt   # noqa: E402
from app.modules.commcalc import report_kinds as rk            # noqa: E402
from app.modules.pos import router as prouter                  # noqa: E402


class _FakeQ:
    """Enough of a PostgREST query to read rows back: every filter returns self."""

    def __init__(self, rows):
        self._rows = rows

    def __getattr__(self, _name):
        return lambda *a, **kw: self

    def execute(self):
        return type("Res", (), {"data": list(self._rows)})()


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **kw):
        return _FakeQ(self._rows)

    def insert(self, *a, **kw):
        return _FakeQ(self._rows)


class FakeClient:
    """A DB-free stand-in: rows keyed by (schema, table). No network, no credentials."""

    def __init__(self, data):
        self._data, self._schema = data, ""

    def schema(self, name):
        self._schema = name
        return self

    def table(self, name):
        return _FakeTable(self._data.get((self._schema, name), []))


def declared_as(pos_keys):
    """`declared_format` read for a tenant whose declaration names `pos_keys`."""
    real = rk.tenant_declaration
    rk.tenant_declaration = lambda client, org_id: {"pos": list(pos_keys)}
    try:
        return sfr.declared_format(FakeClient({}), "O")
    finally:
        rk.tenant_declaration = real


RQ_SOURCE = rfmt.sources()[0]                       # the registry's own first POS, never a literal
RQ_ROW = rfmt.get(RQ_SOURCE)
M_DATA = {
    ("pos", "pos_settings"): [],
    ("pos", "receipt_templates"): [{"id": "t1", "org_id": "O", "name": "Default", "is_default": True,
                                    "layout": sr.LAYOUT_POS_FORMAT, "paper_width_mm": 80,
                                    "font_size_px": 12, "show_store_name": True,
                                    "show_customer": True}],
    ("storeops", "stores"): [{"store_code": "S1", "name": "Shop", "address": "1 St", "phone": "555"}],
}
M_SALE = {"id": "s1", "store_code": "S1", "employee_id": "E1",
          "receipt": {"transactionId": 9,
                      "items": [{"description": "Phone", "qty": 1, "unit_price": 100.0,
                                 "line_total": 100.0}],
                      "subtotal": 100.0, "taxTotal": 0, "total": 100.0, "method": "cash",
                      "tendered": 100.0, "change": 0}}


def bundle_for(pos_keys):
    real = rk.tenant_declaration
    rk.tenant_declaration = lambda client, org_id: {"pos": list(pos_keys)}
    try:
        return prouter._receipt_bundle(FakeClient(M_DATA), "O", M_SALE)
    finally:
        rk.tenant_declaration = real


def declared_format_survives_a_broken_read():
    """A receipt must still print when the declaration read itself fails."""
    real = rk.tenant_declaration

    def boom(client, org_id):
        raise RuntimeError("the declaration table is unreachable")

    rk.tenant_declaration = boom
    try:
        v = sfr.declared_format(FakeClient({}), "O")
    finally:
        rk.tenant_declaration = real
    return (v["available"] is False and v["module"] is None and v["label"] == ""
            and bool(v["reason"]))


_declared = declared_as([RQ_SOURCE])

check("M1 the declared format answers ONE fixed shape, named in the module",
      tuple(sorted(_declared)) == tuple(sorted(sfr.DECLARED_FORMAT_KEYS)))
check("M2 a declared POS the registry knows gives its label AND its module, both dereferenced",
      _declared["available"] is True and _declared["pos"] == RQ_SOURCE
      and _declared["label"] == RQ_ROW["label"] and _declared["module"] is RQ_ROW["module"]
      and _declared["reason"] is None)
check("M3 no POS declared reads as no format, with the reason spoken aloud",
      declared_as([])["available"] is False and declared_as([])["label"] == ""
      and declared_as([])["module"] is None and bool(declared_as([])["reason"]))
check("M4 a declared POS with no registered format keeps the declaration and refuses the format",
      declared_as(["a-pos-nobody-registered"])["available"] is False
      and declared_as(["a-pos-nobody-registered"])["pos"] == "a-pos-nobody-registered"
      and declared_as(["a-pos-nobody-registered"])["module"] is None)
check("M5 REGRESSION — the receipt of a tenant WITH a declared POS builds (it raised AttributeError)",
      bundle_for([RQ_SOURCE])["layout_options"][1]["label"].startswith(RQ_ROW["label"])
      and bundle_for([RQ_SOURCE])["layout"] == sr.LAYOUT_POS_FORMAT
      and len(bundle_for([RQ_SOURCE])["html"]) > 500)
check("M6 a tenant with NO declared POS still prints, on the till slip, and says why",
      bundle_for([])["layout"] == sr.LAYOUT_THERMAL and bool(bundle_for([])["layout_note"])
      and len(bundle_for([])["html"]) > 500)
ROUTER_CODE = code_only(read(ROUTER))
check("M7 LOCK — the POS router no longer resolves the format itself; one reader, four dereferences",
      "resolve_format(" not in ROUTER_CODE and "_declared_receipt_format" not in ROUTER_CODE
      and ROUTER_CODE.count("def _declared_format(") == 1
      and ROUTER_CODE.count("_declared_format(") == 5)
check("M8 LOCK — no caller reads a label or a module off the record by hand",
      'fmt.get("label")' not in ROUTER_CODE and "fmt.LABEL" not in ROUTER_CODE
      and 'fmt.get("module")' not in ROUTER_CODE
      and ROUTER_CODE.count('_sr.layout_options(fmt["label"])') == 2)
# THREE call sites since §POS-08: the print/email body, the admin preview, and the PDF attachment.
# The count is the lock — a fourth that passed the RECORD instead would be the §POS-05 crash again.
check("M9 LOCK — the renderer is handed the MODULE, never the record",
      ROUTER_CODE.count('fmt=fmt["module"]') == 3 and "fmt=fmt," not in ROUTER_CODE)
check("M10 the one home never raises when the declaration cannot be read",
      declared_format_survives_a_broken_read())
check("M11 §POS-05 is registered in the index with its one home",
      "POS-05" in idx and "declared_format" in idx)

print("\n§N  PAY BY BANK TRANSFER — one paragraph, every medium, sealed at rest (§POS-06)")
# Owner 2026-10-21, verbatim: *"need to add the option of adding the payment information to the
# customer when creating the receipt for the customer as a check mark, two boxes to fill out routing
# number and account number to be sent to the customer, tehy will be saved in the register set up"*.
#
# WHAT §N PINS. The numbers are VALIDATED before they are stored (a routing number has a check digit,
# and a receipt is the one place a wrong number is expensive). They are SEALED at rest and masked
# everywhere a screen can see them, because `GET /pos/settings` is read by every POS login and this
# house found two bank routing numbers in plain text on 2026-10-12. The block is ONE paragraph built
# in one place, so the paper copy, the emailed copy, the printer's byte stream and the tenant's own
# POS layout cannot word it or number it differently. And a tick cannot print half a bank account.
from app.core import crypto as _crypto                      # noqa: E402
from app.modules.pos import receipt_remit as rmt            # noqa: E402

RMT_SRC = read("backend/app/modules/pos/receipt_remit.py")
REMIT_FIELDS = "frontend/src/components/pos/ReceiptRemitFields.tsx"
remit_tsx = read(REMIT_FIELDS)
cfg_tsx = read("frontend/src/components/pos/PosConfigSection.tsx")

#: A real routing number's checksum holds; the same digits with one pair transposed does not. Both
#: are arithmetic over a published format, so neither names a bank (RULE TWO).
GOOD_ROUTING = "021000021"
BAD_ROUTING = "021000012"            # last two digits swapped
GOOD_ACCOUNT = "1234567890"
DECLARED_ROWS = [
    {"store_code": None, "key": rmt.ROUTING_KEY, "value": rmt.seal_setting_value(GOOD_ROUTING)},
    {"store_code": None, "key": rmt.ACCOUNT_KEY, "value": rmt.seal_setting_value(GOOD_ACCOUNT)},
]


def refuses_remit(fn, value):
    try:
        fn(value)
        return False
    except rmt.RemitError:
        return True


check("N1 the two settings that hold a bank number are DECLARED once, and the server knows them",
      rmt.KEYS == (rmt.ROUTING_KEY, rmt.ACCOUNT_KEY)
      and all(k in pc.SERVER_DEFAULTS for k in rmt.KEYS)
      and all(pc.SERVER_DEFAULTS[k] == "" for k in rmt.KEYS))
check("N2 a routing number must pass its own check digit — a transposed pair is REFUSED",
      rmt.clean_routing(GOOD_ROUTING) == GOOD_ROUTING
      and rmt.routing_checksum_ok(GOOD_ROUTING) and not rmt.routing_checksum_ok(BAD_ROUTING)
      and refuses_remit(rmt.clean_routing, BAD_ROUTING)
      and refuses_remit(rmt.clean_routing, "02100002"))
check("N3 what a person typed is stored as DIGITS — spaces, dashes and letters never reach a slip",
      rmt.clean_routing(" 021-000 021 ") == GOOD_ROUTING
      and rmt.clean_account("1234-567890") == GOOD_ACCOUNT
      and refuses_remit(rmt.clean_account, "12")
      and refuses_remit(rmt.clean_account, "1" * 18))
check("N4 blank CLEARS a number rather than refusing it (a store that stops taking transfers)",
      rmt.clean_routing("") == "" and rmt.clean_account(None) == ""
      and rmt.seal_setting_value("") == "")
check("N5 a screen only ever sees the last four",
      rmt.mask(GOOD_ACCOUNT) == "••••••7890" and rmt.mask("1234") == "••••"
      and rmt.mask("") == "" and GOOD_ACCOUNT not in rmt.mask(GOOD_ACCOUNT))
check("N6 stored SEALED, and the round trip is the digits again",
      rmt.open_setting_value(rmt.seal_setting_value(GOOD_ACCOUNT)) == GOOD_ACCOUNT
      and (not _crypto.is_enabled()
           or _crypto.is_encrypted(rmt.seal_setting_value(GOOD_ACCOUNT))))
check("N7 the store override → org default rule is DEREFERENCED, never re-derived here",
      "_poscfg.effective(" in RMT_SRC and "store_code" in RMT_SRC
      and rmt.declared(DECLARED_ROWS)["routing"] == GOOD_ROUTING
      and rmt.declared(DECLARED_ROWS + [
          {"store_code": "S9", "key": rmt.ROUTING_KEY,
           "value": rmt.seal_setting_value("011000015")}], "S9")["routing"] == "011000015")
check("N8 half a bank account is not a payment instruction, and never prints",
      rmt.declared(DECLARED_ROWS)["available"] is True
      and rmt.declared(DECLARED_ROWS[:1])["available"] is False
      and rmt.block(rmt.declared(DECLARED_ROWS[:1]), include=True) == ""
      and rmt.block(rmt.declared([]), include=True) == "")
check("N9 off unless THIS receipt asked for it, and a tick cannot turn it on with nothing declared",
      sr.print_choice(T_ORG, remit_available=True)["remit"] is False
      and sr.print_choice(T_ORG, want_remit="1", remit_available=True)["remit"] is True
      and sr.print_choice(T_ORG, want_remit="1", remit_available=False)["remit"] is False
      and sr.print_choice(T_ORG, want_remit="1", remit_available=True)["remit_source"] == "request"
      and sr.print_choice(T_ORG, remit_available=True)["remit_source"] == "off")
check("N10 the wire has ONE spelling of a tick",
      all(sr._asked(v) for v in (True, "1", "true", "on", "YES"))
      and not any(sr._asked(v) for v in (None, "", "0", "false", "off", "no")))

REMIT_BLOCK = rmt.block(rmt.declared(DECLARED_ROWS), include=True)
check("N11 the paragraph names the two numbers in full and says what it is for",
      GOOD_ROUTING in REMIT_BLOCK and GOOD_ACCOUNT in REMIT_BLOCK
      and rmt.HEADING in REMIT_BLOCK and REMIT_BLOCK.count("\n") == 2)
check("N12 it rides the slip's own footer blocks, LAST, and never takes a signature line",
      sr.slip_model(SNAP, ALL_ON, remit_block=REMIT_BLOCK)["blocks"][-1]
      == {"text": REMIT_BLOCK, "signature": False}
      and len(sr.slip_model(SNAP, ALL_ON, disclaimer_blocks=[{"text": "legal", "signature": True}],
                            remit_block=REMIT_BLOCK)["blocks"]) == 2
      and sr.slip_model(SNAP, ALL_ON, disclaimer_blocks=[{"text": "legal", "signature": True}],
                        remit_block=REMIT_BLOCK)["blocks"][0]["text"] == "legal")
check("N13 the PAPER copy carries it",
      GOOD_ROUTING in sr.thermal_html(SNAP, ALL_ON, remit_block=REMIT_BLOCK)
      and GOOD_ACCOUNT in sr.thermal_html(SNAP, ALL_ON, remit_block=REMIT_BLOCK))
ESC_TEXT = " ".join(ln["text"] for ln in esc.lines(
    sr.slip_model(SNAP, ALL_ON, remit_block=REMIT_BLOCK)))
check("N14 the PRINTER's own byte stream carries the same numbers — no renderer change was needed",
      GOOD_ROUTING in ESC_TEXT and GOOD_ACCOUNT in ESC_TEXT
      and "remit" not in code_only(ESC_SRC))
check("N15 the tenant's own POS layout carries it too, in the Document's own free-text block",
      sr.document(SNAP, RQ_ROW["module"], ALL_ON, remit_block=REMIT_BLOCK)["comments"] == REMIT_BLOCK
      and sr.document(SNAP, RQ_ROW["module"], ALL_ON)["comments"] is None)
check("N16 a receipt nobody ticked carries nothing — no heading, no digits, in any medium",
      REMIT_BLOCK not in sr.thermal_html(SNAP, ALL_ON)
      and rmt.HEADING not in sr.thermal_html(SNAP, ALL_ON)
      and GOOD_ACCOUNT not in " ".join(ln["text"] for ln in esc.lines(
          sr.slip_model(SNAP, ALL_ON)))
      and sr.slip_model(SNAP, ALL_ON)["blocks"] == [])
ROUTER_N = code_only(read(ROUTER))
# THREE media are handed the one string since §POS-08 (the HTML body, the printer's bytes, the PDF
# attachment) and it is still built ONCE. Both numbers are the lock.
check("N17 LOCK — the paragraph is built ONCE and every medium is handed that string",
      ROUTER_N.count("_remit.block(") == 1 and ROUTER_N.count("remit_block=remit_block") == 3
      and code_only(SR_SRC).count("def block(") == 0)
check("N18 LOCK — the settings read every POS login can call answers these two keys MASKED",
      "_remit.KEYS" in ROUTER_N and "_remit.mask(_remit.open_setting_value(" in ROUTER_N
      and ROUTER_N.count("def list_settings(") == 1)
check("N19 LOCK — the editor is gated like every other setting write, and is the ONE writer",
      'PUT /pos/receipt-remit' in read(ROUTER)
      and ROUTER_N.count("def upsert_receipt_remit(") == 1
      and '_require_pos_perm(authorization, org_id, "pos_settings")' in ROUTER_N.split(
          "def upsert_receipt_remit(")[1].split("def ")[0]
      and ROUTER_N.split("def upsert_receipt_remit(")[1].split("def ")[0].count(
          "upsert_pos_setting(") == 1)
check("N20 LOCK — one sealer, and the family is registered so old rows cannot stay in the clear",
      code_only(RMT_SRC).count("crypto.encrypt(") == 1
      and "receipt_remit" in read("backend/app/core/encryption_backfill.py")
      and "app/modules/pos/receipt_remit.py" in read("backend/app/core/encryption_backfill.py")
      and "receipt_remit.seal_setting_value" in read("backend/app/core/encryption_backfill.py"))
check("N21 the counter may TICK it but not TYPE it — the numbers are read-only on the print screen",
      "remit_available" in read("frontend/src/components/pos/PrintFormatPicker.tsx")
      and "remit_routing_masked" in read("frontend/src/components/pos/PrintFormatPicker.tsx")
      and "saveReceiptRemit" not in read("frontend/src/components/pos/PrintFormatPicker.tsx"))
check("N22 the generic settings screen cannot save the MASK over the real number",
      "sensitive" in cfg_tsx and "filter(def => !def.sensitive)" in cfg_tsx
      and "sensitive: true" in ts and "saveReceiptRemit" in remit_tsx)
check("N23 RULE TWO — no bank and no tenant is named in any of it",
      not re.search(r"(?i)\b(chase|wells\s*fargo|citibank|citizens|navy\s*federal|"
                    r"vzone|luxelink|cellfonz)\b",
                    code_only(RMT_SRC) + remit_tsx))
check("N24 §POS-06 is registered in the index with its one home",
      "POS-06" in idx and "receipt_remit" in idx and "Pay by bank" in idx)

print("\n§O  THE CUSTOMER'S OWN ADDRESS — one home, every medium (§POS-07)")
# Owner 2026-10-21, verbatim: *"teh receipt shoudl show the complete address for the customer as in
# the system"*. The CLASS, not the instance: "what does this customer's record say, written out"
# had no home, so the one caller that wanted it (the receipt rebuilt from an imported report) spelled
# the five columns itself and the till slip carried the name alone.
from app.modules.pos import customer_bill_to as bt  # noqa: E402

BT_SRC = read("backend/app/modules/pos/customer_bill_to.py")
SFR_SRC = read("backend/app/modules/pos/sales_from_reports.py")
CUST = {"id": "c1", "first_name": "John", "last_name": "Smith",
        "address_1": "123 Main Street", "address_2": "Apt 2",
        "city": "Brooklyn", "state": "NY", "zip": "11219"}
CUST_LINES = bt.lines(CUST)

check("O1 the whole record prints as the street then the city, state and postal code",
      CUST_LINES == ["123 Main Street Apt 2", "Brooklyn NY 11219"])
check("O2 a half-filled record prints what it knows and leaves no blank line",
      bt.lines({"address_1": "123 Main Street"}) == ["123 Main Street"]
      and bt.lines({"city": "Brooklyn", "zip": "11219"}) == ["Brooklyn 11219"]
      and all(ln.strip() for ln in bt.lines({"address_2": "  ", "state": "NY"})))
check("O3 a walk-in with no record is [] and never an error",
      bt.lines(None) == [] and bt.lines({}) == [] and bt.has_address({}) is False
      and bt.has_address(CUST) is True)
check("O4 the five columns are named ONCE and a reader dereferences them",
      bt.FIELDS == ("address_1", "address_2", "city", "state", "zip")
      and bt.COLUMNS == "address_1,address_2,city,state,zip"
      and "_bill_to.COLUMNS" in router
      and not re.search(r'select\("[^"]*address_1', code_only(router)))
check("O5 the bill-to block leads with the name, then where they are",
      bt.block("John Smith", CUST)["lines"] == ["John Smith"] + CUST_LINES
      and bt.block("", CUST)["lines"] == CUST_LINES)

slip = sr.slip_model(SNAP, ALL_ON, brand=BRAND, store_name="STORE 1", customer_lines=CUST_LINES)
heads = [h["text"] for h in slip["head_lines"]]
check("O6 the till slip prints the address directly under the customer's name",
      heads.index("Customer: John Smith") + 1 == heads.index(CUST_LINES[0])
      and heads.index(CUST_LINES[0]) + 1 == heads.index(CUST_LINES[1]))
check("O7 the printer's byte stream carries it too — the same model, no change to the renderer",
      all(ln.encode("cp437") in esc.payload(slip) for ln in CUST_LINES)
      and "customer_lines" not in ESC_SRC)
check("O8 the paper copy and the emailed copy carry it (one renderer of that model)",
      all(ln in sr.thermal_html(SNAP, ALL_ON, customer_lines=CUST_LINES) for ln in CUST_LINES))
doc = sr.document(SNAP, RQ_ROW["module"], ALL_ON, store_row=STORE_ROW, customer_lines=CUST_LINES)
check("O9 the tenant's own POS layout bills to the same block",
      doc["bill_to"]["lines"] == ["John Smith"] + CUST_LINES
      and doc["bill_to"]["name"] == "John Smith")
off = sr.template_view({"show_customer": False, "paper_width_mm": 80})
check("O10 one toggle, one block — with the customer hidden, the address is hidden with it",
      not any(CUST_LINES[0] in h["text"]
              for h in sr.slip_model(SNAP, off, customer_lines=CUST_LINES)["head_lines"])
      and sr.document(SNAP, RQ_ROW["module"], off, customer_lines=CUST_LINES)["bill_to"]["lines"] == [])
check("O11 the imported-receipt path dereferences the one home instead of its own copy",
      "_bt.lines(row)" in SFR_SRC
      and not re.search(r'address_[12]', code_only(SFR_SRC)))
# THE LOCK. One axis, the assembly: a join() or an f-string that mentions an address field. Only the
# display home and the COMPARISON home (`customer_identity.norm_address`, a different question with a
# different shape) may hold one. A second copy appearing anywhere under backend/app fails the build.
BT_ALLOWED = {"app/modules/pos/customer_bill_to.py", "app/modules/pos/customer_identity.py"}
_assembly = re.compile(r'(?:join\([^\n]{0,200}|f["\'][^\n"\']{0,200})'
                       r'(?:address_1|address_2|["\']city["\'])')
_copies = sorted({os.path.relpath(os.path.join(b, f), ROOT).replace(os.sep, "/")
                  for b, _, fs in os.walk(os.path.join(ROOT, "backend", "app"))
                  for f in fs if f.endswith(".py")
                  and _assembly.search(code_only(read(os.path.relpath(os.path.join(b, f), ROOT))))}
                 - {"backend/" + a for a in BT_ALLOWED})
check("O12 LOCK — nothing else writes a customer's address out for a human", not _copies,
      f"a second copy appeared in {_copies}")
# Read ONCE and handed to THREE media since §POS-08: the HTML body, the printer's byte stream and
# the PDF attachment. Both numbers are the lock — a medium that read the customer again could show
# a different address from the one on the paper copy.
check("O13 the reader hands the SAME lines to every medium",
      router.count("customer_lines=customer_lines") == 3
      and router.count("customer_lines = _bill_to.lines(customer)") == 1)
check("O14 the admin preview shows the block, so a toggle can be seen before it ships",
      "_bill_to.lines(PREVIEW_CUSTOMER)" in router and "PREVIEW_CUSTOMER" in router)
check("O15 RULE TWO — no tenant, carrier or real address is written into the rule",
      not re.search(r"(?i)\b(vzone|luxelink|cellfonz|boost|total\s*wireless)\b", code_only(BT_SRC))
      and not re.search(r"\b\d{5}\b", code_only(BT_SRC)))
check("O16 §POS-07 is registered in the index with its one home",
      "POS-07" in idx and "customer_bill_to" in idx)

print("\n§P  THE RECEIPT AS A FILE — a third medium, not a third receipt (§POS-08)")
# Owner 2026-10-21, verbatim: *"the receipt is going the body of the meial it should as a pdf
# attachment aswell"*. An emailed receipt in the body of the mail is one the customer cannot keep.
from app.modules.pos import receipt_pdf_render as rpdf  # noqa: E402
import pdfplumber  # noqa: E402  (already a backend dependency — the format parsers read PDFs with it)

RPDF_SRC = read("backend/app/modules/pos/receipt_pdf_render.py")
T58 = sr.template_view({"show_customer": True, "paper_width_mm": 58})
T_POS = sr.template_view({"show_customer": True, "paper_width_mm": 80,
                          "layout": sr.LAYOUT_POS_FORMAT})
REMIT_BLOCK = "Pay by bank transfer\nRouting number: 021000021\nAccount number: 1234567890"


def pdf_pages(data):
    """(page sizes, the text of each page) — read back with the same library the parsers use, so a
    check here is a check on what a customer's reader will actually show."""
    with pdfplumber.open(io.BytesIO(data)) as doc:
        return ([(round(pg.width, 2), round(pg.height, 2)) for pg in doc.pages],
                [(pg.extract_text() or "") for pg in doc.pages])


SLIP_PDF = rpdf.receipt_pdf(SNAP, ALL_ON, brand=BRAND, store_name="STORE 1",
                            disclaimer_blocks=[{"text": "Return policy", "signature": True}],
                            employee_name="A. Rep", customer_lines=CUST_LINES,
                            remit_block=REMIT_BLOCK)
SLIP_SIZES, SLIP_TEXT = pdf_pages(SLIP_PDF)
SLIP_ALL = "\n".join(SLIP_TEXT)

check("P1 it is a real PDF, and a till slip is ONE page however long it is (a roll has no break)",
      SLIP_PDF[:5] == b"%PDF-" and len(SLIP_SIZES) == 1)
check("P2 the page is as wide as the roll the store declared, not a letter sheet",
      SLIP_SIZES[0][0] == round(80 * rpdf.PT_PER_MM, 2)
      and pdf_pages(rpdf.receipt_pdf(SNAP, T58, store_name="STORE 1"))[0][0][0]
      == round(58 * rpdf.PT_PER_MM, 2))
_slip_rows = [r["text"].strip() for r in esc.lines(
    sr.slip_model(SNAP, ALL_ON, brand=BRAND, store_name="STORE 1",
                  disclaimer_blocks=[{"text": "Return policy", "signature": True}],
                  employee_name="A. Rep", customer_lines=CUST_LINES, remit_block=REMIT_BLOCK))
              if r["text"].strip() and set(r["text"].strip()) != {"-"}]
# Compared with runs of spaces collapsed on BOTH sides: a PDF reader re-spaces a line that the
# printer pads with blanks to right-align a figure (`receipt_escpos.two_col`), so the words and their
# order are the fact here, not the padding — which §L already pins on the printer's own bytes.
def _flat(t):
    return " ".join(str(t).split())


_SLIP_FLAT = _flat(SLIP_ALL)
check("P3 the file says exactly what the printer cuts — every row of the one content home is in it",
      all(_flat(row) in _SLIP_FLAT for row in _slip_rows),
      f"missing {[r for r in _slip_rows if _flat(r) not in _SLIP_FLAT][:3]}")
check("P4 the figures are the snapshot's own, not recomputed for the file",
      f'{SNAP["total"]:,.2f}' in SLIP_ALL and f'{SNAP["tax_total"]:,.2f}' in SLIP_ALL)
check("P5 the pay-by-bank block rides into the file when THIS print asked for it (§POS-06)",
      "Pay by bank transfer" in SLIP_ALL and "021000021" in SLIP_ALL
      and "Pay by bank transfer" not in "\n".join(pdf_pages(
          rpdf.receipt_pdf(SNAP, ALL_ON, store_name="STORE 1"))[1]))
check("P6 the customer's own address rides into it too (§POS-07)",
      all(ln in SLIP_ALL for ln in CUST_LINES))

DOC_PDF = rpdf.receipt_pdf(SNAP, T_POS, fmt=RQ_ROW["module"], render_words=_render.render_words,
                           store_row=STORE_ROW, customer_lines=CUST_LINES)
DOC_SIZES, DOC_TEXT = pdf_pages(DOC_PDF)
check("P7 the tenant's own POS layout is the file's layout too, on its own page size",
      len(DOC_SIZES) == 1 and DOC_SIZES[0] == (612.0, 792.0)
      and "John Smith" in DOC_TEXT[0] and CUST_LINES[0] in DOC_TEXT[0])
# THE DEFECT THIS FOUND. Touching the canvas after a page is closed opens the next one, and the
# library then writes that empty page into the file — a blank second sheet in a customer's receipt.
check("P8 no blank page is written after the last one",
      all(txt.strip() for txt in DOC_TEXT))
_long = dict(SNAP_IN)
_long["items"] = [{"description": f"Item {i}", "serial": "", "qty": 1, "unit_price": 10.0,
                   "discount": 0, "line_total": 10.0} for i in range(120)]
_long_pdf = rpdf.receipt_pdf(sr.snapshot_view(_long, {"status": "completed"}), T_POS,
                             fmt=RQ_ROW["module"], render_words=_render.render_words,
                             store_row=STORE_ROW)
_long_sizes, _long_text = pdf_pages(_long_pdf)
_long_all = "\n".join(_long_text)
check("P9 a receipt longer than the page continues onto the next one and drops no line",
      len(_long_sizes) > 1 and "Item 0" in _long_all and "Item 119" in _long_all
      and all(txt.strip() for txt in _long_text))
check("P10 the LAYOUT is not decided here — it is the same `clean_layout` the mail body asks",
      "_sr.clean_layout(" in RPDF_SRC
      and code_only(RPDF_SRC).count("clean_layout") == 1
      and "layout_note" not in RPDF_SRC)
check("P11 nothing about CONTENT is decided here — no toggle, no figure, no line of its own",
      not re.search(r'template_v\[|show_[a-z_]+|\b(items|totals|subtotal|tax_total|total|paid|'
                    r'discount_total|head_lines|footer|blocks)\b', code_only(RPDF_SRC))
      and "slip_model" in RPDF_SRC and "document(" in RPDF_SRC
      # the sale's number, for the file's own name and title, is all it takes from the snapshot
      and code_only(RPDF_SRC).count('snap[') == 1 and 'transaction_id' in RPDF_SRC)
check("P12 one home for the printed name, so the file cannot say John S. where the mail says John Smith",
      "for_render(" in RPDF_SRC and code_only(SR_SRC).count("def for_render") == 1
      and not re.search(r'customer_name.*=.*customer_display', code_only(RPDF_SRC)))
check("P13 the attachment is named for the sale, so two receipts can be told apart",
      rpdf.filename(77) == "receipt-77.pdf" and rpdf.filename("") == "receipt.pdf"
      # A SLUGIFIER: a path separator is substituted, never dropped, so two different references
      # cannot collapse onto one name and the value cannot be mistaken for a match key (the §E
      # accent-fold class in harness_payroll_hours_basis_lock.py).
      and rpdf.filename("77/../etc") == "receipt-77____etc.pdf"
      and rpdf.filename("A-77") != rpdf.filename("A77"))
check("P14 the mail carries it, and ONLY a path that attaches one pays to build it",
      'want_pdf=True' in router and router.count("want_pdf=True") == 2
      and '"application/pdf"' in router
      and 'pdf_bytes, pdf_error = b"", ""' in router)
check("P15 a file that cannot be built does not cost the customer their receipt",
      re.search(r'except Exception as exc:\s*\n\s*pdf_bytes, pdf_error = b""', router)
      and "went in the body only" in router and '"attached":' in router)
check("P16 RULE TWO — no tenant, POS or printer make is named in the renderer",
      not re.search(r"(?i)\b(vzone|luxelink|cellfonz|boost|epson|citizen|star\s*tsp)\b",
                    code_only(RPDF_SRC)))
check("P17 §POS-08 is registered in the index with its one home",
      "POS-08" in idx and "receipt_pdf_render" in idx)

print("\n§Q  SENDING A RECEIPT — one home, two channels, every screen (§POS-09)")
# Owner 2026-10-21, verbatim: *"i need the emial option in sales log viewer and the send notification
# option via whats app also"*. The class: "send this receipt to the customer" lived inline in the
# till's completion modal, keyed to the sale just rung, so the Sale Log could only PRINT — and a
# second channel would have had to be built twice.
from app.modules.notify import whatsapp_window as wa_win  # noqa: E402

panel = read(PANEL_SEND)
REPRINT_MOUNT = "<SendReceiptPanel key={reprintSaleId} saleId={reprintSaleId}"
WA_META = read("backend/app/modules/notify/channels/whatsapp_meta.py")
CUSTOMER = {"email": "jane@example.com", "phone_primary": "(212) 555-0123"}


def wa_view(customer=None, typed=None, digits=wa_win.digits):
    return sr.whatsapp_view(SNAP, BRAND, ALL_ON, {}, customer=customer, typed=typed,
                            tenant_name="Example Wireless", digits=digits)


check("Q1 the two channels answer the SAME question in the same shape, from one home",
      set(wa_view(CUSTOMER)) == {"to", "to_source", "valid", "from_name", "caption"}
      and set(sr.email_view(SNAP, BRAND, ALL_ON, {}, customer=CUSTOMER))
      - {"subject", "body_declared"} == {"to", "to_source", "valid", "from_name"})
check("Q2 the typed number beats the customer's, and nothing is invented",
      wa_view(CUSTOMER, "+1 646 555 9999")["to"] == "16465559999"
      and wa_view(CUSTOMER, "+1 646 555 9999")["to_source"] == "typed"
      and wa_view(CUSTOMER)["to"] == "12125550123"
      and wa_view(CUSTOMER)["to_source"] == "customer"
      and wa_view({})["to"] == "" and wa_view({})["to_source"] == "none")
check("Q3 an unusable number is refused on the screen, before the provider ever sees it",
      wa_view(CUSTOMER)["valid"] and not wa_view({}, "555-0123")["valid"]
      and not wa_view({})["valid"])
check("Q4 what counts as a phone number is WhatsApp's own rule, DEREFERENCED — not a second copy",
      "from app.modules.notify import whatsapp_window" in WA_META
      and "_win.digits(raw)" in WA_META
      and not re.search(r'c\.isdigit\(\)', code_only(WA_META))
      and not re.search(r'isdigit\(\) for|\.isdigit\(\) if', code_only(SR_SRC))
      and wa_view({}, "2125550123")["to"] == wa_win.digits("2125550123"))
check("Q5 a receipt sent on WhatsApp announces itself exactly as the emailed one does",
      wa_view(CUSTOMER)["caption"]
      == sr.email_view(SNAP, BRAND, ALL_ON, {}, customer=CUSTOMER,
                       tenant_name="Example Wireless")["subject"])
check("Q6 the screen is told who it comes from on this channel too",
      wa_view(CUSTOMER)["from_name"] == sr.company_name(BRAND, "Example Wireless"))

check("Q7 ONE sender in the browser, and both screens mount it",
      panel.count("emailSaleReceipt(") == 1 and panel.count("whatsappSaleReceipt(") == 1
      and reg.count("<SendReceiptPanel") == 2
      and "emailSaleReceipt" not in reg)
check("Q8 the till keeps its own half (an address onto the customer's profile) and no more",
      "saveReceiptEmailToProfile" in reg and "onEmailSent={saveReceiptEmailToProfile}" in reg
      and reg.count("onEmailSent") == 1        # the till's one use of it; the prop lives in the panel
      and panel.count("onEmailSent?.(") == 1
      and "onEmailSent" not in reg.split(REPRINT_MOUNT)[1])
check("Q9 the Sale Log reprint can SEND, which is the ask — not only print",
      REPRINT_MOUNT in reg
      and "reprintChoice" in reg.split(REPRINT_MOUNT)[1][:200])
check("Q10 the copy sent is the copy that was handed over (the print choice rides with it)",
      "choice={printChoice}" in reg and "choice={reprintChoice}" in reg
      and "choiceParams(choice)" in read(CLIENT))

ROUTER_Q = router.split("def whatsapp_sale_receipt(")[1].split("@router.")[0]
check("Q11 the file it sends is the §POS-08 receipt, built once by the one renderer",
      "want_pdf=True" in ROUTER_Q and 'bundle["pdf_bytes"]' in ROUTER_Q
      and '"application/pdf"' in ROUTER_Q)
check("Q12 it never claims an attachment it does not have",
      'if not bundle["pdf_bytes"]:' in ROUTER_Q and "nothing to send" in ROUTER_Q)
check("Q13 WHEN a business may send a file is Meta's rule, and that ladder is not re-implemented",
      "send_document_detailed(" in ROUTER_Q
      and not re.search(r'httpx|plan_delivery|graph\.facebook', ROUTER_Q)
      and "window" not in code_only(ROUTER_Q))   # the docstring NAMES Meta's window; the code never
                                                 # measures it — that is whatsapp_meta's job
check("Q14 a refusal is a sentence, never a 500",
      # three 400s: an unrenderable receipt, no number at all, and a number that is not one
      ROUTER_Q.count("HTTPException(400") == 3 and "HTTPException(503" in ROUTER_Q
      and "HTTPException(502" in ROUTER_Q)
check("Q15 RULE TWO — no tenant, carrier or country is written into either side",
      not re.search(r"(?i)\b(vzone|luxelink|cellfonz|boost|whatsapp\s*business\s*api\s*key)\b",
                    code_only(panel))
      and not re.search(r'\+1\b', code_only(SR_SRC)))
check("Q16 §POS-09 is registered in the index with its one home",
      "POS-09" in idx and "SendReceiptPanel" in idx)
# One customer's address on another customer's receipt is the defect this shape prevents. The panel
# is MOUNTED per sale rather than copying a prop into state after the fact, so there is nothing to
# go stale: opening a different sale mounts a fresh panel with that sale's own answer.
check("Q17 a different sale is a different panel, so no address can be carried onto it",
      "useEffect" not in panel
      and panel.count("useState(view.email.to)") == 1
      and panel.count("useState(view.whatsapp.to)") == 1
      and reg.count("<SendReceiptPanel key={lastSaleId} saleId={lastSaleId}") == 1
      and reg.count(REPRINT_MOUNT) == 1)



def _slip_fits(template_v):
    """Does every word of the till slip land INSIDE the page it is drawn on? A thermal printer fits
    its whole character grid across the roll; the file was set at a fixed point size and the right
    hand column walked off the edge, so a customer's receipt read 'Subtotal 8' where the slip says
    'Subtotal 808.75'. Measured off the rendered file, never off the geometry that drew it."""
    data = rpdf.receipt_pdf(SNAP, template_v, brand=BRAND, store_name="STORE 1",
                            store_row=STORE_ROW, remit_block=REMIT_BLOCK)
    with pdfplumber.open(io.BytesIO(data)) as d:
        pg = d.pages[0]
        text = pg.extract_text() or ""
        return (max((w["x1"] for w in pg.extract_words()), default=0) <= pg.width + 0.5
                and f'{SNAP["total"]:,.2f}' in text and f'{SNAP["tax_total"]:,.2f}' in text)

print("\n§R  WHAT THE SHOP IS — one home, every layout (§POS-10)")
# Owner 2026-10-28, verbatim: *"see the rq receipt email to the customer, it does not show the
# address and looks fake , make sure all teh details are on the recipt like the original receipt
# from rq , the till thermal printer receipt came out ok"*.
#
# THE CLASS, not the instance. "What does a receipt say the shop IS?" had TWO answers: the till slip
# read the receipt-branding rows; the tenant's own POS layout read a `store_lines` list the router
# assembled for it alone out of `storeops.stores`. The second one selected a `name` column that
# table does not have, PostgREST answered 42703, the router swallowed it, and the emailed copy went
# out with the company name and nothing else. One tenant SAW it; every tenant on that layout had it.
from app.modules.pos import receipt_store_block as shop  # noqa: E402

SHOP_SRC = read("backend/app/modules/pos/receipt_store_block.py")
SHOP = shop.block(BRAND, STORE_ROW, tenant_name="Example Wireless Inc", store_code="STORE 1")

check("R1 the block is the branding an admin typed — name, address, licence — plus the store's phone",
      SHOP["name"] == "Example Wireless" and SHOP["address_lines"] == BRAND["address_lines"]
      and SHOP["license"] == "Lic 123456" and SHOP["phone"] == "(555) 010-0100")
check("R2 a shop that filled in no branding address falls back to its OWN row, one line, never blank "
      "— and the market column is NOT folded in (it is a market name, not reliably a state)",
      shop.block({"company_name": "C"}, STORE_ROW)["address_lines"] == ["1 Example St"]
      and shop.block({"company_name": "C"}, {"store_code": "S"})["address_lines"] == []
      and shop.block({}, {"address": "1 Test St Testville NY 10000",
                          "market": "NY"})["address_lines"] == ["1 Test St Testville NY 10000"])
check("R3 nothing is invented for a shop that declared neither",
      shop.block({}, None, store_code="STORE 1")
      == {"name": "STORE 1", "address_lines": [], "phone": "", "license": "",
          "lines": ["STORE 1"]})
check("R4 the columns that read the store row are the ones that table HAS, declared once",
      "name" not in shop.STORE_COLUMNS.split(",")
      and set(shop.STORE_COLUMNS.split(",")) <= set(STORE_ROW)
      and "_shop.STORE_COLUMNS" in router
      and not re.search(r'table\("stores"\)\.select\("[^"]*name', code_only(router)))

_slip_heads = [h["text"] for h in sr.slip_model(SNAP, ALL_ON, brand=BRAND, store_name="STORE 1",
                                                store_row=STORE_ROW)["head_lines"]]
_pos_doc = sr.document(SNAP, _rq, ALL_ON, brand=BRAND, store_row=STORE_ROW, store_name="STORE 1")
_pos_block = _pos_doc["store"]["lines"] + [_pos_doc["store"]["phone"]]
check("R5 THE REGRESSION — the tenant's own POS layout carries the address and the licence, not the "
      "company name alone (this is receipt 4605)",
      "1 Example St" in _pos_block and "Queens NY" in _pos_block and "Lic 123456" in _pos_block
      and len(_pos_doc["store"]["lines"]) > 1)
check("R6 the two layouts say the SAME thing about the shop — one fact, dereferenced twice",
      all(ln in _slip_heads for ln in SHOP["lines"])
      and all(ln in _pos_block for ln in SHOP["lines"]))
check("R7 a store read that FAILS costs the receipt nothing the admin filled in (the branding wins, "
      "the row is only the fallback)",
      sr.document(SNAP, _rq, ALL_ON, brand=BRAND, store_row=None)["store"]["lines"]
      == [BRAND["company_name"]] + BRAND["address_lines"] + [BRAND["license_tax_ids"]])
check("R8 the legal blocks reach the POS layout too — the return policy was on the slip and NOT on "
      "the emailed copy of the same sale",
      "Return policy" in sr.document(SNAP, _rq, ALL_ON, brand=BRAND, store_row=STORE_ROW,
                                     disclaimer_blocks=[{"text": "Return policy"}])["footer_text"])
check("R9 a block that asks for a signature gets its line, in this layout too",
      "Customer signature" in sr.document(SNAP, _rq, ALL_ON, disclaimer_blocks=[
          {"text": "I agree", "signature": True}])["footer_text"])
check("R10 a receipt with no blocks grows no footer of its own",
      sr.document(SNAP, _rq, ALL_ON)["footer_text"] == ALL_ON["footer_text"])
check("R11 LOCK — the store block is ASSEMBLED in one file and read everywhere else",
      len([f for f in ("backend/app/modules/pos/sale_receipt.py",
                       "backend/app/modules/pos/router.py",
                       "backend/app/modules/pos/receipt_pdf_render.py")
           if re.search(r"""brand(\.get\(|\[)\s*["'](address_lines|license_tax_ids)"""
                        r"|license_tax_ids", code_only(read(f)))]) == 0
      and "_shop.block(" in code_only(SR_SRC)
      and code_only(SHOP_SRC).count("address_lines") > 0)
check("R12 RULE TWO — the one home names no tenant, carrier or store, and branches on none",
      not re.search(r"(?i)\b(vzone|boost|verizon|luxelink|cellfonz|total\s|rq)\b",
                    code_only(SHOP_SRC)))
check("R13 the slip's PDF fits the roll it declares — every figure, not the first character of it",
      _slip_fits(ALL_ON) and _slip_fits(T58))
check("R14 §POS-10 is registered in the index with its one home",
      "POS-10" in idx and "receipt_store_block" in idx)

print("\n§J  negative controls")
check("neg-B a default that drifted on one side only is RED",
      ts_default("brand_company_name")[1] == pc.SERVER_DEFAULTS["brand_company_name"]
      and ts_default("receipt_customer_short_name")[1] == pc.SERVER_DEFAULTS["receipt_customer_short_name"])
check("neg-C a layout resolver that honoured an unknown value would be RED",
      sr.clean_layout("rq") != sr.LAYOUT_POS_FORMAT)
check("neg-E a company_name that fell back to a platform name would be RED",
      sr.company_name({}, "") == "" and "MetricsPro" not in read("backend/app/modules/pos/sale_receipt.py"))
check("neg-F a recipient that invented an address would be RED", sr.recipient({}, None) == "")
check("neg-G a renderer that recomputed the total would be RED",
      str(SNAP["total"]) in sr.thermal_html(SNAP, ALL_ON).replace(",", "")
      and round(sum(i["line_total"] for i in SNAP["items"]), 2) != SNAP["total"])
check("neg-H a register that still built its own html would be RED",
      "buildReceiptHtml" not in reg and "buildReceiptHtml" in reg.replace("printSaleReceipt", "buildReceiptHtml"))

check("neg-K a resolver that honoured any requested width would be RED",
      112 not in sr.PAPER_PX and sr.print_choice(T_ORG, want_paper="112")["paper_width_mm"] != 112)
check("neg-K2 a store override that leaked to every store would be RED",
      pc.printer([{"store_code": "S1", "key": "receipt_layout", "value": sr.LAYOUT_POS_FORMAT}],
                 "S2")["layout"] == "")

check("neg-L a byte stream that re-read the sale would be RED (it takes the model, and nothing else)",
      esc.payload(MODEL) == esc.payload(dict(MODEL)) and "snapshot_view" not in ESC_SRC)
check("neg-L2 a store that never opted in printing straight to the printer would be RED",
      sr.print_choice(T_ORG, store_direct=False)["destination"] == sr.DEST_DIALOG)

check("neg-M the two shapes really are different — this is the mix-up, not a style point",
      not hasattr(RQ_ROW, "COLUMNS") and hasattr(RQ_ROW["module"], "COLUMNS")
      and getattr(RQ_ROW["module"], "get", None) is None and callable(RQ_ROW.get))
check("neg-M2 a record that invented a label for an unregistered POS would be RED",
      declared_as(["a-pos-nobody-registered"])["label"] == ""
      and RQ_ROW["label"] not in str(declared_as(["a-pos-nobody-registered"])))

check("neg-N a bank number stored in the clear would be RED (the 2026-10-12 class)",
      not _crypto.is_enabled()
      or rmt.seal_setting_value(GOOD_ACCOUNT) != GOOD_ACCOUNT)
# This one FOUND A DEFECT in its own subject: `digits("••••••7890")` is "7890", a perfectly valid
# four-digit account number, so a mask handed back would have silently replaced the real account
# with its own last four and every receipt after it would have carried an unpayable number.
check("neg-N2 a masked value saved back as the real number would be RED",
      refuses_remit(rmt.clean_account, rmt.mask(GOOD_ACCOUNT))
      and refuses_remit(rmt.clean_routing, rmt.mask(GOOD_ROUTING))
      and rmt.digits(rmt.mask(GOOD_ACCOUNT)) == GOOD_ACCOUNT[-4:])
check("neg-O a renderer that printed the address with the customer hidden would be RED",
      "Brooklyn" in str(sr.slip_model(SNAP, ALL_ON, customer_lines=CUST_LINES)["head_lines"])
      and "Brooklyn" not in str(sr.slip_model(SNAP, off, customer_lines=CUST_LINES)["head_lines"]))
check("neg-O2 a home that invented a line for an empty record would be RED",
      bt.lines({"address_1": "", "city": None}) == []
      and bt.block("", None)["lines"] == [])
check("neg-O3 the lock really can fail — the same regex finds the two homes it allows",
      len([a for a in BT_ALLOWED if _assembly.search(code_only(read("backend/" + a)))]) == 2)
check("neg-Q a seven-digit number called sendable would be RED",
      not wa_view({}, "5550123")["valid"] and wa_view({}, "2125550123")["valid"])
check("neg-Q2 a view that normalised the number ITSELF would be RED (it is handed the rule)",
      wa_view({}, "(212) 555-0123", None)["to"] == "(212) 555-0123"
      and not wa_view({}, "(212) 555-0123", None)["valid"])
check("neg-P a file that re-decided the layout would be RED (it asks the one resolver)",
      rpdf.receipt_pdf(SNAP, T_POS)[:5] == b"%PDF-"
      and pdf_pages(rpdf.receipt_pdf(SNAP, T_POS))[0][0][0] == round(80 * rpdf.PT_PER_MM, 2))
check("neg-P2 a renderer that wrote the roll at letter width would be RED",
      SLIP_SIZES[0][0] < 300 and DOC_SIZES[0][0] == 612.0)
check("neg-P3 an empty document still yields a valid one-page file, never a crash",
      rpdf.words_pdf([])[:5] == b"%PDF-" and len(pdf_pages(rpdf.words_pdf([]))[0]) == 1)
check("neg-N3 a checksum that passed anything would be RED",
      sum(1 for n in range(10**8, 10**8 + 400)
          if rmt.routing_checksum_ok(str(n).zfill(9))) == 40)

print(f"\n{_pass} passed, {_fail} failed")
if _fail:
    sys.exit(1)
print("OK — one renderer for the till copy, the reprint, the preview and the emailed copy; the layout "
      "is a declared per-org choice with its fallback spoken aloud, pickable per print and per store; "
      "the From name is the tenant's own; the printer gets the SAME slip as the paper, only when a "
      "store asked for it; "
      "and the server's POS-setting defaults are pinned to the browser's.")
