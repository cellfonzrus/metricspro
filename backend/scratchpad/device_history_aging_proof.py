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

resA = asyncio.run(R.get_device_history(q=IMEI, authorization="", org_id="A"))
resB = asyncio.run(R.get_device_history(q=IMEI, authorization="", org_id="B"))

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
resSold = asyncio.run(R.get_device_history(q="990000000000001", authorization="", org_id="A"))
check("SOLD: is_sold True + days 50 (acquired→sold)", resSold["aging"]["is_sold"] and resSold["aging"]["days_on_inventory"] == 50)
check("SOLD: billing PayGo/Friday surfaced", resSold["aging"]["billing"]["payg_date"] == "2026-01-15"
      and resSold["aging"]["billing"]["billing_friday"] == "2026-01-16")
check("SOLD: owed_to_vip 250 is the purchase price", resSold["purchase_price"]["amount"] == 250.0)

# NO inventory record (unknown IMEI) — honest empty, never a fabricated zero.
resNone = asyncio.run(R.get_device_history(q="111111111111119", authorization="", org_id="A"))
check("NO-REC: aging.found False + honest note", resNone["aging"]["found"] is False and "No inventory" in resNone["aging"]["note"])
check("NO-REC: purchase_price.found False (not $0)", resNone["purchase_price"]["found"] is False and resNone["purchase_price"]["amount"] is None)


# ══ 8. regression — the existing device_history_proof.py must still be green ═════════════════════════
print("── 8. regression: existing device_history_proof.py ──")
_here = os.path.dirname(__file__)
_r = subprocess.run([sys.executable, os.path.join(_here, "device_history_proof.py")],
                    capture_output=True, text=True)
_tail = _r.stdout.strip().splitlines()[-1] if _r.stdout.strip() else ""
check(f"existing device_history_proof.py exits green ({_tail})", _r.returncode == 0)


print(f"\n==== device-history AGING+PRICE proof: {_pass} passed, {_fail} failed ====")
sys.exit(1 if _fail else 0)
