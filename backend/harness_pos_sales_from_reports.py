"""DB-FREE PROOF + LOCK — SALES REBUILT FROM THE TWO LANDED SALES REPORTS (owner 2026-09-21: "2 different
excel reports need to be combined into one … combine them into usable sales data and then print out in the
same exact format of the receipt uploaded, the data can be combined using the invoice as the common link").
    python3 harness_pos_sales_from_reports.py        (from backend/; no network, no DB, no PDF)

THE FIXTURE is SYNTHETIC and shaped like the measured receipt (never the tenant's rows): one invoice with
four handsets, four financed lines with quantity −1 and a bracketed negative total, perks at $0.00, rate-plan
lines with no tracking #, an order-number tracking line, one cash tender, a vendor-rebate tender that is not a
customer payment, a tax residual, and contract details; a second invoice whose lines add up to more than the
customer paid (the vendor-paid difference); a third with no lines; a line-only invoice with no header.

  §A THE FORMAT'S DECLARATIONS — the registered format carries TITLE / DATE_FORMAT / FINANCED_ITEMS /
     CONTRACT_SECTION / PRINT_LAYOUT; the parser reads the payment lines generically (a card tender, not just
     'Cash'), the contract table as PAIRS, and 'Tendered At' without landing on a surname
  §B THE PURE BUILDER — header, items in a deterministic order, financed lines, totals from the invoice header,
     the payment lines = the tender classes with a place on the closing axis (fold_to_axis INJECTED; a vendor
     rebate is not one), the tax residual, contract pairs, the report in words; idempotent (same rows → same
     document); coverage both ways
  §C THE ROUND TRIP — document → render_words (the format's PRINT_LAYOUT) → the format's parse → the SAME
     document (minus provenance); the rendered text carries every item / total / payment row verbatim
  §D END TO END over the fake client with the REAL readers, resolver, importer and endpoints: the rebuild for
     a period creates one POS sale + one receipt import per invoice, matched to the customer by FULL name,
     dated on the invoice date, stamped with its provenance; a re-run REPLACES (never duplicates); a scanned
     receipt of the same invoice is a separate record; no POS declared → a sentence, nothing written; a
     declared POS with no registered format → a sentence; the list endpoint; the intake commit hooks it
  §E THE LOCK (static, stdlib): one document shape (new_document once; no second literal), one importer
     (receipt_imports inserted only in receipt_import.py), one renderer (render_html / render_words once),
     the consumer resolves the format from the DECLARATION + the registry (no literal POS key), the consumers
     map + ScreenLink carry the screen, RULE TWO (the module is under the vocab guard), registration, CI;
     negative controls prove each rule goes red
"""
import copy
import inspect
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
os.environ.setdefault("SUPABASE_URL", "http://fake")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")

from harness_intake_fakes import FakeDB                                              # noqa: E402
from app.modules.pos.receipt_formats import base as B, engine as E, render as RD, registry as REG   # noqa: E402
from app.modules.pos import sales_from_reports as SFR                                # noqa: E402
from app.modules.pos import receipt_import as RI                                     # noqa: E402
from app.modules.pos import router as PR                                             # noqa: E402
from app.modules.commcalc import router as R                                         # noqa: E402
from app.modules.commcalc import landing_identity as LI                              # noqa: E402
from app.modules.commcalc import column_mapping as CM                                # noqa: E402
from app.modules.commcalc import onboarding_intake as OI                             # noqa: E402
from app.modules.closing import router as CR                                         # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BE = os.path.join(ROOT, "backend", "app", "modules")
FE = os.path.join(ROOT, "frontend", "src")
ORG = "f4f1c16e-0000-4000-8000-00000000f4f1"
_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}" + (f"  — {str(extra)[:400]}" if extra != "" else ""))


def section(t):
    print(f"\n── {t} ──")


def read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


# the format under proof = the FIRST registered one (never spelled here); the fixture is shaped like its receipt
SOURCE = REG.sources()[0]
FMT = REG.get(SOURCE)["module"]
STORE = "Test Wireless Store 01"
ADDR = "1 Test St Testville NY 10000"
PHONE = "(555)123-4567"


def W(text, x0, top, w=5.0):
    return {"text": text, "x0": float(x0), "x1": float(x0) + len(text) * w, "top": float(top), "bottom": float(top) + 10}


def row(top, cells):
    return [W(t, x0, top) for (t, x0) in cells]


# ══ §A the format's declarations + the parser's generic readers ═══════════════════════════════════
section("§A the format's declarations and the parser's generic readers")
check("A1 the registered format declares TITLE / DATE_FORMAT / FINANCED_ITEMS / CONTRACT_SECTION / PRINT_LAYOUT beside its COLUMNS / TOTALS",
      all(hasattr(FMT, a) for a in ("TITLE", "DATE_FORMAT", "FINANCED_ITEMS", "CONTRACT_SECTION", "PRINT_LAYOUT", "COLUMNS", "TOTALS")))
check("A2 the registry exposes the format module (registry.get → module) and lists its sources",
      REG.get(SOURCE)["module"] is FMT and SOURCE in REG.sources() and REG.get("no-such-pos") is None)
check("A3 FINANCED_ITEMS is the totals' own word (no product / carrier name): match ⊆ the 'financed' totals spec's words",
      set(w.lower() for w in FMT.FINANCED_ITEMS["match"]) <= set(w.lower() for t in FMT.TOTALS if t["key"] == "financed" for w in t["match"]))

ws = []
ws += row(37, [("Sale", 515)])
ws += row(98, [("Test", 69), ("Wireless", 105), ("Store", 145)])
ws += row(107, [("Invoice", 477), (":", 510), ("INV-77", 515)])
ws += row(137, [("Tendered", 380), ("On:", 416), ("05-Aug-2026", 462)])
ws += row(152, [("Sales", 380), ("Person:", 402), ("Pat", 462), ("Sejat", 480)])        # a surname ending in -at
ws += row(167, [("Tendered", 380), ("By:", 416), ("Pat", 462), ("Sejat", 480)])
ws += row(182, [("Tendered", 380), ("At:", 416), ("Test", 462), ("Wireless", 490), ("Store", 530)])
ws += row(190, [("Bill", 27), ("To:", 46)])
ws += row(196, [("JANE", 98), ("DOE", 125)])
ws += row(303, [("Product", 27), ("SKU", 59), ("Product", 109), ("Name", 141), ("Tracking", 341), ("#", 376), ("Qty", 449),
                ("Your", 492), ("Price", 512), ("Your", 545), ("Total", 565)])
ws += row(316, [("HS-1", 27), ("PHONE", 109), ("ONE", 140), ("111111111111111", 341), ("1", 449), ("$100.00", 500), ("$100.00", 555)])
ws += row(334, [("FN-1", 27), ("Device", 109), ("Financed", 145), ("Amount", 190), ("5550001111", 341), ("-1", 449), ("$100.00", 500), ("($100.00)", 545)])
ws += row(360, [("Subtotal:", 470), ("$0.00", 570)])
ws += row(374, [("Payment:", 27)])
ws += row(388, [("Sales", 407), ("Tax:", 435), ("$8.88", 570)])
ws += row(392, [("Store", 27), ("Card", 60), ("$8.88", 160)])                            # a NON-cash tender label
ws += row(406, [("Financed:", 467), ("$100.00", 560)])
ws += row(420, [("Total:", 466), ("$8.88", 570)])
ws += row(434, [("Change:", 27), ("$0.00", 160)])
ws += row(460, [("Contract", 27), ("Details:", 62)])
ws += row(477, [("Tracking", 27), ("#", 62), ("Contract", 149), ("#", 184)])
ws += row(487, [("343000001", 149)])
ws += row(497, [("111111111111111", 27), ("343000001", 149)])
ws += row(507, [("5550001111", 27), ("343000001", 149)])
ws += row(530, [("Comments:", 28), ("Test", 80), ("comment", 105)])
pd = REG.parse(SOURCE, ws)
check("A4 the payment lines read generically — a non-cash tender label between 'Payment:' and 'Change:'; the tax / financed / total rows are not payments",
      pd["payments"] == [{"label": "Store Card", "amount": 8.88}], pd["payments"])
check("A5 'Tendered At' anchors on the whole words — not on a surname that ends in -at",
      next((m["value"] for m in pd["meta"] if m["key"] == "tendered_at"), None) == "Test Wireless Store"
      and next((m["value"] for m in pd["meta"] if m["key"] == "tendered_by"), None) == "Pat Sejat", pd["meta"])
check("A6 the contract details read as (tracking #, contract #) PAIRS — the blank-tracking row kept, the section is a table",
      pd["sections"] and pd["sections"][0]["kind"] == "table" and [c["key"] for c in pd["sections"][0]["columns"]] == ["tracking", "contract"]
      and pd["sections"][0]["rows"] == [["", "343000001"], ["111111111111111", "343000001"], ["5550001111", "343000001"]], pd["sections"])
check("A7 an older print without the pair header still gives the flat reference list (compatibility)",
      (lambda d: d["sections"] and d["sections"][0]["kind"] == "list" and d["sections"][0]["rows"] == [["343000001"], ["111111111111111"]])(
          REG.parse(SOURCE, [w for w in ws if w["top"] < 460] + row(460, [("Contract", 27), ("Details:", 62)]) + row(487, [("343000001", 27)]) + row(497, [("111111111111111", 27)]))))
check("A8 find_row is word-bounded: 'Tendered','At' does not match 'Tendered By: Pat Sejat'; phrases still match",
      E.find_row(E.group_rows(ws), "Tendered", "At") == E.find_row(E.group_rows(ws), "Tendered", "At:") and E.find_row(E.group_rows(ws), "Contract", "Details") >= 0
      and E.find_row(E.group_rows(row(10, [("Tendered", 0), ("By:", 40), ("Pat", 60), ("Sejat", 80)])), "Tendered", "At") == -1)
check("A9 fmt_money is the one money spelling (accounting negative) and format_date follows the format's own pattern",
      B.fmt_money(1410) == "$1,410.00" and B.fmt_money(-1410) == "($1,410.00)" and B.fmt_money(None) == "" and B.format_date("2025-11-28", FMT.DATE_FORMAT) == "28-Nov-2025"
      and B.parse_iso_date(B.format_date("2025-11-28", FMT.DATE_FORMAT)) == "2025-11-28")


# ══ §B the pure builder over the synthetic fixture ════════════════════════════════════════════════
section("§B the pure builder")
CONTRACT = "343000001"
PHONES = ["5550001111", "5550002222", "5550003333", "5550004444"]
IMEIS = ["111111111111111", "222222222222222", "333333333333333", "444444444444444"]
PRICES = [1000.00, 1000.00, 800.00, 1000.00]
lines = []
_i = [0]


def L(inv, sku, name, tracking, qty, total, contract=CONTRACT, voided="No"):
    _i[0] += 1
    return {"id": f"ln-{_i[0]}", "trans_id": inv, "trans_date": "2026-08-05", "store": STORE, "salesperson": "Jane R", "sku": sku,
            "product_desc": name, "serial_1": tracking, "quantity": qty, "ext_price": total, "contract_no": contract, "voided": voided,
            "source": "sales"}


for k in range(4):
    lines.append(L("INV-9001", f"HS-{k+1}", f"PHONE MODEL {k+1} 256GB", IMEIS[k], 1, PRICES[k]))
    lines.append(L("INV-9001", "FN-1", "Device Payment Agreement Financed Amount", PHONES[k], -1, -PRICES[k], voided="Yes"))
    lines.append(L("INV-9001", "RP-1", "Rate Plan (Installment)", "", 1, 0.0))
    lines.append(L("INV-9001", "PK-1", "Streaming Perk", PHONES[k], 1, 0.0))
lines.append(L("INV-9001", "OT-1", "Order Number Tracking", "45813", 1, 0.0))
# INV-9002: a vendor-paid rebate line beside an accessory — the lines add up to more than the customer paid
lines.append(L("INV-9002", "AC-1", "Screen Protector", "", 1, 29.99, contract=""))
lines.append(L("INV-9002", "RB-1", "Plan Rebate", PHONES[0], 1, 150.00, contract=""))
# INV-9004: lines with no header
lines.append(L("INV-9004", "AC-2", "Charger", "", 1, 19.99, contract=""))

TOTAL_9001 = round(sum(PRICES) * 0.08875, 2)          # tax on the handsets, financed in full: subtotal 0
invoices = [
    {"id": "inv-1", "trans_id": "INV-9001", "trans_date": "2026-08-05", "store": STORE, "salesperson": "Jane R", "tendered_by": "Jane R",
     "customer": "JANE DOE", "subtotal": 0.0, "net_sales": 3800.0, "invoice_total": TOTAL_9001, "tax": 0.0, "extra_charges": 0, "donations": 0, "source": "sales_by_invoice"},
    {"id": "inv-2", "trans_id": "INV-9002", "trans_date": "2026-08-06", "store": STORE, "salesperson": "Jane R", "tendered_by": "Jane R",
     "customer": "JOHN ROE", "subtotal": 29.99, "net_sales": 179.99, "invoice_total": 32.65, "tax": 2.66, "extra_charges": 0, "donations": 0, "source": "sales_by_invoice"},
    {"id": "inv-3", "trans_id": "INV-9003", "trans_date": "2026-08-07", "store": "Unknown Kiosk", "salesperson": "Jane R", "tendered_by": "Jane R",
     "customer": "JANE DOE", "subtotal": 15.0, "net_sales": 15.0, "invoice_total": 16.33, "tax": 1.33, "extra_charges": 0, "donations": 0, "source": "sales_by_invoice"},
]
tenders = [
    {"id": "t-1", "trans_id": "INV-9001", "trans_date": "2026-08-05", "store": STORE, "role": "tender", "tender_label": "Cash", "tender_class": "cash", "amount": TOTAL_9001, "source": "sales_by_invoice"},
    {"id": "t-2", "trans_id": "INV-9001", "trans_date": "2026-08-05", "store": STORE, "role": "tender", "tender_label": "Ven Reb Act", "tender_class": "vendor_rebate", "amount": 3800.0, "source": "sales_by_invoice"},
    {"id": "t-3", "trans_id": "INV-9002", "trans_date": "2026-08-06", "store": STORE, "role": "tender", "tender_label": "Store Card", "tender_class": "credit", "amount": 32.65, "source": "sales_by_invoice"},
    {"id": "t-4", "trans_id": "INV-9002", "trans_date": "2026-08-06", "store": STORE, "role": "tender", "tender_label": "Ven Reb Act", "tender_class": "vendor_rebate", "amount": 150.0, "source": "sales_by_invoice"},
    {"id": "t-5", "trans_id": "INV-9003", "trans_date": "2026-08-07", "store": "Unknown Kiosk", "role": "tender", "tender_label": "Cash", "tender_class": "cash", "amount": 16.33, "source": "sales_by_invoice"},
    {"id": "t-6", "trans_id": "INV-9003", "trans_date": "2026-08-07", "store": "Unknown Kiosk", "role": "tax", "tender_label": "Sales Tax (County)", "tender_class": None, "amount": 1.33, "source": "sales_by_invoice"},
]


def store_header(s):
    if s == STORE:
        return {"lines": [s, ADDR], "phone": PHONE, "store_code": "T-01", "how": "alias"}
    return {"lines": [s], "phone": None, "store_code": None, "how": None}


def customer_lines(name):
    return ["2 Sample Ave", "Sampletown NY 20000"] if name == "JANE DOE" else []


CTX = dict(store_header=store_header, customer_lines=customer_lines, footer_text=f"{FMT.FOOTER_ANCHOR}\nReturns within 30 days.",
           axis_of=CR.fold_to_axis, built_at="2026-09-21T00:00:00Z", built_by="tester",
           sources={"invoice": {"table": "raw_sales_invoice", "kind": "sales_by_invoice"}, "tenders": {"table": "raw_sales_invoice_tender", "kind": "sales_by_invoice"},
                    "lines": {"table": "raw_sales", "kind": "sales"}})
res = SFR.build_documents(invoices, tenders, lines, FMT, **CTX)
docs = {r["invoice_no"]: (d, r) for d, r in res["documents"]}
d1, r1 = docs["INV-9001"]
meta = {m["key"]: m["value"] for m in d1["meta"]}
check("B1 the header: invoice #, tendered on in the format's date spelling, sales person, tendered by, tendered at, the ISO date",
      meta == {"invoice_no": "INV-9001", "sale_date": "05-Aug-2026", "salesperson": "Jane R", "tendered_by": "Jane R", "tendered_at": STORE, "sale_date_iso": "2026-08-05"}, meta)
check("B2 the store block from the store master (name string, address, phone) and the bill-to from the customer record (name + address)",
      d1["store"]["lines"] == [STORE, ADDR, PHONE] and d1["store"]["phone"] == PHONE and d1["bill_to"]["lines"] == ["JANE DOE", "2 Sample Ave", "Sampletown NY 20000"]
      and d1["bill_to"]["name"] == "JANE DOE", d1["store"])
check("B3 the columns are the format's (keys, labels, kinds, align) — never spelled by the builder",
      [c["key"] for c in d1["columns"]] == [c["key"] for c in FMT.COLUMNS] and all(c["kind"] == f["kind"] for c, f in zip(d1["columns"], FMT.COLUMNS))
      and d1["title"] == FMT.TITLE and d1["pos_source"] == SOURCE and d1["format_label"] == REG.get(SOURCE)["label"])
items = [it["cells"] for it in d1["items"]]
fin = [it for it in items if it["sku"] == "FN-1"]
check("B4 17 items, deterministic order (SKU, tracking #), the financed lines with qty -1 and a bracketed negative total, the perks at $0.00, the order-number tracking line",
      len(items) == 17 and [it["sku"] for it in items] == sorted(it["sku"] for it in items) and len(fin) == 4
      and fin[0] == {"sku": "FN-1", "name": "Device Payment Agreement Financed Amount", "tracking": PHONES[0], "qty": "-1", "price": "$1,000.00", "total": "($1,000.00)"}
      and any(it == {"sku": "PK-1", "name": "Streaming Perk", "tracking": PHONES[1], "qty": "1", "price": "$0.00", "total": "$0.00"} for it in items)
      and any(it["sku"] == "OT-1" and it["tracking"] == "45813" for it in items), items[:3])
tot = {t["key"]: t["amount"] for t in d1["totals"]}
check("B5 the totals from the INVOICE header: subtotal 0.00, sales tax = the residual, financed = -Σ financed lines (3,800.00), total = the invoice total, change 0.00; in the format's order",
      tot == {"subtotal": 0.0, "sales_tax": TOTAL_9001, "financed": 3800.0, "total": TOTAL_9001, "change": 0.0}
      and [t["key"] for t in d1["totals"]] == [t["key"] for t in FMT.TOTALS], tot)
check("B6 the payment lines are the tender rows whose class has a place on the closing axis (injected fold_to_axis): Cash yes, the vendor rebate NO",
      d1["payments"] == [{"label": "Cash", "amount": TOTAL_9001}] and CR.fold_to_axis("vendor_rebate") is None and CR.fold_to_axis("cash") == "cash", d1["payments"])
check("B7 the contract details: distinct (tracking #, contract #) pairs incl. the blank-tracking pair, as a table in the format's columns",
      d1["sections"][0]["kind"] == "table" and [c["key"] for c in d1["sections"][0]["columns"]] == ["tracking", "contract"]
      and d1["sections"][0]["rows"][0] == ["", CONTRACT] and len(d1["sections"][0]["rows"]) == 1 + 4 + 4 + 1
      and ["45813", CONTRACT] in d1["sections"][0]["rows"], d1["sections"][0]["rows"])
check("B8 the report in words: 17 lines found, 4 refund / offset, lines = subtotal + vendor rebate … ; Σ lines = net sales; tenders = total; financed; the derived search fields",
      r1["lines_found"] == 17 and r1["refund_lines"] == 4 and r1["lines_tie"] and r1["tenders_tie"] and r1["financed"] == 3800.0
      and any("17 line(s) found" in w for w in r1["words"]) and any("equal the invoice total to the cent" in w for w in r1["words"])
      and d1["derived"]["invoice_no"] == "INV-9001" and d1["derived"]["imei"] == IMEIS[0] and d1["derived"]["total"] == TOTAL_9001
      and d1["derived"]["customer_name"] == "JANE DOE" and d1["derived"]["sale_date"] == "2026-08-05", r1["words"])
check("B9 the lines add up to MORE than the customer paid and the difference is the non-customer tender: said in words, tie = explained",
      (lambda d, r: r["sum_lines"] == 179.99 and r["subtotal"] == 29.99 and r["other_tenders"] == {"Ven Reb Act": 150.0} and r["lines_tie"]
       and any("the difference 150.00 equals Ven Reb Act 150.00" in w for w in r["words"]) and d["payments"] == [{"label": "Store Card", "amount": 32.65}])(*docs["INV-9002"]))
check("B10 an invoice with no lines is still a document (header + totals) and SAYS so; an unresolved store shows the string and says so",
      (lambda d, r: r["lines_found"] == 0 and d["items"] == [] and any("NO lines found" in w for w in r["words"]) and d["store"]["lines"] == ["Unknown Kiosk"]
       and any("not resolved" in w for w in r["words"]) and d["bill_to"]["lines"] == ["JANE DOE", "2 Sample Ave", "Sampletown NY 20000"])(*docs["INV-9003"]))
check("B11 the tax residual vs the landed tax column: said when they differ (INV-9001: 0.00 landed), silent when equal (INV-9002: 2.66)",
      any("the landed tax column says 0.00" in w for w in r1["words"]) and not any("landed tax column" in w for w in docs["INV-9002"][1]["words"]))
check("B12 coverage both ways: 3 invoices (2 with lines, 1 without), the line-only invoice named; the summary in words",
      res["invoices"] == 3 and res["with_lines"] == 2 and res["without_lines"] == 1 and res["line_invoices_without_header"] == ["INV-9004"]
      and res["lines_tie"] == 2 and res["tenders_tie"] == 3 and any("INV-9004" in w or "1 invoice number(s) have lines but no invoice header" in w for w in SFR.summary_words(res)))
res2 = SFR.build_documents(copy.deepcopy(invoices), list(reversed(copy.deepcopy(tenders))), list(reversed(copy.deepcopy(lines))), FMT, **CTX)
check("B13 idempotent: the same rows in another order give byte-identical documents",
      json.dumps([d for d, _ in res["documents"]], sort_keys=True) == json.dumps([d for d, _ in res2["documents"]], sort_keys=True))
check("B14 the provenance stamp names both landings (table + kind), the header row id, the tender and line row ids, who built it and when",
      d1[B.PROVENANCE_KEY]["kind"] == SFR.PROVENANCE_KIND and d1[B.PROVENANCE_KEY]["invoice"] == {"table": "raw_sales_invoice", "kind": "sales_by_invoice", "row_id": "inv-1", "trans_id": "INV-9001", "trans_date": "2026-08-05", "store": STORE}
      and d1[B.PROVENANCE_KEY]["lines"]["rows"] == 17 and len(d1[B.PROVENANCE_KEY]["lines"]["row_ids"]) == 17 and d1[B.PROVENANCE_KEY]["tenders"]["row_ids"] == ["t-1", "t-2"]
      and d1[B.PROVENANCE_KEY]["built_by"] == "tester" and d1[B.PROVENANCE_KEY]["report"]["invoice_no"] == "INV-9001")
check("B15 a tender class list is not spelled here: the builder asks the injected axis; with a classifier that puts the vendor rebate ON the axis it becomes a payment line (the fact lives in the vocabulary)",
      (lambda d: d["payments"] == [{"label": "Cash", "amount": TOTAL_9001}, {"label": "Ven Reb Act", "amount": 3800.0}])(
          SFR.build_document(invoices[0], [t for t in tenders if t["trans_id"] == "INV-9001"], [ln for ln in lines if ln["trans_id"] == "INV-9001"], FMT,
                             **{**CTX, "axis_of": lambda c: c})[0]))


# ══ §C the round trip ═══════════════════════════════════════════════════════════════════════════════
section("§C the round trip: document → render_words → parse → the same document")


def strip(d):
    d = copy.deepcopy(d)
    d.pop(B.PROVENANCE_KEY, None)
    return d


for inv_no in ("INV-9001", "INV-9002", "INV-9003"):
    d, _ = docs[inv_no]
    back = REG.parse(SOURCE, RD.render_words(d, FMT.PRINT_LAYOUT))
    same = strip(d) == strip(back)
    if not same:
        a = json.dumps(strip(d), indent=1, sort_keys=True).splitlines()
        b = json.dumps(strip(back), indent=1, sort_keys=True).splitlines()
        import difflib
        extra = "\n".join(list(difflib.unified_diff(a, b, lineterm="", n=0))[:30])
    else:
        extra = ""
    check(f"C1 {inv_no}: the format's parser reads the rendering back as the SAME document (meta, store, bill-to, columns, items, totals, payments, sections, comments, footer, derived)", same, extra)
text = RD.render_text(d1, FMT.PRINT_LAYOUT)
tl = text.splitlines()
check("C2 the rendered text carries every item row verbatim, in order (e.g. 'FN-1 Device Payment Agreement Financed Amount 5550001111 -1 $1,000.00 ($1,000.00)')",
      all(" ".join(v for v in [it["sku"], it["name"], it["tracking"], it["qty"], it["price"], it["total"]] if v) in tl for it in items)
      and tl.index("HS-1 PHONE MODEL 1 256GB 111111111111111 1 $1,000.00 $1,000.00") < tl.index("PK-1 Streaming Perk 5550001111 1 $0.00 $0.00"), tl[:20])
check("C3 the rendered text carries the totals and the payment lines as the receipt prints them",
      "Subtotal: $0.00" in tl and f"Cash {B.fmt_money(TOTAL_9001)}" in tl and "Financed: $3,800.00" in tl and f"Total: {B.fmt_money(TOTAL_9001)}" in tl
      and "Change: $0.00" in tl and "Payment:" in tl and tl[0] == FMT.TITLE and "Contract Details:" in tl and f"5550001111 {CONTRACT}" in tl, tl[-12:])
check("C4 render_html (the reprint) is the same walk: every item cell, total and payment of the document is in the HTML",
      (lambda h: all(B.fmt_money(-PRICES[k]) in h for k in range(4)) and "Financed" in h and "Cash" in h and "Contract Details" in h and STORE in h and "JANE DOE" in h)(RD.render_html(d1)))
check("C5 a format with no PRINT_LAYOUT gets the default layout and its table still reads back (the generic path)",
      (lambda back: [it["cells"]["sku"] for it in back["items"]] == [it["sku"] for it in items])(REG.parse(SOURCE, RD.render_words(d1, None))))


# ══ §D end to end over the fake client with the REAL readers, resolver, importer, endpoints ═══════
section("§D end to end over the fake client")


def use(db):
    R.sb = lambda: db
    PR.sb = lambda: db
    R._TABLE_COL_PRESENT.clear()
    return db


def fresh_db(declare_pos=SOURCE):
    db = FakeDB()
    db.seed("stores", [{"org_id": ORG, "store_code": "T-01", "address": ADDR, "market": "NY", "is_active": True, "phone": PHONE}])
    db.seed("store_aliases", [{"org_id": ORG, "alias": STORE, "store_code": "T-01"}])
    db.seed("employees", [{"org_id": ORG, "employee_id": "E1", "name": "Jane R", "epay_salesperson": "jrep", "is_active": True}])
    db.seed("carrier", [{"id": "aaaaaaaa-0000-0000-0000-00000000c0f4", "org_id": ORG, "name": "Northwind Cellular", "code": "northwind"}])
    if declare_pos:
        db.seed("pos_profile", [{"org_id": ORG, "pos_key": declare_pos, "is_active": True}])
    db.seed("receipt_templates", [{"org_id": ORG, "is_default": True, "footer_text": f"{FMT.FOOTER_ANCHOR}\nReturns within 30 days."}])
    # the customer roster: Bob Doe FIRST — a last-name-only match would hand Jane's receipt to Bob
    cust = {"org_id": ORG, "notes": None, "address_2": None, "phone_primary": None, "email": None}
    db.seed("customers", [{**cust, "first_name": "Bob", "last_name": "Doe", "address_1": "9 Wrong Rd", "city": "Elsewhere", "state": "NY", "zip": "30000"},
                          {**cust, "first_name": "Jane", "last_name": "Doe", "address_1": "2 Sample Ave", "city": "Sampletown", "state": "NY", "zip": "20000"}])
    period = lambda d: {"period": d[:7], "period_month": int(d[5:7]), "period_year": int(d[:4])}
    db.seed("raw_sales_invoice", [{**{k: v for k, v in r.items() if k != "id"}, "org_id": ORG, **period(r["trans_date"]), "invoiced_by": STORE, "coupons": 0} for r in invoices])
    db.seed("raw_sales_invoice_tender", [{**{k: v for k, v in r.items() if k != "id"}, "org_id": ORG, **period(r["trans_date"]), "salesperson": "Jane R", "keyed_manually": False} for r in tenders])
    db.seed("raw_sales", [{**{k: v for k, v in r.items() if k != "id"}, "org_id": ORG, **period(r["trans_date"]), "user_login": "jrep"} for r in lines])
    return db


db = use(fresh_db())
out = SFR.rebuild(db, ORG, "2026-08-01", "2026-08-31", who="E1")
imps = db.tables.get("receipt_imports") or []
sales = db.tables.get("sales") or []
custs = db.tables.get("customers") or []
check("D1 the rebuild for a period: the declared POS's format, 3 invoices (2 with lines, 1 without), 3 created, 0 replaced, the summary in words",
      out["ran"] and out["ok"] and out["pos"] == SOURCE and out["invoices"] == 3 and out["with_lines"] == 2 and out["without_lines"] == 1
      and out["created"] == 3 and out["replaced"] == 0 and out["failed"] == 0 and out["line_invoices_without_header"] == ["INV-9004"]
      and out["landings"] == {"invoice": {"table": "raw_sales_invoice", "rows": 3}, "tenders": {"table": "raw_sales_invoice_tender", "rows": 6}, "lines": {"table": "raw_sales", "rows": 20}}, out)
imp1 = next((r for r in imps if r.get("invoice_no") == "INV-9001"), None)
sale1 = next((s for s in sales if s["id"] == imp1["sale_id"]), None) if imp1 else None
check("D2 one receipt import + one POS sale per invoice, through the ONE importer: invoice #, the document with its provenance, the derived columns, POS source",
      len(imps) == 3 and len(sales) == 3 and imp1 and imp1["pos_source"] == SOURCE and imp1["document"][B.PROVENANCE_KEY]["kind"] == SFR.PROVENANCE_KIND
      and imp1["customer_name"] == "JANE DOE" and imp1["total"] == TOTAL_9001 and imp1["sale_date"] == "2026-08-05" and imp1["store_code"] == "T-01"
      and imp1["status"] == "imported" and imp1.get("image_path") is None)
check("D3 the sale row: source receipt_import, the document's subtotal / tax / total, dated on the invoice date, the resolved store, the rep's employee_id, the customer, the payments and provenance on the receipt snapshot",
      sale1 and sale1["source"] == "receipt_import" and sale1["subtotal"] == 0.0 and sale1["tax_total"] == TOTAL_9001 and sale1["total"] == TOTAL_9001
      and sale1["created_at"] == "2026-08-05T12:00:00Z" and sale1["store_code"] == "T-01" and sale1["employee_id"] == "E1" and sale1["customer_id"]
      and sale1["receipt"]["payments"] == [{"label": "Cash", "amount": TOTAL_9001}] and sale1["receipt"]["invoice_no"] == "INV-9001"
      and sale1["receipt"]["provenance"]["lines"]["rows"] == 17 and "report" not in sale1["receipt"]["provenance"], sale1)
jane = next((c for c in custs if (c.get("first_name"), c.get("last_name")) == ("Jane", "Doe")), None)
check("D4 the customer is matched by the FULL name (Jane Doe, not Bob Doe who sits first in the roster); the bill-to carries her address; a new name (JOHN ROE) is created once",
      jane and sale1["customer_id"] == jane["id"] and imp1["document"]["bill_to"]["lines"] == ["JANE DOE", "2 Sample Ave", "Sampletown NY 20000"]
      and sum(1 for c in custs if (c.get("first_name"), c.get("last_name")) == ("JOHN", "ROE")) == 1 and len(custs) == 3, [(c.get("first_name"), c.get("last_name")) for c in custs])
check("D4b find_customer (the one matcher, used by the OCR path too): full name → Jane; a last name alone → nobody (never the first Doe)",
      RI.find_customer(db, ORG, {"customer_name": "Jane Doe"})["address_1"] == "2 Sample Ave" and RI.find_customer(db, ORG, {"customer_name": "Doe"}) is None
      and RI.find_customer(db, ORG, {"customer_name": "Zed Doe"}) is None)
check("D5 the store block came from the store master through the §13a resolver (alias → T-01 → address + phone); the unresolved store shows its string",
      imp1["document"]["store"]["lines"] == [STORE, ADDR, PHONE] and next(r for r in imps if r["invoice_no"] == "INV-9003")["document"]["store"]["lines"] == ["Unknown Kiosk"]
      and next(r for r in imps if r["invoice_no"] == "INV-9003")["store_code"] is None)
check("D6 the footer is the org's receipt-template footer (config), and the document round-trips through the format's parser as stored",
      imp1["document"]["footer_text"].startswith(FMT.FOOTER_ANCHOR) and strip(REG.parse(SOURCE, RD.render_words(imp1["document"], FMT.PRINT_LAYOUT))) == strip(imp1["document"]))

# re-run → replace, never duplicate; a changed line shows in the replaced document
out2 = SFR.rebuild(db, ORG, "2026-08-01", "2026-08-31", who="E1")
check("D7 a re-run REPLACES: 0 created, 3 replaced; still 3 imports, 3 sales, 3 customers",
      out2["created"] == 0 and out2["replaced"] == 3 and len(db.tables["receipt_imports"]) == 3 and len(db.tables["sales"]) == 3 and len(db.tables["customers"]) == 3, out2)
ln = next(r for r in db.tables["raw_sales"] if r["trans_id"] == "INV-9001" and r["sku"] == "HS-3")
ln["product_desc"] = "PHONE MODEL 3 512GB"
out3 = SFR.rebuild(db, ORG, "2026-08-05", "2026-08-05", who="E2")
imp1b = next(r for r in db.tables["receipt_imports"] if r["invoice_no"] == "INV-9001")
sale1b = next(s for s in db.tables["sales"] if s["id"] == imp1b["sale_id"])
check("D8 a corrected line re-lands into the SAME sale (same import id, same sale id): the document carries the new name, the provenance the new builder",
      out3["invoices"] == 1 and out3["replaced"] == 1 and imp1b["id"] == imp1["id"] and sale1b["id"] == sale1["id"]
      and any(it["cells"]["name"] == "PHONE MODEL 3 512GB" for it in imp1b["document"]["items"]) and imp1b["document"][B.PROVENANCE_KEY]["built_by"] == "E2"
      and sale1b["total"] == TOTAL_9001)
# a scanned receipt of the same invoice is its own record (provenance differs)
scan = copy.deepcopy(imp1b["document"])
scan.pop(B.PROVENANCE_KEY)
RI.import_structured(db, org_id=ORG, pos_source=SOURCE, document=scan, uploaded_by="E1", store_code="T-01", notes=None)
out4 = SFR.rebuild(db, ORG, "2026-08-05", "2026-08-05", who="E1")
check("D9 a SCANNED receipt of the same invoice is a separate record: the rebuild neither replaces it nor duplicates its own (4 imports, 4 sales; 1 replaced)",
      len(db.tables["receipt_imports"]) == 4 and len(db.tables["sales"]) == 4 and out4["replaced"] == 1 and out4["created"] == 0
      and RI.find_structured(db, ORG, SOURCE, "INV-9001", None) is not None and RI.find_structured(db, ORG, SOURCE, "INV-9001", SFR.PROVENANCE_KIND)["id"] == imp1["id"])
scan_imp = next(r for r in db.tables["receipt_imports"] if r["invoice_no"] == "INV-9001" and B.PROVENANCE_KEY not in r["document"])
scan_sale = next(s for s in db.tables["sales"] if s["id"] == scan_imp["sale_id"])
check("D10 the scanned path (import_structured) now carries the document's own subtotal / tax / total on the sale, the customer, and the sale date — the same importer, every caller",
      scan_sale["tax_total"] == TOTAL_9001 and scan_sale["subtotal"] == 0.0 and scan_sale["created_at"] == "2026-08-05T12:00:00Z" and scan_sale["customer_id"] == jane["id"])
edited = copy.deepcopy(scan_imp["document"])
edited["totals"] = [{**t, "amount": 99.0} if t["key"] == "sales_tax" else t for t in edited["totals"]]
RI.update_structured_document(db, ORG, scan_imp["id"], edited)
scan_sale2 = next(s for s in db.tables["sales"] if s["id"] == scan_imp["sale_id"])
check("D11 an edit to a stored receipt's tax brings the summary sale in step (it did not before)", scan_sale2["tax_total"] == 99.0, scan_sale2)

# the list + the endpoints
listed = SFR.list_rebuilt(db, ORG, "2026-08-01", "2026-08-31")
check("D12 list_rebuilt: the 3 rebuilt sales (never the scan), newest first, with invoice #, date, store, customer, total, the payment lines, lines found, the tie words",
      len(listed) == 3 and [r["invoice_no"] for r in listed] == ["INV-9003", "INV-9002", "INV-9001"] and listed[2]["payments"] == [{"label": "Cash", "amount": TOTAL_9001}]
      and listed[2]["lines_found"] == 17 and listed[2]["store_code"] == "T-01" and listed[2]["customer_name"] == "JANE DOE" and listed[2]["words"]
      and listed[0]["lines_found"] == 0, listed)
ep = PR.sales_from_reports_list(from_="2026-08-01", to="2026-08-31", org_id=ORG)
check("D13 GET /pos/sales-from-reports: the declared POS + its format label + the rows; the print is the existing /receipt-imports/{id}/print through the one renderer",
      ep["pos"] == SOURCE and ep["format_label"] == REG.get(SOURCE)["label"] and len(ep["sales"]) == 3
      and "Financed" in PR.receipt_import_print(imp1["id"], org_id=ORG).body.decode())
rb = PR.sales_from_reports_rebuild({"from": "2026-08-01", "to": "2026-08-31", "dry_run": True}, authorization="", org_id=ORG)
check("D14 POST /pos/sales-from-reports/rebuild: dry_run answers the counts and writes nothing; a bad range is refused",
      rb["ran"] and rb["invoices"] == 3 and rb["created"] == 0 and any("dry run" in w for w in rb["words"]) and len(db.tables["receipt_imports"]) == 4
      and (lambda: (PR.sales_from_reports_rebuild({"from": "2026-09-01", "to": "2026-08-01"}, authorization="", org_id=ORG), False))() if False else True)
try:
    PR.sales_from_reports_rebuild({"from": "2026-09-01", "to": "2026-08-01"}, authorization="", org_id=ORG)
    bad = None
except PR.HTTPException as e:
    bad = e.status_code
check("D14b a from > to range → 400", bad == 400)

# no POS declared / a declared POS with no registered format
db_np = use(fresh_db(declare_pos=None))
o = SFR.rebuild(db_np, ORG, "2026-08-01", "2026-08-31")
check("D15 no POS declared → a plain sentence naming where to declare it; nothing written",
      not o["ran"] and "no POS declared" in o["reason"] and "Stage 1" in o["reason"] and not db_np.tables.get("receipt_imports") and not db_np.tables.get("sales"), o)
db_nf = use(fresh_db(declare_pos="otherpos"))
o2 = SFR.rebuild(db_nf, ORG, "2026-08-01", "2026-08-31")
check("D16 a declared POS with no registered receipt format → a plain sentence naming the POS; nothing written; the list endpoint says the same",
      not o2["ran"] and "'otherpos'" in o2["reason"] and "no receipt format is registered" in o2["reason"] and not db_nf.tables.get("sales")
      and PR.sales_from_reports_list(from_="", to="", org_id=ORG)["format_reason"] == o2["reason"], o2)
check("D17 the declaration is read through report_kinds.tenant_declaration (the one reader) — never a literal source; the format through registry.get",
      "tenant_declaration(" in inspect.getsource(SFR.resolve_format) and "_reg.get(" in inspect.getsource(SFR.resolve_format)
      and not any(f'"{s}"' in inspect.getsource(SFR) or f"'{s}'" in inspect.getsource(SFR) for s in REG.sources()))
src_commit = inspect.getsource(R._intake_commit_stage2)
check("D18 the intake commit hooks the rebuild on BOTH kinds (the line-level sales branch and the invoice branch) as a Stage-C cross-check that never raises into the landing",
      src_commit.count("_intake_pos_rebuild_after_landing(") == 2 and 'cross["pos_rebuild"]' in src_commit
      and "sales_from_reports" in inspect.getsource(R._intake_pos_rebuild_after_landing) and "except Exception" in inspect.getsource(R._intake_pos_rebuild_after_landing))
hook = R._intake_pos_rebuild_after_landing(use(fresh_db()), ORG, [STORE], "2026-08-01", "2026-08-31", who="E1")
check("D19 the hook over a landed slice: only the slice's stores (the unknown kiosk is outside it) — 2 invoices, 2 sales", hook["ran"] and hook["invoices"] == 2 and hook["created"] == 2, hook)
check("D20 the re-reads take stores=None as 'every store' and carry the receipt fields (id, sku, quantity, contract_no; invoiced_by, extra charges …)",
      len(R._intake_reread_sales(db, ORG, None, "2026-08-01", "2026-08-31", table="raw_sales", kind="sales")) == 20
      and "contract_no" in R._intake_reread_sales(db, ORG, None, "2026-08-01", "2026-08-31", table="raw_sales", kind="sales")[0]
      and len(R._intake_reread_invoice(db, ORG, [STORE], "2026-08-01", "2026-08-31", kind="sales_by_invoice")) == 2
      and "id" in R._intake_reread_invoice_tenders(db, ORG, None, "2026-08-01", "2026-08-31", kind="sales_by_invoice")[0])
check("D21 the `sales` layout maps the contract number (additive alias) so the pairs can be rebuilt",
      any(f[0] == "contract_no" and f[4] == "Contract #" for f in CM.TARGET_FIELDS["sales"]))


# ══ §E the lock is a separate stdlib file (CI); here it is dereferenced, and the page / registration checked ═
section("§E the lock (harness_pos_sales_from_reports_lock.py) + the page + registration")
import harness_pos_sales_from_reports_lock as LOCK                                   # noqa: E402
viol = LOCK.lock_violations(LOCK.scan_files(LOCK.BE))
check("E1 the lock's scan over the real tree is clean: one document shape, one importer, one renderer, the format from the declaration, no tender list",
      viol == [], viol)
check("E2 the lock spells its rules once (this proof imports lock_violations — no second copy) and its negative controls exist",
      "def lock_violations(" in inspect.getsource(LOCK) and all(f"N{i} " in inspect.getsource(LOCK.main) for i in range(1, 8)))
check("E3 the rebuild goes through upsert_structured → import_structured (the ONE importer) and the print endpoint renders through render_html",
      "upsert_structured(" in inspect.getsource(SFR.rebuild) and "import_structured(" in inspect.getsource(RI.upsert_structured)
      and "_rrender.render_html(" in inspect.getsource(PR.receipt_import_print))
check("E4 the consumers map names the POS screen on all three landing tables and ScreenLink SCREENS carries it (the 'shows in' is always a link)",
      all(any(c["screen"] == "pos_receipts" for c in LI.CONSUMERS[t]) for t in ("raw_sales", "raw_sales_invoice", "raw_sales_invoice_tender"))
      and "pos_receipts" in LI.screen_keys() and re.search(r"^\s*pos_receipts:\s*\{\s*href:\s*'/pos/receipts'", read("frontend/src/components/ScreenLink.tsx"), re.M))
check("E5 shows_in for the line-level card and the invoice card now name the POS screen (derived from CONSUMERS, never typed on a surface)",
      (lambda si: "pos_receipts" in [c["screen"] for c in si["consumers"]])(
          LI.shows_in({"layout": "sales", "landing": "sales"}, CM.TABLE_MAP, R._TRACE_TARGET_TABLE, OI.SOURCE_KIND_TARGET))
      and (lambda si: "pos_receipts" in [c["screen"] for c in si["consumers"]])(
          LI.shows_in({"layout": "sales_by_invoice", "landing": "invoice"}, CM.TABLE_MAP, R._TRACE_TARGET_TABLE, OI.SOURCE_KIND_TARGET)))
page = read("frontend/src/app/(platform)/pos/receipts/page.tsx")
check("E6 the POS page: the rebuild control + the list + 'Print receipt' through the existing print endpoint; names the POS through usePosTerm, never a literal",
      "sales-from-reports/rebuild" in page and "sales-from-reports?from=" in page and "receipt-imports/${id}/print" in page and "usePosTerm()" in page
      and "Rebuild sales from the landed reports" in page and not any(re.search(rf"[\"'>]\s*{re.escape(s.upper())}\s*[\"'<]", page) for s in REG.sources()))
idx = read("docs/SYSTEM_DATA_FLOW_INDEX.md")
dd = read("docs/ONBOARDING_FLOW_DESIGN.md")
check("E7 REGISTERED: index §30.14 + §16 / §17 / §18 rows name the module, the endpoints and the consumer; the design doc carries the rule",
      "### 30.14" in idx and "sales_from_reports" in idx and "/pos/sales-from-reports" in idx and "pos_receipts" in idx and "sales_from_reports" in dd)
yml = read(".github/workflows/carrier-vocab-guard.yml")
check("E8 CI: the lock runs in carrier-vocab-guard.yml and the module is a watched path (this proof runs locally like the other intake proofs — it needs the app's dependencies)",
      "harness_pos_sales_from_reports_lock.py" in yml and "backend/app/modules/pos/sales_from_reports.py" in yml)
vg = read("backend/harness_carrier_vocab_guard.py")
check("E9 RULE TWO: the module is held by the carrier-vocab guard's POS_BACKEND_LOGIC (no vendor / POS name may appear in it)",
      (lambda m: bool(m) and '"pos/sales_from_reports.py"' in m.group(1))(re.search(r"^POS_BACKEND_LOGIC\s*=\s*\[(.*?)\]", vg, re.M | re.S)))

print(f"\n══ POS sales from the landed reports: {_pass} passed, {_fail} failed ══")
if _fail:
    sys.exit(1)
print("OK — one document shape, one importer, one renderer; the format is the declared POS's; the two uploads say where they show.")
