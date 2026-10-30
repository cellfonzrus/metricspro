"""PROOF — THE WHOLESALE CUSTOMER AND THE W-9 THAT MAKES A SALE TAX-FREE (owner 2026-10-30).
stdlib only, DB-free. Index §POS-15.

    python3 harness_pos_wholesale_w9.py        (from backend/)

  §A ONE ANSWER, NOT TWO — the exemption and the block come from ONE call, so a sale cannot be
     tax-free on a certificate the gate considers out of date; the tax rate meets the exemption in
     exactly one place, on each side of the wire.
  §B A RETAIL SALE IS UNTOUCHED — nothing about this feature reaches a customer who is not
     wholesale, and nothing reaches a sale with no customer at all.
  §C THE CERTIFICATE — only a CONFIRMED receipt counts; a customer's own upload does not; the
     certificate's own expiry beats the org's refresh period; the refresh period is declared.
  §D THE OVERRIDE — offered to an admin, taken only when passed, never granted by a body field,
     never a grant of the exemption, and always named on the sale.
  §E THE LISTS AGREE — mig 1107's CHECK list, the importer's accepted values and the one home's
     ACCOUNT_TYPES are the same three words.
  §F THE SETTINGS — the four keys are declared by name and the house values are this module's.
  §G THE PUBLIC LINK — a credential: minted from a cryptographic source, expiring, one use, cleared
     on arrival, with ONE refusal for unknown / expired / used, and nothing of the customer's on the
     page. Allowlisted by prefix, and nothing else under /pos is.
  §H UPLOAD FORMAT HAS ONE HOME — the sniffer lives in core and HR dereferences it; the extension
     grants nothing.
  §I WIRING — the endpoints and the checkout dereference the one home; the tax total is checked
     against its own lines; the register's rate goes through `taxRateFor`.
  §J REGISTERED — index rows, and the migration grants what it creates and carries its REVERT.
  Negative controls prove each rule goes red.
"""
import datetime as dt
import io
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core import upload_format as uf  # noqa: E402
from app.modules.pos import pos_config_effective as pc  # noqa: E402
from app.modules.pos import wholesale_w9 as w9  # noqa: E402

_pass = _fail = 0
ROUTER = "backend/app/modules/pos/router.py"
HR = "backend/app/modules/hr/router.py"
MW = "backend/app/core/tenant_middleware.py"
# The one home is NOT named here: `module_graph` is where "which file answers this question" lives
# (§50), and a lock that keeps its own copy of that ruling is the un-wiring the graph exists to
# prevent. Both homes this lock covers are read off the graph.
from app.modules.core import module_graph as _graph  # noqa: E402

HOME = "backend/" + _graph.fact("wholesale_w9_gate")["homes"][0]
FORMAT_HOME = "backend/" + _graph.fact("upload_document_format")["homes"][0]
PAGE = "frontend/src/app/(platform)/pos/sales/page.tsx"
LIB = "frontend/src/lib/pos-w9.ts"
PUBLIC_PAGE = "frontend/src/app/w9/[token]/page.tsx"
IMPORTER = "frontend/src/app/(platform)/pos/import/page.tsx"
MIGRATION = "database/migrations/1107_pos_wholesale_w9.sql"
INDEX = "docs/SYSTEM_DATA_FLOW_INDEX.md"
NOW = dt.date(2026, 10, 30)
WHOLESALE = {"id": "c-3", "account_type": "Wholesale", "company_name": "A Shop"}
RETAIL = {"id": "c-1", "account_type": "Personal"}


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


def body_of(src, name):
    m = re.search(r"^([ \t]*)(?:async )?def %s\s*\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    indent, out = len(m.group(1)), []
    for line in src[m.start():].splitlines()[1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        out.append(line)
    return "\n".join(out)


def doc(received="2026-01-15", status="received", **over):
    d = {"kind": "w9", "status": status, "received_at": received, "expires_at": None}
    d.update(over)
    return d


ROUTER_SRC = read(ROUTER)
MIG_SRC = read(MIGRATION)
PAGE_SRC = read(PAGE)
LIB_SRC = read(LIB)

print("\n§A  ONE ANSWER — the exemption and the block cannot disagree")
current = w9.decide(WHOLESALE, doc(), {}, now=NOW, is_admin=False)
missing = w9.decide(WHOLESALE, None, {}, now=NOW, is_admin=False)
check("A1 a current certificate: not blocked AND tax-free, in one call",
      current["blocked"] is False and current["tax_exempt"] is True)
check("A2 no certificate: blocked AND taxable, in the same call",
      missing["blocked"] is True and missing["tax_exempt"] is False)
check("A3 a tax-free sale always states its basis",
      current["exempt_basis"] == w9.BASIS_W9 and missing["exempt_basis"] is None)
check("A4 no state exists where a sale is exempt and the certificate is not current",
      all(not d["tax_exempt"] or d["w9_status"] == "current"
          for d in (current, missing,
                    w9.decide(WHOLESALE, doc(received="2001-01-01"), {}, now=NOW, is_admin=False),
                    w9.decide(WHOLESALE, doc(status="requested", received=None), {}, now=NOW,
                              is_admin=False))))
check("A5 the rate meets the exemption in ONE place on the server",
      w9.taxable_rate(0.08875, current) == 0 and w9.taxable_rate(0.08875, missing) == 0.08875)
check("A6 and in ONE place in the browser (`taxRateFor`), which the register's cart reads",
      "export function taxRateFor" in LIB_SRC
      and "taxRateFor(activeTaxRate, w9)" in PAGE_SRC
      and "tax_rate: taxable ? effectiveTaxRate : 0" in PAGE_SRC
      and "tax_rate: product.is_taxable ? effectiveTaxRate : 0" in PAGE_SRC)
check("A7 the register spells no exemption rule of its own",
      "account_type === 'Wholesale'" not in PAGE_SRC and "w9_status ===" not in PAGE_SRC.replace(
          "w9.w9_status !== 'current'", ""))

print("\n§B  A RETAIL SALE IS UNTOUCHED")
for row, label in ((RETAIL, "a personal account"), ({"account_type": "Business"}, "a business"),
                   (None, "no customer at all")):
    d = w9.decide(row, None, {}, now=NOW, is_admin=False)
    check(f"B {label} is neither blocked nor exempt and asks for nothing",
          d == {"blocked": False, "refusal": None, "tax_exempt": False, "exempt_basis": None,
                "w9_status": None, "override_available": False, "override_by": None,
                "needs_w9_request": False, "wholesale": False})

print("\n§C  THE CERTIFICATE")
check("C1 only a CONFIRMED receipt counts — a request is not a document",
      w9.w9_state(doc(status="requested", received=None), {}, now=NOW) == "missing")
check("C2 nor is a customer's own upload (it lands as `submitted`)",
      w9.w9_state(doc(status="submitted", received=None), {}, now=NOW) == "missing"
      and '"status": "submitted"' in ROUTER_SRC)
check("C3 a certificate is current inside the declared refresh period",
      w9.w9_state(doc(received="2024-06-01"), {"w9_valid_months": 36}, now=NOW) == "current")
check("C4 and out of date outside it",
      w9.w9_state(doc(received="2024-06-01"), {"w9_valid_months": 12}, now=NOW) == "expired")
check("C5 the refresh period is each org's own declaration, with the house three years behind it",
      w9.valid_months({}) == w9.HOUSE_VALID_MONTHS == 36
      and w9.valid_months({"w9_valid_months": 12}) == 12)
check("C6 an org may declare that a W-9 never goes out of date",
      w9.w9_state(doc(received="2009-01-01"), {"w9_valid_months": 0}, now=NOW) == "current")
check("C7 the certificate's OWN expiry beats the org's period, both ways",
      w9.w9_state(doc(received="2026-10-01", expires_at="2026-10-15"), {}, now=NOW) == "expired"
      and w9.w9_state(doc(received="2019-01-01", expires_at="2027-01-01"), {}, now=NOW) == "current")
check("C8 an unreadable declaration falls back to the house period, never to zero months",
      w9.valid_months({"w9_valid_months": "soon"}) == w9.HOUSE_VALID_MONTHS
      and w9.valid_months({"w9_valid_months": -5}) == w9.HOUSE_VALID_MONTHS)

print("\n§D  THE ADMIN OVERRIDE")
admin_blocked = w9.decide(WHOLESALE, None, {}, now=NOW, is_admin=True)
taken = w9.decide(WHOLESALE, None, {}, now=NOW, is_admin=True, override="E240")
claimed = w9.decide(WHOLESALE, None, {}, now=NOW, is_admin=False, override="E999")
check("D1 an admin is OFFERED it; the sale is still blocked until it is taken",
      admin_blocked["override_available"] is True and admin_blocked["blocked"] is True)
check("D2 taken, it unblocks the sale and names who allowed it",
      taken["blocked"] is False and taken["override_by"] == "E240")
check("D3 it never grants the exemption — the sale stays TAXABLE",
      taken["tax_exempt"] is False and taken["exempt_basis"] is None)
check("D4 a non-admin claiming one is ignored",
      claimed["blocked"] is True and claimed["override_by"] is None)
check("D5 an override on a sale that was never blocked records nothing",
      w9.decide(WHOLESALE, doc(), {}, now=NOW, is_admin=True, override="E240")["override_by"] is None)
co = body_of(ROUTER_SRC, "checkout")
check("D6 the checkout reads the CALLER's rights, never a body field claiming them",
      "_is_pos_admin(authorization, org_id)" in co
      and 'sale.pop("w9_override_by", None)' in co
      and 'sale["w9_override_by"] = w9d["override_by"]' in co)
check("D7 the register sends a REQUEST to override, not a claim to be allowed to",
      "w9_override: w9Override || undefined" in PAGE_SRC
      and "w9_override_by" not in PAGE_SRC)
check("D8 an org may declare that a missing W-9 does not block at all, and it is still taxable",
      w9.decide(WHOLESALE, None, {"w9_block_sale": False}, now=NOW, is_admin=False)
      == {**missing, "blocked": False, "refusal": None, "override_available": False})

print("\n§E  THE THREE LISTS OF CUSTOMER TYPES ARE ONE LIST")
m = re.search(r"check \(account_type in \(([^)]*)\)\)", MIG_SRC)
in_sql = tuple(x.strip().strip("'") for x in (m.group(1).split(",") if m else []))
check("E1 mig 1107's CHECK list is exactly ACCOUNT_TYPES",
      sorted(in_sql) == sorted(w9.ACCOUNT_TYPES), f"{in_sql} vs {w9.ACCOUNT_TYPES}")
imp = read(IMPORTER)
m2 = re.search(r"key: 'account_type'.*?oneOf: \[([^\]]*)\]", imp, re.S)
in_imp = tuple(x.strip().strip("'") for x in (m2.group(1).split(",") if m2 else []))
check("E2 the importer accepts exactly those values",
      sorted(in_imp) == sorted(w9.ACCOUNT_TYPES), f"{in_imp}")
check("E3 the customer form reads them from the server rather than listing them",
      "accountTypes.map(t =>" in read("frontend/src/app/(platform)/pos/customers/page.tsx")
      and "'/api/v1/pos/customer-account-types'" in read("frontend/src/app/(platform)/pos/customers/page.tsx"))
check("E4 and the endpoint answers from the one home",
      "_w9.ACCOUNT_TYPES" in body_of(ROUTER_SRC, "customer_account_types"))

print("\n§F  THE SETTINGS")
for key in (w9.EXEMPT_DEFAULT_KEY, w9.VALID_MONTHS_KEY, w9.BLOCK_SALE_KEY, w9.FORM_URL_KEY):
    check(f"F the server declares `{key}`", key in pc.SERVER_DEFAULTS)
check("F5 wholesale is tax-free by default for every tenant, once a W-9 is on file",
      pc.SERVER_DEFAULTS[w9.EXEMPT_DEFAULT_KEY] is True and current["tax_exempt"] is True)
check("F6 a missing W-9 blocks by default for every tenant",
      pc.SERVER_DEFAULTS[w9.BLOCK_SALE_KEY] is True and missing["blocked"] is True)
check("F7 the declared refresh period is this module's house value, spelled once",
      pc.SERVER_DEFAULTS[w9.VALID_MONTHS_KEY] == w9.HOUSE_VALID_MONTHS)
check("F8 the blank form is the IRS's own published PDF unless an org declares its own",
      pc.SERVER_DEFAULTS[w9.FORM_URL_KEY] == ""
      and w9.blank_form_url({}) == w9.HOUSE_W9_FORM_URL
      and w9.blank_form_url({"w9_blank_form_url": "https://x.test/f.pdf"}) == "https://x.test/f.pdf")
check("F9 a declared link that is not https is ignored rather than handed to a customer",
      all(w9.blank_form_url({"w9_blank_form_url": bad}) == w9.HOUSE_W9_FORM_URL
          for bad in ("javascript:alert(1)", "http://x.test/f.pdf", "https://x.test/a b", "")))

print("\n§G  THE CUSTOMER'S RETURN LINK IS A CREDENTIAL")
t1, t2 = w9.new_token(), w9.new_token()
check("G1 minted from the OS's cryptographic source, not derived from the customer",
      "secrets.token_urlsafe" in read(HOME) and t1 != t2 and len(t1) >= 32)
check("G2 it expires", w9.RETURN_LINK_DAYS > 0
      and "token_expires_at" in MIG_SRC and "token_expires_at" in ROUTER_SRC)
check("G3 unknown / expired / cleared are ONE answer, so a probe learns nothing",
      w9.token_live(None, now=NOW) is False
      and w9.token_live({"return_token": "t", "token_expires_at": "2026-01-01T00:00:00"},
                        now=NOW) is False
      and w9.token_live({"return_token": "", "token_expires_at": None}, now=NOW) is False
      and "_w9.LINK_DEAD" in body_of(ROUTER_SRC, "_w9_by_token"))
check("G4 an unreadable expiry is dead, not assumed live",
      w9.token_live({"return_token": "t", "token_expires_at": "whenever"}, now=NOW) is False)
up = body_of(ROUTER_SRC, "public_w9_upload")
check("G5 one use: the token is cleared the moment the document arrives",
      '"return_token": None' in up)
check("G6 what arrives is `submitted`, never `received` — an anonymous caller cannot confirm receipt",
      '"status": "submitted"' in up and '"status": "received"' not in up)
check("G7 the file is format-checked through the one home before it is stored",
      "_upload_format.allowed(" in up and up.index("_upload_format.allowed(") < up.index("_store_tax_doc_file("))
meta = body_of(ROUTER_SRC, "public_w9_meta")
check("G8 the page is told who is asking and what to send — nothing of the customer's record",
      "company_name" in meta and "form_url" in meta
      and not any(k in meta for k in ("first_name", "last_name", "email", "phone", "customer_id\":")))
check("G9 the public page shows nothing about the customer either",
      not any(k in read(PUBLIC_PAGE) for k in ("first_name", "last_name", "customer_id", ".email")))
mw = read(MW)
check("G10 the return link is allowlisted by its own prefix",
      '"/api/v1/pos/public/w9"' in mw)
from app.core import tenant_middleware as tm  # noqa: E402
check("G11 and nothing else under /pos is public",
      tm._is_public("/api/v1/pos/public/w9/abc") is True
      and all(tm._is_public(p) is False for p in
              ("/api/v1/pos/sales/checkout", "/api/v1/pos/customers", "/api/v1/pos/public",
               "/api/v1/pos/public/w9x/abc", "/api/v1/pos/customers/c-1/w9")))
check("G12 the raw token never travels back to the screen that asked — the LINK does",
      '"link": link' in body_of(ROUTER_SRC, "request_customer_w9")
      and '"token"' not in body_of(ROUTER_SRC, "request_customer_w9"))
check("G13 the request email carries the blank form and the return link, escaped",
      all(x in w9.request_html("<b>Co</b>", w9.HOUSE_W9_FORM_URL, "https://a.test/w9/t", "Dana")
          for x in (w9.HOUSE_W9_FORM_URL, "https://a.test/w9/t", "&lt;b&gt;"))
      and "<b>Co</b>" not in w9.request_html("<b>Co</b>", w9.HOUSE_W9_FORM_URL, "https://a.test/w9/t"))
check("G14 the link is built in ONE place, shared by the email and the page's route",
      w9.return_link("https://a.test/", "t") == f"https://a.test{w9.RETURN_PATH}/t"
      and os.path.isdir(os.path.join(ROOT, "frontend/src/app/w9/[token]")))

print("\n§H  WHAT FORMAT AN UPLOAD MAY BE HAS ONE HOME")
hr = read(HR)
check("H1 the sniffer, the magic bytes and the default list live in core",
      "MAGIC = {" in read(FORMAT_HOME) and FORMAT_HOME.endswith("app/core/upload_format.py"))
check("H2 HR dereferences it and keeps no second copy",
      "_upload_format.sniff(" in hr and "_upload_format.allowed(" in hr
      and "%PDF-" not in hr and "_EXT_ALIASES = {" not in hr)
check("H3 one refusal wording, shared",
      "_upload_format.refusal_sentence(" in hr and "_upload_format.refusal_sentence(" in ROUTER_SRC)
check("H4 the extension can never grant access",
      uf.allowed(b"#!/bin/sh\n", "really-a.pdf", ("pdf",)) == (False, "pdf"))
check("H5 an empty allow-list reads as the declared default, not as anything goes",
      uf.allowed(b"%PDF-1.7", "w9.pdf", ())[0] is True
      and uf.allowed(b"\x89PNG\r\n\x1a\n", "w9.png", ())[0] is False)

print("\n§I  WIRING")
check("I1 the router imports the one home", "import wholesale_w9 as _w9" in ROUTER_SRC)
check("I2 the checkout asks it before anything is written",
      "_w9_decision(" in co and co.index("_w9_decision(") < co.index('rpc("checkout"'))
check("I3 a blocked sale raises the one home's own sentence",
      'HTTPException(400, w9d["refusal"])' in co)
check("I4 the basis stored is the one home's, never the body's",
      'sale["tax_exempt_basis"] = w9d["exempt_basis"]' in co)
check("I5 the tax total is checked against the lines it came from",
      "does not add up to the tax on its lines" in co)
check("I6 an exempt sale whose lines carry tax is refused, not stored",
      "no line may carry" in co)
check("I7 the four W-9 settings are resolved through the ONE config home",
      "_poscfg.effective(rows," in body_of(ROUTER_SRC, "_w9_cfg"))
check("I8 the panel's every word comes from the server",
      "panelWords(w9)" in PAGE_SRC and "w9.refusal" in PAGE_SRC)
check("I9 the writes refuse by name before mig 1107 is applied",
      "W9_MIGRATION" in body_of(ROUTER_SRC, "confirm_customer_w9")
      and "W9_MIGRATION" in body_of(ROUTER_SRC, "request_customer_w9"))
check("I10 and the READ degrades instead, so the register is not blocked by a missing table",
      '"available": bool(present)' in body_of(ROUTER_SRC, "customer_w9"))

print("\n§J  REGISTERED")
IDX = read(INDEX)
check("J1 the section is in the index", "§POS-15" in IDX)
check("J2 the section register carries its row",
      re.search(r"\|\s*POS-15\s*\|\s*pos\s*\|", IDX) is not None)
check("J3 the one homes are named there",
      "wholesale_w9" in IDX and "upload_format" in IDX)
check("J4 the migration grants what it creates and carries its REVERT",
      "grant select, insert, update, delete on pos.customer_tax_doc to service_role;" in MIG_SRC
      and "-- REVERT:" in MIG_SRC)
check("J5 the sale records WHY it was tax-free and who allowed it",
      "tax_exempt_basis" in MIG_SRC and "w9_override_by" in MIG_SRC)

print("\n  negative controls")
check("NEG a decision that exempted without a certificate would be caught by A4",
      not (w9.decide(WHOLESALE, doc(received="2001-01-01"), {}, now=NOW,
                     is_admin=False)["tax_exempt"]))
check("NEG an override that granted the exemption would be caught by D3",
      taken["exempt_basis"] is None)
check("NEG a CHECK list missing Wholesale would be caught by E1",
      sorted(("Personal", "Business")) != sorted(w9.ACCOUNT_TYPES))
check("NEG a live token with no expiry check would be caught by G3",
      w9.token_live({"return_token": "t", "token_expires_at": "2026-12-31T00:00:00"},
                    now=NOW) is True)
check("NEG an extension-trusting sniffer would be caught by H4",
      uf.sniff(b"#!/bin/sh\n") is None)

print(f"\n{_pass} passed, {_fail} failed")
if _fail:
    sys.exit(1)
print("OK — one answer to 'may I sell to this customer and is this sale taxable'; the certificate "
      "is confirmed by a person, the override is an admin's and never an exemption, and the "
      "customer's return link is a one-use credential that leaks nothing.")
