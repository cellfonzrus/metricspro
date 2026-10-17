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

doc = sr.document(SNAP, _rq, ALL_ON, brand=BRAND, store_lines=["STORE 1", "1 Example St"],
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
check("H3 the till copy, the reprint and the auto-print all go through that one call",
      reg.count("printSaleReceipt(") >= 4)
check("H4 the emailed copy is sent through the one client home too", "emailSaleReceipt(" in reg)
check("H5 the register still SHOWS the address and lets it be edited",
      "Email the receipt to" in reg and "setEmailTo(" in reg)
check("H6 and says who it will come from", "sees it from" in reg)
editor = read(EDITOR)
check("H7 the admin screen no longer draws its own imitation of a receipt",
      "VERIZON" not in editor and "iPhone 15 Pro" not in editor)
check("H8 it previews the REAL renderer's output",
      "receipt-template/preview" in editor and "srcDoc={previewHtml}" in editor)
check("H9 the editor offers the layout choice and shows the From name",
      "Receipt layout" in editor and "Emailed receipts come from" in editor)
check("H10 and it says what to run when the layout column is not there yet",
      "migration 1089" in editor)
client = read(CLIENT)
check("H11 the client home spells the receipt URLs once each",
      client.count("/receipt`") == 1 and client.count("/email-receipt`") == 1)

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

print(f"\n{_pass} passed, {_fail} failed")
if _fail:
    sys.exit(1)
print("OK — one renderer for the till copy, the reprint, the preview and the emailed copy; the layout "
      "is a declared per-org choice with its fallback spoken aloud; the From name is the tenant's own; "
      "and the server's POS-setting defaults are pinned to the browser's.")
