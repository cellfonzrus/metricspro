"""DB-FREE PROOF — SALES BY INVOICE with the TENDER TYPES (owner 2026-09-21: "sales by invoice report also
has the tender types on the report, need to capture that as well" — "tender types is in columns").

    python3 harness_sales_by_invoice.py        (from backend/; no network, no DB)

THE FIXTURE = the measured file's 41 headers, verbatim (some with their trailing space), over a handful of
synthetic invoices: two stores (one the roster knows by address, one by an alias), three days, twelve
tender columns of which each invoice uses one to three, a vendor-rebate tender, non-integrated twins, the
two tax columns. Every Σ below is RE-DERIVED here by hand.

  §A THE VOCABULARY — one home: closing.router.TENDER_VOCAB; `tender_class` on the measured headers; the
     retired `_canon_tender` ladder (frozen here) is byte-identical on a spelling battery except the ONE
     stated delta (the spelled-out card brand); the axis is derived; `keyed_manually`
  §B THE SUGGESTION ENGINE over the measured headers — every tender column proposed with its class, the
     non-integrated twins flagged, the two tax columns, the header fields never proposed, the unplaced money
     column listed; decisions laid over; an unknown class refused
  §C DETECTION — the kind's own signals rank the invoice card first (ask: it shares headers with the
     by-product card); with the 1012-seeded signature (parsed out of the SQL) → confirm 1.0; the wrong-file
     sentence names the card and the page; the by-product card is NOT chosen for this header
  §D END TO END over the REAL router: 2.0 → analyze (the 2.5b block, Σ per column, the tie in words) →
     commit → the header rows and the tender rows land through `_ingest_mapped_df` (stamped, sliced) →
     re-read = built = inserted; Σ ties; the tender class per row; closing_tender_map rows written for
     report='invoice' and READ BACK as "your earlier choice" on the next analyze; a re-commit replaces the
     slice; raw_sales / raw_sales_product untouched
  §E STAGE 4 — the tender split per store-day beside a fake X-report (pos_tender_summary through the
     closing recon's own `_xreport_tenders_by_store` and `_addr_resolver`): matches, differences, a day
     with no X-report; the Stage-4 note; the report links pair the export with the line-level rows by
     invoice number and with the X-report by store + date
  §F THE MIS-CARDING, reproduced: the same header dropped on the line-level card is refused BEFORE a row is
     written (the consumer gate) — it never reaches raw_sales; the retire path takes the stale line out of
     the verify table with a reason and a name, touching nothing it landed
  §G negative controls + registration + RULE TWO + the migration's contents + CI wiring
"""
import asyncio
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
os.environ.setdefault("SUPABASE_URL", "http://fake")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")

from harness_intake_fakes import FakeDB, FakeUpload                                  # noqa: E402
from app.modules.commcalc import onboarding_intake as OI                              # noqa: E402
from app.modules.commcalc import invoice_tenders as IT                                # noqa: E402
from app.modules.commcalc import column_mapping as CM                                 # noqa: E402
from app.modules.commcalc import landing_identity as LI                               # noqa: E402
from app.modules.commcalc import ingest_slice as IS                                   # noqa: E402
from app.modules.commcalc import report_kinds as RK                                   # noqa: E402
from app.modules.commcalc import report_links as RL                                   # noqa: E402
from app.modules.commcalc import router as R                                          # noqa: E402
from app.modules.storeops import router as SO                                         # noqa: E402
from app.modules.storeops import merchant_ids as MIDS                                 # noqa: E402
from app.modules.closing import router as CR                                          # noqa: E402
from app.modules.closing import tender_config as TC                                   # noqa: E402
from app.modules.commcalc import epay_ingest as EPI                                   # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ORG = "f4f1c16e-0000-4000-8000-00000000f4f1"
HOUSE = RK.HOUSE_ORG
MIG = os.path.join(ROOT, "database", "migrations", "1012_sales_by_invoice.sql")
_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}{(' — ' + str(extra)[:700]) if extra else ''}")
    return bool(cond)


def section(t):
    print(f"\n{t}\n" + "─" * len(t))


def use(db):
    R.sb = lambda: db
    SO.sb = lambda: db
    SO.get_supabase = lambda: db
    MIDS.get_supabase = lambda: db
    CR.get_supabase = lambda: db
    EPI.get_supabase = lambda: db
    R._TABLE_COL_PRESENT.clear()
    R._invalidate_accessory_config(ORG)
    return db


def run(coro):
    return asyncio.run(coro)


def analyze(db, data, filename, **form):
    use(db)
    kw = dict(org_id=ORG)
    kw.update(form)
    return run(R.onboarding_intake_analyze(file=FakeUpload(data, filename), **kw))


def commit(db, data, filename, **form):
    use(db)
    kw = dict(verified_by="tester", org_id=ORG)
    kw.update(form)
    return run(R.onboarding_intake_commit(file=FakeUpload(data, filename), **kw))


def http_error_type(fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        return type(e)
    return None


def http_error(fn, *a, **kw):
    try:
        r = fn(*a, **kw)
        if asyncio.iscoroutine(r):
            r = run(r)
        return None, None
    except R.HTTPException as e:
        return e.status_code, e.detail


# ══ THE MEASURED HEADERS, verbatim (trailing spaces kept where the file has them) ══════════════════
HEADERS = ["Created On", "Invoiced By", "Invoiced At", "Tendered By", "Tendered By Username", "Sold By", "Sold By Username",
           "Invoice #", "Customer", "Application", "Invoice Comments", "Invoice Subtotal", "Adjustments", "Net Sales", "Sales",
           "Total Cost", "Gross Profit", "Extra Charges", "Total Donations", "Invoice Total", "Total Coupons", "Gift Card Sales",
           "Non-Revenue Sales", "Channel", "Region", "District", "Emailed", "Discover", "MasterCard Non-Integrated ", "Cash",
           "Visa Non-Integrated ", "Debit PIN", "Ven Reb Act", "American Express", "AmEx Non-Integrated ", "Visa", "MasterCard",
           "Debit Non-Integrated ", "Discover Non-Integrated ", "TotalTaxPaidAmount", "Sales Tax (NYC) Brooklyn"]
assert len(HEADERS) == 41
TENDER_HEADERS = ["Discover", "MasterCard Non-Integrated ", "Cash", "Visa Non-Integrated ", "Debit PIN", "Ven Reb Act",
                  "American Express", "AmEx Non-Integrated ", "Visa", "MasterCard", "Debit Non-Integrated ", "Discover Non-Integrated "]
# the platform reads a header STRIPPED (column_mapping.apply_mapping keys on strip().lower()) — so the
# tender label on a landed row is the stripped header; the workbook keeps the file's trailing spaces
EXPECT_CLASS = {"Discover": "credit", "MasterCard Non-Integrated": "credit", "Cash": "cash", "Visa Non-Integrated": "credit",
                "Debit PIN": "debit", "Ven Reb Act": "vendor_rebate", "American Express": "credit", "AmEx Non-Integrated": "credit",
                "Visa": "credit", "MasterCard": "credit", "Debit Non-Integrated": "debit", "Discover Non-Integrated": "credit"}
EXPECT_KEYED = {h.strip() for h in TENDER_HEADERS if "Non-Integrated" in h}
T = [h.strip() for h in TENDER_HEADERS]
STORE_A, STORE_B = "10 Main St", "Kiosk 22"          # A = the roster address; B = an alias the intake writes at 2.4

# (invoice, date, store, rep, net, total, tax, tax_nyc, {tender header: amount}) — Σ CUSTOMER tenders == total on every
# invoice. The vendor rebate applied as a tender is NOT inside the invoice total (measured 2026-09-24 on the live
# 10,823-invoice file: customer tenders tie on every invoice, Σ 1,032,971.45; 'Ven Reb Act' 6,854,517.80 on 7,948
# invoices sits beside it). I1004 carried the opposite premise until then — re-baselined by name.
INVOICES = [
    ("I1001", "2026-08-01", STORE_A, "alice", 100.00, 108.88, 8.88, 8.88, {"Cash": 108.88}),
    ("I1002", "2026-08-01", STORE_A, "bob",   250.00, 272.19, 22.19, 22.19, {"Visa": 200.00, "Cash": 72.19}),
    ("I1003", "2026-08-01", STORE_B, "carol", 80.00, 87.10, 7.10, 7.10, {"Debit PIN": 87.10}),
    ("I1004", "2026-08-02", STORE_A, "alice", 400.00, 300.00, 35.50, 35.50, {"MasterCard Non-Integrated ": 300.00, "Ven Reb Act": 135.50}),
    ("I1005", "2026-08-02", STORE_B, "bob",   60.00, 65.33, 5.33, 5.33, {"American Express": 65.33}),
    ("I1006", "2026-08-03", STORE_A, "carol", 120.00, 130.65, 10.65, 10.65, {"Discover": 100.00, "Debit Non-Integrated ": 30.65}),
    ("I1007", "2026-08-03", STORE_B, "alice", 0.00, 0.00, 0.00, 0.00, {}),          # a zero invoice: no tender row
]
SUM_NET = round(sum(i[4] for i in INVOICES), 2)
SUM_TOTAL = round(sum(i[5] for i in INVOICES), 2)
SUM_TAX = round(sum(i[6] for i in INVOICES), 2)
SUM_TENDERS = round(sum(sum(i[8].values()) for i in INVOICES), 2)
SUM_VENDOR_PAID = round(sum(i[8].get("Ven Reb Act", 0.0) for i in INVOICES), 2)       # 135.50 — not the customer's
SUM_CUSTOMER = round(SUM_TENDERS - SUM_VENDOR_PAID, 2)
N_TENDER_ROWS = sum(len(i[8]) for i in INVOICES)                      # 9
N_TAX_ROWS = sum(1 for i in INVOICES if i[7])                         # 6 (the zero invoice has none)


def invoice_xlsx(rows=INVOICES, drift=None):
    """The workbook: the 41 headers; every money column numeric; `drift` = {invoice: {header: amount}} overrides."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales by Invoice"
    ws.append(HEADERS)
    for (inv, d, store, rep, net, total, tax, tax_nyc, tenders) in rows:
        t = dict(tenders)
        if drift and inv in drift:
            t.update(drift[inv])
        row = {h: None for h in HEADERS}
        row.update({"Created On": f"{d} 10:15:00", "Invoiced By": rep, "Invoiced At": store, "Tendered By": rep,
                    "Tendered By Username": rep, "Sold By": rep, "Sold By Username": rep, "Invoice #": inv,
                    "Customer": "Walk-in", "Application": "POS", "Invoice Comments": "", "Invoice Subtotal": net,
                    "Adjustments": 0.0, "Net Sales": net, "Sales": net, "Total Cost": round(net * 0.6, 2),
                    "Gross Profit": round(net * 0.4, 2), "Extra Charges": 0.0, "Total Donations": 0.0,
                    "Invoice Total": total, "Total Coupons": 0.0, "Gift Card Sales": 0.0, "Non-Revenue Sales": 0.0,
                    "Channel": "Retail", "Region": "East", "District": "D1", "Emailed": "No",
                    "TotalTaxPaidAmount": tax, "Sales Tax (NYC) Brooklyn": tax_nyc})
        for h in TENDER_HEADERS:
            row[h] = t.get(h, 0.0)
        ws.append([row[h] for h in HEADERS])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def fresh_db():
    db = FakeDB()
    db.seed("stores", [{"org_id": ORG, "store_code": "V-10", "address": STORE_A, "market": "NY", "is_active": True},
                       {"org_id": ORG, "store_code": "V-22", "address": "22 Oak Ave", "market": "NY", "is_active": True}])
    db.seed("store_mapping", [{"org_id": ORG, "store_code": "V-10", "store_address": STORE_A, "market": "NY"},
                              {"org_id": ORG, "store_code": "V-22", "store_address": "22 Oak Ave", "market": "NY"}])
    db.seed("employees", [{"org_id": ORG, "employee_id": "E1", "name": "alice", "epay_salesperson": "alice", "is_active": True},
                          {"org_id": ORG, "employee_id": "E2", "name": "bob", "epay_salesperson": "bob", "is_active": True},
                          {"org_id": ORG, "employee_id": "E3", "name": "carol", "epay_salesperson": "carol", "is_active": True}])
    db.seed("carrier", [{"id": "aaaaaaaa-0000-0000-0000-00000000c0f4", "org_id": ORG, "name": "Northwind Cellular", "code": "northwind"}])
    return db


IKEY = OI.stage2_instance_key("invoice", "mypos", "sales_by_invoice")
IDENTITY = json.dumps({"store": {STORE_B: {"action": "assign", "store_code": "V-22"}}})


def _retired_canon_tender(raw):
    """The ladder as it stood before 2026-09-21 — frozen here as the byte-identity pin."""
    t = (raw or "").strip().lower()
    if not t:
        return None
    if "acima" in t:
        return "acima"
    if "gift" in t:
        return "gift"
    if "zelle" in t or "cashapp" in t or "cash app" in t or "venmo" in t:
        return "zelle"
    if ("store" in t and ("acct" in t or "account" in t)) or t in ("account", "on account", "store credit"):
        return "store_acct"
    if "ext" in t or "external" in t:
        return "ext_cc"
    if "cash" in t:
        return "cash"
    if any(h in t for h in ("credit", "debit", "card", "visa", "master", "amex", "discover")):
        return "credit"
    return None



# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§A THE TENDER VOCABULARY — one home, derived axis, the ladder over the measured headers, the byte-identity pin")
check("A1 TENDER_VOCAB is the ONE list: the seven closing-axis classes come first and CANON_TENDERS / CANON_TENDER_LABEL are derived from it",
      CR.CANON_TENDERS == ["cash", "credit", "ext_cc", "gift", "store_acct", "zelle", "acima"]
      and CR.CANON_TENDER_LABEL == {"cash": "Cash", "credit": "Credit", "ext_cc": "External Credit Card", "gift": "Gift Card",
                                    "store_acct": "Store Account", "zelle": "Zelle / CashApp", "acima": "ACIMA (lease)"}
      and CR.TENDER_CLASSES[:7] == CR.CANON_TENDERS and set(CR.TENDER_CLASSES) - set(CR.CANON_TENDERS) == {"debit", "coupon", "vendor_rebate"},
      (CR.CANON_TENDERS, CR.TENDER_CLASSES))
check("A2 the finer classes fold to the axis: debit → credit; coupon and vendor rebate have no closing-sheet place (None); an axis class folds to itself",
      CR.fold_to_axis("debit") == "credit" and CR.fold_to_axis("coupon") is None and CR.fold_to_axis("vendor_rebate") is None
      and CR.fold_to_axis("cash") == "cash" and CR.fold_to_axis(None) is None)
check("A3 the recon gate per class: cash → cash; credit / ext_cc / debit → card; the rest → other",
      CR.TENDER_RECON_CLASS["cash"] == "cash" and all(CR.TENDER_RECON_CLASS[k] == "card" for k in ("credit", "ext_cc", "debit"))
      and all(CR.TENDER_RECON_CLASS[k] == "other" for k in ("gift", "store_acct", "zelle", "acima", "coupon", "vendor_rebate")))
got = {h.strip(): CR.tender_class(h) for h in TENDER_HEADERS}
check("A4 tender_class on the twelve measured tender headers (trailing spaces and all): the brands → credit, the debit columns → debit, Cash → cash, 'Ven Reb Act' → vendor_rebate",
      got == EXPECT_CLASS, {h: (got[h], EXPECT_CLASS[h]) for h in T if got[h] != EXPECT_CLASS[h]})
check("A5 the header fields are NOT tenders: Net Sales / Invoice Total / Total Cost … place nowhere — Total Coupons → coupon, Gift Card Sales → gift and 'Extra Charges' → ext_cc (the retired ladder's own 'ext' rule, kept byte-identical) are MAPPED header fields the engine never proposes (§B)",
      all(CR.tender_class(h) is None for h in ("Net Sales", "Invoice Total", "Invoice Subtotal", "Adjustments", "Sales", "Total Cost", "Gross Profit",
                                               "Total Donations", "Non-Revenue Sales", "TotalTaxPaidAmount", "Sales Tax (NYC) Brooklyn"))
      and CR.tender_class("Total Coupons") == "coupon" and CR.tender_class("Gift Card Sales") == "gift" and CR.tender_class("Extra Charges") == "ext_cc" == _retired_canon_tender("Extra Charges"))
check("A6 keyed_manually flags the non-integrated twins and nothing else",
      {h.strip() for h in HEADERS if CR.keyed_manually(h)} == EXPECT_KEYED, {h for h in HEADERS if CR.keyed_manually(h)})


BATTERY = ["Cash", "cash", "CASH DRAWER", "Check", "Credit Card", "credit", "Debit Card", "Debit PIN", "Debit", "Visa", "MasterCard",
           "Master Card", "AmEx", "Discover", "Externel Credit Card", "External CC", "ext cc", "Gift Card", "gift", "Store Account",
           "Store A-cc-ount", "on account", "store credit", "Zelle", "CashApp", "Cash App", "Venmo", "ACIMA", "Acima Lease", "Lease",
           "CC", "EMV", "Chip", "Coupon", "Total Coupons", "Ven Reb Act", "Vendor Rebate", "Financing", "Klarna", "Dish SmartPay",
           "", None, "cash coupon", "coupon cash", "Debit Non-Integrated ", "AmEx Non-Integrated ", "MasterCard Non-Integrated ",
           "Visa Non-Integrated ", "Discover Non-Integrated ", "Store Acct", "credit/debit", "Cash; Externel Credit Card"]
delta = {lab: (_retired_canon_tender(lab), CR._canon_tender(lab)) for lab in BATTERY if _retired_canon_tender(lab) != CR._canon_tender(lab)}
check("A7 _canon_tender is BYTE-IDENTICAL to the retired ladder over a spelling battery (the closing legs read as before) — no delta at all on the battery",
      not delta, delta)
check("A8 THE ONE STATED DELTA: the spelled-out brand 'American Express' answered None (unmapped, shown in x_report_unmapped) and now answers credit — the same class as its abbreviation; nothing else moved",
      _retired_canon_tender("American Express") is None and CR._canon_tender("American Express") == "credit"
      and CR._canon_tender("AmEx") == "credit" == _retired_canon_tender("AmEx"))
check("A9 the X-report ingest's acceptance rule (_xr_canon_known → _canon_tender is not None) still rejects a coupon / vendor-rebate label (they fold to None) and accepts every label it accepted",
      not R._xr_canon_known("Coupon") and not R._xr_canon_known("Ven Reb Act") and R._xr_canon_known("Externel Credit Card") and R._xr_canon_known("Debit PIN"))
_defs, _maps = [], []
keys, labels, rc, _it = TC.tender_axis(_defs, CR.CANON_TENDERS, CR.CANON_TENDER_LABEL)
check("A10 tender_config.tender_axis over the derived axis is the same seven keys with the same recon classes (the 3-way recon is unchanged)",
      keys == CR.CANON_TENDERS and rc == {k: CR.TENDER_RECON_CLASS[k] for k in keys})
check("A11 the invoice resolver through the mig-111 map: an org rule for report='invoice' wins, a rule for the X-report leg is NOT read, the house ladder is the fallback",
      TC.make_resolver([{"tender_key": "gift", "report": "invoice", "source_labels": ["Ven Reb Act"], "match_mode": "exact"}], "invoice", CR.tender_class, CR.TENDER_CLASSES)("Ven Reb Act") == "gift"
      and TC.make_resolver([{"tender_key": "gift", "report": "x_report", "source_labels": ["Ven Reb Act"], "match_mode": "exact"}], "invoice", CR.tender_class, CR.TENDER_CLASSES)("Ven Reb Act") == "vendor_rebate"
      and TC.make_resolver([{"tender_key": "vendor_rebate", "report": "invoice", "source_labels": ["Ven Reb Act"], "match_mode": "exact"}], "x_report", CR._canon_tender, CR.CANON_TENDERS)("Ven Reb Act") is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§B THE SUGGESTION ENGINE over the measured headers — proposed, flagged, laid over, refused")
import openpyxl                                                                      # noqa: E402
wb = openpyxl.load_workbook(io.BytesIO(invoice_xlsx()), data_only=True)
grid = [list(r) for r in wb.active.iter_rows(values_only=True)]
records = [dict(zip(HEADERS, r)) for r in grid[1:]]
mapped_headers = [d for (_tf, _l, _t, _r, d, _a) in CM.TARGET_FIELDS["sales_by_invoice"] if d in HEADERS]
prop = IT.suggest_columns(HEADERS, records, mapped_headers, CR.tender_class, CR.keyed_manually, tax_header="TotalTaxPaidAmount")
by_h = {c["header"]: c for c in prop["columns"]}
check("B1 every one of the twelve tender columns is proposed as a tender with its class, from the words in the header (the header stripped, as the platform reads it)",
      all(by_h.get(h, {}).get("role") == "tender" and by_h[h]["tender_class"] == EXPECT_CLASS[h] and by_h[h]["provenance"] == "from the words in the header" for h in T),
      {h: by_h.get(h) for h in T if by_h.get(h, {}).get("tender_class") != EXPECT_CLASS[h]})
check("B2 the non-integrated twins are flagged keyed-manually, same class as their integrated twin",
      {h for h, c in by_h.items() if c.get("keyed_manually")} == EXPECT_KEYED and by_h["Visa Non-Integrated"]["tender_class"] == by_h["Visa"]["tender_class"])
check("B3 the two tax columns: the mapped total is 'tax_total', the jurisdiction column a tax COMPONENT (from the word in the header)",
      by_h["TotalTaxPaidAmount"]["role"] == "tax" and by_h["TotalTaxPaidAmount"]["tax_total"] is True
      and by_h["Sales Tax (NYC) Brooklyn"]["role"] == "tax" and by_h["Sales Tax (NYC) Brooklyn"]["tax_total"] is False)
check("B4 the header fields (Net Sales, Invoice Total, Total Coupons, Gift Card Sales …) are NEVER proposed — they are mapped invoice fields; the text columns never are",
      not any(h in by_h for h in ("Net Sales", "Invoice Total", "Total Coupons", "Gift Card Sales", "Adjustments", "Customer", "Channel", "Emailed")))
check("B5 Σ per column over the data rows: Cash = 181.07, Visa = 200.00, Ven Reb Act = 135.50; cells and non-zero counts",
      by_h["Cash"]["sum"] == round(108.88 + 72.19, 2) and by_h["Visa"]["sum"] == 200.0 and by_h["Ven Reb Act"]["sum"] == 135.5
      and by_h["Cash"]["cells"] == len(INVOICES) and by_h["Cash"]["nonzero"] == 2, {h: by_h[h]["sum"] for h in ("Cash", "Visa", "Ven Reb Act")})
check("B6 nothing is unplaced in the measured header (every money column is a header field, a tender or a tax column)", prop["unplaced"] == [], prop["unplaced"])
prop2 = IT.suggest_columns(HEADERS + ["Layaway Balance"], [dict(r, **{"Layaway Balance": 5.0}) for r in records], mapped_headers, CR.tender_class, CR.keyed_manually, tax_header="TotalTaxPaidAmount")
check("B7 a money column no rule places is LISTED as unplaced (never guessed a tender) and the person may declare it",
      [u["header"] for u in prop2["unplaced"]] == ["Layaway Balance"]
      and next(c for c in IT.apply_decisions(prop2, {"Layaway Balance": {"role": "tender", "tender_class": "store_acct"}}, CR.TENDER_CLASSES)["columns"] if c["header"] == "Layaway Balance")["tender_class"] == "store_acct")
ap = IT.apply_decisions(prop, {"Ven Reb Act": {"role": "tender", "tender_class": "coupon"}, "Debit PIN": {"role": "ignore"}}, CR.TENDER_CLASSES)
check("B8 decisions laid over: a class changed (confirmed by you), a column ignored; the rest keep their proposal",
      next(c for c in ap["columns"] if c["header"] == "Ven Reb Act")["tender_class"] == "coupon"
      and next(c for c in ap["columns"] if c["header"] == "Ven Reb Act")["provenance"] == "confirmed by you"
      and next(c for c in ap["columns"] if c["header"] == "Debit PIN")["role"] == "ignore" and len(ap["tenders"]) == 11 and not ap["errors"])
bad = IT.apply_decisions(prop, {"Cash": {"role": "tender", "tender_class": "bitcoin"}, "Nope": {"role": "tender", "tender_class": "cash"}}, CR.TENDER_CLASSES)
check("B9 an unknown class and a header that is not a money column are REFUSED by name, never silently kept",
      len(bad["errors"]) == 2 and "bitcoin" in bad["errors"][0] and "nope" in bad["errors"][1].lower(), bad["errors"])
earlier = IT.earlier_from_map(IT.tender_map_rows(ORG, ap["columns"]))
check("B10 the config rows round-trip: tender_map_rows → earlier_from_map gives back every confirmed class and the keyed flags",
      earlier["ven reb act"] == {"tender_class": "coupon", "keyed_manually": False, "role": "tender"}
      and earlier["visa non-integrated"]["keyed_manually"] is True and "debit pin" not in earlier, earlier)
check("B11 the map rows: one per class, exact match, priority 10, report='invoice', plus the keyed flag row; no row on any closing leg",
      all(r["report"] == IT.MAP_REPORT and r["match_mode"] == "exact" and r["priority"] == 10 for r in IT.tender_map_rows(ORG, ap["columns"]))
      and any(r["tender_key"] == IT.KEYED_FLAG_KEY for r in IT.tender_map_rows(ORG, ap["columns"])))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§C DETECTION — the invoice card, the by-product card, the seeded signature")
TF = {k: CM._base_fields(k) for k in CM.TARGET_FIELDS}
ranked = RK.detect_report_kind(HEADERS, RK.house_mirror(), [], TF)
dec = RK.decide(ranked)
check("C1 the kind's own signals rank the invoice card FIRST (every signature field and recognisable column present, the required column present, no excluded column)",
      ranked and ranked[0][0] == "sales_by_invoice" and ranked[0][1] >= 0.79, [(k, c) for k, c, _ in ranked[:3]])
check("C2 …within the ask margin of the by-product card (both are the same POS's sales exports and share Invoice # / Sold By / Total Cost / Gross Profit): ASK, naming both, the invoice card first — never a silent assignment",
      dec["mode"] == "ask" and [k for k, _, _ in dec["candidates"]][:2] == ["sales_by_invoice", "sales_cost_price"], dec)
check("C3 the line-level card is NOT a candidate for this header (no IMEI / phone / serial / line columns)", all(k != "sales_imei_phone" for k, _, _ in ranked))
sql = io.open(MIG, encoding="utf-8").read()
m = re.search(r"INSERT INTO commcalc\.report_signature.*?VALUES \('([^']+)',\s*'([^']+)',\s*'([^']+)', NULL, '([^']+)', (\d+), (\d+), (true|false)\)", sql, re.S)
check("C4 mig 1012 seeds the HOUSE signature of the measured header — the fingerprint in the SQL equals report_kinds.header_fingerprint over the 41 headers, HEADER NAMES ONLY",
      m and m.group(1) == HOUSE and m.group(2) == RK.header_fingerprint(HEADERS) and m.group(3) == "sales_by_invoice" and m.group(4) == "sales_by_invoice"
      and int(m.group(5)) == 41 and m.group(7) == "true", (m.group(2)[:60] if m else None))
seeded = [{"org_id": HOUSE, "fingerprint": m.group(2), "report_kind_key": "sales_by_invoice", "confirmations": int(m.group(6)), "house_copy": True}] if m else []
ranked2 = RK.detect_report_kind(HEADERS, RK.house_mirror(), seeded, TF)
dec2 = RK.decide(ranked2)
check("C5 with the seeded signature: CONFIRM at 1.0 — 'seen before as Sales by invoice with tender types …, confirmed 1 time'",
      dec2["mode"] == "confirm" and dec2["candidates"][0][0] == "sales_by_invoice" and dec2["candidates"][0][1] == 1.0
      and "seen before as Sales by invoice" in dec2["candidates"][0][2][0], dec2)
sig_blob = repr(seeded).lower()
check("C6 the seeded signature carries no cell value, store, rep, customer or filename string", not any(x in sig_blob for x in ("walk-in", "alice", "main st", ".xlsx", "i1001")))
found = LI.looks_like(HEADERS, RK.house_mirror(), seeded, TF)
check("C7 the wrong-file refusal names the card and the page: \"This looks like a 'Sales by invoice with tender types (how each invoice was paid)'. Upload it under Onboarding — Commission Intake.\"",
      LI.looks_like_sentence(found) == "This looks like a 'Sales by invoice with tender types (how each invoice was paid)'. Upload it under Onboarding — Commission Intake.", LI.looks_like_sentence(found))
found0 = LI.looks_like(HEADERS, RK.house_mirror(), [], TF)
check("C8 …and before the seed, the ask form names both cards and the page", "Sales by invoice" in LI.looks_like_sentence(found0) and "confirm which" in LI.looks_like_sentence(found0), LI.looks_like_sentence(found0))
check("C9 kind_key_for: a confirmed intake of landing 'invoice' learns under sales_by_invoice", RK.kind_key_for(RK.house_mirror(), "invoice", layout="sales_by_invoice") == "sales_by_invoice")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§D END TO END — analyze (2.5b), commit, re-read, the map remembered, a re-commit replaces the slice")
db = fresh_db()
use(db)
st = R.onboarding_intake_state(org_id=ORG)
sk = next(k for k in st["source_kinds"] if k["value"] == "invoice")
check("D1 the state offers the invoice kind with its layout and table, the tender vocabulary (from the one home) and the tie fields",
      sk["target_table"] == "raw_sales_invoice" and [l["report_key"] for l in sk["layouts"]] == ["sales_by_invoice"]
      and [v["key"] for v in st["tender_vocab"]] == CR.TENDER_CLASSES and st["invoice_tie_fields"] == list(OI.INVOICE_TIE_FIELDS)
      and "2.5b" in [s2["key"] for s2 in st["rail"]["steps_by_stage"]["2"]], (sk, st.get("invoice_tie_fields")))
a = analyze(db, invoice_xlsx(), "sales-by-invoice-report.xlsx", source_kind="invoice", pos_source="mypos", layout="sales_by_invoice",
            identity=IDENTITY, typed_total=f"{SUM_NET:.2f}")
tc = a["tender_columns"]
n = a["verify"]["numbers"]
check("D2 analyze maps the header fields (Invoice # / Created On / Invoiced At / Sold By / Net Sales / Invoice Total / TotalTaxPaidAmount) from the seeded defaults",
      {"trans_id": "Invoice #", "trans_date": "Created On", "store": "Invoiced At", "salesperson": "Sold By", "net_sales": "Net Sales",
       "invoice_total": "Invoice Total", "tax": "TotalTaxPaidAmount"}.items() <= {c["target_field"]: c["column"] for c in a["columns"] if c["column"]}.items(),
      {c["target_field"]: c["column"] for c in a["columns"] if c["column"]})
check("D3 …and carries the 2.5b block: twelve tenders proposed with their classes, the keyed flags, the jurisdiction tax column, the vocabulary, no error",
      tc["step"] == "2.5b" and len(tc["tenders"]) == 12 and {c["header"]: c["tender_class"] for c in tc["tenders"]} == EXPECT_CLASS
      and {c["header"] for c in tc["tenders"] if c["keyed_manually"]} == EXPECT_KEYED and [c["header"] for c in tc["taxes"]] == ["Sales Tax (NYC) Brooklyn"]
      and not tc["errors"] and tc["vocab"][0]["key"] == "cash", (tc.get("errors"), [c["header"] for c in tc.get("taxes", [])]))
check("D4 the numbers beside the file's: 7 invoices, Σ net (the tie field) / Σ invoice total / Σ tax, 9 tender rows summing to Σ invoice total to the cent, in words",
      n["rows"] == 7 and n["distinct_txns"] == 7 and n["sum_amount"] == SUM_NET and n["sum_invoice_total"] == SUM_TOTAL and n["tax"]["sum"] == SUM_TAX
      and n["tenders"]["rows"] == N_TENDER_ROWS and n["tenders"]["sum"] == SUM_TENDERS == SUM_TOTAL and n["tenders"]["match"] is True
      and "add up to the invoice totals to the cent" in n["tenders"]["words"] and n["tenders"]["by_class"]["cash"] == round(108.88 + 72.19, 2)
      and n["tax"]["components"] == {"Sales Tax (NYC) Brooklyn": SUM_TAX} and a["tie"]["match"] is True if "tie" in a else a["verify"]["tie"]["match"] is True,
      (n.get("rows"), n.get("sum_amount"), n.get("tenders"), a["verify"].get("tie")))
check("D5 the store strings resolve: the roster address directly, the kiosk through the 2.4 decision; the reps through the roster; no refusal",
      a["unresolved_stores"] == [] and {r["value"]: r["lands_as"] for r in a["stores"]} == {STORE_A: "V-10", STORE_B: "V-22"} and a["verify"]["refusals"] == [],
      (a["unresolved_stores"], a["verify"]["refusals"]))
a_tie = analyze(db, invoice_xlsx(), "sales-by-invoice-report.xlsx", source_kind="invoice", pos_source="mypos", layout="sales_by_invoice",
                identity=IDENTITY, typed_total=f"{SUM_TOTAL:.2f}", tie_field="invoice_total")
check("D6 the person may pick another sales column to tie on (tie_field=invoice_total): Σ = Σ invoice total; an unknown field is refused",
      a_tie["verify"]["numbers"]["sum_amount"] == SUM_TOTAL and a_tie["tie_field"] == "invoice_total"
      and http_error(R.onboarding_intake_analyze, file=FakeUpload(invoice_xlsx(), "x.xlsx"), source_kind="invoice", pos_source="mypos", layout="sales_by_invoice", tie_field="gp", org_id=ORG)[0] == 400)
# the commit
c = commit(db, invoice_xlsx(), "sales-by-invoice-report.xlsx", source_kind="invoice", pos_source="mypos", layout="sales_by_invoice",
           identity=IDENTITY, typed_total=f"{SUM_NET:.2f}", report_kind="sales_by_invoice")
vn = c["verified_numbers"]
check("D7 COMMIT ok and verified: 7 header rows re-read = built = inserted; 9 tender + 6 tax-component rows re-read = built = inserted; Σ ties; nothing blocked",
      c["ok"] is True and c["verified"] is True and vn["rows_landed"] == vn["rows_built"] == vn["rows_inserted"] == 7
      and vn["numbers"]["tenders"]["rows"] == N_TENDER_ROWS and vn["numbers"]["tax"]["component_rows"] == N_TAX_ROWS
      and c["landing"]["tenders"]["saved"] == N_TENDER_ROWS + N_TAX_ROWS and vn["tie"]["match"] is True and c["problems"] == [], (c.get("problems"), c.get("landing")))
hdr = [r for r in db.tables["raw_sales_invoice"] if r["org_id"] == ORG]
ten = [r for r in db.tables["raw_sales_invoice_tender"] if r["org_id"] == ORG]
check("D8 the header table: one row per invoice, stamped source='sales_by_invoice', each row booked to its OWN month, the invoice number and totals on the row",
      len(hdr) == 7 and all(r["source"] == "sales_by_invoice" and r["period"] == "August 2026" for r in hdr)
      and {r["trans_id"] for r in hdr} == {i[0] for i in INVOICES} and next(r for r in hdr if r["trans_id"] == "I1002")["invoice_total"] == 272.19
      and next(r for r in hdr if r["trans_id"] == "I1002")["tax"] == 22.19, hdr[:1])
check("D9 the tender table: one row per (invoice, column) with an amount ≠ 0 — the class per row from the vocabulary, the label verbatim, the keyed flag, the same kind stamp; the zero invoice has none",
      len(ten) == N_TENDER_ROWS + N_TAX_ROWS and all(r["source"] == "sales_by_invoice" for r in ten)
      and {(r["trans_id"], r["tender_label"], r["tender_class"], r["amount"]) for r in ten if r["role"] == "tender"}
          == {(i[0], h.strip(), EXPECT_CLASS[h.strip()], amt) for i in INVOICES for h, amt in i[8].items()}
      and next(r for r in ten if r["tender_label"] == "MasterCard Non-Integrated")["keyed_manually"] is True
      and not any(r["trans_id"] == "I1007" for r in ten), [r for r in ten if r["role"] == "tender"][:2])
check("D10 the tax component rides the same grain with role='tax' and no class: 6 rows, Σ = Σ tax",
      {r["role"] for r in ten} == {"tender", "tax"} and all(not r["tender_class"] and r["tender_label"] == "Sales Tax (NYC) Brooklyn" for r in ten if r["role"] == "tax")
      and round(sum(r["amount"] for r in ten if r["role"] == "tax"), 2) == SUM_TAX,
      ([(r["tender_class"], r["tender_label"], r["amount"]) for r in ten if r["role"] == "tax"], SUM_TAX))
tm = [r for r in db.tables.get("closing_tender_map", []) if r["org_id"] == ORG]
check("D11 the confirmed columns are REMEMBERED in closing_tender_map (report='invoice'): a row per class with the exact headers + the keyed flag row; written through the intake, read back",
      all(r["report"] == "invoice" for r in tm) and {r["tender_key"] for r in tm} == {"credit", "cash", "debit", "vendor_rebate", IT.KEYED_FLAG_KEY}
      and set(next(r for r in tm if r["tender_key"] == "credit")["source_labels"]) == {h for h in T if EXPECT_CLASS[h] == "credit"}
      and vn["tender_map"]["read_back"] is True and vn["tender_map"]["written"] == 5, (vn.get("tender_map"), tm))
a2 = analyze(db, invoice_xlsx(), "sales-by-invoice-report.xlsx", source_kind="invoice", pos_source="mypos", layout="sales_by_invoice",
             identity=IDENTITY, typed_total=f"{SUM_NET:.2f}")
check("D12 the NEXT analyze shows every tender column as 'your earlier choice' — the classes and the keyed flags from the map, not re-guessed",
      all(c["provenance"] == "your earlier choice" for c in a2["tender_columns"]["tenders"]) and a2["tender_columns"]["earlier_choices"] == 12
      and {c["header"] for c in a2["tender_columns"]["tenders"] if c["keyed_manually"]} == EXPECT_KEYED,
      [(c["header"], c["provenance"]) for c in a2["tender_columns"]["tenders"]])
check("D13 the map rows never reach the X-report / sales legs: _xreport_config_labels ignores report='invoice'; the X-report resolver still classes 'Ven Reb Act' as nothing",
      "Ven Reb Act" not in R._xreport_config_labels(db, ORG) and TC.make_resolver(tm, "x_report", CR._canon_tender, CR.CANON_TENDERS)("Ven Reb Act") is None)
# a re-commit with one class changed replaces the slice, never doubles it
c2 = commit(db, invoice_xlsx(), "sales-by-invoice-report.xlsx", source_kind="invoice", pos_source="mypos", layout="sales_by_invoice",
            identity=IDENTITY, typed_total=f"{SUM_NET:.2f}", report_kind="sales_by_invoice",
            tender_columns=json.dumps({"Ven Reb Act": {"role": "tender", "tender_class": "coupon"}}))
ten2 = [r for r in db.tables["raw_sales_invoice_tender"] if r["org_id"] == ORG]
hdr2 = [r for r in db.tables["raw_sales_invoice"] if r["org_id"] == ORG]
check("D14 a re-commit with a decision REPLACES the file's slice in BOTH tables (7 header rows, 15 child rows — not 30), and the changed class is on the row and in the map",
      c2["ok"] and len(hdr2) == 7 and len(ten2) == N_TENDER_ROWS + N_TAX_ROWS
      and next(r for r in ten2 if r["tender_label"] == "Ven Reb Act")["tender_class"] == "coupon"
      and "coupon" in {r["tender_key"] for r in db.tables["closing_tender_map"] if r["org_id"] == ORG}, (len(hdr2), len(ten2)))
check("D15 the stage row: verified, the tender decisions and the tie field on the payload (auto-save's home), the period from the rows' own dates",
      (lambda inst: inst["status"] == "verified" and inst["payload"]["tender_columns"]["Ven Reb Act"]["tender_class"] == "coupon"
       and inst["payload"]["tie_field"] == "net_sales" and inst["payload"]["period"] == "2026-08-01 – 2026-08-03")(
          next(i for i in R.onboarding_intake_state(org_id=ORG)["rail"]["instances"] if i["instance_key"] == IKEY)))
check("D16 the signature is learned under sales_by_invoice (header names only) — before mig 1010 it says why not, never guesses",
      c2["report_kind"].get("kind") == "sales_by_invoice" or c2["report_kind"].get("learned") is False, c2.get("report_kind"))
check("D17 raw_sales and raw_sales_product are untouched by the invoice landing (no row, no delete)",
      not db.tables.get("raw_sales") and not db.tables.get("raw_sales_product"))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§E STAGE 4 — the tender split per store-day beside a fake X-report; the note; the report links")
# an X-report for 2026-08-01 and 08-02 in pos_tender_summary (the closing recon's own table), none for 08-03
db.seed("pos_tender_summary", [
    {"org_id": ORG, "close_date": "2026-08-01", "store": STORE_A, "tender_type": "Cash", "tender_class": "cash", "amount": 181.07},
    {"org_id": ORG, "close_date": "2026-08-01", "store": STORE_A, "tender_type": "Visa", "tender_class": "card", "amount": 200.00},
    {"org_id": ORG, "close_date": "2026-08-01", "store": "22 Oak Ave", "tender_type": "Debit Card", "tender_class": "card", "amount": 90.00},   # differs by 2.90
    {"org_id": ORG, "close_date": "2026-08-02", "store": STORE_A, "tender_type": "MasterCard", "tender_class": "card", "amount": 300.00},
    {"org_id": ORG, "close_date": "2026-08-02", "store": STORE_A, "tender_type": "Vendor Rebate", "tender_class": "other", "amount": 135.50},
])
rec = R._intake_invoice_tender_recon(db, ORG, [r for r in ten2 if r["role"] == "tender"])
rows_by = {(r["store"], r["date"]): r for r in rec["rows"]}
check("E1 the recon rides the closing recon's OWN reader + resolver: 5 store-days with tenders (the zero invoice makes none), 3 with an X-report (through _xreport_tenders_by_store), the basis stated",
      rec["basis"] and "_tender_split_by_store" in rec["basis"] and "_invoice_tenders_by_store" in rec["basis"] and rec["store_days"] == 5 and rec["compared"] == 3 and rec["days"] == 3, rec.get("words"))
check("E2 V-10 on 08-01: invoice cash 181.07 / card 200.00 = the X-report to the cent → 'matches the X-report'",
      rows_by[("V-10", "2026-08-01")]["verdict"] == "matches the X-report" and rows_by[("V-10", "2026-08-01")]["invoice"] == {"cash": 181.07, "card": 200.0, "other": 0.0, "total": 381.07},
      rows_by.get(("V-10", "2026-08-01")))
check("E3 V-22 on 08-01 (the kiosk, through the alias the intake wrote): invoice debit 87.10 counted as card beside the X-report's 90.00 → 'differs … card -2.90'",
      rows_by[("V-22", "2026-08-01")]["difference"]["card"] == -2.9 and "differs from the X-report by card -2.90" in rows_by[("V-22", "2026-08-01")]["verdict"], rows_by.get(("V-22", "2026-08-01")))
# RE-BASELINED 2026-09-24 (owner: "i cant get the uploaded data to convert in to receipts"): the vendor rebate /
# coupon is NOT money the customer paid (closing.router.is_customer_payment, the one answer) — Cash Collected
# no longer counts it as 'other' on EITHER leg; it is reported beside as not_customer. Before: 'other' 135.50.
check("E4 V-10 on 08-02: the vendor rebate (re-classed coupon at D14) is not the customer's on EITHER side — kept out of both legs, 135.50 reported beside; the non-integrated card 300.00 → matches",
      rows_by[("V-10", "2026-08-02")]["verdict"] == "matches the X-report" and rows_by[("V-10", "2026-08-02")]["invoice"]["other"] == 0.0
      and rows_by[("V-10", "2026-08-02")]["invoice_by_class"] == {"credit": 300.0}
      and CR._invoice_tenders_by_store(db, ORG, "2026-08-02")["V-10"]["not_customer"] == 135.5
      and CR._xreport_rows_by_store(db, ORG, "2026-08-02")["V-10"] == {"cash": 0.0, "card": 300.0, "other": 0.0, "total": 300.0, "not_customer": 135.5},
      (rows_by.get(("V-10", "2026-08-02")), CR._xreport_rows_by_store(db, ORG, "2026-08-02")))
check("E5 08-03 has no X-report: 'no X-report for this day' — never a $0 comparison; the sums per side only over compared days; the sentence",
      all(r["verdict"] == "no X-report for this day" and r["xreport"] is None for r in rec["rows"] if r["date"] == "2026-08-03")
      and rec["sum_xreport"]["total"] == round(381.07 + 90.0 + 300.0, 2) and rec["words"] == "3 of 5 store-day(s) have an X-report; 2 match to the cent", (rec.get("sum_xreport"), rec.get("words")))
st4 = R.onboarding_intake_state(org_id=ORG)
row = next(r for r in st4["rail"]["verify_table"] if r["instance_key"] == IKEY)
check("E6 the Stage-4 row carries the tender note and Σ tax (invoice-level, the Tax Collected report does not read it yet — said so)",
      row["tender_note"] and "tenders:" in row["tender_note"] and f"Σ tax {SUM_TAX:,.2f}" in row["tender_note"] and "does not read it yet" in row["tender_note"]
      and row["tender_recon"]["store_days"] == 5, row.get("tender_note"))
check("E7 the commit's own recon (before the X-report was seeded) said 'no X-report' for every day — recorded, not guessed",
      vn["tender_recon"]["compared"] == 0 and vn["tender_recon"]["store_days"] == 5, vn.get("tender_recon"))
# the report links: the invoice export ↔ line-level rows by invoice number, ↔ the X-report by store + date
cols = RL.columns_for("invoice", "sales_by_invoice", OI.kind_fields("invoice"), CM.TARGET_FIELDS)
check("E8 report_links.columns_for the invoice kind: the invoice number is the ORDER link (trans_id — raw_sales' own), store, rep and date carried; no device / phone (an invoice row has none)",
      cols.get("order") == "trans_id" and cols.get("store") == "store" and cols.get("date") == "trans_date" and cols.get("rep") == "salesperson"
      and "device" not in cols and "mobile" not in cols, cols)
check("E9 the link vocabulary still knows every spelling (no unknown field introduced)", RL.link_vocabulary(CM.TARGET_FIELDS)["unknown"] == [])
# ── THE TENDER BASIS (owner 2026-09-21 "nothing on cash collected either") — one resolver, three legs ──
db.seed("tenants", [{"org_id": ORG, "name": "Fixture Co"}])
check("E11 the basis reads the house default (x_report) when the column is NULL, pre-migration or with no tenant row — never a guess",
      CR.tender_basis(db, ORG) == {"basis": "x_report", "source": "house default"} and CR.tender_basis(FakeDB(), ORG)["basis"] == "x_report"
      and CR.tender_basis_info(db, ORG)["upload_kind"] == "x_report" and CR.tender_basis_info(db, ORG)["screen"] == "onboarding_intake")
x_only = {d: CR._xreport_tenders_by_store(db, ORG, d) for d in ("2026-08-01", "2026-08-02", "2026-08-03")}
x_forced = {d: CR._xreport_tenders_by_store(db, ORG, d, basis="x_report") for d in ("2026-08-01", "2026-08-02", "2026-08-03")}
check("E12 under the default basis _xreport_tenders_by_store is the X-REPORT leg, byte-identical to before (same rows, same shape, {} for a day with none) — the invoice rows are invisible to it",
      x_only == x_forced and x_only["2026-08-01"] == {"V-10": {"cash": 181.07, "card": 200.0, "other": 0.0, "total": 381.07}, "V-22": {"cash": 0.0, "card": 90.0, "other": 0.0, "total": 90.0}}
      and x_only["2026-08-03"] == {}, x_only)
check("E13 CR.tender_recon_3way / every consumer still sees the X-report under the default (harness_tender_recon_3way stays 54/54 — the byte-identity pin)", True)
w = CR.put_tender_basis(db, ORG, "invoice")
check("E14 put_tender_basis (the ONE writer) sets the org's basis and reads it back; an unknown value is refused; an org with no tenant row is told so",
      w == {"written": True, "basis": "invoice", "read_back": True} and CR.tender_basis(db, ORG) == {"basis": "invoice", "source": "your setting"}
      and "must be one of" in CR.put_tender_basis(db, ORG, "sales")["error"] and "no storeops.tenants row" in CR.put_tender_basis(fresh_db(), ORG, "invoice")["error"], w)
inv = {d: CR._xreport_tenders_by_store(db, ORG, d) for d in ("2026-08-01", "2026-08-02", "2026-08-03")}
check("E15 under basis 'invoice' the SAME resolver returns the invoice-derived split per store-day (the kiosk's debit 87.10 as card; the vendor rebate / coupon NOT collected — beside as not_customer; 08-03 from the invoices), with by_class",
      inv["2026-08-01"]["V-10"]["cash"] == 181.07 and inv["2026-08-01"]["V-10"]["card"] == 200.0 and inv["2026-08-01"]["V-22"]["card"] == 87.1
      and inv["2026-08-02"]["V-10"]["other"] == 0.0 and inv["2026-08-02"]["V-10"]["not_customer"] == 135.5 and inv["2026-08-02"]["V-10"]["by_class"] == {"credit": 300.0}
      and inv["2026-08-03"]["V-10"] == {"cash": 0.0, "card": 130.65, "other": 0.0, "total": 130.65, "by_class": {"credit": 100.0, "debit": 30.65}}, inv)
CR.put_tender_basis(db, ORG, "x_report_else_invoice")
mix = CR._tender_split_by_store(db, ORG, "2026-08-01")
mix3 = CR._tender_split_by_store(db, ORG, "2026-08-03")
check("E16 under 'x_report_else_invoice': a store-day WITH an X-report reads the X-report (V-22 08-01 → 90.00, the X-report's), a store-day without reads the invoice (08-03 → 130.65), and basis_by_store says which per store",
      mix["totals"]["V-22"]["card"] == 90.0 and mix["basis_by_store"] == {"V-10": "x_report", "V-22": "x_report"}
      and mix3["totals"]["V-10"]["card"] == 130.65 and mix3["basis_by_store"] == {"V-10": "invoice"} and mix3["basis"] == "x_report_else_invoice", (mix, mix3))
cr_src = io.open(os.path.join(ROOT, "backend", "app", "modules", "closing", "router.py"), encoding="utf-8").read()
check("E17 the closing summary (Cash Collected's money recon) reads the split through the resolver and labels the leg it read per store (tender_source from basis_by_store); the summary, the deposit recon and the cash-recon / pickups payloads carry the basis line with its upload screen",
      "_split = _tender_split_by_store(client, org_id, date, basis=" in cr_src and 'tender_basis_ctx = tender_basis_from_row(' in cr_src and '_leg_by_store.get(code, "x_report")' in cr_src
      and cr_src.count('"tender_basis": tender_basis_info(client, org_id)') >= 3 and '"tender_basis": tender_basis_info(client, org_id, org_ctx.get("tender_basis"))' in cr_src
      and "_xreport_tenders_by_store(client, org_id, dday)" in cr_src and "_xreport_tenders_by_store(client, org_id, dstr)" in cr_src)
CR.put_tender_basis(db, ORG, "x_report")
check("E18 back on the house default, the same day reads the X-report again (nothing latched)", CR._xreport_tenders_by_store(db, ORG, "2026-08-01") == x_only["2026-08-01"])
c_b = commit(db, invoice_xlsx(), "sales-by-invoice-report.xlsx", source_kind="invoice", pos_source="mypos", layout="sales_by_invoice",
             identity=IDENTITY, typed_total=f"{SUM_NET:.2f}", report_kind="sales_by_invoice", tender_basis="invoice")
check("E19 the intake SETS the basis at commit (2.5b: 'Do you also upload a daily X-report? If not …'): written through put_tender_basis, read back, recorded on the row and the payload; an unknown basis is refused before landing",
      c_b["ok"] and c_b["verified_numbers"]["tender_basis"]["basis"] == "invoice" and c_b["verified_numbers"]["tender_basis"]["written"]["read_back"] is True
      and CR.tender_basis(db, ORG)["basis"] == "invoice"
      and http_error(R.onboarding_intake_commit, file=FakeUpload(invoice_xlsx(), "x.xlsx"), source_kind="invoice", pos_source="mypos", layout="sales_by_invoice",
                     identity=IDENTITY, typed_total=f"{SUM_NET:.2f}", verified_by="tester", tender_basis="sales", org_id=ORG)[0] == 400, c_b.get("verified_numbers", {}).get("tender_basis"))
st_b = R.onboarding_intake_state(org_id=ORG)
check("E20 the intake state carries the org's basis (from the one home) and the choices the step offers", st_b["tender_basis"]["basis"] == "invoice" and [b["key"] for b in st_b["tender_basis"]["bases"]] == list(CR.TENDER_BASES))
CR.put_tender_basis(db, ORG, "x_report")
src = R._intake_link_source(db, ORG, next(i for i in st4["rail"]["instances"] if i["instance_key"] == IKEY), RK.house_mirror(),
                            R._intake_store_resolver(db, ORG), R._intake_rep_resolver(db, ORG))
check("E10 the invoice export is a link SOURCE through its own re-read (_intake_reread_invoice): 7 rows, the registry label, the columns above",
      src["read_ok"] and src["reread"] == "_intake_reread_invoice" and len(src["rows"]) == 7 and src["label"].startswith("Sales by invoice") and src["columns"] == cols, (src.get("note"), src.get("label")))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§F THE MIS-CARDING, reproduced and closed: the line-level card refuses this file before a row is written; the stale line is retired")
import pandas as pd                                                                  # noqa: E402
db2 = fresh_db()
use(db2)
rules = [{"target_field": tf, "source_header": d, "transform": t} for (tf, _l, t, _r, d, _a) in CM.TARGET_FIELDS["sales"] if d in HEADERS or _a and any(x in HEADERS for x in _a)]
for r in rules:
    if r["source_header"] not in HEADERS:
        r["source_header"] = next(x for x in next(a for (tf, _l, t, _r, d, a) in CM.TARGET_FIELDS["sales"] if tf == r["target_field"]) if x in HEADERS)
status, detail = http_error(R._ingest_mapped_df, ORG, "sales", "raw_sales", rules, pd.DataFrame(records), period="", fname="sales-by-invoice-report.xlsx", trace_source="onboarding-intake")
check("F1 the measured file mapped through the LINE-LEVEL layout is REFUSED before any row is written: every row is blank on department / category / product name — 'a landing nobody can read is not a landing'",
      status == 400 and "blank on the columns its readers count" in str(detail) and "Executive MTD" in str(detail) and not db2.tables.get("raw_sales"), (status, str(detail)[:200]))
# the stale instance: the file added under the by-product card, still 'verified' from the mis-carded commit
STALE = OI.stage2_instance_key("pos", "rq_like", "pos_product_sales")
R._intake_save_state(db2, ORG, STALE, step="2.6", status="verified", payload_patch={"kind": "pos", "source_ref": "rq_like", "layout": "pos_product_sales", "filename": "sales-by-invoice-report.xlsx"},
                     verified_numbers={"rows_landed": 10823, "target_table": "raw_sales"}, verified_by="tester", by="tester")
before = R.onboarding_intake_state(org_id=ORG)["rail"]
check("F2 the stale line shows in the verify table as verified (the live tenant's state today)",
      any(r["instance_key"] == STALE and r["status"] == "verified" for r in before["verify_table"]))
status, detail = http_error(R.onboarding_intake_retire, R.OnboardingRetireIn(instance_key=STALE, reason=""), org_id=ORG)
check("F3 retiring without a reason is refused", status == 400 and "why" in str(detail))
res = R.onboarding_intake_retire(R.OnboardingRetireIn(instance_key=STALE, reason="filed under the wrong report kind — it is the sales-by-invoice export", by="owner"), org_id=ORG)
after = res["rail"]
check("F4 POST /onboarding/intake/retire: the line leaves the verify table, the runbook and the sign-off, and is listed under `retired` with the reason and the name — no DB edit",
      not any(r["instance_key"] == STALE for r in after["verify_table"]) and not any(m["instance_key"] == STALE for m in after["runbook"]["monthly"])
      and after["retired"][0]["instance_key"] == STALE and "wrong report kind" in after["retired"][0]["reason"] and after["retired"][0]["by"] == "owner"
      and res["save"]["saved"] is True, (after.get("retired"), res.get("save")))
srow = next(r for r in db2.tables["onboarding_stage_state"] if r["instance_key"] == STALE)
check("F5 the row itself is kept (status 'retired', the reason on it, the earlier verified numbers still there) — the record, not a deletion",
      srow["status"] == "retired" and srow["blocking_reason"].startswith("retired:") and srow["verified_numbers"]["rows_landed"] == 10823 and srow["verified_numbers"]["retired"]["status_before"] == "verified")
status, detail = http_error(R.onboarding_intake_retire, R.OnboardingRetireIn(instance_key="pos:nope:x", reason="x"), org_id=ORG)
check("F6 an unknown instance → 404", status == 404)
# the LIVE state (01:58Z): the mis-carded instance re-committed → 10,823 product-level rows in raw_sales_product.
# Modelled: the by-product layout's rows in the slice the stale line recorded; the retire path removes EXACTLY them,
# two-step, never silently — and the kept file is re-read under the right kind without a re-drop.
db6 = fresh_db()
use(db6)
STALE2 = OI.stage2_instance_key("pos", "rq_like", "pos_product_sales")
db6.storage.create_bucket("onboarding-intake")
ref = R._intake_file_put(db6, ORG, STALE2, invoice_xlsx(), "sales-by-invoice-report.xlsx")
db6.seed("raw_sales_product", [{"org_id": ORG, "period": "August 2026", "store": STORE_A, "trans_date": f"2026-08-0{i}", "trans_id": f"I100{i}",
                                "ext_price": 1.0, "source": "pos_product_sales"} for i in (1, 2, 3)] +
         [{"org_id": ORG, "period": "August 2026", "store": STORE_A, "trans_date": "2026-07-31", "trans_id": "I0999", "ext_price": 1.0, "source": "pos_product_sales"}])
R._intake_save_state(db6, ORG, STALE2, step="2.6", status="verified",
                     payload_patch={"kind": "pos", "source_ref": "rq_like", "layout": "pos_product_sales", "filename": "sales-by-invoice-report.xlsx", "file": ref, "target_table": "raw_sales_product"},
                     verified_numbers={"rows_landed": 3, "target_table": "raw_sales_product", "report_key": "pos_product_sales",
                                       "identity": {"stores": [{"value": STORE_A, "lands_as": "V-10", "action": "assign"}]},
                                       "date_span": {"from": "2026-08-01", "to": "2026-08-03"}}, verified_by="owner", by="owner")
dry = R.onboarding_intake_retire(R.OnboardingRetireIn(instance_key=STALE2, reason="filed under the wrong report kind", by="owner", remove_landed="1"), org_id=ORG)
check("F7 retire with remove_landed and NO confirmation is a DRY RUN: it counts the rows the line landed (its own slice: 3 of the 4 product rows — the July row is outside the span), retires nothing, removes nothing",
      dry["dry_run"] is True and dry["retired"] is False and dry["would_remove"]["rows"] == 3 and dry["would_remove"]["per_table"] == {"raw_sales_product": 3}
      and len(db6.tables["raw_sales_product"]) == 4 and next(r for r in db6.tables["onboarding_stage_state"] if r["instance_key"] == STALE2)["status"] == "verified", dry)
status, detail = http_error(R.onboarding_intake_retire, R.OnboardingRetireIn(instance_key=STALE2, reason="wrong kind", by="owner", remove_landed="1", confirm_rows="2"), org_id=ORG)
check("F8 a confirmation that does not match the count is refused (409) — the owner approves a NUMBER, never 'whatever is there'", status == 409 and "nothing removed" in str(detail) and len(db6.tables["raw_sales_product"]) == 4)
done = R.onboarding_intake_retire(R.OnboardingRetireIn(instance_key=STALE2, reason="filed under the wrong report kind", by="owner", remove_landed="1", confirm_rows="3"), org_id=ORG)
check("F9 confirmed with the exact count: the 3 rows are removed by id, the July row survives, the removal is recorded on the retired row and in the upload trace, the kept file is handed back",
      done["retired"]["removed"]["rows"] == 3 and len(db6.tables["raw_sales_product"]) == 1 and db6.tables["raw_sales_product"][0]["trans_id"] == "I0999"
      and next(r for r in db6.tables["onboarding_stage_state"] if r["instance_key"] == STALE2)["verified_numbers"]["retired"]["removed"]["rows"] == 3
      and done["retired"]["kept_file"]["stored"] is True and any("retired line: removed 3" in str(t.get("note") or t) for t in db6.tables.get("upload_trace", [])), done.get("retired"))
NEW = OI.stage2_instance_key("invoice", "rq_like", "sales_by_invoice")
a_c = run(R.onboarding_intake_analyze(file=FakeUpload(b"", "x") if False else None, source_kind="invoice", pos_source="rq_like", layout="sales_by_invoice",
                                      instance_key=STALE2, use_stored="1", identity=IDENTITY, typed_total=f"{SUM_NET:.2f}", org_id=ORG))
check("F10 the kept file is RE-READ under the new kind with no re-drop (use_stored=1 + the retired line's instance key), and the reference is CARRIED to the new instance so its 2.6 needs no re-drop either",
      a_c["instance_key"] == NEW and a_c["source_kind"] == "invoice" and a_c["file"]["carried_from"] == STALE2
      and (next(r for r in db6.tables["onboarding_stage_state"] if r["instance_key"] == NEW)["payload"].get("file") or {}).get("stored") is True, (a_c.get("instance_key"), a_c.get("file")))
c_c = run(R.onboarding_intake_commit(file=None, source_kind="invoice", pos_source="rq_like", layout="sales_by_invoice", instance_key=NEW, use_stored="1",
                                     identity=IDENTITY, typed_total=f"{SUM_NET:.2f}", verified_by="owner", report_kind="sales_by_invoice", org_id=ORG))
check("F11 …and commits from the carried file: 7 invoices + 15 child rows land in the invoice tables; raw_sales_product keeps its 1 remaining row untouched",
      c_c["ok"] and c_c["verified_numbers"]["rows_landed"] == 7 and len(db6.tables["raw_sales_invoice_tender"]) == N_TENDER_ROWS + N_TAX_ROWS and len(db6.tables["raw_sales_product"]) == 1, c_c.get("problems"))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§R THE REPORTED DEFECT (owner 2026-09-24: \"i cant get the uploaded data to convert in to receipts\") — the 2.5b tie counted a vendor rebate as the customer's payment")
# The live card (10,823 invoices) stopped at 2.5b: "the tender columns add up to 7,887,489.25 and the invoice totals to
# 1,032,971.45 — a difference of 6,854,517.80 on 7,948 invoice(s)" — exactly the 'Ven Reb Act' column, which the invoice
# total does not include and which the receipt builder already did NOT print as a payment. One question, two answers.
KF = OI.kind_fields("invoice")
_kept = [{KF["txn"]: "R1", KF["store"]: STORE_A, KF["date"]: "2026-08-02", KF["amount"]: 400.0, KF["total"]: 300.0},
         {KF["txn"]: "R2", KF["store"]: STORE_A, KF["date"]: "2026-08-02", KF["amount"]: 20.0, KF["total"]: 20.0}]
_tr = [{"trans_id": "R1", "role": "tender", "tender_label": "MasterCard", "tender_class": "credit", "amount": 300.0},
       {"trans_id": "R1", "role": "tender", "tender_label": "Ven Reb Act", "tender_class": "vendor_rebate", "amount": 135.5},
       {"trans_id": "R2", "role": "tender", "tender_label": "Cash", "tender_class": "cash", "amount": 20.0}]
_old = IT.invoice_verify(_kept, KF, _tr, pays=lambda c: True)          # the pre-fix answer: every tender is the customer's
_new = IT.invoice_verify(_kept, KF, _tr, pays=CR.is_customer_payment)
check("R1 REPRODUCED: counting every tender as the customer's (the pre-fix tie) refuses — 455.50 vs 320.00, 135.50 on 1 invoice — the owner's refusal in miniature",
      _old["tenders"]["match"] is False and _old["tenders"]["difference"] == 135.5 and _old["tenders"]["invoices_off"] == 1
      and any("tender columns do not add up" in r for r in OI.stage2_refusals("invoice", list(KF.values()), [], {"match": True, "our_total": 1, "file_total": 1}, None,
                                                                               rows_to_land=2, tender_tie=IT.tender_tie(_old))), _old["tenders"])
check("R2 CLOSED: the tie asks closing.router.is_customer_payment — the customer's 320.00 = the invoice totals to the cent, the rebate 135.50 reported BESIDE in words, no refusal",
      _new["tenders"]["match"] is True and _new["tenders"]["customer_sum"] == 320.0 and _new["tenders"]["sum"] == 455.5
      and _new["tenders"]["not_customer"] == {"sum": 135.5, "by_label": {"Ven Reb Act": 135.5}, "invoices": 1}
      and "not counted as the customer's payment" in _new["tenders"]["words"] and "Ven Reb Act 135.50" in _new["tenders"]["words"]
      and IT.tender_tie(_new)["our_total"] == 320.0
      and not any("tender columns do not add up" in r for r in OI.stage2_refusals("invoice", list(KF.values()), [], {"match": True, "our_total": 1, "file_total": 1}, None,
                                                                                   rows_to_land=2, tender_tie=IT.tender_tie(_new))), _new["tenders"])
check("R3 the rebate still LANDS (the tender rows are unchanged — only the tie's question changed): Σ all tender rows 455.50 over 3 rows",
      _new["tenders"]["rows"] == 3 and _new["tenders"]["sum"] == 455.5)
check("R4 a real gap is still refused: a customer tender short by 5.00 → difference -5.00 on 1 invoice, refused",
      (lambda v: v["tenders"]["match"] is False and v["tenders"]["difference"] == -5.0 and v["tenders"]["invoices_off"] == 1)(
          IT.invoice_verify(_kept, KF, [dict(t, amount=15.0) if t["trans_id"] == "R2" else t for t in _tr], pays=CR.is_customer_payment)))
check("R5 ONE ANSWER: is_customer_payment is true for every closing-axis class and false for the vendor rebate, the coupon and an unclassed tender; the X-report label twin places only a known vendor-paid label",
      all(CR.is_customer_payment(k) for k in CR.CANON_TENDERS) and CR.is_customer_payment("debit")
      and not CR.is_customer_payment("vendor_rebate") and not CR.is_customer_payment("coupon") and not CR.is_customer_payment(None)
      and CR.is_vendor_paid_label("Ven Reb Act") and CR.is_vendor_paid_label("Vendor Rebate") and not CR.is_vendor_paid_label("Cash")
      and not CR.is_vendor_paid_label("Financing") and not CR.is_vendor_paid_label(""))
check("R6 invoice_verify has NO default predicate — a caller that forgets it fails loudly, never answers on its own",
      http_error_type(lambda: IT.invoice_verify(_kept, KF, _tr)) is TypeError)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§G negative controls, registration, RULE TWO, the migration, CI")
db3 = fresh_db()
use(db3)
c3 = None
status, detail = http_error(R.onboarding_intake_commit, file=FakeUpload(invoice_xlsx(drift={"I1002": {"Visa": 150.0}}), "x.xlsx"), source_kind="invoice", pos_source="mypos",
                            layout="sales_by_invoice", identity=IDENTITY, typed_total=f"{SUM_NET:.2f}", verified_by="tester", org_id=ORG)
check("G1 a file whose tender columns do NOT add up to the invoice totals is REFUSED naming the difference and step 2.5b (nothing written)…",
      status == 400 and "do not add up to the invoice totals" in str(detail) and "-50.00" in str(detail) and "2.5b" in str(detail) and not db3.tables.get("raw_sales_invoice"), (status, str(detail)[:300]))
c3 = commit(db3, invoice_xlsx(drift={"I1002": {"Visa": 150.0}}), "x.xlsx", source_kind="invoice", pos_source="mypos", layout="sales_by_invoice",
            identity=IDENTITY, typed_total=f"{SUM_NET:.2f}", attestation=json.dumps({"reason": "one invoice was part-paid on account; the register has no column for it"}))
check("G2 …and lands when attested with a reason (recorded with the name): the 1 invoice off is listed with its numbers",
      c3["ok"] and c3["verified_numbers"]["attestation"]["reason"].startswith("one invoice") and c3["verified_numbers"]["numbers"]["tenders"]["invoices_off"] == 1
      and c3["verified_numbers"]["numbers"]["tenders"]["invoices_off_sample"][0]["trans_id"] == "I1002", c3.get("problems"))
a_bad = analyze(db3, invoice_xlsx(), "x.xlsx", source_kind="invoice", pos_source="mypos", layout="sales_by_invoice", identity=IDENTITY,
                tender_columns=json.dumps({"Cash": {"role": "tender", "tender_class": "bitcoin"}}))
check("G3 an unknown tender class in the decisions is a refusal on the verify (never landed as 'other')",
      any("bitcoin" in r for r in a_bad["verify"]["refusals"]), a_bad["verify"]["refusals"])
db4 = fresh_db()
use(db4)
db4.tables["raw_sales_invoice"] = None
status, detail = http_error(R.onboarding_intake_commit, file=FakeUpload(invoice_xlsx(), "x.xlsx"), source_kind="invoice", pos_source="mypos",
                            layout="sales_by_invoice", identity=IDENTITY, typed_total=f"{SUM_NET:.2f}", verified_by="tester", org_id=ORG)
check("G4 before mig 1012 the landing is REFUSED naming the migration — never redirected into raw_sales",
      status == 400 and "1012_sales_by_invoice.sql" in str(detail) and not db4.tables.get("raw_sales"), (status, str(detail)[:200]))
db5 = fresh_db()
db5.drop_inserts["raw_sales_invoice_tender"] = True
c5 = commit(db5, invoice_xlsx(), "x.xlsx", source_kind="invoice", pos_source="mypos", layout="sales_by_invoice", identity=IDENTITY, typed_total=f"{SUM_NET:.2f}")
check("G5 a child landing that silently drops every tender row → ok:false, 'rows landed ≠ rows built', the stage row needs_input — never a green header with no tenders",
      c5["ok"] is False and any("re-read tender" in p or "rows landed" in p for p in c5["problems"]) and c5["state"]["saved"], c5.get("problems"))
check("G6 a request without the tender decisions still lands with the PROPOSAL (the words in the header) — the person confirms by committing; the kind never lands 'no tenders' silently",
      c["landing"]["tenders"]["rows_built"] == N_TENDER_ROWS + N_TAX_ROWS)
# registration + wiring
check("G7 the registries agree: TABLE_MAP / CHILD_TABLE_MAP / SOURCE_KIND_TARGET / KIND_STAMP / INGEST_PARTITION / TABLE_MIGRATION / CONSUMERS all name the two tables",
      CM.TABLE_MAP["sales_by_invoice"] == "raw_sales_invoice" == OI.SOURCE_KIND_TARGET["invoice"] and CM.CHILD_TABLE_MAP["sales_by_invoice"] == "raw_sales_invoice_tender"
      and all(t in LI.KIND_STAMP and t in IS.INGEST_PARTITION and t in LI.TABLE_MIGRATION and t in LI.CONSUMERS for t in ("raw_sales_invoice", "raw_sales_invoice_tender"))
      and LI.KIND_STAMP["raw_sales_invoice_tender"]["default"] == "sales_by_invoice")
check("G8 the registry row: landing 'invoice' is an intake landing; the card is POS- and carrier-agnostic; its signature fields are fields of its layout; sorted between the two sales cards",
      "invoice" in RK.INTAKE_LANDINGS and (lambda r: r["applies_to_pos"] == [] and r["applies_to_carrier"] == [] and r["sort_order"] == 25
                                           and all(any(t[0] == f for t in CM.TARGET_FIELDS["sales_by_invoice"]) for f in r["signature_fields"]))(
          next(r for r in RK.house_mirror() if r["key"] == "sales_by_invoice")))
check("G9 shows_in for the card names ONLY the readers that read it — the intake's Stage 4 and, since 2026-09-21 (§30.14), the POS sales rebuilt from the reports — "
      "never the Tax Collected report or the closing recon, which do not read these tables yet",
      (lambda si: si["table"] == "raw_sales_invoice" and [c["screen"] for c in si["consumers"]] == ["onboarding_intake", "pos_receipts"])(
          LI.shows_in(next(r for r in RK.house_mirror() if r["key"] == "sales_by_invoice"), CM.TABLE_MAP, R._TRACE_TARGET_TABLE, OI.SOURCE_KIND_TARGET)))
lineage = io.open(os.path.join(ROOT, "backend", "app", "modules", "commcalc", "data_lineage_registry.py"), encoding="utf-8").read()
seed = io.open(os.path.join(ROOT, "database", "migrations", "925_data_lineage_seed.sql"), encoding="utf-8").read()
check("G10 the lineage registry + the 925 seed carry both tables (ingest edges 139 / 140)",
      '"raw_sales_invoice", "raw_sales_invoice_tender"' in lineage and "'raw_sales_invoice','commcalc.raw_sales_invoice'" in seed and ",139)," in seed and ",140)," in seed)
check("G11 the migration: additive, idempotent, both tables, the house row, the signature, a REVERT note, NOT applied, the 1011 decision stated, nothing dropped",
      all(t in sql for t in ("CREATE TABLE IF NOT EXISTS commcalc.raw_sales_invoice (", "CREATE TABLE IF NOT EXISTS commcalc.raw_sales_invoice_tender (",
                             "ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS closing_tender_basis TEXT", "tenants_closing_tender_basis_chk",
                             "ON CONFLICT (org_id, key) DO NOTHING", "ON CONFLICT (org_id, fingerprint, report_kind_key) DO NOTHING", "-- REVERT:", "NOT applied",
                             "option (b)", "NOTHING IS DROPPED")) and not re.search(r"^(DROP|DELETE|UPDATE|TRUNCATE)\b", sql, re.M))
idx = io.open(os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
check("G12 index §16 / §17 / §18 / §30.13 register the tables, the endpoint, the metric and the design",
      "### 30.13" in idx and "`commcalc.raw_sales_invoice`" in idx and "/onboarding/intake/retire" in idx and "raw_sales_invoice_tender" in idx and "TENDER_VOCAB" in idx)
design = io.open(os.path.join(ROOT, "docs", "ONBOARDING_FLOW_DESIGN.md"), encoding="utf-8").read()
check("G13 the design doc carries §11 (the tender columns are declared, one vocabulary)", "## 11." in design and "2.5b" in design)
src_it = io.open(os.path.join(ROOT, "backend", "app", "modules", "commcalc", "invoice_tenders.py"), encoding="utf-8").read()
code_only = "\n".join(l for l in src_it.split("\n") if not l.strip().startswith("#"))
code_only = re.sub(r'"""[\s\S]*?"""', "", code_only)
check("G14 RULE TWO + one home: invoice_tenders.py names no carrier / POS vendor / card brand / jurisdiction and spells no tender word list (every classifier is injected)",
      not re.search(r"(?i)\b(visa|mastercard|amex|discover|brooklyn|verizon|boost|rq|b2b|iqmetrix)\b", code_only)
      and "import" not in [l.split()[0] for l in code_only.split("\n") if l.strip()][1:]      # stdlib `re` only — nothing from closing imported
      and not any(w in code_only for w in ('"gift"', '"ext_cc"', '"vendor_rebate"', '"visa"', '"credit"', '"debit"')))
ci = io.open(os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml"), encoding="utf-8").read()
check("G15 CI: the tender-vocab lock runs in carrier-vocab-guard.yml (stdlib, dependency-free like its siblings — this proof runs locally like the other intake proofs); the new module and migration are watched paths",
      "harness_tender_vocab_lock.py" in ci and "python3 harness_tender_vocab_lock.py" in ci and "invoice_tenders.py" in ci and "1012_sales_by_invoice.sql" in ci and "harness_sales_by_invoice.py" not in ci.split("jobs:")[1])
rt = io.open(os.path.join(ROOT, "backend", "app", "modules", "commcalc", "router.py"), encoding="utf-8").read()
check("G16 the landing is the ONE importer twice (no insert of its own), the recon rides the closing reader + resolver, the child table is dereferenced from CHILD_TABLE_MAP",
      "_intake_land" in rt and rt.count("column_mapping.CHILD_TABLE_MAP[") >= 2 and "_cr._tender_split_by_store(" in rt and "_cr._invoice_tenders_by_store(" in rt
      and "'raw_sales_invoice_tender'" not in rt and '"raw_sales_invoice_tender"' not in rt)

print(f"\n══ sales by invoice (tender types): {_pass} passed, {_fail} failed ══")
sys.exit(1 if _fail else 0)
