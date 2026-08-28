"""Harness — app/modules/pos/receipt_import.py::normalize_receipt (the PURE receipt parser).

Proves, WITHOUT a network or DB, that the vision model's loose JSON is coerced into clean, typed
fields the importer can trust:

  A. Money strings ('$1,299.00') → floats; total derived from lines+tax when the receipt total
     wasn't read.
  B. Phone → digits only.
  C. IMEI validation: a 14-15 digit id is kept; a stray price/SKU in the imei slot is rejected.
  D. Primary device/IMEI picked from the first line; all IMEIs collected.
  E. A bad/blank date is dropped (never corrupts the record); an ISO date is kept.
  F. Empty / missing input degrades to a well-formed empty record (no crash).

Run: python3 backend/harness_pos_receipt_parse.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.pos.receipt_import import normalize_receipt  # noqa: E402

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {extra}")


# ── A/B/C/D: a full, messy receipt ───────────────────────────────────────────────────────────────
raw = {
    "customer_name": "  John Q Public ",
    "phone": "(917) 555-1234",
    "email": "JOHN@EXAMPLE.COM",
    "items": [
        {"description": "iPhone 15 128GB", "imei": "356789012345678", "qty": 1, "unit_price": "$999.00"},
        {"description": "Case", "imei": "N/A", "qty": 2, "unit_price": "19.99"},
    ],
    "subtotal": "1,038.98",
    "tax": "92.21",
    "total": None,  # not read → must be derived
    "sale_date": "2026-08-24",
    "payment_method": "Visa",
}
n = normalize_receipt(raw)

ok("A money coerced (unit_price $999 → 999.0)", n["items"][0]["unit_price"] == 999.0)
ok("A total derived from lines + tax",
   n["total"] == round(999.0 + 19.99 * 2 + 92.21, 2), f"got {n['total']}")
ok("B phone digits only", n["phone"] == "9175551234", f"got {n['phone']}")
ok("C valid IMEI kept", n["items"][0]["imei"] == "356789012345678")
ok("C non-IMEI ('N/A') rejected", n["items"][1]["imei"] is None)
ok("D primary device name", n["device_name"] == "iPhone 15 128GB")
ok("D primary imei denormalized", n["imei"] == "356789012345678")
ok("D imeis collects only valid", n["imeis"] == ["356789012345678"])
ok("email trimmed (case preserved)", n["email"] == "JOHN@EXAMPLE.COM")
ok("customer name trimmed", n["customer_name"] == "John Q Public")

# ── E: bad date dropped, good date kept ──────────────────────────────────────────────────────────
ok("E bad date dropped", normalize_receipt({"sale_date": "Aug 24 2026"})["sale_date"] is None)
ok("E ISO date kept", normalize_receipt({"sale_date": "2026-08-24"})["sale_date"] == "2026-08-24")

# ── F: empty input is safe ───────────────────────────────────────────────────────────────────────
e = normalize_receipt(None)
ok("F None input → empty record", e["items"] == [] and e["total"] is None and e["imei"] is None)
ok("F missing keys → no crash", normalize_receipt({})["customer_name"] is None)

# total present on the receipt wins over derivation
ok("total on receipt is honored", normalize_receipt({"total": "500", "tax": "40"})["total"] == 500.0)

# qty defaults / floors
ok("qty defaults to 1 when absent",
   normalize_receipt({"items": [{"description": "x", "unit_price": 5}]})["items"][0]["qty"] == 1)


print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
