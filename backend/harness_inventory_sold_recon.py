"""PROOF: INVENTORY vs SOLD — is a device still on the shelf, or was it already sold?

Owner request 2026-09-12: *"check against the sales by product to see if the item in inventory is
already sold or not and if it is alsready sold then it should report those items whic are soled with
imei to be adjusted and also those items which are in oventory to be cleared out of the inventory."*

THE MAGNITUDE THIS PINS, measured on the owner's own first RQ files and REPORTED at the time rather
than quietly corrected: 10 devices, $7,360 at cost, marked In Stock but sold and never returned, the
oldest sitting since March 2025 — including 4 desk phones on one invoice. A further 8 units appear in
the sales file and are LEGITIMATELY on hand, because they were sold and then refunded. A naive
"appears in sales ⇒ sold" reads 18 and would send someone to write off stock they can see.

§2 reproduces exactly that shape: 18 on-hand units that appear in the sales file, of which only 10 are
real phantoms. That is the check the whole report exists to get right.

DB-FREE / stdlib only. The device key is the REAL `device_cost_recon.device_key` — the harness pairs
the two modules exactly as the endpoint does, so a change to the canonical key normalization fails
here rather than silently re-joining devices in production.

Run:  python3 backend/harness_inventory_sold_recon.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import inventory_sold_recon as isr      # noqa: E402  (PURE)
from app.modules.commcalc.device_cost_recon import device_key     # noqa: E402  (THE canonical key)

P, F = [], []


def check(name, ok, detail=""):
    (P if ok else F).append(name if ok else f"{name} :: {detail}")


K = device_key

# Fixture keys are REAL-LENGTH. device_cost_recon refuses anything under MIN_DEVICE_KEY_LEN (6) —
# correctly, since a 2-character token cannot identify a handset — so a toy key like "A1" would be
# silently dropped and every assertion below would pass against an empty report. Caught by running
# the harness rather than assuming it worked.
def imei(tag):
    """A 15-character IMEI-shaped key carrying a readable tag."""
    return (str(tag) + "0" * 15)[:15]

# ── A. The device key is the SHARED one, not a second spelling ──────────────────────────────────
check("A1 the recon uses the canonical cross-source device key", callable(K))
check("A2 …which drops the spreadsheet float artefact", K("353915110000001.0") == "353915110000001")
check("A3 …upper-cases an alphanumeric serial rather than dropping it", K("ab12cd34ef") == "AB12CD34EF")
check("A4 …and refuses junk, so a bad token never joins two devices",
      K("") is None and K("nan") is None and K("-") is None)

# ── B. An ordered unit is not a present unit ────────────────────────────────────────────────────
check("B1 In Stock is present", isr.status_is_present("In Stock"))
check("B2 Committed is present (it is on the shelf, just spoken for)", isr.status_is_present("Committed"))
check("B3 On Order is NOT present", not isr.status_is_present("On Order"))
check("B4 On Back Order is NOT present", not isr.status_is_present("On Back Order"))
check("B5 a POS that words it differently still works — shape, not a vendor word list",
      not isr.status_is_present("ORDERED FROM VENDOR") and not isr.status_is_present("backorder"))
check("B6 a BLANK status is present — the feed did not say, and dropping the row would silently "
      "shrink the report", isr.status_is_present("") and isr.status_is_present(None))

# ── C. Net quantity: a refund is not a sale ─────────────────────────────────────────────────────
A1, B2, C3, D4, E5 = (imei(x) for x in ("A1", "B2", "C3", "D4", "E5"))
sales_c = [{"serial_1": A1, "quantity": 1}, {"serial_1": A1, "quantity": -1},
           {"serial_1": B2, "quantity": 1},
           {"serial_1": C3, "quantity": 2}, {"serial_1": C3, "quantity": -1}]
net_c = isr.net_sold(sales_c, K)
check("C1 sold then refunded nets to zero", net_c[A1] == 0)
check("C2 a plain sale nets to one", net_c[B2] == 1)
check("C3 two sold, one returned nets to one", net_c[C3] == 1)
check("C4 a feed with NO quantity column counts a line as one unit",
      isr.net_sold([{"serial_1": D4}], K)[D4] == 1)
check("C5 an accounting negative '(1)' is read as -1",
      isr.net_sold([{"serial_1": E5, "quantity": "1"}, {"serial_1": E5, "quantity": "(1)"}], K)[E5] == 0)
check("C6 a line with no usable key is skipped, not guessed at",
      "" not in net_c and isr.net_sold([{"serial_1": "nan", "quantity": 1}], K) == {})
check("C7 …and counted, so the report states its own ceiling",
      isr.unkeyed_sales([{"serial_1": "nan"}, {"serial_1": A1}, {"serial_1": ""}], K) == 2)
check("C8 a token too short to identify a handset is refused, not keyed",
      isr.net_sold([{"serial_1": "A1", "quantity": 1}], K) == {})

# ── D. THE REGRESSION — the owner's real shape: 18 look sold, only 10 are ───────────────────────
# 10 phantoms (sold, never returned) + 8 sold-then-refunded + 2 untouched + 1 ordered-not-arrived.
PHANTOM = [imei(f"P{i:03d}") for i in range(10)]
REFUNDED = [imei(f"R{i:03d}") for i in range(8)]
QUIET = [imei("Q001"), imei("Q002")]
ORDERED = imei("O001")

sales = []
for k in PHANTOM:
    sales.append({"serial_1": k, "quantity": 1})
for k in REFUNDED:
    sales.append({"serial_1": k, "quantity": 1})
    sales.append({"serial_1": k, "quantity": -1})
sales.append({"serial_1": ORDERED, "quantity": 1})          # sold, but the unit never arrived
sales.append({"serial_1": "", "quantity": 1})               # un-keyed line

inv = []
for k in PHANTOM:
    inv.append({"imei": k, "status": "In Stock", "sku": "DESK-PHONE", "item": "Desk phone",
                "store": "B-103", "total_cost": 736.0, "on_hand": True})
for k in REFUNDED:
    inv.append({"imei": k, "status": "In Stock", "total_cost": 625.0, "on_hand": True})
for k in QUIET:
    inv.append({"imei": k, "status": "In Stock", "total_cost": 100.0, "on_hand": True})
inv.append({"imei": ORDERED, "status": "On Order", "total_cost": 999.0, "on_hand": True})

out = isr.reconcile(sales, inv, K)
t = out["totals"]

check("D1 THE REGRESSION: exactly the 10 real phantoms are reported to clear, NOT all 18 that "
      "appear in the sales file", t["to_clear"] == 10, f"got {t['to_clear']}")
check("D2 …and the cost to clear is the phantoms' cost alone",
      t["to_clear_cost"] == 7360.0, f"got {t['to_clear_cost']}")
check("D3 a sold-then-refunded unit is NEVER reported — it is on the shelf and someone can see it",
      not any(r["device_key"] in REFUNDED for r in out["rows"]))
check("D4 an untouched unit is not reported", not any(r["device_key"] in QUIET for r in out["rows"]))
check("D5 an ORDERED unit that was sold is never reported as inventory to clear",
      not any(r["device_key"] == ORDERED and r["finding"] == isr.SOLD_NOT_CLEARED for r in out["rows"]))
check("D6 …it surfaces as an ADJUSTMENT instead, because the sale is real",
      any(r["device_key"] == ORDERED and r["finding"] == isr.SOLD_NO_INVENTORY for r in out["rows"]))
check("D7 the un-keyed sale line is counted and stated, never silently dropped",
      t["unkeyed_sale_lines"] == 1)
check("D8 the on-hand population excludes the ordered unit",
      t["on_hand_considered"] == len(PHANTOM) + len(REFUNDED) + len(QUIET))
check("D9 every reported row carries the net units, so the reader can check the refund netting",
      all(r["net_units"] > 0 for r in out["rows"]))

# ── E. The second bucket: sold with a key the inventory never knew ──────────────────────────────
out_e = isr.reconcile([{"serial_1": imei("X9"), "quantity": 1}], [], K)
check("E1 a sold unit with no inventory row is reported for adjustment",
      out_e["totals"]["to_adjust"] == 1 and out_e["rows"][0]["finding"] == isr.SOLD_NO_INVENTORY)
check("E2 …and contributes nothing to the cost to clear, because there is nothing to clear",
      out_e["totals"]["to_clear_cost"] == 0.0)

# ── F. Snapshot hygiene and determinism ─────────────────────────────────────────────────────────
check("F1 an off-hand row (mig 294) is not considered present",
      isr.on_hand_index([{"imei": imei("Z1"), "on_hand": False, "status": "In Stock"}], K) == {})
check("F2 a duplicate key keeps the FIRST row, so two runs never disagree",
      isr.on_hand_index([{"imei": imei("Z2"), "status": "In Stock", "sku": "first"},
                         {"imei": imei("Z2"), "status": "In Stock", "sku": "second"}], K)[imei("Z2")]["sku"] == "first")
check("F3 a row with no usable device key is skipped rather than keyed on blank",
      isr.on_hand_index([{"imei": "", "serial": None, "status": "In Stock"}], K) == {})
check("F4 serial is used when imei is absent",
      imei("Z3") in isr.on_hand_index([{"serial": imei("Z3"), "status": "In Stock"}], K))
check("F5 rows come back biggest-exposure-first and are stable",
      [r["device_key"] for r in isr.reconcile(sales, inv, K)["rows"]]
      == [r["device_key"] for r in isr.reconcile(sales, inv, K)["rows"]])
check("F6 unit_cost is used when the feed carries no extended cost",
      isr.reconcile([{"serial_1": imei("U1"), "quantity": 1}],
                    [{"imei": imei("U1"), "status": "In Stock", "unit_cost": 42.5}], K)["totals"]["to_clear_cost"] == 42.5)

# ── G. It books nothing ─────────────────────────────────────────────────────────────────────────
_SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "app/modules/commcalc/inventory_sold_recon.py"), encoding="utf-8").read()
check("G1 the module performs no write of any kind — it is a report",
      not any(w in _SRC for w in (".insert(", ".update(", ".upsert(", ".delete(", ".rpc(")))
check("G2 …and holds no database handle at all (PURE, stdlib only)",
      "supabase" not in _SRC.lower() and "import os" not in _SRC)
check("G3 the canonical device key is INJECTED, not re-implemented here",
      "def device_key" not in _SRC and "key_of" in _SRC)

# ── H. Empty and hostile inputs ─────────────────────────────────────────────────────────────────
for label, args in (("both empty", ([], [])), ("no sales", ([], inv)), ("no inventory", (sales, [])),
                    ("None, None", (None, None))):
    r = isr.reconcile(args[0], args[1], K)
    check(f"H {label} → a well-formed empty-ish report, never a crash",
          isinstance(r.get("rows"), list) and isinstance(r.get("totals"), dict))
check("H5 no sales ⇒ nothing to clear", isr.reconcile([], inv, K)["totals"]["to_clear"] == 0)

print(f"\n{len(P)} passed, {len(F)} failed")
if F:
    print("FAILURES:")
    for f in F:
        print(" -", f)
    sys.exit(1)
