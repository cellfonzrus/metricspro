"""DB-FREE PROOF — THE CUSTOMER MASTER (owner 2026-09-24; index §30.16).
    python3 harness_customer_master.py        (from backend/; no network, no DB)

OWNER, verbatim: *"now these customers which are uploaded should be available in the pos to make the sales in future
and if any data is later uploaded for the same customer the system to check for existing fields to match the data and
update their record rather than creating a new record, the matching will be based on customer name address and phone
numbers, - phone number and name to be checked first for all, if in the last 2 years data a customer came in twice to
get phones on different names they should be combined together and shows in the customer pages as separate line items
with sales from different dates which can be opened, edited or notes added, option to add notes on every single line of
sales data per phone number, each customer will bear separate lines with individual phone numbers in line with their
imei and plan shown on the customer page to give more details for that customer, with another tab showing how much they
paid for that invoice"*.

The REAL functions are driven over `harness_intake_fakes.FakeDB` (tables declared exactly as migrations 725 / 726 / 865 /
1017 define them; `drop_migration_1017()` is the live shape before 1017 is applied). The fixture is SYNTHETIC.

  §P THE PURE RULES — norm_phone (one rule: a device id is not a phone, < 10 digits is not a phone, else the CRM's
     national number — agreeing with it and with the inventory's mobile key on every clean number); tracking_kind;
     decide() for every rule: (a) phone + name, (b) phone within two years under another name → combined + alias,
     both sides of the window, older → reassigned → create, (c) the name alone / an alias / an address that rules out /
     picks / several alike, (d) create, a placeholder never, a merged-away customer ignored; fill_patch never overwrites;
     invoice_lines: the device paired per contract group, else per invoice, ambiguous never guessed, the plan per number
  §M THE ONE MATCHER over the fake client — update-not-create, fill empty fields only, the alias written, the OCR path
  §L THE LINES through the REAL rebuild — one pos.activations row per sale × number with IMEI + plan + date; the owner's
     two-year rule end to end (combined; a reassigned number is a new customer); a placeholder sale keeps no customer and
     no line; a re-run replaces nothing twice and never overwrites an edit
  §E THE ENDPOINTS — /customers/{id}/lines, /invoices (what the CUSTOMER paid — the vendor rebate is not), /aliases,
     the search (a full name, a phone line, an alias; merged hidden), merge + un-merge round trip with the real gate,
     the dedupe dry run → wrong count refused → confirmed → undone
  §B BEFORE MIGRATION 1017 — nothing crashes; combining still works on the lines; every answer says "apply migration 1017"
  §R THE DEFECT REPRODUCED — the same person under two names on one phone line used to become two customers
"""
import copy
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
os.environ.setdefault("SUPABASE_URL", "http://fake")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")

from harness_intake_fakes import FakeDB                                              # noqa: E402
from app.modules.pos import customer_identity as CID                                 # noqa: E402
from app.modules.pos import customer_master as CM                                    # noqa: E402
from app.modules.pos import receipt_import as RI                                     # noqa: E402
from app.modules.pos import sales_from_reports as SFR                                # noqa: E402
from app.modules.pos import router as PR                                             # noqa: E402
from app.modules.pos.receipt_formats import registry as REG                          # noqa: E402
from app.modules.commcalc import router as R                                         # noqa: E402
from app.modules.commcalc import inventory_sold_recon as ISR                         # noqa: E402
from app.modules.commcalc import device_cost_recon as DCR                            # noqa: E402
from app.modules.crm import pipeline_core as CRM                                     # noqa: E402
from app.modules.core import router as CORE                                          # noqa: E402

ORG = "c0ffee00-0000-4000-8000-00000000c0de"
_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}" + (f"  — {str(extra)[:500]}" if extra != "" else ""))


def section(t):
    print(f"\n── {t} ──")


def status_of(fn):
    try:
        fn()
    except PR.HTTPException as e:
        return e.status_code, e.detail
    return None, None


# ══ §P the pure rules ════════════════════════════════════════════════════════════════════════════════
section("§P the pure rules")
check("P1 norm_phone: one rule — formatting / a leading 1 / an extension at the end → the 10-digit national number",
      CID.norm_phone("(555) 123-4567") == "5551234567" and CID.norm_phone("+1 555 123 4567") == "5551234567"
      and CID.norm_phone("1-555-123-4567") == "5551234567" and CID.norm_phone("5165550134 x22") == "5165550134")
check("P2 norm_phone: fewer than 10 digits is not a phone; a 14–16-digit device id (IMEI) is not a phone",
      CID.norm_phone("555-1234") is None and CID.norm_phone("555123456") is None and CID.norm_phone("") is None
      and CID.norm_phone("356938035643809") is None and CID.norm_phone("35693803564380") is None and CID.norm_phone(None) is None)
_battery = ["5551234567", "15551234567", "(555) 123-4567", "+1 (555) 123-4567", "555.123.4567", "1 555 123 4567"]
check("P3 norm_phone DEREFERENCES the CRM's national-number rule (crm.pipeline_core.normalize_phone) and agrees with the inventory's "
      "mobile key on every clean number (the three normalisers no longer disagree where a customer is matched)",
      all(CID.norm_phone(v) == CRM.normalize_phone(v) == ISR.mobile_key(v) for v in _battery)
      and "_national(" in open(CID.__file__).read())
check("P4 tracking_kind: a line's tracking # is a phone number OR a device id — never both",
      CID.tracking_kind("5550001111") == ("phone", "5550001111") and CID.tracking_kind("356938035643809") == ("device", "356938035643809")
      and CID.tracking_kind("45813") == (None, None) and CID.tracking_kind("") == (None, None))

MARIA = {"id": "c-maria", "name": "Maria Lopez", "aliases": [], "address": "", "created_at": "2025-01-01", "phones": {"5550001111": "2025-03-10"}}
d = CID.decide({"name": "MARIA  LOPEZ", "phones": ["(555) 000-1111"], "date": "2026-01-15"}, [MARIA])
check("P5 (a) a phone line + the same name (normalised) → match, rule phone_and_name, no alias", d["action"] == "match" and d["customer_id"] == "c-maria"
      and d["rule"] == CID.RULE_PHONE_NAME and d["alias_to_add"] is None, d)
d = CID.decide({"name": "M Lopez", "phones": ["5550001111"], "date": "2026-01-15"}, [MARIA])
check("P6 (b) the same phone line under ANOTHER name within two years (after) → combined: match + the new name as an alias, said in words",
      d["action"] == "match" and d["rule"] == CID.RULE_PHONE_COMBINE and d["alias_to_add"] == "M Lopez" and "combined" in d["reason"], d)
d = CID.decide({"name": "M Lopez", "phones": ["5550001111"], "date": "2023-04-01"}, [MARIA])
check("P7 (b) the window holds on BOTH sides: an incoming sale 710 days BEFORE the line's last date is combined too",
      d["action"] == "match" and d["rule"] == CID.RULE_PHONE_COMBINE and CID.days_apart("2023-04-01", "2025-03-10") == 709, d)
d = CID.decide({"name": "Peter Pan", "phones": ["5550001111"], "date": "2027-03-12"}, [MARIA])
check("P8 (b) a line last seen MORE than two years before (731 days) under another name → the number was reassigned: a new customer, the number listed",
      d["action"] == "create" and d["reassigned"] == ["5550001111"] and "reassigned" in d["reason"] and CID.days_apart("2027-03-12", "2025-03-10") == 732, d)
d = CID.decide({"name": "Peter Pan", "phones": ["5550001111"], "date": "2027-03-10"}, [MARIA])
check("P8b the boundary: exactly 730 days apart is still within two years", d["action"] == "match" and d["rule"] == CID.RULE_PHONE_COMBINE, d)
d = CID.decide({"name": "Maria Lopez", "phones": ["5550001111"], "date": "2031-01-01"}, [MARIA])
check("P9 (a) needs no window: the same name on the same line years later is still her", d["action"] == "match" and d["rule"] == CID.RULE_PHONE_NAME, d)
d = CID.decide({"name": "maria lopez", "phones": [], "date": "2026-01-15"}, [MARIA])
check("P10 (c) no phone line: the full name on exactly one customer → match, rule name", d["action"] == "match" and d["rule"] == CID.RULE_NAME, d)
d = CID.decide({"name": "M Lopez", "phones": ["5559999999"], "date": "2026-01-15"}, [{**MARIA, "aliases": ["M LOPEZ"]}])
check("P11 (c) an also-known-as name matches like the name", d["action"] == "match" and d["customer_id"] == "c-maria", d)
TWIN_A = {"id": "c-a", "name": "John Smith", "address": "1 main st springfield", "created_at": "2024-01-01", "phones": {}}
TWIN_B = {"id": "c-b", "name": "John Smith", "address": "9 oak ave shelbyville", "created_at": "2024-06-01", "phones": {}}
d1 = CID.decide({"name": "John Smith", "phones": [], "address": "9 Oak Ave, Shelbyville"}, [TWIN_A, TWIN_B])
d2 = CID.decide({"name": "John Smith", "phones": [], "address": "77 Elm Rd"}, [TWIN_A, TWIN_B])
d3 = CID.decide({"name": "John Smith", "phones": []}, [TWIN_A, TWIN_B])
check("P12 (c) the address: one that agrees picks among same-name customers; one that differs from both rules them out (a new customer); "
      "none → the first created, said (merge on the customer page)",
      d1["customer_id"] == "c-b" and d1["rule"] == CID.RULE_NAME and d2["action"] == "create"
      and d3["customer_id"] == "c-a" and d3["rule"] == CID.RULE_NAME_AMBIGUOUS and "merge" in d3["reason"], (d1, d2, d3))
d = CID.decide({"name": "Zed Nobody", "phones": ["5552223333"], "date": "2026-01-01"}, [MARIA])
check("P13 (d) no phone line and no name in common → create", d["action"] == "create" and d["rule"] == CID.RULE_CREATE, d)
d = CID.decide({"name": "Walk In", "phones": ["5550001111"], "date": "2026-01-15"}, [MARIA])
check("P14 a placeholder bill-to never matches (not even on a phone line) and never creates", d["action"] == "none" and d["rule"] == CID.RULE_PLACEHOLDER, d)
d = CID.decide({"name": "Maria Lopez", "phones": ["5550001111"], "date": "2026-01-15"}, [{**MARIA, "merged_into": "c-other"}])
check("P15 a merged-away customer is never matched (its lines and names live on the survivor)", d["action"] == "create", d)
d = CID.decide({"name": "M Lopez", "phones": ["5550001111"], "date": None}, [MARIA])
check("P16 a shared line with no date on one side does not combine two different names (said)", d["action"] == "create" and "no date" in d["reason"], d)
row = {"phone_primary": "5550001111", "phone_secondary": "", "email": "maria@example.test", "address_1": "", "city": "", "zip": ""}
p = CID.fill_patch(row, {"phones": ["5550001111", "5550002222", "5550003333"], "email": "other@example.test", "address": "2 Sample Ave"})
check("P17 fill_patch writes EMPTY fields only: the secondary phone (a new number), the address — never the filled primary phone or e-mail",
      p == {"phone_secondary": "5550002222", "address_1": "2 Sample Ave"}, p)
check("P18 fill_patch on a full record writes nothing", CID.fill_patch({"phone_primary": "5550001111", "phone_secondary": "5550002222", "email": "x",
                                                                         "address_1": "a"}, {"phones": ["5559990000"], "email": "y", "address": "b"}) == {})

IMEI1, IMEI2, IMEI3 = "356938035643801", "356938035643802", "356938035643803"
MDN1, MDN2, MDN3 = "5550001111", "5550002222", "5550003333"
PLAN = lambda r: "plan" in (r.get("product_desc") or "").lower()                   # noqa: E731
inv = [{"trans_id": "I1", "serial_1": IMEI1, "contract_no": "C1", "product_desc": "PHONE A"},
       {"trans_id": "I1", "serial_1": MDN1, "contract_no": "C1", "product_desc": "Unlimited Plan"},
       {"trans_id": "I1", "serial_1": IMEI2, "contract_no": "C2", "product_desc": "PHONE B"},
       {"trans_id": "I1", "serial_1": MDN2, "contract_no": "C2", "product_desc": "Streaming Perk"},
       {"trans_id": "I1", "serial_1": "", "contract_no": "C2", "product_desc": "Unlimited Plan Plus"},
       {"trans_id": "I1", "serial_1": MDN3, "contract_no": "", "product_desc": "Case"},
       {"trans_id": "I1", "serial_1": "45813", "contract_no": "", "product_desc": "Order Number"}]
L = {x["mdn"]: x for x in CID.invoice_lines(inv, ISR.line_pairings, DCR.device_key, PLAN)}
check("P19 invoice_lines: one entry per phone number; the device paired through its CONTRACT group by the one pairing rule (line_pairings, 'same transaction')",
      sorted(L) == [MDN1, MDN2, MDN3] and L[MDN1]["imei"] == IMEI1 and L[MDN2]["imei"] == IMEI2 and L[MDN1]["pairing"] == ISR.PAIR_MOBILE_VIA_SALES
      and L[MDN1]["device_name"] == "PHONE A", L)
check("P20 a number whose group holds no device is paired over the whole invoice — two devices there → unpaired with the rule's own reason (never guessed)",
      L[MDN3]["imei"] is None and L[MDN3]["reason"] == ISR.UNPAIR_AMBIGUOUS, L[MDN3])
check("P21 the plan: the plan line tracked to the number (MDN1), else the only plan in its contract group (MDN2), else none — another number's plan is never borrowed (MDN3)",
      L[MDN1]["plan"] == "Unlimited Plan" and L[MDN2]["plan"] == "Unlimited Plan Plus" and L[MDN3]["plan"] == "", L)
L1 = CID.invoice_lines([r for r in inv if r["serial_1"] != IMEI2 and r["contract_no"] != "C2"] , ISR.line_pairings, DCR.device_key, PLAN)
check("P22 one device on the invoice: a number with no contract is paired to it over the invoice", {x["mdn"]: x["imei"] for x in L1} == {MDN1: IMEI1, MDN3: IMEI1}, L1)
check("P23 no plan words configured → no plan (nothing guessed); a line with a separate mdn column is a phone line too, paired on the SAME line",
      all(x["plan"] == "" for x in CID.invoice_lines(inv, ISR.line_pairings, DCR.device_key, None))
      and CID.invoice_lines([{"trans_id": "I9", "serial_1": IMEI3, "mdn": "(555) 000-4444"}], ISR.line_pairings, DCR.device_key)[0]["imei"] == IMEI3)

# ══ fixture for the I/O sections ══════════════════════════════════════════════════════════════════════
SOURCE = REG.sources()[0]
FMT = REG.get(SOURCE)["module"]
STORE = "Test Wireless Store 01"
ADMIN, CLERK = "tok-admin", "tok-clerk"


def use(db):
    R.sb = lambda: db
    PR.sb = lambda: db
    R._TABLE_COL_PRESENT.clear()
    RI._CUSTOMER_PRESENT.clear()
    RI._IDENTITY_CFG.clear()
    CM.reset_caches()
    return db


CORE._uid_from_token = lambda a: {ADMIN: "uid-admin", CLERK: "uid-clerk"}.get(a)


def period(dt):
    return {"period": dt[:7], "period_month": int(dt[5:7]), "period_year": int(dt[:4])}


def L_(inv, date, sku, name, tracking, total, contract="", cat=""):
    return {"trans_id": inv, "trans_date": date, "store": STORE, "salesperson": "Jane R", "sku": sku, "product_desc": name,
            "serial_1": tracking, "quantity": 1, "ext_price": total, "contract_no": contract, "voided": "No", "category": cat,
            "source": "sales", "org_id": ORG, "user_login": "jrep", **period(date)}


def INV(inv, date, customer, total):
    return {"trans_id": inv, "trans_date": date, "store": STORE, "salesperson": "Jane R", "tendered_by": "Jane R", "customer": customer,
            "subtotal": total, "net_sales": total, "invoice_total": total, "tax": 0.0, "extra_charges": 0, "donations": 0,
            "source": "sales_by_invoice", "org_id": ORG, "invoiced_by": STORE, "coupons": 0, **period(date)}


def T_(inv, date, label, cls, amount):
    return {"trans_id": inv, "trans_date": date, "store": STORE, "role": "tender", "tender_label": label, "tender_class": cls, "amount": amount,
            "source": "sales_by_invoice", "org_id": ORG, "salesperson": "Jane R", "keyed_manually": False, **period(date)}


def fresh_db(plan_words=True):
    db = FakeDB()
    db.seed("stores", [{"org_id": ORG, "store_code": "T-01", "address": "1 Test St", "market": "NY", "is_active": True, "phone": "(555)123-4567"}])
    db.seed("store_aliases", [{"org_id": ORG, "alias": STORE, "store_code": "T-01"}])
    db.seed("employees", [{"org_id": ORG, "employee_id": "E1", "name": "Jane R", "epay_salesperson": "jrep", "is_active": True}])
    db.seed("pos_profile", [{"org_id": ORG, "pos_key": SOURCE, "is_active": True}])
    db.seed("app_users", [{"id": "au-1", "org_id": ORG, "auth_id": "uid-admin", "role": "Owner", "super_admin": False, "employee_id": "E1"},
                          {"id": "au-2", "org_id": ORG, "auth_id": "uid-clerk", "role": "Clerk", "super_admin": False, "employee_id": "E2"}])
    db.seed("roles", [{"org_id": ORG, "name": "Owner", "permissions": {"scope": "all"}},
                      {"org_id": ORG, "name": "Clerk", "permissions": {"scope": "store"}}])
    if plan_words:     # the org's confirmed plan words (plan_sources `sales_lines`) — config, never code
        db.seed("pos_settings", [{"org_id": ORG, "store_code": None, "key": "plan_sources",
                                  "value": {"sources": {"sales_lines": {"enabled": True, "include": ["plan"], "exclude": ["perk"]}}}}])
    db.seed("raw_sales_invoice", [
        INV("INV-0", "2022-01-05", "OLD OWNER", 50.0),
        INV("INV-1", "2025-03-10", "MARIA LOPEZ", 1000.0),
        INV("INV-4", "2025-05-01", "Walk In", 20.0),
        INV("INV-5", "2025-06-01", "maria  lopez", 30.0),
        INV("INV-2", "2026-01-15", "M LOPEZ", 600.0)])
    db.seed("raw_sales_invoice_tender", [
        T_("INV-0", "2022-01-05", "Cash", "cash", 50.0),
        T_("INV-1", "2025-03-10", "Cash", "cash", 1000.0), T_("INV-1", "2025-03-10", "Ven Reb Act", "vendor_rebate", 200.0),
        T_("INV-4", "2025-05-01", "Cash", "cash", 20.0),
        T_("INV-5", "2025-06-01", "Store Card", "credit", 30.0),
        T_("INV-2", "2026-01-15", "Cash", "cash", 400.0), T_("INV-2", "2026-01-15", "Store Card", "credit", 200.0)])
    db.seed("raw_sales", [
        L_("INV-0", "2022-01-05", "RP-0", "Basic Plan", MDN2, 50.0),
        L_("INV-1", "2025-03-10", "HS-1", "PHONE A", IMEI1, 500.0, "C1"),
        L_("INV-1", "2025-03-10", "RP-1", "Unlimited Plan", MDN1, 0.0, "C1"),
        L_("INV-1", "2025-03-10", "HS-2", "PHONE B", IMEI2, 500.0, "C2"),
        L_("INV-1", "2025-03-10", "PK-1", "Streaming Perk", MDN2, 0.0, "C2"),
        L_("INV-1", "2025-03-10", "RP-2", "Unlimited Plan Plus", "", 0.0, "C2"),
        L_("INV-4", "2025-05-01", "AC-1", "Charger", MDN1, 20.0),
        L_("INV-5", "2025-06-01", "AC-2", "Case", "", 30.0),
        L_("INV-2", "2026-01-15", "HS-3", "PHONE C", IMEI3, 600.0, "C3"),
        L_("INV-2", "2026-01-15", "UP-1", "Upgrade Fee", MDN1, 0.0, "C3")])
    return db


def cust_named(db, full):
    return [c for c in db.tables.get("customers") or [] if CID.norm_name(CID.display_name(c)) == CID.norm_name(full)]


# ══ §M the one matcher ═════════════════════════════════════════════════════════════════════════════
section("§M the one matcher over the fake client")
mdb = use(FakeDB())
mdb.seed("customers", [{"org_id": ORG, "first_name": "Maria", "last_name": "Lopez", "phone_primary": None, "email": "maria@example.test",
                        "is_active": True, "created_at": "2025-01-01T00:00:00Z"}])
mid = mdb.tables["customers"][0]["id"]
mdb.seed("activations", [{"org_id": ORG, "customer_id": mid, "cell_number": MDN1, "activation_date": "2025-03-10", "status": "active"}])
r1 = RI.match_or_create(mdb, ORG, {"customer_name": "MARIA LOPEZ", "phones": [MDN1, MDN2], "sale_date": "2025-09-01", "email": "new@example.test"})
m_row = mdb.tables["customers"][0]
check("M1 a later upload for the same customer UPDATES her record instead of creating one: matched on the phone line + name; the empty "
      "primary phone filled with the first number, the other number into the empty secondary; the filled e-mail is NOT overwritten",
      r1["customer_id"] == mid and not r1["created"] and len(mdb.tables["customers"]) == 1 and m_row["phone_primary"] == MDN1
      and m_row["phone_secondary"] == MDN2 and m_row["email"] == "maria@example.test", (r1, m_row))
r2 = RI.match_or_create(mdb, ORG, {"customer_name": "M. Lopez", "phones": [MDN1], "sale_date": "2025-12-01"})
check("M2 the same line under another name within two years → combined into her, 'M. Lopez' recorded as an also-known-as name (mig 1017)",
      r2["customer_id"] == mid and r2["decision"]["rule"] == CID.RULE_PHONE_COMBINE and len(mdb.tables["customers"]) == 1
      and [(a["customer_id"], a["alias_name"], a["alias_norm"], a["source"]) for a in mdb.tables["customer_aliases"]] == [(mid, "M. Lopez", "m lopez", "upload")], r2)
r3 = RI.match_or_create(mdb, ORG, {"customer_name": "M Lopez", "phone": "(555) 999-0000"})
check("M3 the OCR / scanned path uses the SAME matcher: a formatted phone on the receipt, the alias name → her (rule name via the alias)",
      r3["customer_id"] == mid and r3["decision"]["rule"] == CID.RULE_NAME, r3)
r4 = RI.match_or_create(mdb, ORG, {"customer_name": "Walk In", "phones": [MDN1]})
check("M4 a placeholder bill-to: no customer, nothing created", r4["customer_id"] is None and len(mdb.tables["customers"]) == 1, r4)
check("M5 find_customer (the read half, every caller's shape) answers the matched row", (RI.find_customer(mdb, ORG, {"customer_name": "maria lopez"}) or {}).get("id") == mid)

# ══ §L the lines through the REAL rebuild ═════════════════════════════════════════════════════════════
section("§L the lines through the REAL rebuild (sales_from_reports.rebuild → upsert_structured → the matcher → sync_invoice_lines)")
db = use(fresh_db())
out = SFR.rebuild(db, ORG, "2022-01-01", "2026-01-31", who="E1")
custs = db.tables["customers"]
maria = cust_named(db, "MARIA LOPEZ")
old = cust_named(db, "OLD OWNER")
acts = db.tables.get("activations") or []
imps = {r["invoice_no"]: r for r in db.tables["receipt_imports"]}
sales = {s["id"]: s for s in db.tables["sales"]}
check("L1 the rebuild ran: 5 invoices, 5 POS sales; 2 customers — OLD OWNER and MARIA LOPEZ (the placeholder made none; 'maria  lopez' and "
      "'M LOPEZ' are her)", out["ran"] and out["created"] == 5 and len(custs) == 2 and len(maria) == 1 and len(old) == 1,
      [(CID.display_name(c)) for c in custs])
maria, old = maria[0], old[0]
maria["created_at"], old["created_at"] = "2025-03-10T12:00:00Z", "2022-01-05T12:00:00Z"     # the DB's created_at default (the fake sets none)
check("L2 THE TWO-YEAR RULE end to end: MDN2 was OLD OWNER's line in Jan 2022; MARIA carried it in Mar 2025 (> 2 years) — the number was "
      "reassigned, so she is a NEW customer, not combined into OLD OWNER",
      sales[imps["INV-1"]["sale_id"]]["customer_id"] == maria["id"] and sales[imps["INV-0"]["sale_id"]]["customer_id"] == old["id"])
check("L3 'M LOPEZ' (Jan 2026) on MARIA's line MDN1 (Mar 2025) → combined into MARIA; 'M LOPEZ' is her alias; 'maria  lopez' (no line) matched on the name",
      sales[imps["INV-2"]["sale_id"]]["customer_id"] == maria["id"] and sales[imps["INV-5"]["sale_id"]]["customer_id"] == maria["id"]
      and [a["alias_name"] for a in db.tables["customer_aliases"] if a["customer_id"] == maria["id"]] == ["M LOPEZ"])
check("L4 the 'Walk In' sale keeps NO customer and records no line (its lines stay in the landed report for reporting)",
      not sales[imps["INV-4"]["sale_id"]].get("customer_id") and not any(a["sale_id"] == imps["INV-4"]["sale_id"] for a in acts))
by = {(a["sale_id"], a["cell_number"]): a for a in acts}
a1 = by.get((imps["INV-1"]["sale_id"], MDN1)) or {}
a2 = by.get((imps["INV-1"]["sale_id"], MDN2)) or {}
a3 = by.get((imps["INV-2"]["sale_id"], MDN1)) or {}
check("L5 one pos.activations row per sale × phone number (4): each with its IMEI (paired per contract), its plan (the org's plan words), the "
      "invoice date, the store, the customer, status active, number mirrored into mobile_phone",
      len(acts) == 4 and a1.get("phone_serial") == IMEI1 and a1.get("plan_description") == "Unlimited Plan" and a1.get("phone_model") == "PHONE A"
      and a2.get("phone_serial") == IMEI2 and a2.get("plan_description") == "Unlimited Plan Plus" and a3.get("phone_serial") == IMEI3
      and a1.get("activation_date") == "2025-03-10" and a3.get("activation_date") == "2026-01-15" and a1.get("store_code") == "T-01"
      and a1.get("customer_id") == maria["id"] and a3.get("customer_id") == maria["id"] and a1.get("status") == "active"
      and a1.get("mobile_phone") == MDN1 and "INV-1" in (a1.get("memo") or ""), acts)
check("L6 MARIA was created with her first number as the primary phone and the second as the secondary (empty fields filled, nothing overwritten)",
      maria.get("phone_primary") == MDN1 and maria.get("phone_secondary") == MDN2, maria)
check("L7 the rebuild says what happened to the customers, in words (created / combined / matched on the name / placeholder / lines recorded)",
      out["customers"]["rules"] == {CID.RULE_CREATE: 2, CID.RULE_PHONE_COMBINE: 1, CID.RULE_NAME: 1, CID.RULE_PLACEHOLDER: 1}
      and any("combined with the customer already on that phone line" in w for w in out["words"])
      and any("4 phone line(s) recorded on customers" in w for w in out["words"]), out["words"])
# an edit on the customer page, then a re-run
PR.update_activation(a1["id"], {"plan_description": "Unlimited Plan (edited)"}, authorization=ADMIN, org_id=ORG)
out2 = SFR.rebuild(db, ORG, "2022-01-01", "2026-01-31", who="E1")
acts2 = db.tables["activations"]
check("L8 a re-run REPLACES the sales and never duplicates a line (still 4 rows, 2 customers, the sales keep their customer) and never "
      "overwrites an edit made on the customer page",
      out2["replaced"] == 5 and len(acts2) == 4 and len(db.tables["customers"]) == 2 and out2["customers"]["lines_written"] == 0
      and next(a for a in acts2 if a["id"] == a1["id"])["plan_description"] == "Unlimited Plan (edited)"
      and out2["customers"]["rules"] == {"kept": 4, CID.RULE_PLACEHOLDER: 1}, (out2["customers"], acts2))
ndb = use(fresh_db(plan_words=False))
SFR.rebuild(ndb, ORG, "2022-01-01", "2026-01-31", who="E1")
check("L9 no plan words confirmed for the org → the lines carry no plan (nothing guessed); the devices are still paired",
      all(not a.get("plan_description") for a in ndb.tables["activations"]) and any(a.get("phone_serial") == IMEI1 for a in ndb.tables["activations"]))
db = use(db)

# ══ §E the endpoints ═══════════════════════════════════════════════════════════════════════════════
section("§E the endpoints (the customer page)")
lp = PR.customer_lines(maria["id"], org_id=ORG, authorization=ADMIN)
lines = {ln["mdn"]: ln for ln in lp["lines"]}
check("E1 GET /customers/{id}/lines: one entry per phone number (MDN1, MDN2) with its latest IMEI, every IMEI seen, its plan, first / last date",
      sorted(lines) == [MDN1, MDN2] and lines[MDN1]["imei"] == IMEI3 and lines[MDN1]["imeis"] == [IMEI3, IMEI1]
      and lines[MDN1]["first_date"] == "2025-03-10" and lines[MDN1]["last_date"] == "2026-01-15" and lines[MDN2]["plan"] == "Unlimited Plan Plus", lp)
s1 = lines[MDN1]["sales"]
check("E2 each line's dated sales, newest first, each openable: the activation id (edit / notes), the invoice #, the receipt import id to print, "
      "the sale total and what the receipt prints for that number's lines",
      [s["invoice_no"] for s in s1] == ["INV-2", "INV-1"] and s1[0]["receipt_import_id"] == imps["INV-2"]["id"] and s1[0]["sale_total"] == 600.0
      and s1[1]["activation_id"] == a1["id"] and s1[1]["line_amount"] == 500.0 and s1[1]["plan"] == "Unlimited Plan (edited)", s1)
PR.add_activation_note(a1["id"], {"note": "customer asked about the upgrade", "severity": "important"}, authorization=ADMIN, org_id=ORG)
lp2 = PR.customer_lines(maria["id"], org_id=ORG, authorization=ADMIN)
check("E3 a note on ONE dated line of sales data (the existing /activations/{id}/notes) shows on that line only",
      next(s for ln in lp2["lines"] for s in ln["sales"] if s["activation_id"] == a1["id"])["notes"] == 1
      and sum(s["notes"] for ln in lp2["lines"] for s in ln["sales"]) == 1
      and PR.list_activation_notes(a1["id"], org_id=ORG)["notes"][0]["note"] == "customer asked about the upgrade")
iv = PR.customer_invoices(maria["id"], org_id=ORG, authorization=ADMIN)
ivs = {r["invoice_no"]: r for r in iv["invoices"]}
check("E4 GET /customers/{id}/invoices: per invoice what the CUSTOMER paid — INV-1 paid 1,000.00 (Cash; the 200.00 vendor rebate is NOT the "
      "customer's), INV-2 600.00 over two tenders, INV-5 30.00 — with the total, the balance and the receipt to print",
      sorted(ivs) == ["INV-1", "INV-2", "INV-5"] and ivs["INV-1"]["paid"] == 1000.0 and ivs["INV-1"]["payments"] == [{"label": "Cash", "amount": 1000.0}]
      and ivs["INV-2"]["paid"] == 600.0 and len(ivs["INV-2"]["payments"]) == 2 and ivs["INV-5"]["paid"] == 30.0 and ivs["INV-1"]["balance"] == 0.0
      and ivs["INV-2"]["receipt_import_id"] == imps["INV-2"]["id"] and iv["words"] == ["3 invoice(s); the customer paid 1,630.00 of 1,630.00"], iv)
al = PR.customer_aliases(maria["id"], org_id=ORG, authorization=ADMIN)
check("E5 GET /customers/{id}/aliases: 'M LOPEZ' (combined on the shared line), ready", al["ready"] and [a["alias_name"] for a in al["aliases"]] == ["M LOPEZ"], al)
f = lambda q, **kw: [CID.display_name(c) for c in PR.list_customers(search=q, org_id=ORG, authorization=ADMIN, **kw)["customers"]]   # noqa: E731
check("E6 the POS customer search finds a FULL name ('Maria Lopez' — it found nobody before), a phone number on her LINES (MDN1), "
      "an also-known-as name ('M Lopez'), a single word ('lopez'); a stranger finds nothing",
      f("Maria Lopez") == ["MARIA LOPEZ"] and f(MDN1) == ["MARIA LOPEZ"] and f("555-000-1111") == ["MARIA LOPEZ"] and f("M Lopez") == ["MARIA LOPEZ"]
      and f("lopez") == ["MARIA LOPEZ"] and f("Nobody Here") == [] and len(f("")) == 2)
code, _ = status_of(lambda: PR.customer_merge(old["id"], {"into": maria["id"]}, authorization=CLERK, org_id=ORG))
check("E7 merge is gated (pos_customers_merge; a store-scope role without it → 403)", code == 403, code)
mr = PR.customer_merge(old["id"], {"into": maria["id"], "reason": "same person"}, authorization=ADMIN, org_id=ORG)
old_row = next(c for c in db.tables["customers"] if c["id"] == old["id"])
check("E8 POST /customers/{id}/merge: OLD OWNER's sale, receipt and line move to MARIA; OLD OWNER kept inactive with merged_into and the move "
      "list; 'OLD OWNER' becomes her alias; a note on MARIA records it",
      mr["ok"] and mr["moved"] == {"sales": 1, "receipt_imports": 1, "activations": 1} and old_row["merged_into"] == maria["id"]
      and old_row["is_active"] is False and old_row["merge_record"]["moved"]["sales"] == [imps["INV-0"]["sale_id"]]
      and sales[imps["INV-0"]["sale_id"]]["customer_id"] == maria["id"]
      and sorted(a["alias_name"] for a in PR.customer_aliases(maria["id"], org_id=ORG, authorization=ADMIN)["aliases"]) == ["M LOPEZ", "OLD OWNER"]
      and any("Merged customer" in n["note"] for n in db.tables["customer_notes"] if n["customer_id"] == maria["id"]), (mr, old_row))
check("E9 a merged-away customer is hidden from the search and the list (even 'show inactive'); the survivor's lines now carry MDN2 twice-dated",
      f("OLD OWNER") == ["MARIA LOPEZ"] and "OLD OWNER" not in f("", active_only=False)
      and len({ln["mdn"]: ln for ln in PR.customer_lines(maria["id"], org_id=ORG, authorization=ADMIN)["lines"]}[MDN2]["sales"]) == 2)
code, _ = status_of(lambda: PR.customer_merge(maria["id"], {"into": old["id"]}, authorization=ADMIN, org_id=ORG))
check("E10 merging INTO a merged-away customer is refused (400, said)", code == 400, code)
um = PR.customer_unmerge(old["id"], {"reason": "not the same person"}, authorization=ADMIN, org_id=ORG)
old_row = next(c for c in db.tables["customers"] if c["id"] == old["id"])
check("E11 POST /customers/{id}/unmerge: exactly the moved rows go back, the alias the merge added is removed, the customer active again, "
      "merged_into cleared; a note on both",
      um["ok"] and sales[imps["INV-0"]["sale_id"]]["customer_id"] == old["id"] and old_row["merged_into"] is None and old_row["is_active"] is True
      and [a["alias_name"] for a in PR.customer_aliases(maria["id"], org_id=ORG, authorization=ADMIN)["aliases"]] == ["M LOPEZ"]
      and next(a for a in db.tables["activations"] if a["sale_id"] == imps["INV-0"]["sale_id"])["customer_id"] == old["id"]
      and sum(1 for n in db.tables["customer_notes"] if "Un-merged" in n["note"]) == 2, um)
code, _ = status_of(lambda: PR.customer_unmerge(old["id"], {}, authorization=ADMIN, org_id=ORG))
check("E12 un-merging a customer that is not merged → 400, said", code == 400, code)
# the one-time cleanup: a duplicate made by hand (the POS create endpoint), a placeholder customer left from before the matcher
dup = PR.create_customer({"first_name": "Maria", "last_name": "Lopez"}, org_id=ORG)["customer"]
next(c for c in db.tables["customers"] if c["id"] == dup["id"])["created_at"] = "2026-09-24T09:00:00Z"
db.seed("customers", [{"org_id": ORG, "first_name": "Walk", "last_name": "In", "is_active": True, "created_at": "2025-01-02T00:00:00Z"}])
walk = db.tables["customers"][-1]
db.tables["sales"].append({"id": "sale-walk", "org_id": ORG, "customer_id": walk["id"], "total": 5.0, "status": "completed"})
plan = PR.customers_dedupe({"dry_run": True}, authorization=ADMIN, org_id=ORG)
check("E13 POST /customers/dedupe (dry run): plans 1 merge (the hand-made 'Maria Lopez' into the first MARIA LOPEZ) + 1 placeholder detach "
      "('Walk In'); count 2; writes nothing; says how to apply",
      plan["dry_run"] and plan["count"] == 2 and plan["merges"][0]["from"] == dup["id"] and plan["merges"][0]["into"] == maria["id"]
      and plan["detach"][0]["id"] == walk["id"] and any("confirm_count = 2" in w for w in plan["words"])
      and next(c for c in db.tables["customers"] if c["id"] == dup["id"]).get("merged_into") is None, plan)
code, detail = status_of(lambda: PR.customers_dedupe({"dry_run": False, "confirm_count": 5}, authorization=ADMIN, org_id=ORG))
check("E14 a confirm_count that is not the planned count is refused (409, the fresh plan in words); nothing written",
      code == 409 and "the plan now holds 2" in detail and next(c for c in db.tables["customers"] if c["id"] == dup["id"]).get("merged_into") is None, detail)
ap = PR.customers_dedupe({"dry_run": False, "confirm_count": 2}, authorization=ADMIN, org_id=ORG)
check("E15 confirmed with the planned count: the duplicate merged (hidden from the search), the placeholder's sale detached and the record deactivated",
      ap["applied"] and ap["done"] == 2 and next(c for c in db.tables["customers"] if c["id"] == dup["id"])["merged_into"] == maria["id"]
      and next(s for s in db.tables["sales"] if s["id"] == "sale-walk")["customer_id"] is None and walk["is_active"] is False
      and f("Maria Lopez") == ["MARIA LOPEZ"], ap)
PR.customer_unmerge(walk["id"], {}, authorization=ADMIN, org_id=ORG)
check("E16 each cleanup change can be undone: un-merge on the placeholder puts its sale back", next(s for s in db.tables["sales"] if s["id"] == "sale-walk")["customer_id"] == walk["id"])
check("E17 a second dry run after the cleanup plans only what is left (the placeholder was restored → 1)",
      PR.customers_dedupe({}, authorization=ADMIN, org_id=ORG)["count"] == 1)
code, _ = status_of(lambda: PR.customer_lines("no-such-customer", org_id=ORG, authorization=ADMIN))
check("E18 an unknown customer (or another company's) → 404; every read is org-scoped", code == 404)

# ══ §B before migration 1017 ═══════════════════════════════════════════════════════════════════════
section("§B before migration 1017 is applied (the live shape today)")
bdb = use(fresh_db().drop_migration_1017())
try:
    bout = SFR.rebuild(bdb, ORG, "2022-01-01", "2026-01-31", who="E1")
    crashed = None
except Exception as e:     # noqa: BLE001
    bout, crashed = {}, e
bm = cust_named(bdb, "MARIA LOPEZ")
check("B1 the rebuild does not crash; combining still works on the lines (M LOPEZ → MARIA), the alias is not recorded and the words SAY "
      "'apply migration 1017'",
      crashed is None and len(bdb.tables["customers"]) == 2 and len(bm) == 1
      and any("apply migration 1017" in w for w in bout.get("words", [])), (crashed, bout.get("words")))
check("B2 schema probe: aliases / merged_into / merge_record reported missing",
      CM.identity_schema(bdb, ORG)["missing"] == ["customer_aliases", "merged_into", "merge_record"])
code, detail = status_of(lambda: PR.customer_merge(bm[0]["id"], {"into": cust_named(bdb, "OLD OWNER")[0]["id"]}, authorization=ADMIN, org_id=ORG))
_before = [(s["id"], s.get("customer_id")) for s in bdb.tables["sales"]]
check("B3 merge refuses (409) and says 'apply migration 1017' — nothing moved", code == 409 and "apply migration 1017" in detail
      and _before == [(s["id"], s.get("customer_id")) for s in bdb.tables["sales"]], detail)
bal = PR.customer_aliases(bm[0]["id"], org_id=ORG, authorization=ADMIN)
bpl = PR.customers_dedupe({}, authorization=ADMIN, org_id=ORG)
check("B4 the aliases endpoint answers (not ready, the words), the list and search work, the lines and invoices answer; the dedupe dry run "
      "answers and says apply 1017",
      bal["ready"] is False and bal["aliases"] == [] and any("apply migration 1017" in w for w in bal["words"])
      and [CID.display_name(c) for c in PR.list_customers(search="Maria Lopez", org_id=ORG, authorization=ADMIN)["customers"]] == ["MARIA LOPEZ"]
      and len(PR.customer_lines(bm[0]["id"], org_id=ORG, authorization=ADMIN)["lines"]) == 2
      and any("apply migration 1017" in w for w in bpl["words"]))
code, detail = status_of(lambda: PR.customers_dedupe({"dry_run": False, "confirm_count": bpl["count"]}, authorization=ADMIN, org_id=ORG))
check("B5 the dedupe apply refuses before 1017 (409, says why)", code == 409 and "apply migration 1017" in detail, detail)

# ══ §R the defect reproduced ═══════════════════════════════════════════════════════════════════════
section("§R THE DEFECT REPRODUCED → CLOSED")
rdb = use(fresh_db())
_real = RI._candidates


def _name_only(client, org_id, inc):        # the matcher as it was: the name only — no phone line, no alias
    return _real(client, org_id, {**inc, "phones": []})


RI._candidates = _name_only
SFR.rebuild(rdb, ORG, "2025-03-01", "2026-01-31", who="E1")
before = sorted(CID.display_name(c) for c in rdb.tables["customers"])
RI._candidates = _real
rdb2 = use(fresh_db())
SFR.rebuild(rdb2, ORG, "2025-03-01", "2026-01-31", who="E1")
after = sorted(CID.display_name(c) for c in rdb2.tables["customers"])
check("R1 the same person under two names on one phone line within two years: the name-only matcher made TWO customers "
      "('M LOPEZ', 'MARIA LOPEZ'); the customer master makes ONE, with the other name kept",
      before == ["M LOPEZ", "MARIA LOPEZ"] and after == ["MARIA LOPEZ"]
      and [a["alias_name"] for a in rdb2.tables["customer_aliases"]] == ["M LOPEZ"], (before, after))
_owners = lambda d: sorted({a["customer_id"] for a in d.tables["activations"] if a["cell_number"] == MDN1})   # noqa: E731
check("R2 … so the phone line MDN1 was split across two customer pages; now ONE customer carries both dated sales on it",
      len(_owners(rdb)) == 2 and len(_owners(rdb2)) == 1 and len(rdb2.tables["activations"]) == 3, (_owners(rdb), _owners(rdb2)))

print(f"\n══ The customer master: {_pass} passed, {_fail} failed ══")
if _fail:
    sys.exit(1)
print("OK — one matcher (phone + name first, two years to combine), one line per phone number with its device and plan, what the customer "
      "paid per invoice, reversible merges; before migration 1017 nothing crashes and every answer says so.")
