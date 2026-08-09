"""Proof for the P&L `service_income` line (mig 613) — owner directive 2026-08-09:
"epay service charge of $4 is an income to the store and should be added as a line item in p&l".

The whole risk of this feature is ONE confusion: the fee and the bill payment it rides on live in the
SAME department ('Bill Payments'). The fee is income; the refill is the customer's money passing
through. A department-level mapping would have booked ~$60k/month of pass-through as store revenue.
So the config is a PRODUCT list, matched EXACTLY, and that is what these checks pin down.

Pure unit tests over the REAL coa functions with an in-memory client; NO live DB.
Run:  cd backend && python3 scratchpad/service_fee_income_proof.py
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.modules.account import coa  # noqa: E402

ORG = "00000000-0000-0000-0000-000000000001"
PERIOD = "July 2026"
STORE = "957 Pennsylvania Ave"

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}")


# ── in-memory client (schema/table/select/eq/in_/limit/range/execute) ─────────────────────────────
class Res:
    def __init__(self, data): self.data = data


class Q:
    def __init__(self, rows): self.rows = rows
    def select(self, *a, **k): return self
    def eq(self, col, val): return Q([r for r in self.rows if str(r.get(col)) == str(val)])
    def in_(self, col, vals):
        v = {str(x) for x in vals}
        return Q([r for r in self.rows if str(r.get(col)) in v])
    def limit(self, n): return Q(self.rows[:n])
    def range(self, a, b): return Q(self.rows[a:b + 1])
    def execute(self): return Res(self.rows)


class Client:
    def __init__(self, tables): self.tables = tables
    def schema(self, _): return self
    def table(self, name): return Q(list(self.tables.get(name, [])))


def line(prod, dept, cat="", ext=0.0, gp=0.0, tid="1"):
    return {"trans_id": tid, "department": dept, "category": cat, "product_desc": prod,
            "ext_price": ext, "gp": gp, "voided": "", "store": STORE,
            "org_id": ORG, "period": PERIOD}


# The real shape of a Boost bill-payment receipt: the customer's refill, and the store's $4 fee.
SALES = [
    line("Boost RTR $1-$650", "Bill Payments", "Other Charge", ext=60.00, tid="9001"),
    line("ePay Service Charge", "Bill Payments", "Other Charge", ext=4.00, tid="9001"),
    line("ePay Service Charge", "Bill Payments", "Other Charge", ext=4.00, tid="9002"),
    line("MyBat Tempered Glass Screen Protector", "Ondigo", "Screen Protectors", ext=30.00, gp=20.0, tid="9003"),
]


def inputs_with(service_products):
    cfg_row = {"org_id": ORG, "accessory_cogs_pct": 0.20, "service_fee_products": service_products}
    c = Client({"account_config": [cfg_row], "raw_sales": SALES})
    return coa.build_inputs(c, ORG, PERIOD)


print("(1) the fee books as revenue; the bill payment it rides on does NOT")
I = inputs_with(["ePay Service Charge"])
svc = I["service_income"]["by_store"].get(STORE, 0.0)
check("two $4 fees → $8.00 of service_income", svc == 8.00)
check("the $60 refill on the SAME department is booked nowhere (pass-through, not income)",
      I["device_rev"]["by_store"].get(STORE, 0.0) == 0.0
      and I["accessory_rev"]["by_store"].get(STORE, 0.0) == 30.00)
check("service fee carries NO COGS (a fee costs the store nothing to collect)",
      I["device_cost"]["by_store"].get(STORE, 0.0) == 0.0
      and I["accessory_cost"]["by_store"].get(STORE, 0.0) == 6.00)   # 20% of the $30 accessory only
check("the fee is drillable by product name", I["service_income"]["detail"] == {"ePay Service Charge": 8.00})

print("(2) EXACT match, never containment — the guard against booking the refill as income")
I2 = inputs_with(["ePay"])                       # a partial/typed value must match nothing
check("a partial product value books nothing", I2["service_income"]["by_store"].get(STORE, 0.0) == 0.0)
I3 = inputs_with(["epay service charge"])        # casing is not significant
check("matching is case-insensitive", I3["service_income"]["by_store"].get(STORE, 0.0) == 8.00)

print("(3) UNCONFIGURED = byte-identical (no tenant's P&L moves until a product is picked)")
I0 = inputs_with([])
check("empty config → no service_income line", I0["service_income"]["by_store"].get(STORE, 0.0) == 0.0)
check("empty config → accessory revenue/cost unchanged",
      I0["accessory_rev"]["by_store"].get(STORE, 0.0) == 30.00
      and I0["accessory_cost"]["by_store"].get(STORE, 0.0) == 6.00)
noconf = coa.build_inputs(Client({"raw_sales": SALES}), ORG, PERIOD)   # no account_config row at all
check("no account_config row at all → still no service_income, no crash",
      noconf["service_income"]["by_store"].get(STORE, 0.0) == 0.0)

print("(4) the line is on the P&L spec as store-grained REVENUE")
spec = {k: (lbl, sec, kind, grain) for k, lbl, sec, kind, grain in coa.PL_SPEC}
check("service_income is a revenue line", spec["service_income"][1] == "revenue")
check("service_income is store-grained (so it survives the store/market filter)",
      spec["service_income"][3] == "store")

print(f"\n{PASS}/{PASS + FAIL} passed" + ("" if not FAIL else f"  ({FAIL} FAILED)"))
sys.exit(1 if FAIL else 0)
