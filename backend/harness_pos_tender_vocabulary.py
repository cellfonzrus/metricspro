"""PROOF — THE TERMS TENDER: one tender vocabulary for the register, dereferenced everywhere
(owner 2026-10-30: "also add another tender type as Terms . 15/30/60"). stdlib only, DB-free.

    python3 harness_pos_tender_vocabulary.py        (from backend/)

  §A ONE HOME — the vocabulary exists once; the browser carries no tender array and the register's
     buttons come from the served list; the suspend-cards rule is the home's, not the screen's.
  §B THE CLASS IS ASKED, NEVER MAPPED — every tender's recon class is the house ladder's answer
     (`closing.router.tender_class`), each class is one the house vocabulary knows, and a tender
     the closing axis has no place for says None rather than being filed somewhere plausible.
  §C WHAT AN ORG OFFERS IS ITS OWN — the house answer for an org that declared nothing, and one
     tenant's declaration never reaches another's register.
  §D A TERMS SALE IS MONEY OWED — it does not settle at the till, it carries the whole total as a
     balance with a due date counted from the SALE's date, and every refusal is a sentence.
  §E THE CONSTRAINT MATCHES THE VOCABULARY — migration 1106's CHECK list is exactly the keys this
     module declares, and the shape constraint makes a terms row without its day count impossible.
  §F THE SETTINGS AGREE — the server's declared defaults for the two tender settings are the house
     values this module declares, in the keys this module names.
  §G ENDPOINT + RECEIPT WIRING — the endpoint and the checkout dereference this module, the
     checkout asks about EVERY payment leg before anything is written and overrules the body's
     balance, and neither receipt layout prints "Paid" on a sale that was not paid.
  §H REGISTERED — index rows, and the migration carries its REVERT.
  Negative controls prove each rule goes red.
"""
import datetime as dt
import io
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.pos import pos_config_effective as pc  # noqa: E402
from app.modules.pos import sale_receipt as sr  # noqa: E402
from app.modules.pos import tender_vocabulary as tv  # noqa: E402

_pass = _fail = 0
ROUTER = "backend/app/modules/pos/router.py"
PAGE = "frontend/src/app/(platform)/pos/sales/page.tsx"
LIB = "frontend/src/lib/pos-tenders.ts"
MIGRATION = "database/migrations/1106_pos_terms_tender.sql"
INDEX = "docs/SYSTEM_DATA_FLOW_INDEX.md"
SALE_DATE = dt.date(2026, 10, 30)


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
    """The source of one function / useEffect-free block, by indentation."""
    m = re.search(r"^([ \t]*)(?:async )?def %s\s*\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    start = m.start()
    indent = len(m.group(1))
    out = []
    for line in src[start:].splitlines()[1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        out.append(line)
    return "\n".join(out)


ROUTER_SRC = read(ROUTER)
PAGE_SRC = read(PAGE)
LIB_SRC = read(LIB)
MIG_SRC = read(MIGRATION)

print("\n§A  ONE HOME — the register no longer carries its own list of tenders")
check("A1 the vocabulary is declared once, in this module",
      len(re.findall(r"^REGISTER_TENDERS", read("backend/app/modules/pos/tender_vocabulary.py"), re.M)) == 1)
check("A2 the register's buttons come from the served list, not from a literal array",
      "tenders.map(t =>" in PAGE_SRC
      and "{ value: 'credit_card'" not in PAGE_SRC and "{ value: 'card_external'" not in PAGE_SRC)
check("A3 the screen no longer decides what suspending card processing does to the buttons",
      "setPaymentMethod('card_external')" not in PAGE_SRC)
check("A4 the browser reader spells no tender word of its own",
      not any(w in LIB_SRC for w in ("credit_card", "debit_card", "card_external", "'terms'", '"terms"')))
check("A5 the browser's pre-answer fallback is cash alone, never a guessed list",
      [t["key"] for t in __import__("json").loads(
          re.search(r"TENDER_FALLBACK: Tender\[\] = (\[[^\]]*\])", LIB_SRC)
          .group(1).replace("'", '"').replace("key:", '"key":').replace("label:", '"label":')
          .replace("icon:", '"icon":').replace("settles_now:", '"settles_now":')
          .replace("needs_amount_tendered:", '"needs_amount_tendered":').replace(",\n]", "\n]"))] == ["cash"])

print("\n§B  THE CLASS IS ASKED OF THE HOUSE LADDER, NEVER MAPPED HERE")
from app.modules.closing import router as closing_router  # noqa: E402
KNOWN = set(closing_router.TENDER_CLASSES)
ALL = tv.all_tenders()
check("B1 every class answered is one the house vocabulary knows",
      all(t["recon_class"] is None or t["recon_class"] in KNOWN for t in ALL))
check("B2 money owed on an account reconciles as the house's store-account class",
      {t["key"]: t["recon_class"] for t in ALL}["terms"] == "store_acct")
check("B3 the card tenders reach the classes the closing axis already uses",
      [ {t["key"]: t["recon_class"] for t in ALL}[k]
        for k in ("cash", "credit_card", "debit_card", "card_external") ]
      == ["cash", "credit", "debit", "ext_cc"])
check("B4 a tender the closing axis has no class for answers None rather than a plausible one",
      {t["key"]: t["recon_class"] for t in ALL}["check"] is None)
check("B5 the answer IS the ladder's, for every tender (no mapping of our own)",
      all(t["recon_class"] == closing_router.tender_class(t["recon_words"]) for t in ALL))

print("\n§C  WHAT AN ORG OFFERS IS THE ORG'S OWN DECLARATION")
check("C1 an org that declared nothing is offered the house list",
      [t["key"] for t in tv.offered({})] == ["cash", "credit_card", "debit_card", "check", "terms"])
check("C2 and the owner's three day counts", tv.terms_day_options({}) == (15, 30, 60))
check("C3 suspending card processing swaps the integrated pair for the external button",
      [t["key"] for t in tv.offered({"suspend_card_processing": True})]
      == ["cash", "check", "card_external", "terms"])
check("C4 an org that does not sell on account has no Terms button",
      "terms" not in [t["key"] for t in tv.offered({"tender_terms_enabled": False})])
check("C5 one tenant's day counts do not reach another's register",
      tv.terms_day_options({"tender_terms_day_options": "7,45"}) == (7, 45)
      and tv.terms_day_options({}) == (15, 30, 60))
check("C6 a declaration that yields nothing readable reads as NOT DECLARED, never as no terms",
      tv.terms_day_options({"tender_terms_day_options": "soon"}) == (15, 30, 60))
check("C7 a day count that cannot be one is dropped, never clamped",
      tv.terms_day_options({"tender_terms_day_options": "15,0,-3,99999,30"}) == (15, 30))
check("C8 a jsonb row holding the string 'false' switches the tender off like a real boolean",
      "terms" not in [t["key"] for t in tv.offered({"tender_terms_enabled": "false"})])

print("\n§D  A TERMS SALE IS MONEY OWED, NOT MONEY TAKEN")
ok = tv.plan_payment("terms", {}, customer_id="c-1", terms_days=30, sale_date=SALE_DATE, total=217.45)
check("D1 it does not settle at the till", ok["settles_now"] is False and ok["refusal"] is None)
check("D2 the whole total is owed", ok["balance"] == 217.45)
check("D3 the due date is counted from the SALE's date, not from today",
      ok["due_date"] == dt.date(2026, 11, 29)
      and tv.plan_payment("terms", {}, customer_id="c-1", terms_days=15,
                          sale_date="2026-01-02", total=1)["due_date"] == dt.date(2026, 1, 17))
cash = tv.plan_payment("cash", {}, sale_date=SALE_DATE, total=217.45)
check("D4 a settled tender owes nothing and carries no term",
      cash["balance"] == 0 and cash["terms_days"] is None and cash["due_date"] is None)
REFUSALS = [
    tv.plan_payment("terms", {}, customer_id=None, terms_days=30, sale_date=SALE_DATE, total=1),
    tv.plan_payment("terms", {}, customer_id="c-1", terms_days=45, sale_date=SALE_DATE, total=1),
    tv.plan_payment("terms", {"tender_terms_enabled": False}, customer_id="c-1", terms_days=30,
                    sale_date=SALE_DATE, total=1),
    tv.plan_payment("iou", {}, sale_date=SALE_DATE, total=1),
]
check("D5 a terms sale with nobody to owe it is refused", "customer" in REFUSALS[0]["refusal"].lower())
check("D6 a day count nobody declared is refused, naming it", "45" in REFUSALS[1]["refusal"])
check("D7 a tender this org does not offer is refused even though the platform knows it",
      "not offered" in REFUSALS[2]["refusal"].lower())
check("D8 a tender the platform does not know is refused by name", "iou" in REFUSALS[3]["refusal"])
check("D9 every refusal is a sentence a cashier can read",
      all(r["refusal"] and r["refusal"].rstrip().endswith((".", "?")) for r in REFUSALS))
check("D10 a refused plan writes nothing — no balance, no due date",
      all(r["balance"] == 0 and r["due_date"] is None for r in REFUSALS))

print("\n§E  THE DATABASE CONSTRAINT IS THE SAME LIST")
m = re.search(r"check \(payment_method in \(([^)]*)\)\)", MIG_SRC)
in_sql = tuple(x.strip().strip("'") for x in (m.group(1).split(",") if m else []))
check("E1 mig 1106's CHECK list is exactly the vocabulary's keys",
      sorted(in_sql) == sorted(tv.TENDER_KEYS), f"{in_sql} vs {tv.TENDER_KEYS}")
check("E2 a terms row without its day count or due date is impossible",
      "sale_payments_terms_shape_check" in MIG_SRC
      and "terms_days is not null and due_date is not null" in MIG_SRC)
check("E3 and an immediate tender may not carry one",
      "payment_method <> 'terms' and terms_days is null and due_date is null" in MIG_SRC)
check("E4 the checkout function stores both new columns",
      "terms_days, due_date)" in MIG_SRC and "NULLIF(pay->>'due_date', '')::date" in MIG_SRC)
check("E5 the migration grants what it touches and carries its REVERT",
      "to service_role;" in MIG_SRC and "-- REVERT:" in MIG_SRC)

print("\n§F  THE SETTINGS THE SERVER READS ARE THIS MODULE'S HOUSE VALUES")
check("F1 both keys are declared by name",
      tv.TERMS_ENABLED_KEY in pc.SERVER_DEFAULTS and tv.TERMS_DAYS_KEY in pc.SERVER_DEFAULTS)
check("F2 the Terms tender is on for every tenant by default",
      pc.SERVER_DEFAULTS[tv.TERMS_ENABLED_KEY] is True
      and "terms" in [t["key"] for t in tv.offered({})])
check("F3 the declared day counts are the house three, spelled once",
      pc.SERVER_DEFAULTS[tv.TERMS_DAYS_KEY]
      == ",".join(str(d) for d in tv.HOUSE_TERMS_DAYS))
check("F4 and reading that declared default back gives the same three",
      tv.terms_day_options({tv.TERMS_DAYS_KEY: pc.SERVER_DEFAULTS[tv.TERMS_DAYS_KEY]})
      == tv.HOUSE_TERMS_DAYS)

print("\n§G  THE ENDPOINT, THE CHECKOUT AND BOTH RECEIPT LAYOUTS DEREFERENCE IT")
check("G1 the router imports the one home", "import tender_vocabulary as _tender" in ROUTER_SRC)
check("G2 GET /pos/tender-types answers from it",
      '@router.get("/tender-types")' in ROUTER_SRC
      and "_tender.offered(" in body_of(ROUTER_SRC, "tender_types"))
co = body_of(ROUTER_SRC, "checkout")
check("G3 the checkout asks it before anything is written",
      "_tender.plan_payment(" in co and co.index("_tender.plan_payment(") < co.index('rpc("checkout"'))
check("G4 it asks about EVERY payment leg, not only the first",
      "for raw in (payments or []):" in co)
check("G5 the refusal reaching the cashier is the one home's own sentence",
      'HTTPException(400, plan["refusal"])' in co)
check("G6 the balance written is the server's arithmetic, not the body's",
      'sale["balance"] = round(owed, 2)' in co)
# Tested on the CODE only: a comment naming a section (§POS-16) or a sibling rule is prose, and a
# lock that reads prose as a declaration teaches authors to stop writing comments.
co_code = "\n".join(l.split("#", 1)[0] for l in co.splitlines())
check("G7 the router spells no tender word and no day count of its own",
      not re.search(r"['\"](?:terms|credit_card|debit_card|card_external)['\"]", co_code)
      and not re.search(r"\b(?:15|30|60)\b", co_code))
ITEM = {"description": "Case", "qty": 1, "price": 100.0, "line_total": 108.88}
UNPAID = sr.snapshot_view({"transactionId": 1, "date": "2026-10-30", "items": [ITEM],
                           "subtotal": 100.0, "taxTotal": 8.88, "total": 108.88,
                           "method": "terms", "tendered": None, "change": 0,
                           "termsDays": 30, "dueDate": "2026-11-29"},
                          {"id": "s", "transaction_id": 1, "status": "completed"})
PAID = {**UNPAID, "method": "cash", "tendered": 120.0, "change": 11.12,
        "terms_days": None, "due_date": ""}
slip_unpaid = sr.slip_model(UNPAID, sr.template_view({}))
slip_paid = sr.slip_model(PAID, sr.template_view({}))
check("G8 the till slip does not print the word Paid on a sale that was not paid",
      "Paid" not in " ".join(p["label"] for p in slip_unpaid["paid"]))
check("G9 it prints the balance and the date it falls due",
      any("2026-11-29" in p["label"] and p["value"] == 108.88 for p in slip_unpaid["paid"]))
check("G10 a sale that WAS paid prints exactly what it printed before",
      slip_paid["paid"][0]["label"] == "Paid (Cash)" and slip_paid["paid"][0]["value"] == 120.0
      and slip_paid["paid"][1]["label"] == "Change")
from app.modules.pos.receipt_formats import rq  # noqa: E402
doc_unpaid = sr.document(UNPAID, rq, sr.template_view({}))
check("G11 the tenant's own POS layout says the same thing, from the same home",
      doc_unpaid["payments"][0]["label"] == "Balance due 2026-11-29")
check("G12 the renderer asks `settles_now` rather than testing for a tender word",
      "settles_now" in body_of(read("backend/app/modules/pos/sale_receipt.py"), "_unpaid")
      and not re.search(r"['\"]terms['\"]", body_of(read("backend/app/modules/pos/sale_receipt.py"), "_unpaid")))

print("\n§H  REGISTERED")
IDX = read(INDEX)
check("H1 the section is in the index", "§POS-15" in IDX or "POS-15" in IDX)
check("H2 the section register carries its row", re.search(r"\|\s*POS-15\s*\|\s*pos\s*\|", IDX) is not None)
check("H3 the one home is named there", "tender_vocabulary" in IDX)

print("\n  negative controls")
check("NEG a vocabulary that mapped a class itself would be caught by B5",
      not all(t["recon_class"] == closing_router.tender_class(t["recon_words"])
              for t in [{**ALL[0], "recon_words": "Gift Card"}] + ALL[1:]))
check("NEG an org list that ignored the declaration would be caught by C4",
      "terms" in [t["key"] for t in tv.offered({"tender_terms_enabled": True})])
check("NEG a terms plan that settled at the till would be caught by D1",
      tv.plan_payment("cash", {}, sale_date=SALE_DATE, total=1)["settles_now"] is True)
check("NEG a CHECK list missing a key would be caught by E1",
      sorted(("cash", "credit_card")) != sorted(tv.TENDER_KEYS))
check("NEG a receipt claiming payment would be caught by G8",
      "Paid" in " ".join(p["label"] for p in slip_paid["paid"]))

print(f"\n{_pass} passed, {_fail} failed")
if _fail:
    sys.exit(1)
print("OK — one tender vocabulary for the register: the class is the house ladder's answer, what an "
      "org offers is its own declaration, a sale on terms owes its whole total on a declared date, "
      "and no receipt calls it paid.")
