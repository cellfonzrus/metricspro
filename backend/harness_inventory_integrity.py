"""PROOF: INVENTORY INTEGRITY — the POS's units crashed against the sale lines by invoice AND the commission report,
flagged, assigned with one click, held until management verifies; the duplicate-IMEI guard on every landing path;
the adjustment ledger on every status change (index §11b; owner 2026-09-24).

Owner: *"it is not possible that 383 imei have not ben sold but appering in the invnetory so they have to be crashed
agains the sales report by invoice and the commission received reports to … it shoudl apprear as a flag and also
highlight which customer it was sold to from commisison report and have the ability to assign it tot hte customer
with one click … the flag still stays there till verified by the management … platform wide"*.

THE REGRESSION FIXTURE mirrors the read-only check on one live tenant, ANONYMISED (no real name, IMEI, store or
invoice): 383 units in stock —
    4  commission kept, no sale line and no receipt            → sold_no_receipt
    6  sold on the sales report, commission kept, still here   → sold_still_on_hand
    2  commission kept, but the sale shows a return            → returned_commission_kept
    8  returned / reversed (5 with the commission charged back, 3 with none) → NOT flagged (legitimately back)
  363  no sale and no commission                               → NOT flagged
and no duplicate IMEI. The commission rows are shaped like the feed (several component rows per device, a
reversal line negative with the chargeback cell set).

§A the engine (registry fact, classifier table)  §B the live-shaped regression + negative controls
§C duplicate / received-after-sold               §D end to end over the in-memory client through the REAL endpoints:
scan idempotent, one-click assign (customer through THE matcher), the flag stays until verify, dismiss, reject,
re-open only on changed evidence  §E the 409 guard on EVERY landing path (receive, edit, onboarding bring-over)
§F a ledger row on every status change; the compensation  §G org scoping  §H the pure state machine.

DB-free: `harness_intake_fakes.FakeDB` (declared columns = the migrations). Run:  python3 backend/harness_inventory_integrity.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import HTTPException                                  # noqa: E402

from harness_intake_fakes import FakeDB                             # noqa: E402
from app.modules.commcalc import data_lineage_registry as dlr      # noqa: E402
from app.modules.commcalc import inventory_sold_recon as isr       # noqa: E402
from app.modules.commcalc.device_cost_recon import device_key      # noqa: E402  THE canonical key
from app.modules.pos import inventory_integrity as ii              # noqa: E402
from app.modules.pos import inventory_integrity_router as iir      # noqa: E402
from app.modules.pos import router as pr                           # noqa: E402
from app.modules.core import onboarding as ob                      # noqa: E402

P, F = [], []


def check(name, ok, detail=""):
    (P if ok else F).append(name if ok else f"{name} :: {str(detail)[:500]}")
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"   {str(detail)[:500]}"))


def raises(fn, status=None):
    try:
        fn()
    except HTTPException as e:
        return e if status is None or e.status_code == status else None
    except Exception:
        return None
    return None


ORG, ORG2 = "0f0f0f0f-0000-4000-8000-000000000001", "0f0f0f0f-0000-4000-8000-000000000002"


def imei(n):
    return "35" + f"{n:013d}"        # 15 digits, anonymous


# ══ the anonymised live-shaped fixture ════════════════════════════════════════════════════════════════════
def fixture():
    units, sales, com = [], [], []
    n = 0

    def unit(i, received="2025-01-10"):
        units.append({"id": f"u-{i:04d}", "org_id": ORG, "product_id": "p-1", "store_code": "S-01",
                      "serial_number": imei(i), "imei": imei(i), "condition": "new", "status": "in_stock", "cost": 500.0,
                      "date_received": received, "created_at": "2026-09-21T01:00:00Z"})

    def comm_rows(i, sold_on, reversed_=False, customer=None, mdn=None):
        cust = customer or f"Test Customer{i:04d}"
        for amt, name in ((120.0, "device rebate"), (35.0, "rate plan rebate"), (5.0, "feature")):
            com.append({"org_id": ORG, "imei": imei(i), "earned_amount": amt, "quantity": 1, "customer_name": cust,
                        "customer_ref": f"CR{i:05d}", "mdn": mdn or f"555{i:07d}", "invoice_no": f"INV-{i:05d}",
                        "sold_on": sold_on, "device_name": "PHONE MODEL X", "device_sku": "SKU-X", "rate_plan": "PLAN A",
                        "store": "Store One", "charge_back": "No", "rebate_name": name})
            if reversed_:
                com.append({"org_id": ORG, "imei": imei(i), "earned_amount": -amt, "quantity": -1, "customer_name": cust,
                            "customer_ref": f"CR{i:05d}", "mdn": mdn or f"555{i:07d}", "invoice_no": f"INV-{i:05d}",
                            "sold_on": "2025-04-01", "device_name": "PHONE MODEL X", "device_sku": "SKU-X",
                            "rate_plan": "PLAN A", "store": "Store One", "charge_back": "Yes", "rebate_name": name})

    def sale(i, qty, day, tid=None, customer=None):
        sales.append({"org_id": ORG, "period": "2025-03", "serial_1": imei(i), "quantity": qty, "trans_id": tid or f"INV-{i:05d}",
                      "trans_date": day, "customer": customer, "mdn": None})

    # 4 — commission kept, no sale line, no receipt
    for _ in range(4):
        n += 1; unit(n); comm_rows(n, "2025-03-02")
    # 6 — sold on the sales report (by invoice), commission kept, still in stock
    for _ in range(6):
        n += 1; unit(n); sale(n, 1, "2025-03-05", customer=f"Line Customer{n:04d}"); comm_rows(n, "2025-03-05")
    # 2 — commission kept, the sale shows a return (nets to zero)
    for _ in range(2):
        n += 1; unit(n); sale(n, 1, "2025-03-06"); sale(n, -1, "2025-03-20", tid=f"RET-{n:05d}"); comm_rows(n, "2025-03-06")
    # 8 — returned / reversed: 5 with the commission charged back, 3 with no commission at all
    for j in range(8):
        n += 1; unit(n); sale(n, 1, "2025-03-07"); sale(n, -1, "2025-03-21", tid=f"RET-{n:05d}")
        if j < 5:
            comm_rows(n, "2025-03-07", reversed_=True)
    # 363 — no sale, no commission
    for _ in range(363):
        n += 1; unit(n)
    # sale lines for devices NOT in inventory, and an unkeyed line — neither may create a flag
    sales.append({"org_id": ORG, "period": "2025-03", "serial_1": imei(9000), "quantity": 1, "trans_id": "INV-OTHER",
                  "trans_date": "2025-03-09", "customer": None, "mdn": None})
    sales.append({"org_id": ORG, "period": "2025-03", "serial_1": "", "quantity": 1, "trans_id": "INV-ACC",
                  "trans_date": "2025-03-09", "customer": None, "mdn": None})
    return units, sales, com


UNITS, SALES, COM = fixture()

print("\n§A — the engine: the registry fact and the one classifier")
check("A1 the commission-per-device feed is ONE registered fact (a constant + its column map), dereferenced by the ingest list",
      dlr.COMMISSION_PER_DEVICE_FEED == "raw_vendor_rebate" and dlr.COMMISSION_PER_DEVICE_FEED in dlr.INGEST_TABLES_BY_MODULE["commcalc"]
      and dlr.COMMISSION_PER_DEVICE_COLUMNS["earned"] == "earned_amount"
      and dlr.commission_per_device_select().split(",")[0] == dlr.COMMISSION_PER_DEVICE_COLUMNS["device"])
_ci = isr.commission_index([{"imei": imei(1) + ".0", "earned_amount": 100}, {"imei": imei(1), "earned_amount": -100, "charge_back": "Yes"},
                            {"imei": imei(2), "earned_amount": 50, "customer_name": "A B", "sold_on": "2025-01-02T10:00:00"},
                            {"imei": "", "earned_amount": 999}], device_key)
check("A2 commission nets chargebacks per device through the REAL device_key (a '.0' spelling is the same device; a keyless row counts for nothing)",
      _ci[imei(1)]["net_earned"] == 0 and not _ci[imei(1)]["kept"] and _ci[imei(1)]["reversed"] and _ci[imei(1)]["rows"] == 2
      and _ci[imei(2)]["kept"] and _ci[imei(2)]["sold_on"] == "2025-01-02" and len(_ci) == 2, _ci)
_S = {"net": 1.0, "last_sold_on": "2025-03-01"}
_R = {"net": 0.0}
_K = {"kept": True, "sold_on": "2025-03-02"}
_X = {"kept": False, "sold_on": "2025-03-02"}
check("A3 the ONE classifier: sold→still on hand; kept+no line→no receipt; kept+return→review; return w/o kept→not flagged; nothing→not flagged",
      isr.classify_unit(_S, None)[0] == isr.SOLD_STILL_ON_HAND and isr.classify_unit(None, _K)[0] == isr.SOLD_NO_RECEIPT
      and isr.classify_unit(_R, _K)[0] == isr.RETURNED_COMMISSION_KEPT and isr.classify_unit(_R, _X)[1] == isr.NOT_FLAGGED_RETURNED
      and isr.classify_unit(_R, None)[1] == isr.NOT_FLAGGED_RETURNED and isr.classify_unit(None, None)[1] == isr.NOT_FLAGGED_NO_EVIDENCE)
check("A4 received AFTER the sale date → received_after_sold (same day is not after)",
      isr.classify_unit(_S, None, "2025-03-02")[0] == isr.RECEIVED_AFTER_SOLD and isr.classify_unit(_S, None, "2025-03-01")[0] == isr.SOLD_STILL_ON_HAND
      and isr.classify_unit(None, _K, "2026-01-01")[0] == isr.RECEIVED_AFTER_SOLD)

print("\n§B — THE REGRESSION: the live tenant's 383 on-hand units, anonymised")
REP = isr.integrity(UNITS, SALES, COM, device_key, ii.is_live)
T = REP["totals"]
check("B1 383 live units; exactly 4 sold_no_receipt, 6 sold_still_on_hand, 2 returned_commission_kept, no duplicate, none received after sold",
      T["units_live"] == 383 and T["by_kind"] == {"sold_no_receipt": 4, "sold_still_on_hand": 6, "returned_commission_kept": 2,
                                                   "received_after_sold": 0, "duplicate_on_hand": 0}, T)
check("B2 the rest is ACCOUNTED FOR, not dropped: 8 returned / reversed (not flagged) + 363 with no sale and no commission",
      T["not_flagged"] == {"returned_no_commission_kept": 8, "no_sale_no_commission": 363} and T["flagged"] + 8 + 363 == 383, T)
_nr = [r for r in REP["rows"] if r["kind"] == "sold_no_receipt"]
check("B3 each flag names the customer FROM THE COMMISSION REPORT, the invoice, the sold-on date and the net commission kept",
      all(r["customer"]["source"] == "commission" and r["customer"]["name"].startswith("Test Customer") and r["customer"]["mobile"]
          and r["invoice_no"].startswith("INV-") and r["sold_on"] == "2025-03-02" and r["commission"]["net_earned"] == 160.0
          for r in _nr) and len(_nr) == 4, _nr[:1])
_sh = [r for r in REP["rows"] if r["kind"] == "sold_still_on_hand"]
check("B4 a sold-still-on-hand flag carries its sale LINE by invoice (invoice, date, qty) beside the commission",
      all(r["sale"]["lines"][0]["invoice"].startswith("INV-") and r["sale"]["net"] == 1 and r["commission"]["kept"] for r in _sh))
check("B5 cost of the flagged units is summed (12 × 500)", T["flagged_cost"] == 6000.0, T["flagged_cost"])
check("B6 deterministic: the same data twice gives the same rows", isr.integrity(UNITS, SALES, COM, device_key, ii.is_live)["rows"] == REP["rows"])
# negative controls — the two netting rules are what make the report usable
_real_net = isr.net_sold
isr.net_sold = lambda rows, key_of, *a, **k: {key_of(r.get("serial_1")): 1.0 for r in rows or [] if key_of(r.get("serial_1"))}
try:
    _nc1 = isr.integrity(UNITS, SALES, COM, device_key, ii.is_live)["totals"]["by_kind"]
finally:
    isr.net_sold = _real_net
check("B7 NEG: without refund netting the 10 returned units read as sold (16 sold_still_on_hand) → the regression goes RED",
      _nc1["sold_still_on_hand"] == 16 and _nc1 != T["by_kind"], _nc1)
_gross = [dict(r, earned_amount=abs(r["earned_amount"])) for r in COM]
_nc2 = isr.integrity(UNITS, SALES, _gross, device_key, ii.is_live)["totals"]["by_kind"]
check("B8 NEG: summing commission unsigned (chargebacks ignored) flags the 5 charged-back returns → RED",
      _nc2["returned_commission_kept"] == 7, _nc2)
_aging = [{"imei": u["imei"], "serial": u["serial_number"], "on_hand": True, "unit_cost": 500.0, "status": "In Stock"} for u in UNITS]
_rc = isr.reconcile(SALES, _aging, device_key, commission_rows=COM)
_rc0 = isr.reconcile(SALES, _aging, device_key)
check("B9 THE SIBLING (Inventory vs Sold on the landed aging snapshot) rides the SAME classifier: 4 sold-no-receipt, 2 review, 6 to clear with the customer",
      _rc["totals"]["sold_no_receipt"] == 4 and _rc["totals"]["returned_commission_kept"] == 2 and _rc["totals"]["to_clear"] == 6
      and all(r["customer"]["source"] == "commission" for r in _rc["rows"] if r["finding"] == "sold_not_cleared"), _rc["totals"])
check("B10 …and without the commission source it is byte-identical to before (no new keys, 6 to clear)",
      "sold_no_receipt" not in _rc0["totals"] and _rc0["totals"]["to_clear"] == 6 and all("commission" not in r for r in _rc0["rows"]))

print("\n§C — duplicate on hand, received after sold, the landing check")
_dup_units = [
    {"id": "d-1", "serial_number": "SER-AAA-1", "imei": imei(7001), "status": "in_stock", "store_code": "S-01", "cost": 300, "created_at": "2026-01-01"},
    {"id": "d-2", "serial_number": imei(7001), "imei": None, "status": "in_stock", "store_code": "S-02", "cost": 300, "created_at": "2026-02-01"},
    {"id": "d-3", "serial_number": imei(7002), "imei": imei(7002), "status": "sold", "store_code": "S-01", "cost": 300, "created_at": "2026-01-01"},
    {"id": "d-4", "serial_number": imei(7002), "imei": imei(7002), "status": "in_stock", "store_code": "S-01", "cost": 300, "created_at": "2026-03-01"},
    {"id": "d-5", "serial_number": imei(7003), "imei": imei(7003), "status": "in_stock", "store_code": "S-01", "cost": 300,
     "date_received": "2026-05-01", "created_at": "2026-05-01"},
]
_dcom = [{"imei": imei(7003), "earned_amount": 90, "sold_on": "2026-04-01", "customer_name": "Test Buyer"}]
_dr = isr.integrity(_dup_units, [], _dcom, device_key, ii.is_live)
_dd = [r for r in _dr["rows"] if r["kind"] == "duplicate_on_hand"]
check("C1 the same device on two LIVE units (one by IMEI column, one by serial) → ONE duplicate_on_hand flag on the NEWEST unit, both listed",
      len(_dd) == 1 and _dd[0]["unit_id"] == "d-2" and _dd[0]["unit_ids"] == ["d-1", "d-2"], _dd)
check("C2 a sold unit and a live unit of the same device is NOT a duplicate on hand (only live units count)",
      not any(r["device_key"] == imei(7002) and r["kind"] == "duplicate_on_hand" for r in _dr["rows"]))
check("C3 a unit received (2026-05-01) after its device was sold (commission 2026-04-01) → received_after_sold, with the buyer",
      any(r["kind"] == "received_after_sold" and r["unit_id"] == "d-5" and r["customer"]["name"] == "Test Buyer" for r in _dr["rows"]), _dr["rows"])
_chk = isr.receive_check({"serial_number": imei(7002)}, _dup_units, device_key, ii.is_live)
check("C4 receive_check: an existing record of the device (any status) blocks, with the existing units named",
      _chk["blocking"] and _chk["reasons"] == ["duplicate_on_hand"] and {u["id"] for u in _chk["existing"]} == {"d-3", "d-4"}, _chk)
_chk2 = isr.receive_check({"serial_number": imei(8001)}, [], device_key, ii.is_live, None, {"kept": True, "sold_on": "2026-01-02"})
check("C5 receive_check: a new device whose commission was kept blocks as already_sold; a fresh device does not",
      _chk2["blocking"] and _chk2["reasons"] == ["already_sold"]
      and not isr.receive_check({"serial_number": imei(8002)}, [], device_key, ii.is_live)["blocking"])

# ══ §D end to end over the in-memory client ══════════════════════════════════════════════════════════════
_so = isr.integrity([{"id": "s-1", "serial_number": imei(7100), "imei": imei(7100), "status": "in_stock", "cost": 1}],
                    [{"serial_1": imei(7100), "quantity": 1, "trans_id": "INV-7100", "trans_date": "2026-01-02", "customer": None}],
                    [], device_key, ii.is_live, invoice_customers={"INV-7100": "Header Customer"})
check("C6 no commission line: the customer comes from the sale line, else ITS INVOICE HEADER (sales by invoice)",
      _so["rows"][0]["customer"] == {"name": "Header Customer", "mobile": None, "ref": None, "source": "sales"}, _so["rows"])

print("\n§D — end to end: scan, one-click assign, verify / dismiss / reject, over the in-memory client")
CTX = {
    "mgr": {"perms": {"pos_inventory_adjust": True, "pos_inventory_verify": True, "scope": "store"}, "role": "manager", "super_admin": False},
    "adj": {"perms": {"pos_inventory_adjust": True, "scope": "store"}, "role": "lead", "super_admin": False},
    "clerk": {"perms": {"scope": "store"}, "role": "clerk", "super_admin": False},
}
KEYSETS = {"mgr": None, "adj": None, "clerk": None, "s2": {"S-02"}}
_saved = {k: getattr(pr, k) for k in ("sb", "_caller_ctx", "_caller_employee", "_caller_store_keyset")}
_ob_sb = ob.sb


def world():
    db = FakeDB()
    db.declared["products"] = ["id", "org_id", "upc", "product_code", "short_name", "full_name"]
    # the onboarding bring-over's two sources + its upload trace (as harness_pos_onboarding §P18 declares them)
    db.declared["asset_ledger"] = ["id", "org_id", "esn_imei", "device_model", "store", "acquired_date", "owed_to_vip",
                                   "category", "date_sold"]
    db.declared["upload_trace"] = ["id", "org_id", "created_at", "source", "filename", "upload_type", "target_table", "rows_in",
                                   "rows_saved", "status"]
    db.seed("products", [{"id": "p-1", "org_id": ORG, "upc": None, "product_code": 1, "short_name": "PHONE MODEL X", "full_name": "PHONE MODEL X"},
                         {"id": "p-9", "org_id": ORG2, "upc": None, "product_code": 1, "short_name": "PHONE", "full_name": "PHONE"}])
    db.seed("inventory_serial", [dict(u) for u in UNITS])
    db.seed("raw_sales", [dict(s) for s in SALES])
    db.seed("raw_vendor_rebate", [{k: v for k, v in c.items()} for c in COM])
    db.seed("customers", [])
    db.tables.setdefault("inventory_flags", [])
    db.tables.setdefault("inventory_adjustments", [])
    db.tables.setdefault("raw_sales_invoice", [])
    pr.sb = lambda: db
    pr._caller_ctx = lambda auth, org: CTX.get(auth)
    pr._caller_employee = lambda auth, org: {"mgr": "E-MGR", "adj": "E-ADJ", "clerk": "E-CLK"}.get(auth, "")
    pr._caller_store_keyset = lambda auth, org: KEYSETS.get(auth)
    return db


def flags(db, org=ORG):
    return [f for f in db.tables.get("inventory_flags") or [] if f["org_id"] == org]


def ledger(db, org=ORG):
    return [a for a in db.tables.get("inventory_adjustments") or [] if a["org_id"] == org]


def unit_of(db, uid):
    return next(u for u in db.tables["inventory_serial"] if u["id"] == uid)


try:
    db = world()
    g0 = iir.integrity_report(authorization="mgr", org_id=ORG)
    check("D1 GET before any scan: the 12 findings, all 'new', the summary counts and cost, the basis per source, the permissions",
          len(g0["rows"]) == 12 and all(r["flag_status"] == "new" for r in g0["rows"]) and g0["summary"]["open"] == 12
          and g0["summary"]["open_cost"] == 6000.0 and g0["basis"]["commission"]["read_ok"] and g0["basis"]["sales"]["read_ok"]
          and g0["can"] == {"adjust": True, "verify": True} and g0["totals"]["units_live"] == 383, g0["summary"])
    check("D2 every row shows product, store, kind label, the commission customer, invoice, date and commission",
          all(r["product_name"] == "PHONE MODEL X" and r["store_code"] == "S-01" and r["kind_label"] for r in g0["rows"])
          and all((r.get("customer") or {}).get("name") for r in g0["rows"] if r["kind"] != "duplicate_on_hand"))
    check("D3 a clerk may LOOK but not scan (pos_inventory_adjust / pos_inventory_verify)",
          raises(lambda: iir.scan_endpoint(authorization="clerk", org_id=ORG), 403) is not None)
    s1 = iir.scan_endpoint(authorization="mgr", org_id=ORG)
    s2 = iir.scan_endpoint(authorization="mgr", org_id=ORG)
    check("D4 scan persists 12 open flags; a second scan inserts NONE and refreshes the 12 in place (idempotent)",
          s1["inserted"] == 12 and s2["inserted"] == 0 and s2["refreshed"] == 12 and len(flags(db)) == 12
          and all(f["status"] == "open" and f["evidence_hash"] for f in flags(db)), (s1, s2))
    fl_nr = next(f for f in flags(db) if f["kind"] == "sold_no_receipt")
    u_nr = fl_nr["unit_id"]
    check("D5 assign needs pos_inventory_adjust", raises(lambda: iir.assign_flag(fl_nr["id"], {}, authorization="clerk", org_id=ORG), 403) is not None)
    a = iir.assign_flag(fl_nr["id"], {"note": "sold at the counter, no receipt rung"}, authorization="adj", org_id=ORG)
    cust = db.tables["customers"]
    u = unit_of(db, u_nr)
    led = [x for x in ledger(db) if x["flag_id"] == fl_nr["id"]]
    check("D6 ONE CLICK: the customer is created through THE matcher from the commission name + phone (first + last, the phone digits)",
          a["customer_id"] and len(cust) == 1 and cust[0]["first_name"] == "Test" and cust[0]["last_name"].startswith("Customer")
          and cust[0]["phone_primary"] and cust[0]["org_id"] == ORG, cust)
    check("D7 …the unit leaves stock as SOLD dated the commission's sold-on day",
          u["status"] == "sold" and str(u["sold_at"]).startswith("2025-03-02"), u)
    check("D8 …ONE ledger row: out, assign_sold, in_stock → sold, the flag, the customer, who, the note",
          len(led) == 1 and led[0]["direction"] == "out" and led[0]["reason"] == "assign_sold" and led[0]["from_status"] == "in_stock"
          and led[0]["to_status"] == "sold" and led[0]["customer_id"] == a["customer_id"] and led[0]["created_by"] == "E-ADJ"
          and led[0]["note"], led)
    fl_after = next(f for f in flags(db) if f["id"] == fl_nr["id"])
    check("D9 …the flag is ASSIGNED (customer, by, at) — not closed", fl_after["status"] == "assigned" and fl_after["assigned_by"] == "E-ADJ"
          and fl_after["assigned_customer_id"] == a["customer_id"] and fl_after["assigned_at"])
    g1 = iir.integrity_report(authorization="mgr", org_id=ORG)
    row = next(r for r in g1["rows"] if r["flag_id"] == fl_nr["id"])
    check("D10 THE FLAG STAYS VISIBLE after assign (the unit left the shelf, so the engine no longer finds it — shown from its evidence)",
          row["flag_status"] == "assigned" and row["still_found"] is False and row["customer"]["name"] == fl_nr["customer_name"]
          and len(g1["rows"]) == 12, row)
    s3 = iir.scan_endpoint(authorization="mgr", org_id=ORG)
    check("D11 a scan after assign neither duplicates nor closes it", s3["inserted"] == 0 and len(flags(db)) == 12
          and next(f for f in flags(db) if f["id"] == fl_nr["id"])["status"] == "assigned", s3)
    check("D12 a second assign of the same flag is refused (409)", raises(lambda: iir.assign_flag(fl_nr["id"], {}, authorization="adj", org_id=ORG), 409) is not None)
    check("D13 VERIFY is management-only (pos_inventory_verify): the adjuster who assigned cannot",
          raises(lambda: iir.verify_flag(fl_nr["id"], {}, authorization="adj", org_id=ORG), 403) is not None)
    v = iir.verify_flag(fl_nr["id"], {"note": "checked the carrier statement"}, authorization="mgr", org_id=ORG)
    check("D14 the manager verifies: assigned → verified (by, at)", v["flag"]["status"] == "verified" and v["flag"]["verified_by"] == "E-MGR")
    g2 = iir.integrity_report(authorization="mgr", org_id=ORG)
    g2c = iir.integrity_report(include_closed=True, authorization="mgr", org_id=ORG)
    check("D15 a verified flag leaves the default view (11 remain) and is still listed with include_closed",
          len(g2["rows"]) == 11 and any(r["flag_id"] == fl_nr["id"] and r["flag_status"] == "verified" for r in g2c["rows"]))
    # dismiss
    fl_rv = next(f for f in flags(db) if f["kind"] == "returned_commission_kept")
    check("D16 dismiss needs a note (400) and the manager key (403)",
          raises(lambda: iir.dismiss_flag(fl_rv["id"], {}, authorization="mgr", org_id=ORG), 400) is not None
          and raises(lambda: iir.dismiss_flag(fl_rv["id"], {"note": "x"}, authorization="adj", org_id=ORG), 403) is not None)
    d = iir.dismiss_flag(fl_rv["id"], {"note": "a real return — the carrier will claw back next cycle"}, authorization="mgr", org_id=ORG)
    check("D17 dismissed with the note; the unit is NOT touched and no ledger row is written",
          d["flag"]["status"] == "dismissed" and unit_of(db, fl_rv["unit_id"])["status"] == "in_stock"
          and not [x for x in ledger(db) if x["flag_id"] == fl_rv["id"]])
    s4 = iir.scan_endpoint(authorization="mgr", org_id=ORG)
    check("D18 a scan NEVER re-opens a dismissed flag while the evidence is the same", s4["inserted"] == 0 and s4["unchanged"] == 1
          and next(f for f in flags(db) if f["id"] == fl_rv["id"])["status"] == "dismissed", s4)
    # the evidence changes: another commission line on that device
    db.seed("raw_vendor_rebate", [{"org_id": ORG, "imei": fl_rv["imei_key"], "earned_amount": 40.0, "sold_on": "2025-05-01",
                                   "customer_name": "Test Customer", "invoice_no": "INV-LATE", "charge_back": "No"}])
    s5 = iir.scan_endpoint(authorization="mgr", org_id=ORG)
    re_rows = [f for f in flags(db) if f["imei_key"] == fl_rv["imei_key"] and f["kind"] == "returned_commission_kept"]
    check("D19 …but when the evidence CHANGES the scan re-opens it as a NEW open flag (the dismissed row kept as history, the note says why)",
          s5["inserted"] == 1 and s5["reopened"] == 1 and sorted(f["status"] for f in re_rows) == ["dismissed", "open"]
          and "re-opened" in next(f for f in re_rows if f["status"] == "open")["note"], (s5, re_rows))
    # reject
    fl_sh = next(f for f in flags(db) if f["kind"] == "sold_still_on_hand")
    iir.assign_flag(fl_sh["id"], {}, authorization="mgr", org_id=ORG)
    check("D20 reject needs a note", raises(lambda: iir.verify_flag(fl_sh["id"], {"approve": False}, authorization="mgr", org_id=ORG), 400) is not None)
    rj = iir.verify_flag(fl_sh["id"], {"approve": False, "note": "the customer never took it"}, authorization="mgr", org_id=ORG)
    led_sh = sorted((x for x in ledger(db) if x["flag_id"] == fl_sh["id"]), key=lambda x: x["reason"])
    check("D21 REJECT: the unit comes back IN (sold → in_stock) through the ledger and the flag re-opens",
          rj["flag"]["status"] == "open" and unit_of(db, fl_sh["unit_id"])["status"] == "in_stock"
          and unit_of(db, fl_sh["unit_id"])["sold_at"] is None
          and [(x["reason"], x["direction"], x["from_status"], x["to_status"]) for x in led_sh]
          == [("assign_sold", "out", "in_stock", "sold"), ("reject_assign", "in", "sold", "in_stock")], led_sh)
    _shf = [f for f in flags(db) if f["kind"] == "sold_still_on_hand"]
    check("D22 a sold-still-on-hand unit whose sale line names another customer: the COMMISSION report's customer is the one shown (the owner's ask)",
          _shf and all(f["customer_name"].startswith("Test Customer") and f["evidence"]["sale"]["customer"].startswith("Line Customer")
                       for f in _shf), [(f["customer_name"], (f["evidence"].get("sale") or {}).get("customer")) for f in _shf][:2])
    # a second assign of a customer already in the book matches, not duplicates
    before = len(db.tables["customers"])
    fl_nr2 = [f for f in flags(db) if f["kind"] == "sold_no_receipt" and f["status"] == "open"][0]
    db.tables["customers"].append({"id": "c-existing", "org_id": ORG, "first_name": "Test",
                                   "last_name": fl_nr2["customer_name"].split(" ", 1)[1], "phone_primary": None})
    a2 = iir.assign_flag(fl_nr2["id"], {}, authorization="adj", org_id=ORG)
    check("D23 assign MATCHES an existing customer through the one matcher (the full name — no second customer made)",
          a2["customer_id"] == "c-existing" and len(db.tables["customers"]) == before + 1 and not a2["customer_created"], a2)
finally:
    for k, v in _saved.items():
        setattr(pr, k, v)

# ══ §E the 409 guard on every landing path ═══════════════════════════════════════════════════════════════
print("\n§E — the duplicate / already-sold guard on EVERY landing path")
try:
    db = world()
    new_body = {"product_id": "p-1", "serial_number": imei(5), "imei": imei(5), "store_code": "S-01", "cost": 500}
    e409 = raises(lambda: pr.add_inventory_serial(dict(new_body), authorization="clerk", org_id=ORG), 409)
    check("E1 POST /pos/inventory/serial with an IMEI already in stock → 409 with a STRUCTURED payload (the unit, its status, the sale / commission evidence)",
          e409 is not None and e409.detail["code"] == "inventory_duplicate" and e409.detail["check"]["existing"][0]["status"] == "in_stock"
          and "duplicate_on_hand" in e409.detail["check"]["reasons"] and e409.detail["check"]["commission"]["kept"]
          and "Duplicate IMEI" in e409.detail["message"], getattr(e409, "detail", None))
    n_before = len(db.tables["inventory_serial"])
    ok = pr.add_inventory_serial({**new_body, "confirm_duplicate": True}, authorization="clerk", org_id=ORG)
    led_r = [x for x in ledger(db) if x["unit_id"] == ok["unit"]["id"]]
    check("E2 'Receive anyway' (confirm_duplicate) lands it — and the ledger says it was received over the warning, and by whom",
          len(db.tables["inventory_serial"]) == n_before + 1 and len(led_r) == 1 and led_r[0]["reason"] == "received_duplicate_confirmed"
          and led_r[0]["direction"] == "in" and led_r[0]["created_by"] == "E-CLK" and "duplicate_on_hand" in (led_r[0]["note"] or ""), led_r)
    sold_only = {"product_id": "p-1", "serial_number": imei(9500), "store_code": "S-01"}
    db.seed("raw_vendor_rebate", [{"org_id": ORG, "imei": imei(9500), "earned_amount": 80.0, "sold_on": "2025-06-01",
                                   "customer_name": "Test Earlier", "invoice_no": "INV-9500", "charge_back": "No"}])
    e2 = raises(lambda: pr.add_inventory_serial(dict(sold_only), authorization="clerk", org_id=ORG), 409)
    check("E3 a NEW device whose commission was already kept → 409 already_sold (sold on, to whom)",
          e2 is not None and e2.detail["check"]["reasons"] == ["already_sold"] and e2.detail["check"]["customer"]["name"] == "Test Earlier"
          and e2.detail["check"]["sold_on"] == "2025-06-01", getattr(e2, "detail", None))
    fresh = pr.add_inventory_serial({"product_id": "p-1", "serial_number": imei(9600), "store_code": "S-01"}, authorization="clerk", org_id=ORG)
    check("E4 a fresh device lands with no pop-up and a 'received' ledger row",
          fresh["landing"]["guard"] is None and any(x["unit_id"] == fresh["unit"]["id"] and x["reason"] == "received" for x in ledger(db)))
    e3 = raises(lambda: pr.update_inventory_serial(fresh["unit"]["id"], {"imei": imei(6)}, authorization="adj", org_id=ORG), 409)
    check("E5 PATCH that changes the IMEI to one already in stock → 409 (the edit is a landing path too)",
          e3 is not None and e3.detail["code"] == "inventory_duplicate", getattr(e3, "detail", None))
    check("E6 the IMEI / serial is an integrity field: a clerk cannot change it (403)",
          raises(lambda: pr.update_inventory_serial(fresh["unit"]["id"], {"imei": imei(9601)}, authorization="clerk", org_id=ORG), 403) is not None)
    # the onboarding bring-over (bulk import) — the third landing path
    db.seed("inventory_aging_device", [
        {"org_id": ORG, "imei": imei(20), "serial": imei(20), "sku": "X", "item": "PHONE MODEL X", "store": "S-01", "unit_cost": 500, "on_hand": True},   # duplicate of a unit
        {"org_id": ORG, "imei": imei(9500), "serial": imei(9500), "sku": "X", "item": "PHONE MODEL X", "store": "S-01", "unit_cost": 500, "on_hand": True,
         "received_date": "2026-09-01"},                                                                                                                  # already sold (commission)
        {"org_id": ORG, "imei": imei(9700), "serial": imei(9700), "sku": "X", "item": "PHONE MODEL X", "store": "S-01", "unit_cost": 500, "on_hand": True},  # fresh
        {"org_id": ORG, "imei": imei(9700), "serial": imei(9700), "sku": "X", "item": "PHONE MODEL X", "store": "S-01", "unit_cost": 500, "on_hand": True},  # same IMEI twice in the file
    ])
    db.seed("stores", [{"org_id": ORG, "store_code": "S-01", "address": "1 Test Ave", "market": "NY", "is_active": True}])
    ob.sb = lambda: db
    ap = ob.apply_import("inventory_from_metricspro", ORG, "inventory_aging")
    rep = ap["landing"]["report"]
    check("E7 the onboarding bring-over asks THE SAME guard: the duplicate is skipped and REPORTED with the existing unit's status",
          [d["serial_number"] for d in rep["duplicates"]] == [imei(20)] and rep["duplicates"][0]["existing"][0]["status"] == "in_stock", rep)
    check("E8 …the second copy of an IMEI in the same file is skipped and reported", len(rep["within_file"]) == 1, rep["within_file"])
    check("E9 …an already-sold device LANDS (it is on the shelf per the export) and is REPORTED with the customer and sold-on date",
          [d["serial_number"] for d in rep["already_sold"]] == [imei(9500)] and rep["already_sold"][0]["customer"]["name"] == "Test Earlier"
          and ap["created"] == 2 and ap["skipped"] == 2, (ap["created"], ap["skipped"], rep["already_sold"]))
    check("E10 …the landed units get 'import' ledger rows, and the flags scan picks the already-sold one up for the one-click",
          ap["landing"]["ledger"]["written"] == 2 and ap["landing"]["flags"].get("inserted", 0) >= 1
          and any(f["imei_key"] == imei(9500) and f["kind"] in ("received_after_sold", "sold_no_receipt") and f["status"] == "open"
                  for f in flags(db)), ap["landing"])
    ap2 = ob.apply_import("inventory_from_metricspro", ORG, "inventory_aging")
    check("E11 re-running the bring-over adds nothing (all 4 file rows are now reported duplicates of existing units)", ap2["created"] == 0
          and len(ap2["landing"]["report"]["duplicates"]) == 4 and "flags" not in ap2["landing"], ap2)
finally:
    ob.sb = _ob_sb
    for k, v in _saved.items():
        setattr(pr, k, v)

# ══ §F the ledger on every status change ═════════════════════════════════════════════════════════════════
print("\n§F — every status change writes its ledger row; manual adjust in / out; the compensation")
try:
    db = world()
    uid = "u-0100"
    check("F1 adjust needs pos_inventory_adjust", raises(lambda: iir.adjust_unit({"unit_id": uid, "direction": "out", "reason": "lost"},
                                                                                  authorization="clerk", org_id=ORG), 403) is not None)
    o = iir.adjust_unit({"unit_id": uid, "direction": "out", "reason": "damaged_write_off", "note": "screen cracked in the back"},
                        authorization="adj", org_id=ORG)
    check("F2 adjust OUT (damaged write-off) → adjusted_out, ledger out row with the note",
          o["unit"]["status"] == "adjusted_out" and o["adjustment"]["direction"] == "out" and o["adjustment"]["reason"] == "damaged_write_off"
          and o["adjustment"]["note"] == "screen cracked in the back" and o["adjustment"]["imei_key"] == imei(100))
    i_ = iir.adjust_unit({"unit_id": uid, "direction": "in", "reason": "found"}, authorization="adj", org_id=ORG)
    check("F3 adjust IN (found) → in_stock, ledger in row", i_["unit"]["status"] == "in_stock" and i_["adjustment"]["direction"] == "in"
          and i_["adjustment"]["from_status"] == "adjusted_out")
    check("F4 refused, with a sentence: out of a unit not in stock; in of a unit in stock; an unknown reason",
          raises(lambda: iir.adjust_unit({"unit_id": uid, "direction": "in", "reason": "found"}, authorization="adj", org_id=ORG), 409) is not None
          and raises(lambda: iir.adjust_unit({"unit_id": uid, "direction": "out", "reason": "teleported"}, authorization="adj", org_id=ORG), 409) is not None)
    n_led = len(ledger(db))
    p = pr.update_inventory_serial(uid, {"status": "rma", "color": "Blue", "adjust_note": "sent back for repair"}, authorization="adj", org_id=ORG)
    last = ledger(db)[-1]
    check("F5 PATCH that changes the status goes THROUGH THE LEDGER (manual_edit, out, in_stock → rma, the note)",
          p["unit"]["status"] == "rma" and p["unit"]["color"] == "Blue" and len(ledger(db)) == n_led + 1
          and (last["reason"], last["direction"], last["from_status"], last["to_status"], last["note"])
          == ("manual_edit", "out", "in_stock", "rma", "sent back for repair"), last)
    check("F6 a status PATCH without pos_inventory_adjust → 403 (it used to be open to any member, unaudited)",
          raises(lambda: pr.update_inventory_serial(uid, {"status": "in_stock"}, authorization="clerk", org_id=ORG), 403) is not None)
    n_led = len(ledger(db))
    c = pr.update_inventory_serial(uid, {"color": "Red", "status": "rma"}, authorization="clerk", org_id=ORG)
    check("F7 a non-integrity edit (colour) by a clerk still works; an unchanged status writes NO ledger row",
          c["unit"]["color"] == "Red" and len(ledger(db)) == n_led)
    # the compensation: the ledger cannot be written → the unit is put back
    db.tables["inventory_adjustments"] = None          # the table is gone (migration 1018 not applied)
    e = raises(lambda: iir.adjust_unit({"unit_id": "u-0101", "direction": "out", "reason": "lost"}, authorization="adj", org_id=ORG), 503)
    check("F8 no ledger → the status change does NOT persist (the unit is put back) and the caller is told to apply 1018",
          e is not None and "1018" in str(e.detail) and unit_of(db, "u-0101")["status"] == "in_stock", getattr(e, "detail", None))
    rcv = pr.add_inventory_serial({"product_id": "p-1", "serial_number": imei(9800), "store_code": "S-01"}, authorization="clerk", org_id=ORG)
    check("F9 …while RECEIVING keeps working before 1018 is applied (a landing is not a status change): the missing ledger is SAID, not hidden",
          rcv["unit"]["id"] and rcv["landing"]["ledger"]["written"] == 0 and "1018" in rcv["landing"]["ledger"]["error"])
    db.tables["inventory_adjustments"] = []
    check("F10 a concurrent change wins: a stale unit (status no longer what the plan read) → 409, nothing written",
          raises(lambda: iir.apply_status_change(db, ORG, {**unit_of(db, "u-0102"), "status": "in_transit"}, "lost", "E", direction="out"), 409) is not None
          and unit_of(db, "u-0102")["status"] == "in_stock" and not ledger(db))
finally:
    for k, v in _saved.items():
        setattr(pr, k, v)

# ══ §G org scoping ═══════════════════════════════════════════════════════════════════════════════════════
print("\n§G — org scoping and store scope")
try:
    db = world()
    # the SAME device in another org: a unit, a sale, a commission line
    db.seed("inventory_serial", [{"id": "o2-1", "org_id": ORG2, "product_id": "p-9", "store_code": "S-01", "serial_number": imei(1),
                                  "imei": imei(1), "status": "in_stock", "cost": 1, "created_at": "2026-01-01"}])
    db.seed("raw_vendor_rebate", [{"org_id": ORG2, "imei": imei(300), "earned_amount": 99, "sold_on": "2025-01-01", "customer_name": "Other Org"}])
    gA = iir.integrity_report(authorization="mgr", org_id=ORG)
    gB = iir.integrity_report(authorization="mgr", org_id=ORG2)
    check("G1 another org's unit with the same IMEI is NOT a duplicate here, and its commission flags nothing here",
          gA["totals"]["by_kind"]["duplicate_on_hand"] == 0 and len(gA["rows"]) == 12 and gA["totals"]["units_live"] == 383)
    check("G2 the other org sees only its own unit, and ORG's commission for that IMEI does not reach it",
          gB["totals"]["units_live"] == 1 and gB["rows"] == [], gB["totals"])
    iir.scan_endpoint(authorization="mgr", org_id=ORG)
    f0 = flags(db)[0]
    check("G3 a flag id of org A is not found from org B (assign / verify / dismiss → 404)",
          raises(lambda: iir.assign_flag(f0["id"], {}, authorization="mgr", org_id=ORG2), 404) is not None
          and raises(lambda: iir.dismiss_flag(f0["id"], {"note": "x"}, authorization="mgr", org_id=ORG2), 404) is not None)
    check("G4 a unit of org A cannot be adjusted from org B (404)",
          raises(lambda: iir.adjust_unit({"unit_id": "u-0001", "direction": "out", "reason": "lost"}, authorization="mgr", org_id=ORG2), 404) is not None)
    CTX["s2"] = CTX["mgr"]
    gS = iir.integrity_report(authorization="s2", org_id=ORG)
    check("G5 a manager scoped to another store sees none of S-01's flags and no org-wide totals",
          gS["rows"] == [] and gS["totals"] is None)
    check("G6 …and cannot act on an S-01 unit (403)", raises(lambda: iir.assign_flag(f0["id"], {}, authorization="s2", org_id=ORG), 403) is not None)
    check("G7 every row the harness wrote carries its org", all(r.get("org_id") in (ORG, ORG2) for t in ("inventory_flags", "inventory_adjustments")
                                                                for r in db.tables.get(t) or []))
finally:
    for k, v in _saved.items():
        setattr(pr, k, v)

# ══ §H the pure state machine ════════════════════════════════════════════════════════════════════════════
print("\n§H — the flag state machine and the adjustment rules (pure)")
check("H1 open → assigned → verified; assigned → open (reject); open → dismissed",
      ii.transition("open", "assign") == "assigned" and ii.transition("assigned", "verify") == "verified"
      and ii.transition("assigned", "reject") == "open" and ii.transition("open", "dismiss") == "dismissed")
bad = []
for st, act in (("open", "verify"), ("verified", "assign"), ("dismissed", "assign"), ("verified", "dismiss"), ("assigned", "dismiss"),
                ("assigned", "assign")):
    try:
        ii.transition(st, act)
        bad.append((st, act))
    except ii.IntegrityError:
        pass
check("H2 every other move is refused (verify an open flag; touch a closed one; dismiss an assigned one; assign twice)", not bad, bad)
check("H3 the reasons map to statuses: duplicate / write-off / count correction → adjusted_out; lost / stolen / rma → themselves; every in → in_stock",
      ii.ADJUST_REASONS["out"]["duplicate_record"] == "adjusted_out" and ii.ADJUST_REASONS["out"]["lost"] == "lost"
      and set(ii.ADJUST_REASONS["in"].values()) == {"in_stock"} and "adjusted_out" in ii.UNIT_STATUSES)
check("H4 the direction of an edit is DERIVED, never trusted: live → not live is out, the reverse in, sold → returned is a status move",
      ii.direction_of("in_stock", "sold") == "out" and ii.direction_of("rma", "in_stock") == "in"
      and ii.direction_of("sold", "returned") == "status" and ii.direction_of("in_stock", "in_transit") == "status")
_f = {"kind": "sold_no_receipt", "device_key": "K", "unit_ids": ["u"], "commission": {"net_earned": 10, "kept": True, "invoices": ["I"]}}
check("H5 the evidence fingerprint is stable and moves only with the deciding facts",
      ii.evidence_hash(_f) == ii.evidence_hash(dict(_f)) and ii.evidence_hash(_f) != ii.evidence_hash({**_f, "commission": {**_f["commission"], "net_earned": 20}})
      and ii.evidence_hash(_f) == ii.evidence_hash({**_f, "product_name": "renamed"}))
_dup_flag = {"kind": "duplicate_on_hand", "evidence": {"unit_ids": ["a", "b"]}}
_plan = ii.assign_plan(_dup_flag, {"a": {"id": "a", "status": "in_stock", "created_at": "1"}, "b": {"id": "b", "status": "in_stock", "created_at": "2"}})
check("H6 the one-click on a duplicate removes the NEWEST record as adjusted_out, with no customer",
      _plan["unit"]["id"] == "b" and _plan["to_status"] == "adjusted_out" and _plan["customer"] is None)
check("H7 every engine kind has a label on the page", set(ii.KIND_LABELS) == set(isr.INTEGRITY_KINDS))

print(f"\n══ inventory integrity: {len(P)} passed, {len(F)} failed ══")
for f in F:
    print("  FAILED:", f)
sys.exit(1 if F else 0)
