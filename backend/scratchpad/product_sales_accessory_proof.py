"""Proof for the Sales by Product dataset (accessory sales by department) — owner 2026-08-26.

Uses the owner's sample "Sales by Product" rows, captured via custom import (raw_custom_import JSONB) with
the report's HIERARCHICAL shape: 'Department:' header rows, product rows, and a per-department subtotal row.
Proves the resolver:
  • walks rows in file order and carries the Department onto each product row,
  • drops header + subtotal rows (no double counting),
  • flags Accessories / C2wireless as accessory departments,
  • yields accessory_sales / accessory_gp so grouping gives the accessory totals.

Run:  cd backend && python3 scratchpad/product_sales_accessory_proof.py
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

ORG, PERIOD = "org-lux", "August 2026"

def row(i, cat, prod, qty, ext, cost, comm, gp, pgp):
    return {"data": {"Category": cat, "Product Desc": prod, "Qty": qty, "Ext Price": ext,
                     "Ext Cost": cost, "Total Exp Comm": comm, "GP": gp, "Product GP": pgp},
            "row_index": i, "source_filename": "SalesByProduct.xlsx", "period": PERIOD}

# The owner's sample, IN ORDER, incl. the two 'Department:' headers + the first department's subtotal.
ROWS = [
    row(0, "Department:  ", "", "", "", "", "", "", ""),                                    # header (blank dept)
    row(1, "", "Customer Phone", "125", "$0.00", "$0.00", "$1,742.26", "$1,742.26", "$0.00"),
    row(2, "", "", "125", "$0.00", "$0.00", "$1,742.26", "$1,742.26", "$0.00"),             # subtotal -> skip
    row(3, "Department: Accessories ", "", "", "", "", "", "", ""),                          # header
    row(4, "Speakers", "Light-Up LED Wireless Speaker", "2", "$34.99", "$7.98", "$0.00", "$27.01", "$27.01"),
    row(5, "Earphones", "Headphones BYOD", "4", "$119.96", "$19.96", "$0.00", "$100.00", "$100.00"),
    row(6, "Earphones", "HYPERGEAR POWER FULL SOUND-BYOD", "81", "$1,209.20", "$242.19", "$0.00", "$967.01", "$967.01"),
    row(7, "Chargers", "Car Chargers BYOD", "1", "$29.99", "$19.99", "$0.00", "$10.00", "$10.00"),
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


rows = R._cr_resolve_product_sales(_Client(ROWS), ORG, PERIOD, {"market_for": (lambda s: "")})

print("(1) resolver — headers/subtotals dropped, department carried down")
check("5 product rows (2 headers + 1 subtotal dropped)", len(rows) == 5, len(rows))
cp = next((r for r in rows if r["product_desc"] == "Customer Phone"), None)
check("Customer Phone dept is blank (first group) and NOT accessory",
      cp and cp["department"] == "" and cp["is_accessory"] == "No", cp)
acc = [r for r in rows if r["is_accessory"] == "Yes"]
check("4 accessory rows, all under 'Accessories'",
      len(acc) == 4 and all(r["department"] == "Accessories" for r in acc), [len(acc)])

print("(2) accessory totals (Accessories + C2wireless depts)")
asum = round(sum(r["accessory_sales"] for r in rows), 2)
agp = round(sum(r["accessory_gp"] for r in rows), 2)
check("accessory_sales (Ext Price) = 1394.14", asum == 1394.14, asum)
check("accessory_gp (GP) = 1104.02", agp == 1104.02, agp)
check("Customer Phone contributes 0 to accessory_sales", cp and cp["accessory_sales"] == 0.0)

print("(3) group by Accessory dept? via the report engine")
ds = custom_report.dataset_by_key("product_sales")
gfield = custom_report.resolve_group_field(ds, "is_accessory")
out_rows, _ = custom_report.group_and_aggregate(rows, ds["columns"], gfield)
by = {r.get("is_accessory"): r for r in out_rows}
check("Yes group: accessory_sales 1394.14", by.get("Yes", {}).get("accessory_sales") == 1394.14,
      by.get("Yes", {}).get("accessory_sales"))
check("No group: gp 1742.26 (Customer Phone)", by.get("No", {}).get("gp") == 1742.26,
      by.get("No", {}).get("gp"))

print("(4) wired")
check("product_sales in resolver registry", "product_sales" in R._CUSTOM_REPORT_RESOLVERS)
check("C2wireless recognized as accessory dept", "c2wireless" in R._ACCESSORY_DEPARTMENTS)

print(f"\n{'='*60}\n  {PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)
