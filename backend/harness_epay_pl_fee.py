"""Offline proof for the ePay (Boost) service-charge FEE as a P&L income line (P3, owner 2026-08-20).

Drives the REAL shipped `account.coa.build_inputs` + `account.engine._assemble` against an in-memory
FAKE Supabase client (no DB, no network), and proves the new `epay_fee_income` P&L line equals the ePay
fee-recon's SYSTEM side for the same stores/period — using the recon's OWN `aggregate_system_fee`
helper, so the two figures can never drift.

Run: `python3 harness_epay_pl_fee.py` from backend/.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.modules.account import coa                         # noqa: E402
from app.modules.account import engine                      # noqa: E402
from app.modules.commcalc import epay_fee_recon as FR       # noqa: E402

PASS, FAIL = [], []

ORG = "00000000-0000-0000-0000-000000000001"
PERIOD = "2026-08"          # coa._period.period_keys also matches the "August 2026" spelling

ADDR_A = "116-36 Queens Blvd"
ADDR_B = "3 Palisade Ave"
GHOST = "Ghost Store"        # a store with NO mapping — resolves to itself on BOTH sides


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


def approx(a, b, eps=0.005):
    return abs(float(a) - float(b)) < eps


# ── in-memory Supabase client: schema/table/select/eq/in_/gte/lt/ilike/range/limit/execute ──────────
# Missing tables return empty (build_inputs is fully defensive), and every WRITE verb raises so a clean
# run also proves build_inputs never writes.
class _Res:
    def __init__(self, data):
        self.data = data


class _WriteAttempted(AssertionError):
    pass


class _Q:
    def __init__(self, rows):
        self.rows = rows
        self._eq, self._in, self._gte, self._lt, self._ilike = {}, {}, {}, {}, None

    def select(self, *a, **k):
        return self

    def eq(self, c, v):
        self._eq[c] = v
        return self

    def in_(self, c, v):
        self._in[c] = list(v)
        return self

    def gte(self, c, v):
        self._gte[c] = v
        return self

    def lt(self, c, v):
        self._lt[c] = v
        return self

    def ilike(self, c, pat):
        self._ilike = (c, str(pat).strip("%").lower())
        return self

    def limit(self, n):
        return self

    def order(self, *a, **k):
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def insert(self, *a, **k):
        raise _WriteAttempted("insert")

    def upsert(self, *a, **k):
        raise _WriteAttempted("upsert")

    def update(self, *a, **k):
        raise _WriteAttempted("update")

    def delete(self, *a, **k):
        raise _WriteAttempted("delete")

    def execute(self):
        out = []
        for r in self.rows:
            ok = all(str(r.get(k)) == str(v) for k, v in self._eq.items())
            for k, v in self._in.items():
                ok = ok and str(r.get(k)) in {str(x) for x in v}
            for k, v in self._gte.items():
                ok = ok and r.get(k) is not None and str(r.get(k)) >= str(v)
            for k, v in self._lt.items():
                ok = ok and r.get(k) is not None and str(r.get(k)) < str(v)
            if self._ilike:
                col, pat = self._ilike
                ok = ok and pat in str(r.get(col) or "").lower()
            if ok:
                out.append(dict(r))
        rng = getattr(self, "_range", None)
        return _Res(out[rng[0]:rng[1] + 1] if rng else out)


class _Schema:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(list(self.tables.get(name, [])))

    def rpc(self, *a, **k):
        raise _WriteAttempted("rpc")


class Client:
    def __init__(self, tables):
        self.tables = tables

    def schema(self, _):
        return self._schema()

    def _schema(self):
        return _Schema(self.tables)

    def table(self, name):
        return _Q(list(self.tables.get(name, [])))


def sale(store, prod, ext, tid, voided="", dept="Bill Payments", cat="Other Charge",
         date="2026-08-18", gp=0.0):
    return {"trans_id": tid, "org_id": ORG, "period": PERIOD, "store": store,
            "trans_date": date, "department": dept, "category": cat, "product_desc": prod,
            "ext_price": ext, "gp": gp, "voided": voided}


# The real shape of a Boost bill-payment receipt: the customer's refill passes through, the store's $4
# ePay service charge is income. Two fees at A, a voided fee at A (excluded), a fee at B, and a fee at an
# UNMAPPED store (kept, keyed by its own name on both sides).
SALES = [
    sale(ADDR_A, "Boost RTR $1-$650", 60.00, "t1"),                 # pass-through, not income
    sale(ADDR_A, "ePay Service Charge", 4.00, "t2"),
    sale(ADDR_A, "ePay Service Charge", 4.00, "t3"),
    sale(ADDR_A, "ePay Service Charge", 4.00, "t4", voided="true"),  # voided → excluded
    sale(ADDR_A, "MyBat Screen Protector", 30.00, "t5", dept="Ondigo", cat="Screen Protectors", gp=20.0),
    sale(ADDR_B, "ePay Service Charge", 4.00, "t6"),
    sale(GHOST, "ePay Service Charge", 4.00, "t7"),
]

STORE_MAPPING = [
    {"org_id": ORG, "store_code": "1001", "store_address": ADDR_A},
    {"org_id": ORG, "store_code": "1002", "store_address": ADDR_B},
]


def build(service_products=None):
    cfg = {"org_id": ORG, "accessory_cogs_pct": 0.20,
           "service_fee_products": service_products or []}
    tables = {"raw_sales": SALES, "store_mapping": STORE_MAPPING, "account_config": [cfg],
              "companies": [{"id": "c1", "name": "Default Company", "org_id": ORG}]}
    return coa.build_inputs(Client(tables), ORG, PERIOD)


# ── 1. the matcher is literally the recon's own helper ──────────────────────────────────────────────
check("is_fee_desc is the recon helper", FR.is_fee_desc("ePay Service Charge") is True
      and FR.is_fee_desc("Boost RTR $1-$650") is False)

# ── 2. default (no service_fee_products): fees land on epay_fee_income, at full $, NO COGS ───────────
I = build()
fee = I["epay_fee_income"]["by_store"]
check("store A: 2×$4 (voided one excluded) = $8", approx(fee.get(ADDR_A, 0), 8.00), fee)
check("store B: $4", approx(fee.get(ADDR_B, 0), 4.00), fee)
check("unmapped store keyed by itself: $4", approx(fee.get(GHOST, 0), 4.00), fee)
check("ePay fee carries NO COGS", approx(I["accessory_cost"]["by_store"].get(ADDR_A, 0), 6.00)   # 20% of the $30 accessory only
      and I["device_cost"]["by_store"].get(ADDR_A, 0) == 0.0)
check("the $60 refill on the SAME dept is booked NOWHERE (pass-through)",
      I["accessory_rev"]["by_store"].get(ADDR_A, 0) == 30.00
      and I["device_rev"]["by_store"].get(ADDR_A, 0) == 0.0
      and I["service_income"]["by_store"].get(ADDR_A, 0) == 0.0)
check("fee is drillable by name", I["epay_fee_income"]["detail"].get("ePay service charge") == 16.00)

# ── 3. THE PROOF: the P&L fee line == the recon's SYSTEM fee for the same stores/period ──────────────
# Same rows, same store resolver (coa's) run through the recon's OWN aggregation — so any drift in the
# matcher, the void rule, the $ column, or store resolution would break this equality.
resolver = coa.store_resolver(Client({"store_mapping": STORE_MAPPING}), ORG)
recon_sd = FR.aggregate_system_fee(SALES, resolver)          # {(store, date): fee}
recon_by_store = {}
for (store, _d), amt in recon_sd.items():
    recon_by_store[store] = round(recon_by_store.get(store, 0.0) + amt, 2)
check("P&L epay_fee_income per-store == recon system fee per-store", fee == recon_by_store,
      f"pl={fee} recon={recon_by_store}")
pl_total = round(sum(fee.values()) + I["epay_fee_income"]["company_wide"], 2)
recon_total = round(sum(recon_by_store.values()), 2)
check("P&L epay_fee_income TOTAL == recon system fee TOTAL ($16)",
      approx(pl_total, recon_total) and approx(pl_total, 16.00), f"pl={pl_total} recon={recon_total}")

# ── 4. backward-compat: a tenant that routed this product into service_income keeps it (no double) ──
I2 = build(service_products=["ePay Service Charge"])
check("service_fee_products configured → fee stays in service_income",
      approx(I2["service_income"]["by_store"].get(ADDR_A, 0), 8.00))
check("service_fee_products configured → epay_fee_income is empty (no double-count)",
      I2["epay_fee_income"]["by_store"].get(ADDR_A, 0) == 0.0)

# ── 5. spec placement: revenue, auto_opt, store-grained ─────────────────────────────────────────────
spec = {k: (lbl, sec, kind, grain) for k, lbl, sec, kind, grain in coa.PL_SPEC}
check("epay_fee_income is a REVENUE line", spec["epay_fee_income"][1] == "revenue")
check("epay_fee_income is auto_opt (hidden when zero)", spec["epay_fee_income"][2] == "auto_opt")
check("epay_fee_income is store-grained (survives the store/market filter)",
      spec["epay_fee_income"][3] == "store")

# ── 6. assembled statement: line MATERIALIZES with value, and is HIDDEN at zero ──────────────────────
PL_SECTIONS = [("Revenue", "revenue"), ("Cost of Goods Sold", "cogs"),
               ("Operating Expenses", "opex"), ("Other", "other")]


def assemble(inputs):
    return engine._assemble(inputs, [], coa.PL_SPEC, coa.PL_LABEL, PL_SECTIONS,
                            "consolidated", None, True)


pl = assemble(I)
rev_lines = next(s["lines"] for s in pl["sections"] if s["type"] == "revenue")
epay_line = next((ln for ln in rev_lines if ln["key"] == "epay_fee_income"), None)
check("assembled P&L shows the epay_fee_income line", epay_line is not None)
check("assembled line amount == $16 (8+4+4)", epay_line and approx(epay_line["amount"], 16.00),
      epay_line)

# no ePay fee rows at all → the auto_opt line must NOT appear (byte-identical to before the feature)
NO_FEE_SALES = [sale(ADDR_A, "MyBat Screen Protector", 30.00, "z1", dept="Ondigo",
                     cat="Screen Protectors", gp=20.0)]
tables0 = {"raw_sales": NO_FEE_SALES, "store_mapping": STORE_MAPPING,
           "account_config": [{"org_id": ORG, "accessory_cogs_pct": 0.20, "service_fee_products": []}],
           "companies": [{"id": "c1", "name": "Default Company", "org_id": ORG}]}
pl0 = assemble(coa.build_inputs(Client(tables0), ORG, PERIOD))
rev0 = next(s["lines"] for s in pl0["sections"] if s["type"] == "revenue")
check("no ePay fees → epay_fee_income line is hidden (auto_opt)",
      not any(ln["key"] == "epay_fee_income" for ln in rev0))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1)
