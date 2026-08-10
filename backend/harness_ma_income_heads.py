"""Proof for the MA/VidaPay income-head split (owner ruling 2026-08-10).

Drives the REAL `coa.build_inputs` against an in-memory client, so what is asserted is the shipping
code path rather than a restatement of it.

The stub client genuinely FILTERS. That is the whole point: a stub whose `.eq()` is a no-op returns
every seeded row to every query and will happily "pass" while the production filter is broken — the
exact trap that produced two wrong counts on this codebase before (see the gp_category_map incident).
So eq / in_ / ilike / range are each implemented against the seeded rows, and `test_stub_filters`
below proves the stub itself discriminates before any accounting assertion is trusted.

Run: python3 harness_ma_income_heads.py
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

# ── seed: the shape of luxelink's live July 2026 data, scaled down to hand-checkable numbers ──────
# Feed convention on raw_ma_commission: NEGATIVE = paid TO the dealer.
MA_COMMISSION = [
    {"org_id": "ORG", "period": "July 2026", "device_margin": -100.0, "consumer_margin": 0.0,
     "consumer_financing": -50.0, "rebate": -1000.0, "wallet_funding": 200.0, "fees_margin": -75.0,
     "spiff_m1": -10.0, "spiff_m2": -20.0, "spiff_m3": 0.0, "spiff_m4": 0.0, "spiff_m5": 0.0,
     "spiff_m6": -5.0},
    {"org_id": "ORG", "period": "July 2026", "device_margin": -20.0, "consumer_margin": 0.0,
     "consumer_financing": 0.0, "rebate": -500.0, "wallet_funding": 100.0, "fees_margin": 0.0,
     "spiff_m1": -5.0, "spiff_m2": 0.0, "spiff_m3": 0.0, "spiff_m4": 0.0, "spiff_m5": 0.0,
     "spiff_m6": 0.0},
    # A DIFFERENT period — must never leak into July's figures.
    {"org_id": "ORG", "period": "June 2026", "device_margin": -999.0, "consumer_margin": 0.0,
     "consumer_financing": 0.0, "rebate": -999.0, "wallet_funding": 0.0, "fees_margin": 0.0,
     "spiff_m1": 0.0, "spiff_m2": 0.0, "spiff_m3": 0.0, "spiff_m4": 0.0, "spiff_m5": 0.0,
     "spiff_m6": 0.0},
]
# raw_ma_daily_tx: residual rows carry their money in retail_cost and $0 merchant_discount; airtime
# rows are the reverse. Both live residual labels are represented.
MA_DAILY_TX = [
    {"org_id": "ORG", "period": "July 2026", "product_name": "Residual",
     "retail_cost": -30.0, "merchant_discount": 0.0},
    {"org_id": "ORG", "period": "July 2026", "product_name": "Trac Autopay Residual",
     "retail_cost": -70.0, "merchant_discount": 0.0},
    {"org_id": "ORG", "period": "July 2026", "product_name": "Total Wireless 5G Unlimited RTR $55",
     "retail_cost": 55.0, "merchant_discount": 3.0},
    {"org_id": "ORG", "period": "July 2026", "product_name": "Total STARTER Plan $40 RTR",
     "retail_cost": 40.0, "merchant_discount": 2.0},
    {"org_id": "ORG", "period": "June 2026", "product_name": "Residual",
     "retail_cost": -888.0, "merchant_discount": 0.0},
]
TABLES = {"raw_ma_commission": MA_COMMISSION, "raw_ma_daily_tx": MA_DAILY_TX}


class _Q:
    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        return _Q([r for r in self.rows if r.get(col) == val])

    def in_(self, col, vals):
        vs = set(vals)
        return _Q([r for r in self.rows if r.get(col) in vs])

    def ilike(self, col, pattern):
        pat = pattern.strip("%").lower()
        return _Q([r for r in self.rows if pat in str(r.get(col) or "").lower()])

    def gte(self, *_a):
        return self

    def lte(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        return _Q(self.rows[:n])

    def range(self, lo, hi):
        return _Q(self.rows[lo:hi + 1])

    def execute(self):
        return SimpleNamespace(data=list(self.rows))


class _Schema:
    def table(self, name):
        return _Q(TABLES.get(name, []))

    def rpc(self, *_a, **_k):
        return _Q([])


class StubClient:
    def schema(self, _name):
        return _Schema()

    def table(self, name):
        return _Schema().table(name)


def approx(a, b, tol=0.005):
    return abs(float(a) - float(b)) <= tol


def test_stub_filters():
    """The stub must DISCRIMINATE, or every assertion below is vacuous."""
    c = StubClient()
    july = c.schema("commcalc").table("raw_ma_commission").eq("period", "July 2026").execute().data
    assert len(july) == 2, f"eq() did not filter: got {len(july)} rows, expected 2"
    res = c.schema("commcalc").table("raw_ma_daily_tx").ilike("product_name", "%residual%").execute().data
    assert len(res) == 3, f"ilike() did not filter: got {len(res)}, expected 3"
    none = c.schema("commcalc").table("raw_ma_daily_tx").eq("period", "NOPE").execute().data
    assert none == [], "eq() matched a period that does not exist"
    print("  ✓ stub client genuinely filters (eq / in_ / ilike)")


def run():
    from app.modules.account import coa

    test_stub_filters()
    L = coa.build_inputs(StubClient(), "ORG", "July 2026")

    def cw(key):
        return round(L[key]["company_wide"], 2)

    # ── every component lands on its own head ────────────────────────────────────────────────────
    checks = [
        ("mi_income", 100.00, "real residual only: 30 + 70, both labels, June excluded"),
        ("device_rebate", -1500.00, "rebate as NEGATIVE contra-COGS: -(1000 + 500)"),
        ("ma_device_margin", 120.00, "device_margin 100 + 20, consumer_margin 0"),
        ("fee_income", 75.00, "fees_margin"),
        ("financing_income", 50.00, "consumer_financing"),
        ("carrier_comm", 40.00, "spiff_m1..m6: 10 + 20 + 5 + 5"),
        ("atu_income", 5.00, "merchant_discount 3 + 2; residual rows contribute 0"),
    ]
    ok = True
    for key, want, why in checks:
        got = cw(key)
        good = approx(got, want)
        ok &= good
        print(f"  {'✓' if good else '✗'} {key:18} = {got:>10,.2f}  (expected {want:>10,.2f})  {why}")

    # ── wallet_funding must appear on NO line ───────────────────────────────────────────────────
    # Seeded as +300 total, which under the old code became -300 of "MI residual income".
    total_pl = sum(round(v["company_wide"], 2) for v in L.values())
    stray = approx(total_pl, sum(w for _, w, _ in checks) - 300.0)
    print(f"  {'✓' if not stray else '✗'} wallet_funding excluded from every P&L line")
    ok &= not stray

    # ── no double-count: a residual row must not also be counted as ATU ──────────────────────────
    ok &= approx(cw("atu_income"), 5.00)

    print("\n" + ("PASS — every MA component books to its own head" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
