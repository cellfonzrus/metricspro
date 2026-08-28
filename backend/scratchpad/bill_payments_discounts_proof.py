"""Proof for the Bill Payments dataset + discounts report (owner 2026-08-26).

The b2b "Bill Payment Transactions Processed" report is ingested via the self-serve CUSTOM IMPORT path
(commcalc.raw_custom_import JSONB — no per-report table). `_cr_resolve_bill_payments` detects it by column
SIGNATURE (a 'Discounts' column + a Bill-Pay identifier), coerces the money columns, and excludes voided
lines. The Custom Report engine then groups by Store and sums Discount → the discounts-on-bill-payments
report the owner asked for.

Proves: signature detection (ignores a non-bill-payment custom sheet), voided exclusion, money coercion
('$', commas, blank), one-row-per-payment count, and correct per-store Discount/Payment sums.

Run:  cd backend && python3 scratchpad/bill_payments_discounts_proof.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app.modules.commcalc.router as R
from app.modules.commcalc import custom_report

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  \033[92mPASS\033[0m {name}")
    else:
        FAIL += 1; print(f"  \033[91mFAIL\033[0m {name}  {extra}")

ORG = "org-lux"
PERIOD = "August 2026"

# raw_custom_import rows AS CAPTURED: every original column stringified under `data`. Two stores of bill
# payments, one VOIDED line, plus a DIFFERENT custom sheet (no bill-pay signature) that must be ignored.
def bp(store, tid, payment, disc, voided="", created_by="REP1", extra=None):
    d = {"Store": store, "Trans Date": "2026-08-05", "Trans ID": tid, "Created By": created_by,
         "Bill Pay System": "Boost RTR", "Bill Pay ID": "BP1", "Carrier ID": "Boost",
         "Payment": payment, "Fee": "1.00", "Total Amt": payment, "Discounts": disc, "Tax": "0.00",
         "Voided": voided, "Customer Type": "Existing", "Tender Type": "Cash"}
    if extra:
        d.update(extra)
    return {"data": d, "period": PERIOD, "source_filename": "BillPay.xlsx"}

CUSTOM_ROWS = [
    bp("Diversey", "B1", "50.00", "$5.00"),
    bp("Diversey", "B2", "1,200.00", "10"),          # comma money
    bp("Diversey", "B3", "30.00", "", voided="Yes"),  # VOIDED -> excluded
    bp("Halsted",  "B4", "40.00", "2.50"),
    bp("Halsted",  "B5", "60.00", ""),                # blank discount -> 0
    # A DIFFERENT custom sheet (no 'Bill Pay System'/'Discounts' signature) -> must be ignored:
    {"data": {"Store": "Diversey", "Trans ID": "X9", "Some Metric": "999"}, "period": PERIOD,
     "source_filename": "Other.xlsx"},
]


class _Res:
    def __init__(self, d): self.data = d
class _Q:
    def __init__(self, rows): self._rows = rows
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def execute(self): return _Res(self._rows)
class _Schema:
    def __init__(self, rows): self._rows = rows
    def table(self, *a, **k): return _Q(self._rows)
class _Client:
    def __init__(self, rows): self._rows = rows
    def schema(self, *a, **k): return _Schema(self._rows)


ctx = {"market_for": (lambda s: "Chicago" if s else "")}
rows = R._cr_resolve_bill_payments(_Client(CUSTOM_ROWS), ORG, PERIOD, ctx)

print("(1) resolver — signature detection, voided exclusion, money coercion")
tids = {r["trans_id"] for r in rows}
check("5 bill-payment rows kept (voided B3 + non-bill X9 excluded)", len(rows) == 4, [len(rows), tids])
check("voided B3 excluded", "B3" not in tids, tids)
check("non-bill-payment sheet (X9) excluded by signature", "X9" not in tids, tids)
b2 = next((r for r in rows if r["trans_id"] == "B2"), None)
check("comma money coerced ('1,200.00' -> 1200.0)", b2 and b2["payment"] == 1200.0, b2 and b2["payment"])
b1 = next((r for r in rows if r["trans_id"] == "B1"), None)
check("'$5.00' discount coerced -> 5.0", b1 and b1["discount"] == 5.0, b1 and b1["discount"])
b5 = next((r for r in rows if r["trans_id"] == "B5"), None)
check("blank discount -> 0.0", b5 and b5["discount"] == 0.0, b5 and b5["discount"])
check("every row is one payment (txns=1)", all(r["txns"] == 1 for r in rows))
check("market stamped via ctx", all(r["market"] == "Chicago" for r in rows))

print("(2) discounts report — group by Store, sum Discount (the owner's ask)")
ds = custom_report.dataset_by_key("bill_payments")
cols = ds["columns"]
gfield = custom_report.resolve_group_field(ds, "store")
out_rows, out_cols = custom_report.group_and_aggregate(rows, cols, gfield)
by_store = {r.get("store"): r for r in out_rows}
# Diversey: B1 $5 + B2 $10 = $15 discount, 2 payments (B3 voided out). Halsted: $2.50 + $0 = $2.50, 2.
check("Diversey discount sums to 15.0", by_store.get("Diversey", {}).get("discount") == 15.0,
      by_store.get("Diversey", {}).get("discount"))
check("Diversey counts 2 bill payments", by_store.get("Diversey", {}).get("txns") == 2,
      by_store.get("Diversey", {}).get("txns"))
check("Halsted discount sums to 2.5", by_store.get("Halsted", {}).get("discount") == 2.5,
      by_store.get("Halsted", {}).get("discount"))
check("Halsted payment sums to 100.0", by_store.get("Halsted", {}).get("payment") == 100.0,
      by_store.get("Halsted", {}).get("payment"))

print("(3) dataset is registered + resolver wired")
check("bill_payments in resolver registry", "bill_payments" in R._CUSTOM_REPORT_RESOLVERS)
check("dataset exposes a Discount money column",
      any(c["field"] == "discount" and c["type"] == "money" for c in cols))

print(f"\n{'='*60}\n  {PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)
