"""Proof for agent/commission/salesreport-device-drilldown — the Sales Report drill-down detail now
carries WHICH PHONE was sold (owner request 2026-07-17), plus SKU where the source table has it.

Pure unit test over the REAL router.sales_report_detail endpoint (monkeypatched sb() → in-memory
FakeClient); NO live DB. Drives the whole handler so the per-table sku selection, the box/serial device
tag, the per-transaction device summary and the payload shape are exercised as they actually run.

Run:  cd backend && python3 scratchpad/salesreport_device_drilldown_proof.py

Proves:
 1. CLOSED month → raw_sales primary (has `sku`): every device line carries its SKU; the transaction
    exposes `device` = the phone model on its box/device line(s); an accessory-only transaction → device None.
 2. Device detection is CONFIG-DRIVEN by box_departments (mig 218) — a custom box department (non-Boost
    tenant) tags its device line — with a universal serial/IMEI FALLBACK so a device still surfaces when
    the box config isn't set. An accessory line (no box dept, no serial) is never tagged.
 3. OPEN month → daily_sales_feed primary (NO sku column): selecting `sku` is SKIPPED (no throw → the
    historic "drill-down shows 0 transactions" bug can't recur); `device` still derives from the serial.
 4. Export column parity (RULE FOUR): the payload carries every field the drill-down export columns read
    (device / product / sku / department / category / contract / mdn / serial / price / gp) AND the
    frontend page defines a Device + Product + SKU export column over them.
 5. Money-safe / display-only: no total/gp/count is changed by the new tags (device tagging never alters
    a line's ext_price or gp; totals equal the sum of the lines).
"""
import sys, os, asyncio, re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date as _date  # noqa: E402
from app.modules.commcalc import router  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


# ── in-memory chainable Supabase stub ────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, client, table):
        self.c = client
        self.table = table
        self.count_mode = False
        self.req_cols = []
        self.preds = []          # list of (kind, col, val)

    def select(self, *a, **kw):
        cols = a[0] if a else "*"
        self.req_cols = [x.strip() for x in str(cols).split(",") if x.strip()]
        if kw.get("count"):
            self.count_mode = True
        return self

    def eq(self, col, val):
        self.preds.append(("eq", col, val)); return self

    def in_(self, col, vals):
        self.preds.append(("in", col, list(vals))); return self

    def neq(self, col, val):
        self.preds.append(("neq", col, val)); return self

    def gte(self, col, val):
        self.preds.append(("gte", col, val)); return self

    def lt(self, col, val):
        self.preds.append(("lt", col, val)); return self

    def limit(self, *a, **k):
        return self

    def _known(self):
        return self.c.cols.get(self.table, set())

    def execute(self):
        # Emulate 42703: selecting a column the table does not have raises (swallowed by callers).
        known = self._known()
        for col in self.req_cols:
            if col in ("*", "id"):
                continue
            if col not in known:
                raise Exception(f"column {self.table}.{col} does not exist (42703)")
        rows = list(self.c.tables.get(self.table, []))
        for kind, col, val in self.preds:
            if kind == "eq":
                rows = [r for r in rows if str(r.get(col)) == str(val)]
            elif kind == "in":
                sval = {str(v) for v in val}
                rows = [r for r in rows if str(r.get(col)) in sval]
            elif kind == "neq":
                rows = [r for r in rows if str(r.get(col) or "") != str(val)]
            elif kind == "gte":
                rows = [r for r in rows if str(r.get(col) or "") >= str(val)]
            elif kind == "lt":
                rows = [r for r in rows if str(r.get(col) or "") < str(val)]
        if self.count_mode:
            return _Resp(count=len(rows))
        return _Resp(data=rows)


class FakeClient:
    def __init__(self, tables, cols):
        self.tables = tables
        self.cols = cols

    def schema(self, _s):
        return self

    def table(self, t):
        return _Query(self, t)


ORG = "00000000-0000-0000-0000-000000000001"
_T = _date.today()
OPEN = f"{_T.year}-{_T.month:02d}"
CLOSED = "2026-05" if OPEN != "2026-05" else "2026-04"

# Column availability: raw_sales HAS sku; the daily feed does NOT (the real schema divergence).
COLS = {
    "raw_sales": {"trans_id", "trans_date", "store", "salesperson", "customer", "department", "category",
                  "contract_type", "product_desc", "ext_price", "gp", "mdn", "serial_1", "voided", "sku", "period", "org_id"},
    "daily_sales_feed": {"trans_id", "trans_date", "store", "salesperson", "customer", "department", "category",
                         "contract_type", "product_desc", "ext_price", "gp", "mdn", "serial_1", "voided", "period", "org_id"},
    "accessory_config": {"org_id", "departments", "categories", "product_keywords", "acima_tenders", "box_departments", "setup_fee_keywords"},
    "flag_rules": set(),
    "gp_category_map": {"org_id", "department", "category"},
}


def _row(**kw):
    base = {"org_id": ORG, "store": "0123 Main St", "salesperson": "Jane Rep", "customer": "ACME",
            "voided": "false", "mdn": "", "serial_1": "", "sku": None, "category": "", "contract_type": ""}
    base.update(kw)
    return base


async def run():
    # ───────────────────────────────────────────────────────────────── Scenario 1: closed month, raw_sales
    raw1 = [
        # T1: device line (box dept) + accessory + setup fee
        _row(trans_id="T1", trans_date=f"{CLOSED}-12", period="May 2026", department="IPHONE - XP",
             product_desc="APPLE IPHONE 15 128GB BLACK", sku="IP15-128-BLK", serial_1="356938111222333",
             contract_type="Activation", ext_price=899.00, gp=120.00, category="Phone"),
        _row(trans_id="T1", trans_date=f"{CLOSED}-12", period="May 2026", department="Accessories",
             product_desc="OtterBox Case", sku="OTB-15", ext_price=39.99, gp=25.00, category="Case"),
        _row(trans_id="T1", trans_date=f"{CLOSED}-12", period="May 2026", department="Fees",
             product_desc="Device Setup Charge", sku="SETUP", ext_price=30.00, gp=30.00),
        # T2: accessory-only (NO device)
        _row(trans_id="T2", trans_date=f"{CLOSED}-12", period="May 2026", department="Accessories",
             product_desc="Screen Protector", sku="SP-9", ext_price=19.99, gp=12.00, category="Protector"),
    ]
    fc1 = FakeClient({"raw_sales": raw1, "daily_sales_feed": [], "accessory_config": [],
                      "flag_rules": [], "gp_category_map": []}, COLS)
    router.sb = lambda: fc1
    res = await router.sales_report_detail(period=CLOSED, store="0123 Main St", salesperson="Jane Rep",
                                           date=f"{CLOSED}-12", org_id=ORG)
    txns = {t["trans_id"]: t for t in res["transactions"]}
    check("S1 two transactions returned", res["txn_count"] == 2 and set(txns) == {"T1", "T2"})
    check("S1 T1.device == the phone model", txns["T1"]["device"] == "APPLE IPHONE 15 128GB BLACK")
    check("S1 T2 (accessory-only) device is None", txns["T2"]["device"] is None)
    t1_lines = {l["product"]: l for l in txns["T1"]["lines"]}
    check("S1 device line tagged is_device", t1_lines["APPLE IPHONE 15 128GB BLACK"]["is_device"] is True)
    check("S1 accessory line NOT is_device", t1_lines["OtterBox Case"]["is_device"] is False)
    check("S1 setup-fee line NOT is_device", t1_lines["Device Setup Charge"]["is_device"] is False)
    check("S1 device line carries SKU (raw_sales has the column)",
          t1_lines["APPLE IPHONE 15 128GB BLACK"]["sku"] == "IP15-128-BLK")
    check("S1 accessory line carries SKU", t1_lines["OtterBox Case"]["sku"] == "OTB-15")
    # money-safe: totals equal the sum of the lines, unchanged by tagging
    check("S1 T1 total == sum(lines ext)", abs(txns["T1"]["total"] - (899.00 + 39.99 + 30.00)) < 1e-6)
    check("S1 T1 gp == sum(lines gp)", abs(txns["T1"]["gp"] - (120.00 + 25.00 + 30.00)) < 1e-6)

    # ───────────────────────────────────────────────── Scenario 2: config-driven box dept + serial fallback
    raw2 = [
        _row(trans_id="X1", trans_date=f"{CLOSED}-13", period="May 2026", department="Total Devices",
             product_desc="SAMSUNG GALAXY A15", sku="SGA15", serial_1="", ext_price=199.00, gp=40.00),  # box dept, no serial
        _row(trans_id="X1", trans_date=f"{CLOSED}-13", period="May 2026", department="Mystery Dept",
             product_desc="MOTOROLA MOTO G", sku="MOTOG", serial_1="990011223344", ext_price=149.00, gp=30.00),  # serial fallback
        _row(trans_id="X1", trans_date=f"{CLOSED}-13", period="May 2026", department="Acc",
             product_desc="Car Charger", sku="CC-1", serial_1="", ext_price=15.00, gp=9.00),  # not a device
    ]
    fc2 = FakeClient({"raw_sales": raw2, "daily_sales_feed": [],
                      "accessory_config": [{"org_id": ORG, "box_departments": ["Total Devices"]}],
                      "flag_rules": [], "gp_category_map": []}, COLS)
    router.sb = lambda: fc2
    res2 = await router.sales_report_detail(period=CLOSED, store="0123 Main St", salesperson="Jane Rep",
                                            date=f"{CLOSED}-13", org_id=ORG)
    x1 = res2["transactions"][0]
    lines2 = {l["product"]: l for l in x1["lines"]}
    check("S2 custom box dept line tagged", lines2["SAMSUNG GALAXY A15"]["is_device"] is True)
    check("S2 serial-only line tagged (fallback)", lines2["MOTOROLA MOTO G"]["is_device"] is True)
    check("S2 non-device accessory NOT tagged", lines2["Car Charger"]["is_device"] is False)
    check("S2 device summary lists BOTH phones",
          x1["device"] == "SAMSUNG GALAXY A15 · MOTOROLA MOTO G")

    # ───────────────────────────────────────────────── Scenario 3: OPEN month, feed primary, NO sku column
    feed3 = [
        _row(trans_id="F1", trans_date=f"{OPEN}-03", period=OPEN, department="IPHONE - XP",
             product_desc="APPLE IPHONE 16 PRO", serial_1="355112233445566", category="Phone",
             contract_type="Activation", ext_price=999.00, gp=150.00),
        _row(trans_id="F1", trans_date=f"{OPEN}-03", period=OPEN, department="Accessories",
             product_desc="USB-C Cable", serial_1="", category="Cable", ext_price=19.99, gp=12.00),
    ]
    for r in feed3:
        r.pop("sku", None)  # the feed simply has no such column
    fc3 = FakeClient({"daily_sales_feed": feed3, "raw_sales": [], "accessory_config": [],
                      "flag_rules": [], "gp_category_map": []}, COLS)
    router.sb = lambda: fc3
    res3 = await router.sales_report_detail(period=OPEN, store="0123 Main St", salesperson="Jane Rep",
                                            date=f"{OPEN}-03", org_id=ORG)
    check("S3 feed drill-down NOT empty (sku select skipped, no throw)", res3["txn_count"] == 1)
    f1 = res3["transactions"][0]
    check("S3 device derives from serial on feed", f1["device"] == "APPLE IPHONE 16 PRO")
    fl = {l["product"]: l for l in f1["lines"]}
    check("S3 feed line sku is None (column absent)", fl["APPLE IPHONE 16 PRO"]["sku"] is None)
    check("S3 device line still tagged is_device", fl["APPLE IPHONE 16 PRO"]["is_device"] is True)

    # ───────────────────────────────────────────────── Scenario 4: export column parity (RULE FOUR)
    line_keys = set(res["transactions"][0]["lines"][0].keys())
    for k in ("department", "category", "contract_type", "product", "sku", "mdn", "serial", "ext_price", "gp", "is_device"):
        check(f"S4 payload line carries '{k}'", k in line_keys)
    check("S4 payload transaction carries 'device'", "device" in res["transactions"][0])
    page = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "frontend", "src", "app", "(platform)", "commcalc", "sales-report", "page.tsx")
    src = open(page, encoding="utf-8").read()
    check("S4 FE defines a Device export column", bool(re.search(r"header:\s*'Device'", src)))
    check("S4 FE defines a Product export column", bool(re.search(r"header:\s*'Product'", src)))
    check("S4 FE defines a SKU export column", bool(re.search(r"header:\s*'SKU'", src)))
    check("S4 FE flattens detailRows from transactions/lines", "detailRows" in src and "t.device" in src)
    check("S4 FE drill-down renders ReportExportBar", "ReportExportBar" in src)
    check("S4 FE collapsed header shows the device", "t.device &&" in src)

    print(f"\n{'='*54}\n  {PASS} passed · {FAIL} failed\n{'='*54}")
    return FAIL == 0


if __name__ == "__main__":
    ok = asyncio.run(run())
    sys.exit(0 if ok else 1)
