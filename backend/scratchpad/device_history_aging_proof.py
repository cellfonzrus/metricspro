"""Proof harness for agent/commission/device-history-aging-price (commission-16 extension).

Owner directive 2026-07-17: "Device history should add the aging history for that IMEI and our
purchase price — on the employee portal — and visible on admin under the commission tab."

Two layers, both driven with NO DB / NO network:
  • PURE helpers in app.modules.commcalc.device_history — aging-day math (incl. unsold current-age),
    the aging-bucket edges (44/45/60/61 — kept in lock-step with the asset Inventory Aging report),
    the money-ish parser (None vs a REAL 0), the raw_row purchase-cost scan (allowlist + exclusions so
    the SALE price is never picked), and the source-priority purchase-price pick with provenance.
  • the REAL router endpoint `get_device_history` over an in-memory FakeClient — proving the NEW
    asset_ledger reads are org-scoped (tenant A never sees tenant B's identical-IMEI row), unsold vs
    sold aging, raw_row-wins provenance, and the HONEST empty state for a tenant with no inventory row.
Plus a regression run of the existing device_history_proof.py (must stay green).

Run:  cd backend && python3 scratchpad/device_history_aging_proof.py
"""


def run_route(x):
    """Call a commcalc route handler in EITHER shape.

    ASYNC-SWEEP 2026-08-04: commcalc's zero-`await` route handlers were converted from `async def` to
    `def` so FastAPI runs them in the threadpool instead of on the single uvicorn event loop (an
    `async def` doing blocking Supabase I/O froze the whole product for its duration). The only textual
    change was the keyword. This helper awaits a coroutine when it gets one and passes a plain result
    straight through, so the proof works against BOTH shapes and needs no further edit if a handler
    ever legitimately becomes a coroutine again."""
    import asyncio as _a
    return _a.run(x) if _a.iscoroutine(x) else x
import os, sys, asyncio, subprocess
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.modules.commcalc import device_history as dh
import app.modules.commcalc.router as R

_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}   {extra}")


TODAY = date.today().isoformat()


# ══ 1. date math (dependency-free; None on unparseable — never a fake 0) ═════════════════════════════
print("── 1. date math (days_between / iso_date) ──")
check("days_between exact span", dh.days_between("2026-01-01", "2026-02-01") == 31)
check("days_between across leap Feb", dh.days_between("2024-02-01", "2024-03-01") == 29)
check("days_between with trailing time tolerated", dh.days_between("2026-01-01 09:30:00", "2026-01-11") == 10)
check("days_between unparseable start → None", dh.days_between("nope", "2026-01-11") is None)
check("days_between None end → None", dh.days_between("2026-01-01", None) is None)
check("days_between negative allowed (sold<acquired data anomaly)", dh.days_between("2026-02-01", "2026-01-01") == -31)
check("iso_date normalizes datetime-ish", dh.iso_date("2026-07-01 00:00:00") == "2026-07-01")
check("iso_date on a date object", dh.iso_date(date(2026, 7, 1)) == "2026-07-01")
check("iso_date junk → None", dh.iso_date("garbage") is None)


# ══ 2. aging-bucket edges — LOCK-STEP with the asset report (<45 / 45-60 / >60) ══════════════════════
print("── 2. aging-bucket edges (44/45/60/61) ──")
check("44 days → under45 (Fresh)", dh.aging_bucket(44)["key"] == "under45")
check("45 days → warn (Aging)", dh.aging_bucket(45)["key"] == "warn")
check("60 days → warn (inclusive upper)", dh.aging_bucket(60)["key"] == "warn")
check("61 days → missed (Overaged)", dh.aging_bucket(61)["key"] == "missed")
check("0 days → under45", dh.aging_bucket(0)["key"] == "under45")
check("None days → None bucket (unknown, not fabricated)", dh.aging_bucket(None) is None)
check("bucket carries label + range", dh.aging_bucket(50)["label"] == "Aging" and dh.aging_bucket(50)["range"] == "45–60 days")


# ══ 3. build_aging (pure) — sold vs unsold vs empty vs no-acquired ══════════════════════════════════
print("── 3. build_aging (pure) ──")
# SOLD via the ledger's own date_sold: age = acquired → sold (days on inventory at sale).
sold_row = {"acquired_date": "2026-01-01", "date_sold": "2026-02-20", "store": "S1",
            "device_model": "iPhone 15", "category": "Sold", "owed_to_vip": 299}
a_sold = dh.build_aging(sold_row, None, TODAY)
check("sold: found + source asset_ledger", a_sold["found"] and a_sold["source"] == "asset_ledger")
check("sold: is_sold True, sold_source=asset_ledger", a_sold["is_sold"] and a_sold["sold_source"] == "asset_ledger")
check("sold: days_on_inventory = acquired→sold (50)", a_sold["days_on_inventory"] == 50)
check("sold: bucket = warn (50)", a_sold["bucket"]["key"] == "warn")
check("sold: age_basis names 'days on inventory'", "days on inventory" in a_sold["age_basis"])
# SOLD only via the B2B sale MATCH (ledger has no date_sold) — the fallback path.
a_match = dh.build_aging({"acquired_date": "2026-01-01"}, "2026-01-10", TODAY)
check("sold via sale-match fallback: sold_source=raw_sales_match", a_match["sold_source"] == "raw_sales_match")
check("sold via sale-match: days = 9", a_match["days_on_inventory"] == 9)
# UNSOLD → current age acquired → today.
acq40 = (date.today() - timedelta(days=40)).isoformat()
a_uns = dh.build_aging({"acquired_date": acq40}, None, TODAY)
check("unsold: is_sold False", a_uns["is_sold"] is False)
check("unsold: current age = 40 (acquired→today)", a_uns["days_on_inventory"] == 40)
check("unsold: bucket = under45", a_uns["bucket"]["key"] == "under45")
check("unsold: age_basis names 'current age'", "current age" in a_uns["age_basis"])
# billing dates surface only when present.
a_bill = dh.build_aging({"acquired_date": acq40, "payg_date": "2026-03-01",
                         "billing_friday": "2026-03-06", "due_date": "2026-02-28"}, None, TODAY)
check("billing dates surfaced (payg/friday/due)", a_bill["billing"]["payg_date"] == "2026-03-01"
      and a_bill["billing"]["billing_friday"] == "2026-03-06" and a_bill["billing"]["due_date"] == "2026-02-28")
check("no-billing row → billing None (not empty dict)", dh.build_aging({"acquired_date": acq40}, None, TODAY)["billing"] is None)
# no acquired date → honest note, days None.
a_noacq = dh.build_aging({"store": "S1"}, None, TODAY)
check("no acquired_date → days None + note", a_noacq["days_on_inventory"] is None and "no acquired date" in a_noacq["note"])
# empty asset_row → honest 'no inventory record', found False.
a_empty = dh.build_aging(None, None, TODAY)
check("no asset row → found False + honest note", a_empty["found"] is False and "No inventory" in a_empty["note"])
check("no asset row → carries asof", a_empty["asof"] == TODAY)


# ══ 4. to_amount — None (unknown) is DISTINCT from a real 0 ══════════════════════════════════════════
print("── 4. to_amount (None vs real 0) ──")
check("'$1,234.50' → 1234.5", dh.to_amount("$1,234.50") == 1234.5)
check("plain number", dh.to_amount(299) == 299.0)
check("float passthrough", dh.to_amount(19.99) == 19.99)
check("real 0 stays 0.0 (not None)", dh.to_amount("0") == 0.0)
check("blank → None (unknown, not 0)", dh.to_amount("") is None)
check("'nan' → None", dh.to_amount("nan") is None)
check("None → None", dh.to_amount(None) is None)
check("'-' placeholder → None", dh.to_amount("-") is None)
check("bool True → None (never coerced to 1)", dh.to_amount(True) is None)
check("non-numeric text → None", dh.to_amount("iPhone") is None)


# ══ 5. scan_raw_row_price — allowlist priority + exclusions (never grab the SALE price) ══════════════
print("── 5. scan_raw_row_price (raw_row cost column) ──")
r1, h1 = dh.scan_raw_row_price({"Device Cost": "349.00", "Selling Price": "699.00", "Notes": "x"})
check("picks 'Device Cost' as cost", r1 == 349.0 and h1 == "Device Cost")
r2, h2 = dh.scan_raw_row_price({"Selling Price": "699.00", "Sale Price": "650"})
check("EXCLUDES selling/sale price → None", r2 is None and h2 is None)
r3, h3 = dh.scan_raw_row_price({"Purchase Price": "500", "Device Cost": "400"})
check("priority: 'purchase price' key beats 'device cost'", r3 == 500.0 and h3 == "Purchase Price")
r4, h4 = dh.scan_raw_row_price({"Reimbursement": "100", "Owed to VIP": "300"})
check("reimbursement + owed excluded from raw scan → None", r4 is None)
check("non-dict raw_row → (None,None)", dh.scan_raw_row_price("notadict") == (None, None))
check("empty raw_row → (None,None)", dh.scan_raw_row_price({}) == (None, None))
r5, h5 = dh.scan_raw_row_price({"Unit Cost": "$1,050.00"})
check("money-formatted unit cost parsed", r5 == 1050.0 and h5 == "Unit Cost")


# ══ 6. pick_purchase_price — source priority, first-non-None, honest empty, provenance ═══════════════
print("── 6. pick_purchase_price (source priority + provenance) ──")
pp = dh.pick_purchase_price([
    {"amount": 349.0, "source": "asset_ledger.raw_row[Device Cost]", "label": "VIP asset ledger — Device Cost"},
    {"amount": 299.0, "source": "asset_ledger.owed_to_vip", "label": "VIP device cost (Owed to VIP)"},
])
check("picks FIRST present candidate (raw_row wins)", pp["found"] and pp["amount"] == 349.0)
check("provenance names label + source", "Device Cost" in pp["provenance"] and "raw_row" in pp["provenance"])
check("candidates_considered lists ALL (transparency)", len(pp["candidates_considered"]) == 2)
pp2 = dh.pick_purchase_price([
    {"amount": None, "source": "asset_ledger.raw_row[Device Cost]", "label": "raw"},
    {"amount": 299.0, "source": "asset_ledger.owed_to_vip", "label": "VIP device cost (Owed to VIP)"},
])
check("skips a None candidate → falls to owed_to_vip", pp2["found"] and pp2["amount"] == 299.0
      and pp2["source"] == "asset_ledger.owed_to_vip")
pp3 = dh.pick_purchase_price([])
check("no candidates → honest empty (found False, amount None)", pp3["found"] is False and pp3["amount"] is None)
check("empty pick → 'No purchase-price record' provenance (never a fake 0)", "No purchase-price record" in pp3["provenance"])
pp4 = dh.pick_purchase_price([{"amount": None, "source": "s", "label": "l"}])
check("all-None candidates → still honest empty", pp4["found"] is False)
pp5 = dh.pick_purchase_price([{"amount": 12.005, "source": "s", "label": "l"}])
check("amount rounded to cents", pp5["amount"] == 12.0 or pp5["amount"] == 12.01)


# ══ 7. REAL router endpoint over a FakeClient — ORG ISOLATION of the new asset reads + integration ═══
print("── 7. get_device_history (real router) — org isolation + aging/price integration ──")


class FakeResult:
    def __init__(self, data): self.data = data


class FakeQuery:
    def __init__(self, store, table):
        self.store, self.t, self.f = store, table, []

    def select(self, *a, **k): return self
    def eq(self, c, v): self.f.append(("eq", c, v)); return self
    def in_(self, c, v): self.f.append(("in", c, list(v))); return self
    def limit(self, n): return self
    def range(self, a, b): return self
    def order(self, *a, **k): return self

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == "eq" and rv != v:
                return False
            if k == "in" and rv not in v:
                return False
        return True

    def execute(self):
        rows = self.store.get(self.t, [])
        return FakeResult([dict(r) for r in rows if self._m(r)])


class FakeSchema:
    def __init__(self, store): self.store = store
    def table(self, t): return FakeQuery(self.store, t)
    def rpc(self, *a, **k): raise Exception("no rpc")


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, s): return FakeSchema(self.store)


IMEI = "355123456789012"
ACQ40 = (date.today() - timedelta(days=40)).isoformat()
ACQ80 = (date.today() - timedelta(days=80)).isoformat()

# Tenant A: unsold, acquired 40 days ago, owed_to_vip 299, an explicit raw_row Device Cost 349.
# Tenant B: DIFFERENT everything for the SAME IMEI (acquired 80 days ago, owed 999) — must never leak.
STORE = {
    "asset_ledger": [
        {"org_id": "A", "esn_imei": IMEI, "phone_number": "5551230001", "store": "A-STORE",
         "market": "PA", "device_model": "iPhone 15", "category": "On Inventory", "status": "In Stock",
         "acquired_date": ACQ40, "due_date": None, "payg_date": None, "date_sold": None,
         "owed_to_vip": 299, "reimbursement": 0, "selling_price": None,
         "raw_row": {"Device Cost": "349.00", "Selling Price": "699.00"}},
        {"org_id": "B", "esn_imei": IMEI, "phone_number": "5559990002", "store": "B-STORE",
         "market": "NJ", "device_model": "Galaxy S24", "category": "On Inventory", "status": "In Stock",
         "acquired_date": ACQ80, "due_date": None, "payg_date": None, "date_sold": None,
         "owed_to_vip": 999, "reimbursement": 0, "selling_price": None, "raw_row": {}},
    ],
}
R.sb = lambda: FakeClient(STORE)

resA = run_route(R.get_device_history(q=IMEI, authorization="", org_id="A"))
resB = run_route(R.get_device_history(q=IMEI, authorization="", org_id="B"))

check("A: found via asset_ledger", resA["found"] and resA["aging"]["found"])
check("A: aging store is A's", resA["aging"]["store"] == "A-STORE")
check("A: unsold current-age = 40", resA["aging"]["days_on_inventory"] == 40)
check("A: bucket under45", resA["aging"]["bucket"]["key"] == "under45")
check("A: purchase price = raw_row Device Cost 349 (wins over owed 299)", resA["purchase_price"]["amount"] == 349.0)
check("A: provenance names raw_row Device Cost", "Device Cost" in resA["purchase_price"]["provenance"])
check("A: purchase_price UNGATED (present without any auth)", resA["purchase_price"]["found"] is True)
check("A: money still LOCKED without the grant", resA["commission_visible"] is False and resA["money"] is None)

# ISOLATION — B's read returns ONLY B's row; A's numbers never appear in B and vice-versa.
check("ISO: B aging store is B's (not A's)", resB["aging"]["store"] == "B-STORE")
check("ISO: B current-age = 80 (bucket missed)", resB["aging"]["days_on_inventory"] == 80 and resB["aging"]["bucket"]["key"] == "missed")
check("ISO: B has no raw_row cost → owed_to_vip 999", resB["purchase_price"]["amount"] == 999.0
      and resB["purchase_price"]["source"] == "asset_ledger.owed_to_vip")
check("ISO: A never shows B's 999 / Galaxy / B-STORE",
      resA["aging"]["device_model"] == "iPhone 15" and resA["purchase_price"]["amount"] != 999.0)
check("ISO: B never shows A's 349 / A-STORE", resB["purchase_price"]["amount"] != 349.0 and resB["aging"]["store"] != "A-STORE")

# SOLD device end-to-end — age = acquired→sold, is_sold True.
STORE["asset_ledger"].append(
    {"org_id": "A", "esn_imei": "990000000000001", "phone_number": "5551230009", "store": "A-STORE",
     "market": "PA", "device_model": "Pixel 8", "category": "Sold", "status": "Sold",
     "acquired_date": "2026-01-01", "date_sold": "2026-02-20", "payg_date": "2026-01-15",
     "billing_friday": "2026-01-16", "owed_to_vip": 250, "raw_row": {}})
resSold = run_route(R.get_device_history(q="990000000000001", authorization="", org_id="A"))
check("SOLD: is_sold True + days 50 (acquired→sold)", resSold["aging"]["is_sold"] and resSold["aging"]["days_on_inventory"] == 50)
check("SOLD: billing PayGo/Friday surfaced", resSold["aging"]["billing"]["payg_date"] == "2026-01-15"
      and resSold["aging"]["billing"]["billing_friday"] == "2026-01-16")
check("SOLD: owed_to_vip 250 is the purchase price", resSold["purchase_price"]["amount"] == 250.0)

# NO inventory record (unknown IMEI) — honest empty, never a fabricated zero.
resNone = run_route(R.get_device_history(q="111111111111119", authorization="", org_id="A"))
check("NO-REC: aging.found False + honest note", resNone["aging"]["found"] is False and "No inventory" in resNone["aging"]["note"])
check("NO-REC: purchase_price.found False (not $0)", resNone["purchase_price"]["found"] is False and resNone["purchase_price"]["amount"] is None)


# ══ V2-A. pos_cost_from_sale — at-sale POS cost = ext_price − GP (universal) ═════════════════════════
print("── V2-A. pos_cost_from_sale (ext − GP) ──")
check("normal: 699 − 200 = 499", dh.pos_cost_from_sale(699, 200) == 499.0)
check("GP 0 (BYOD cost==price) → cost = ext (799)", dh.pos_cost_from_sale(799, 0) == 799.0)
check("GP None (unknown) → None (not a fake ext)", dh.pos_cost_from_sale(699, None) is None)
check("ext None → None", dh.pos_cost_from_sale(None, 200) is None)
check("negative GP (sold below cost) → cost = ext + |gp| (1000−(−50)=1050)", dh.pos_cost_from_sale(1000, -50) == 1050.0)
check("GP ≥ ext (cost ≤ 0 anomaly) → None", dh.pos_cost_from_sale(100, 150) is None)
check("$0 line (ext 0, gp 0) → None (bad-export guard)", dh.pos_cost_from_sale(0, 0) is None)
check("money-formatted strings parse ('$699.00','$200')", dh.pos_cost_from_sale("$699.00", "$200") == 499.0)
check("blank gp → None", dh.pos_cost_from_sale(699, "") is None)


# ══ V2-B. inv_device_cost — per-IMEI inventory-aging unit cost ════════════════════════════════════════
print("── V2-B. inv_device_cost (inventory_aging_device.unit_cost) ──")
a, s = dh.inv_device_cost({"unit_cost": 349.0, "sku": "APL-15-128"})
check("unit_cost + sku returned", a == 349.0 and s == "APL-15-128")
check("0 unit_cost → (None,None) (no-signal, not fake 0)", dh.inv_device_cost({"unit_cost": 0}) == (None, None))
check("missing unit_cost → (None,None)", dh.inv_device_cost({"sku": "X"}) == (None, None))
check("non-dict → (None,None)", dh.inv_device_cost(None) == (None, None))
a2, s2 = dh.inv_device_cost({"unit_cost": "$1,050.00"})
check("money-formatted unit_cost parsed, sku None ok", a2 == 1050.0 and s2 is None)


# ══ V2-C. MA marketplace linkage — imei → activation_order → order_number → price ═════════════════════
print("── V2-C. pick_ma_marketplace_price (MA join) ──")
comm = [{"imei": "355000000000001", "activation_order": "ORD-9001"}]
ful = [{"order_number": "ORD-9001", "price": 529.99, "product_name": "Galaxy A15"},
       {"order_number": "ORD-0000", "price": 111.0}]
hit = dh.pick_ma_marketplace_price(comm, ful)
check("HIT: price 529.99 via ORD-9001", hit["found"] and hit["amount"] == 529.99 and hit["order_number"] == "ORD-9001")
check("HIT: product name carried", hit["product_name"] == "Galaxy A15")
no_order = dh.pick_ma_marketplace_price([{"imei": "355000000000002", "activation_order": "ORD-XYZ"}], ful)
check("ORDER-BUT-NO-PRICE: found False + honest note + linked order surfaced",
      no_order["found"] is False and "no marketplace fulfillment price" in no_order["note"]
      and no_order["order_number"] == "ORD-XYZ")
no_comm = dh.pick_ma_marketplace_price([], ful)
check("IMEI-MISSING (no commission row) → found False + honest note",
      no_comm["found"] is False and "No MA commission" in no_comm["note"])
check("norm_order collapses '.0' + case", dh.norm_order("ORD-9001.0") == "ord-9001")
hit2 = dh.pick_ma_marketplace_price([{"imei": "x", "activation_order": "ord-9001"}],
                                    [{"order_number": "ORD-9001", "price": 529.99}])
check("case/format drift still matches (ord-9001 ↔ ORD-9001)", hit2["found"] and hit2["amount"] == 529.99)
check("order_candidates gives raw + '.0'-stripped forms", set(dh.order_candidates(["ORD-1.0", "ORD-2"])) == {"ORD-1.0", "ORD-1", "ORD-2"})
check("MA fulfillment 0/negative price ignored", dh.pick_ma_marketplace_price(
    [{"imei": "x", "activation_order": "O1"}], [{"order_number": "O1", "price": 0}])["found"] is False)


# ══ V2-D. build_aging_inventory — non-VIP inventory-aging aging path ══════════════════════════════════
print("── V2-D. build_aging_inventory (non-VIP aging) ──")
inv_recv = {"received_date": (date.today() - timedelta(days=52)).isoformat(), "store": "TOTAL-1",
            "item": "Galaxy A15", "sku": "SMS-A15"}
ai = dh.build_aging_inventory(inv_recv, None, TODAY)
check("received→today unsold current-age = 52 + source", ai["days_on_inventory"] == 52
      and ai["source"] == "inventory_aging_device")
check("bucket warn (52) + device_model from item", ai["bucket"]["key"] == "warn" and ai["device_model"] == "Galaxy A15")
check("current-age basis names 'received → today'", "received → today" in ai["age_basis"])
ai_sold = dh.build_aging_inventory({"received_date": "2026-01-01", "store": "T2"}, "2026-02-20", TODAY)
check("sold via sale-match: days 50 + is_sold", ai_sold["is_sold"] and ai_sold["days_on_inventory"] == 50)
ai_dis = dh.build_aging_inventory({"days_in_stock": 73, "as_of_date": "2026-07-01", "sku": "X"}, None, TODAY)
check("days_in_stock used when no received_date (73 → missed)", ai_dis["days_on_inventory"] == 73
      and ai_dis["bucket"]["key"] == "missed")
check("days_in_stock basis names the report", "inventory-aging report" in ai_dis["age_basis"])
ai_none = dh.build_aging_inventory({"sku": "X"}, None, TODAY)
check("no date at all → note + days None", ai_none["days_on_inventory"] is None and "no received/aging date" in ai_none["note"])
check("empty inv_row → honest 'no inventory record' (found False)", dh.build_aging_inventory(None, None, TODAY)["found"] is False)


# ══ V2-E. per-IMEI ingest parse — extract_inventory_devices (flat + grouped + honest) ════════════════
print("── V2-E. extract_inventory_devices (ingest parse) ──")
from app.modules.commcalc import b2b_sweep as B
flat = [
    {"Store": "A-STORE", "IMEI": "355111111111111", "SKU": "APL-15", "Item": "iPhone 15",
     "Unit Cost": "$549.00", "Received Date": "2026-05-01", "Days In Stock": "60"},
    {"Store": "A-STORE", "Serial": "SN-2222", "SKU": "SMS-A15", "Item": "Galaxy A15",
     "Cost": "199.99", "Received Date": "06/10/2026"},
    {"Store": "A-STORE", "Item": "no-device-id row (skipped)", "Cost": "9.99"},  # no imei/serial → skip
]
dev_flat = B.extract_inventory_devices(flat, as_of_date="2026-07-01")
check("flat: 2 device rows (the no-id row is skipped honestly)", len(dev_flat) == 2)
check("flat: imei row cost 549 + received parsed", dev_flat[0]["imei"] == "355111111111111"
      and dev_flat[0]["unit_cost"] == 549.0 and dev_flat[0]["received_date"] == "2026-05-01")
check("flat: serial-only row uses serial as device key + US date → ISO", dev_flat[1]["imei"] == "SN-2222"
      and dev_flat[1]["received_date"] == "2026-06-10" and dev_flat[1]["unit_cost"] == 199.99)
check("flat: days_in_stock parsed to int", dev_flat[0]["days_in_stock"] == 60)
check("flat: as_of stamped + raw_row captured", dev_flat[0]["as_of_date"] == "2026-07-01" and isinstance(dev_flat[0]["raw_row"], dict))
# GROUPED layout: uniform columns (as pandas reads them); first col = a line field that is populated
# on detail rows and empty on subtotals; the store is a 'Store: <addr>' HEADER row (mirrors
# normalize_inventory's grouped convention). The store fills DOWN onto the detail row from cur_store.
grouped = [
    {"Line": "Store: B-STORE", "IMEI": "", "SKU": "", "Unit Cost": "", "Item": ""},        # header row
    {"Line": "1", "IMEI": "355222222222222", "SKU": "PXL-8", "Unit Cost": "429.00", "Item": "Pixel 8"},  # detail
    {"Line": "", "IMEI": "", "SKU": "", "Unit Cost": "", "Item": ""},                        # subtotal/blank → skipped
]
dev_grp = B.extract_inventory_devices(grouped, as_of_date="2026-07-01")
check("grouped: 1 device row, store filled DOWN from 'Store:' header", len(dev_grp) == 1
      and dev_grp[0]["store"] == "B-STORE" and dev_grp[0]["imei"] == "355222222222222")
# honest 0-device parse (no imei/serial + no cost columns anywhere).
none_file = [{"Category": "Phones", "Qty": "12", "Value": "3000"}]
check("honest: no per-device columns → [] (never faked)", B.extract_inventory_devices(none_file) == [])
ddiag = B.device_diagnostics(none_file)
check("device_diagnostics names missing imei/cost cols honestly",
      ddiag["imei_col"] is None and ddiag["cost_col"] is None and ddiag["n_rows"] == 1)
ddiag2 = B.device_diagnostics(flat)
check("device_diagnostics finds imei + cost cols on a real file (matched candidate)",
      ddiag2["imei_col"] in ("imei", "IMEI") and ddiag2["cost_col"] == "Unit Cost")


# ══ V2-F. REAL router — source priority ①→⑤, POS/MA integration, ORG ISOLATION of new reads ═════════
print("── V2-F. get_device_history v2 (priority + org isolation of new sources) ──")
IMEI_INV = "355444444444444"     # tenant C: has an inventory_aging_device cost (source ①)
IMEI_POS = "355555555555555"     # tenant C: sold via raw_sales (source ②) — no inventory row
IMEI_MA  = "355666666666666"     # tenant D: MA marketplace order (source ③)
STORE2 = {
    "inventory_aging_device": [
        {"org_id": "C", "imei": IMEI_INV, "serial": None, "sku": "APL-15-128", "item": "iPhone 15",
         "store": "C-STORE", "unit_cost": 549.0, "received_date": (date.today() - timedelta(days=52)).isoformat(),
         "days_in_stock": None, "as_of_date": "2026-07-01"},
        # tenant D has a DIFFERENT cost for the SAME imei — must never leak into C.
        {"org_id": "D", "imei": IMEI_INV, "serial": None, "sku": "X", "item": "Galaxy",
         "store": "D-STORE", "unit_cost": 999.0, "received_date": "2026-01-01",
         "days_in_stock": None, "as_of_date": "2026-07-01"},
    ],
    "raw_sales": [
        {"org_id": "C", "serial_1": IMEI_POS, "mdn": "5551110000", "trans_date": "2026-06-15",
         "store": "C-STORE", "salesperson": "Rep C", "product_desc": "Moto G", "contract_type": "New",
         "ext_price": 299.0, "gp": 120.0, "trans_id": "T1"},
    ],
    "raw_ma_commission": [
        {"org_id": "D", "imei": IMEI_MA, "activation_order": "ORD-D-1", "sku": "S", "tx_date": "2026-06-01"},
    ],
    "raw_ma_fulfillment": [
        {"org_id": "D", "order_number": "ORD-D-1", "price": 259.5, "product_name": "TCL 40",
         "date_ordered": "2026-05-20"},
        {"org_id": "C", "order_number": "ORD-D-1", "price": 1.0},   # C must not see D's order
    ],
}
R.sb = lambda: FakeClient(STORE2)

# ① inventory-aging cost wins (universal POS/SKU), owed_to_vip absent for this tenant.
rInv = run_route(R.get_device_history(q=IMEI_INV, authorization="", org_id="C"))
check("① inv-aging cost 549 chosen + provenance names POS inventory cost",
      rInv["purchase_price"]["amount"] == 549.0 and "POS inventory cost" in rInv["purchase_price"]["label"])
check("① aging from inventory_aging_device (non-VIP path), current-age 52",
      rInv["aging"]["source"] == "inventory_aging_device" and rInv["aging"]["days_on_inventory"] == 52)
check("① found True via inventory row", rInv["found"] is True)
# ORG ISOLATION — C never sees D's 999 for the same imei.
rInvD = run_route(R.get_device_history(q=IMEI_INV, authorization="", org_id="D"))
check("ISO: D inv cost 999 (its own), C stays 549", rInvD["purchase_price"]["amount"] == 999.0
      and rInv["purchase_price"]["amount"] == 549.0)
check("ISO: C aging store C-STORE, never D-STORE", rInv["aging"]["store"] == "C-STORE")

# ② at-sale POS cost (ext − GP = 299 − 120 = 179) when there is NO inventory row.
rPos = run_route(R.get_device_history(q=IMEI_POS, authorization="", org_id="C"))
check("② POS at-sale cost 179 (299−120) + provenance 'ext − GP'",
      rPos["purchase_price"]["amount"] == 179.0 and "ext − GP" in rPos["purchase_price"]["label"])
check("② sold_by_us True + device model from sale", rPos["sold_by_us"] and rPos["device"]["phone_model"] == "Moto G")

# ③ MA marketplace order price (tenant D) — imei → activation_order → order_number → price.
rMa = run_route(R.get_device_history(q=IMEI_MA, authorization="", org_id="D"))
check("③ MA marketplace price 259.5 + provenance names the order",
      rMa["purchase_price"]["amount"] == 259.5 and "MA marketplace order" in rMa["purchase_price"]["label"])
check("③ found via MA commission row", rMa["found"] is True)
# ORG ISOLATION of MA — C asking for D's imei sees NOTHING of D's order/price.
rMaC = run_route(R.get_device_history(q=IMEI_MA, authorization="", org_id="C"))
check("ISO: C never sees D's MA order/price (honest empty for C)",
      rMaC["purchase_price"]["found"] is False and rMaC["found"] is False)

# ④/⑤ asset_ledger raw_row beats owed_to_vip; owed_to_vip is LAST RESORT, relabeled house-basis.
STORE2["asset_ledger"] = [
    {"org_id": "C", "esn_imei": "355777777777777", "phone_number": "5550000001", "store": "C-STORE",
     "market": "PA", "device_model": "iPhone 14", "category": "On Inventory", "status": "In Stock",
     "acquired_date": "2026-01-01", "owed_to_vip": 300, "raw_row": {"Device Cost": "410.00"}},
    {"org_id": "C", "esn_imei": "355888888888888", "phone_number": "5550000002", "store": "C-STORE",
     "market": "PA", "device_model": "iPhone 13", "category": "On Inventory", "status": "In Stock",
     "acquired_date": "2026-01-01", "owed_to_vip": 275, "raw_row": {}},
]
r4 = run_route(R.get_device_history(q="355777777777777", authorization="", org_id="C"))
check("④ raw_row Device Cost 410 wins over owed 300", r4["purchase_price"]["amount"] == 410.0)
r5 = run_route(R.get_device_history(q="355888888888888", authorization="", org_id="C"))
check("⑤ owed_to_vip 275 LAST RESORT + relabeled 'VIP billing basis (house...)'",
      r5["purchase_price"]["amount"] == 275.0 and "VIP billing basis" in r5["purchase_price"]["label"])

# full priority: an inventory-aging cost OUTRANKS an asset_ledger owed_to_vip for the SAME device.
STORE2["asset_ledger"].append(
    {"org_id": "C", "esn_imei": "355999999999999", "phone_number": "5550000003", "store": "C-STORE",
     "market": "PA", "device_model": "iPhone 15", "acquired_date": "2026-01-01", "owed_to_vip": 800, "raw_row": {}})
STORE2["inventory_aging_device"].append(
    {"org_id": "C", "imei": "355999999999999", "sku": "APL-15", "item": "iPhone 15", "store": "C-STORE",
     "unit_cost": 500.0, "received_date": "2026-06-01", "as_of_date": "2026-07-01"})
rPri = run_route(R.get_device_history(q="355999999999999", authorization="", org_id="C"))
check("PRIORITY: inv-aging 500 (①) beats owed_to_vip 800 (⑤) for same device",
      rPri["purchase_price"]["amount"] == 500.0 and rPri["purchase_price"]["source"] == "inventory_aging_device.unit_cost")
check("PRIORITY: asset_ledger still drives aging when present (house path)",
      rPri["aging"]["source"] == "asset_ledger")

# honest empty across ALL v2 sources — unknown device, no rows anywhere.
rEmpty = run_route(R.get_device_history(q="000000000000001", authorization="", org_id="C"))
check("EMPTY: no source → purchase_price.found False (never $0)", rEmpty["purchase_price"]["found"] is False)
check("EMPTY: provenance mentions the pending feed", "feed pending" in rEmpty["purchase_price"]["provenance"])


# ══ 8. regression — the existing device_history_proof.py must still be green ═════════════════════════
print("── 8. regression: existing device_history_proof.py ──")
_here = os.path.dirname(__file__)
_r = subprocess.run([sys.executable, os.path.join(_here, "device_history_proof.py")],
                    capture_output=True, text=True)
_tail = _r.stdout.strip().splitlines()[-1] if _r.stdout.strip() else ""
check(f"existing device_history_proof.py exits green ({_tail})", _r.returncode == 0)


# ══ 9. regression — the frontend export builder proof (RULE FOUR) must still be green ════════════════
print("── 9. regression: frontend export mjs (prove_device_history_export.mjs) ──")
_mjs = os.path.abspath(os.path.join(_here, "..", "..", "frontend", "scratchpad", "prove_device_history_export.mjs"))
_node = subprocess.run(["bash", "-lc", "command -v node || true"], capture_output=True, text=True).stdout.strip()
if _node and os.path.exists(_mjs):
    _rm = subprocess.run([_node, _mjs], capture_output=True, text=True)
    _mtail = _rm.stdout.strip().splitlines()[-1] if _rm.stdout.strip() else ""
    check(f"export mjs exits green ({_mtail})", _rm.returncode == 0, _rm.stderr[-300:])
else:
    print("  SKIP  export mjs (node not on PATH here — run it directly; see verify note)")


print(f"\n==== device-history AGING+PRICE proof: {_pass} passed, {_fail} failed ====")
sys.exit(1 if _fail else 0)
