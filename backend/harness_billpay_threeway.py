"""Offline proof harness — BILL-PAY-ON-CREDIT column + 3-WAY bill-payment recon (owner directive
2026-09-02 #2; mig 944).

Owner, verbatim: "in the billpayment pick, add another column for bill payment on credit card, and
the pos bill payments are showing 0 as the pos does not store the bill payment on credit card
separately, two ways it will be done and a part of 3 way recon for bill payments, 1 will be the
total of bill payments received on credit card from the sales transactions for that day from the
email ingested reports and the second will be from the owners portal report for bill payment in
case of boost and the daily tx report for total, again nothing hardcoded and everything indexed
for future."

WHAT IS PROVEN (all against the REAL functions, no reimplementation; stdlib only, no DB/network)
  A. classify_tender — the POS tender vocabulary truth table: card / cash / MIXED (a multi-tender
     receipt is never silently attributed to either side) / other / blank; per-org config tokens
     override the house defaults (RULE TWO — mig 944 columns, defaults in metric_recon).
  B. ma_billpay_predicate — the carrier daily-TX bill-payment ROW FILTER (the "pos bill payments
     showing 0" defect class): order-type family + product vocabulary (curated exact list ∪
     containment tokens), all config-driven; a handset / residual / spiff row NEVER counts as a
     bill payment again.
  C. reconcile_billpay_three_way_days — the 3-way math truth table: agreement, per-pair
     mismatches with signed deltas, tolerance edges, the HONEST-GAP states (feed absent for the
     range ⇒ leg None + gap flag, NEVER a fake zero or fake mismatch; feed present but silent for
     a store-day ⇒ honest zero), declared_only, undeclared store-days surfacing, tender split
     pass-through, summary totals.
  D. _sales_cell_agg tender extension — tender_cfg=None keeps every pre-existing caller
     BYTE-IDENTICAL (the split accumulators exist but stay 0.0 and bill_amt is unchanged);
     tender_cfg on splits the SAME classified bill-payment dollars by tender, and the buckets
     always sum back to bill_amt_tendered ≤ bill_amt.

Run: `cd backend && python3 harness_billpay_threeway.py`
"""
import sys

sys.path.insert(0, ".")

from app.modules.commcalc.metric_recon import (          # noqa: E402
    classify_tender, ma_billpay_predicate, reconcile_billpay_three_way_days,
    DEFAULT_CARD_TENDERS, DEFAULT_CASH_TENDERS,
    DEFAULT_MA_BILLPAY_ORDER_TYPES, DEFAULT_MA_BILLPAY_PRODUCT_TOKENS)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


# ── A. classify_tender truth table ─────────────────────────────────────────────────────────────
check("A1. 'Credit Card' → card (house default)", classify_tender("Credit Card") == "card")
check("A2. 'Debit Card' → card (card = credit|debit by default — a card is a card)",
      classify_tender("Debit Card") == "card")
check("A3. 'Cash' → cash", classify_tender("Cash") == "cash")
check("A4. 'Cash; Debit Card' → MIXED — a multi-tender line is never attributed to either side",
      classify_tender("Cash; Debit Card") == "mixed")
check("A5. 'External Payment' → other (neither vocabulary)",
      classify_tender("External Payment") == "other")
check("A6. blank/None → '' (no tender recorded — counts only in the untendered total)",
      classify_tender("") == "" and classify_tender(None) == "")
check("A7. case/whitespace-insensitive containment", classify_tender("  CASH  ") == "cash")
check("A8. per-org config tokens OVERRIDE the defaults (RULE TWO): card list without 'debit' "
      "sends a debit line to other",
      classify_tender("Debit Card", card_tokens=("credit",)) == "other"
      and classify_tender("Credit Card", card_tokens=("credit",)) == "card")
check("A9. config cash tokens override symmetrically",
      classify_tender("Efectivo", cash_tokens=("efectivo",)) == "cash")
check("A10. house defaults are the documented vocabulary",
      DEFAULT_CARD_TENDERS == ("credit", "debit") and DEFAULT_CASH_TENDERS == ("cash",))

# ── B. ma_billpay_predicate (the POS-zero defect class) ────────────────────────────────────────
pred = ma_billpay_predicate()   # pure house defaults
check("B1. an RTR Sales Order row IS a bill payment (default tokens)",
      pred({"order_type": "Sales Order", "product_name": "Total Wireless 5G Unlimited RTR $55"}))
check("B2. a wallet-funding Sales Order row IS a bill payment",
      pred({"order_type": "Sales Order", "product_name": "Wallet Funding"}))
check("B3. a HANDSET row is NOT (the live defect: $599.99 marketplace iPhones summed as billpay)",
      not pred({"order_type": "Postpaid Branded MarketPlace",
                "product_name": "Apple iPhone 16e 128GB Black TO"}))
check("B4. a residual row is NOT",
      not pred({"order_type": "Postpaid Residual Order", "product_name": "Residual"}))
check("B5. a non-billpay product inside the order-type family is NOT (Premium Store Spiff)",
      not pred({"order_type": "Sales Order", "product_name": "Premium Store Spiff"}))
check("B6. blank product never matches", not pred({"order_type": "Sales Order", "product_name": ""}))
pred_cfg = ma_billpay_predicate(order_types=("Airtime Order",),
                                exact_products=("acme refill",),
                                product_tokens=("topup",))
check("B7. config order types REPLACE the default family",
      pred_cfg({"order_type": "Airtime Order", "product_name": "Acme Topup $10"})
      and not pred_cfg({"order_type": "Sales Order", "product_name": "Acme Topup $10"}))
check("B8. the org's curated exact list (mig-214 billpay_products, reused) matches verbatim",
      pred_cfg({"order_type": "Airtime Order", "product_name": "ACME Refill"}))
check("B9. exact list and tokens are a UNION (either qualifies)",
      pred_cfg({"order_type": "Airtime Order", "product_name": "Mega TOPUP"}))
check("B10. house default families are the documented ones",
      DEFAULT_MA_BILLPAY_ORDER_TYPES == ("Sales Order",)
      and DEFAULT_MA_BILLPAY_PRODUCT_TOKENS == ("rtr", "wallet funding"))

# ── C. the 3-way recon truth table ─────────────────────────────────────────────────────────────
K1, K2, K3 = ("S1", "2026-08-05"), ("S1", "2026-08-06"), ("S2", "2026-08-05")

# C1: all three agree within tolerance
rows, summ = reconcile_billpay_three_way_days(
    {K1: 100.0}, {K1: {"amount": 100.5, "card": 40.0, "cash": 60.5}}, {K1: 99.5},
    tolerance_amt=1.0)
r = rows[0]
check("C1. all legs within tolerance → ok, deltas signed declared−sales / declared−proc / sales−proc",
      len(rows) == 1 and r["status"] == "ok" and r["delta_declared_sales"] == -0.5
      and r["delta_declared_processor"] == 0.5 and r["delta_sales_processor"] == 1.0
      and r["gaps"] == [], str(r))
check("C2. tender split passes through (card = the bill payments received on credit card)",
      r["sales_card"] == 40.0 and r["sales_cash"] == 60.5 and r["sales_mixed"] == 0.0)

# C3: a real mismatch on one pair flags the row
rows, summ = reconcile_billpay_three_way_days({K1: 200.0}, {K1: {"amount": 100.0}}, {K1: 199.0},
                                              tolerance_amt=1.0)
check("C3. one out-of-tolerance pair → mismatch (declared 200 vs sales 100, proc fine)",
      rows[0]["status"] == "mismatch" and rows[0]["delta_declared_sales"] == 100.0
      and summ["mismatched"] == 1)

# C4: tolerance edge is inclusive
rows, _ = reconcile_billpay_three_way_days({K1: 101.0}, {K1: {"amount": 100.0}}, {K1: 100.0},
                                           tolerance_amt=1.0)
check("C4. |delta| == tolerance → still ok (inclusive edge)", rows[0]["status"] == "ok")

# C5: processor feed ABSENT for the range → honest gap, never a mismatch against the absent leg
rows, summ = reconcile_billpay_three_way_days({K1: 100.0}, {K1: {"amount": 100.0}}, {},
                                              tolerance_amt=1.0, processor_present=False)
r = rows[0]
check("C5. processor absent → leg None + no_processor_data gap; status from the present pair",
      r["processor"] is None and r["delta_declared_processor"] is None
      and "no_processor_data" in r["gaps"] and r["status"] == "ok"
      and summ["processor"] is None)

# C6: sales feed absent too → declared_only (both gaps), never a fake zero mismatch
rows, summ = reconcile_billpay_three_way_days({K1: 500.0}, {}, {}, tolerance_amt=1.0,
                                              sales_present=False, processor_present=False)
r = rows[0]
check("C6. both cross-legs absent → declared_only + both gap flags (never 'mismatch vs 0')",
      r["status"] == "declared_only" and set(r["gaps"]) == {"no_sales_data", "no_processor_data"}
      and r["sales"] is None and r["sales_card"] is None and summ["declared_only"] == 1
      and summ["mismatched"] == 0)

# C7: feed PRESENT but silent for a store-day → honest zero → a real mismatch when declared > tol
rows, summ = reconcile_billpay_three_way_days({K1: 100.0, K2: 80.0},
                                              {K1: {"amount": 100.0}},
                                              {K1: 100.0, K2: 80.0}, tolerance_amt=1.0,
                                              sales_present=True, processor_present=True)
by = {(r["store"], r["day"]): r for r in rows}
check("C7. present-but-silent sales store-day → honest 0.0 and a REAL mismatch (declared 80 vs 0)",
      by[K2]["sales"] == 0.0 and by[K2]["status"] == "mismatch" and by[K1]["status"] == "ok")

# C8: a store-day only the cross-legs know (never declared) still surfaces
rows, _ = reconcile_billpay_three_way_days({K1: 100.0}, {K1: {"amount": 100.0},
                                                         K3: {"amount": 55.0}},
                                           {K1: 100.0}, tolerance_amt=1.0)
by = {(r["store"], r["day"]): r for r in rows}
check("C8. an UNDECLARED store-day with sales bill payments appears (declared 0 vs sales 55 → mismatch)",
      K3 in by and by[K3]["declared"] == 0.0 and by[K3]["sales"] == 55.0
      and by[K3]["status"] == "mismatch")

# C9: summary totals + ordering (day then store)
rows, summ = reconcile_billpay_three_way_days(
    {K1: 10.0, K2: 20.0, K3: 30.0},
    {K1: {"amount": 10.0, "card": 4.0}, K2: {"amount": 20.0, "card": 20.0},
     K3: {"amount": 30.0, "card": 0.0}},
    {K1: 10.0, K2: 20.0, K3: 30.0}, tolerance_amt=1.0)
check("C9. summary sums every leg + card share; rows sorted (day, store)",
      summ["declared"] == 60.0 and summ["sales"] == 60.0 and summ["sales_card"] == 24.0
      and summ["processor"] == 60.0 and summ["mismatched"] == 0
      and [(r["store"], r["day"]) for r in rows] == [K1, K3, K2])

# C10: plain-float and {'amount':…} leg shapes are interchangeable
rows_f, _ = reconcile_billpay_three_way_days({K1: 42.0}, {K1: 42.0}, {K1: {"amount": 42.0}},
                                             tolerance_amt=0.0)
check("C10. float legs and {'amount'} legs reconcile identically",
      rows_f[0]["status"] == "ok" and rows_f[0]["sales"] == 42.0)

# ── D. _sales_cell_agg tender extension (byte-identity + the split) ────────────────────────────
from app.modules.commcalc.router import _sales_cell_agg  # noqa: E402

EXEC_CFG = {"phones": {"rules": {}}, "activation_fee": {"rules": {}}, "protect": {"rules": {}},
            "activation": {"rules": {}},
            "bill_payment": {"rules": {"department": ["rtr"], "category": [],
                                       "product_desc_contains": ["wallet funding"]}}}
ROWS = [
    {"trans_id": "t1", "trans_date": "2026-08-05", "store": "S1", "salesperson": "Ana",
     "department": "Rtr", "category": "RTR Product", "product_desc": "Total 5G RTR $55",
     "ext_price": 55.0, "gp": 0, "tender_type": "Cash"},
    {"trans_id": "t2", "trans_date": "2026-08-05", "store": "S1", "salesperson": "Ana",
     "department": "Rtr", "category": "RTR Product", "product_desc": "Total 5G RTR $40",
     "ext_price": 40.0, "gp": 0, "tender_type": "Credit Card"},
    {"trans_id": "t3", "trans_date": "2026-08-05", "store": "S1", "salesperson": "Ana",
     "department": "Rtr", "category": "RTR Product", "product_desc": "Wallet Funding",
     "ext_price": 25.0, "gp": 0, "tender_type": "Cash; Debit Card"},
    {"trans_id": "t4", "trans_date": "2026-08-05", "store": "S1", "salesperson": "Ana",
     "department": "Rtr", "category": "RTR Product", "product_desc": "Total 5G RTR $30",
     "ext_price": 30.0, "gp": 0, "tender_type": ""},          # no tender recorded
    {"trans_id": "t5", "trans_date": "2026-08-05", "store": "S1", "salesperson": "Ana",
     "department": "Accessories", "category": "Case", "product_desc": "Phone Case",
     "ext_price": 20.0, "gp": 10, "tender_type": "Credit Card"},   # NOT a bill payment
]
ACFG = {"billpay_products": set(), "contract_type_map": None, "activation_rules": [],
        "box_departments": set(), "departments": {"accessories"}, "categories": set(),
        "products": set(), "setup_fee_products": set(), "catalog_classifier": None}

cells_off = _sales_cell_agg(list(ROWS), ACFG, exec_cfg=EXEC_CFG)
c_off = cells_off[("S1", "Ana", "2026-08-05")]
check("D1. tender_cfg=None: bill_amt unchanged and every split accumulator stays 0.0 "
      "(pre-existing callers byte-identical)",
      c_off["bill_amt"] == 150.0 and c_off["bill_qty"] == 4
      and c_off["bill_amt_card"] == 0.0 and c_off["bill_amt_cash"] == 0.0
      and c_off["bill_amt_mixed"] == 0.0 and c_off["bill_amt_tendered"] == 0.0)

TCFG = {"card": DEFAULT_CARD_TENDERS, "cash": DEFAULT_CASH_TENDERS, "classify": classify_tender}
cells_on = _sales_cell_agg(list(ROWS), ACFG, exec_cfg=EXEC_CFG, tender_cfg=TCFG)
c_on = cells_on[("S1", "Ana", "2026-08-05")]
check("D2. tender_cfg on: the SAME classified dollars split by tender "
      "(cash 55 / card 40 / mixed 25; the untendered $30 counts only in bill_amt)",
      c_on["bill_amt"] == 150.0 and c_on["bill_amt_cash"] == 55.0
      and c_on["bill_amt_card"] == 40.0 and c_on["bill_amt_mixed"] == 25.0
      and c_on["bill_amt_other"] == 0.0 and c_on["bill_amt_tendered"] == 120.0, str(c_on))
check("D3. buckets always reconcile: card+cash+mixed+other == tendered ≤ bill_amt",
      round(c_on["bill_amt_card"] + c_on["bill_amt_cash"] + c_on["bill_amt_mixed"]
            + c_on["bill_amt_other"], 2) == c_on["bill_amt_tendered"]
      and c_on["bill_amt_tendered"] <= c_on["bill_amt"])
check("D4. a NON-bill-payment line never enters any bill bucket (accessory $20 untouched)",
      c_on["accessory_rev"] == 20.0 and c_on["bill_amt"] == 150.0)
check("D5. every non-billpay cell field identical with and without tender_cfg",
      all(c_off[k] == c_on[k] for k in c_off
          if not str(k).startswith("bill_amt_")), )

# ── Summary ────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
