"""Proof harness — bill-pay coverage recon + P&L pass-through carve-out (owner 2026-09-02,
item 5; mig 939).

Proves, stdlib-only and DB-free:
  A. metric_recon.reconcile_billpay_coverage (PURE): "billpay ≤ cash + card" per store/day —
     exceptions only when billpay EXCEEDS collected (+tolerance); covered days never flag;
     totals/coverage; no-data honesty.
  B. billpay_pl config: house default 'off'; presentation/settlement validation; pre-939 schema
     degrades to defaults.
  C. billpay_cells (PURE): the declared ePay split summed per store, DM-VERIFIED corrections
     winning at store-day grain (unverified corrections ignored), cash/credit kept separate.
  D. billpay_bookings (PURE): the matched ± pair — Σ(collected) + Σ(offset) == 0 for EVERY input
     (net income can never move), offset label follows the org's settlement convention
     ('remit_separate' vs 'net_from_commission' — config words, no carrier name in code),
     'off' books nothing.
  E. PL_SPEC: both lines present as revenue/auto_opt/store.

Run: python3 backend/harness_billpay_pl.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))
sys.path.insert(0, os.path.dirname(__file__))

from app.modules.commcalc.metric_recon import reconcile_billpay_coverage  # noqa: E402
from app.modules.account import billpay_pl  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


print("A. reconcile_billpay_coverage")
bp = {("S1", "2026-08-01"): {"amount": 500.0, "_name": "Store 1"},
      ("S1", "2026-08-02"): {"amount": 900.0},
      ("S2", "2026-08-01"): {"amount": 100.0}}
col = {("S1", "2026-08-01"): {"cash": 600.0, "card": 200.0},   # covered (500 ≤ 800)
       ("S1", "2026-08-02"): {"cash": 400.0, "card": 100.0},   # EXCEPTION (900 > 500)
       ("S2", "2026-08-01"): {"cash": 100.0, "card": 0.0}}     # exactly equal — fine ("equal to or less")
r = reconcile_billpay_coverage(bp, col, tolerance_amt=1.0)
check("only the uncovered day flags", r["counts"] == {"store_days": 3, "covered": 2, "exceptions": 1})
ex = r["store_days"][0]
check("exception row carries the evidence", ex["day"] == "2026-08-02" and ex["billpay"] == 900.0
      and ex["collected"] == 500.0 and ex["excess"] == 400.0)
check("equal-to is covered (owner: 'equal to or less')",
      all(row["day"] != "2026-08-01" for row in r["store_days"]))
check("totals + coverage pct", r["totals"]["billpay"] == 1500.0 and r["totals"]["collected"] == 1400.0)
check("status mismatch → 'exceptions'", r["status"] == "exceptions")
check("all covered → 'covered'", reconcile_billpay_coverage(
    {("S1", "d"): {"amount": 10.0}}, {("S1", "d"): {"cash": 50.0, "card": 0.0}})["status"] == "covered")
check("no data honest", reconcile_billpay_coverage({}, {})["status"] == "no_data")
check("billpay day with NO closing at all flags (collected 0)",
      reconcile_billpay_coverage({("S3", "d"): {"amount": 50.0}}, {})["counts"]["exceptions"] == 1)
check("tolerance band", reconcile_billpay_coverage(
    {("S1", "d"): {"amount": 100.5}}, {("S1", "d"): {"cash": 100.0, "card": 0.0}},
    tolerance_amt=1.0)["counts"]["exceptions"] == 0)

print("B. billpay_pl config")
check("house default off + remit_separate",
      billpay_pl.default_config() == {"presentation": "off", "settlement": "remit_separate"})


class _FailingClient:
    def schema(self, *_a, **_k):
        raise RuntimeError("no db in harness")


check("pre-939 DB degrades to defaults",
      billpay_pl.load_config(_FailingClient(), "org") == billpay_pl.default_config())

print("C. billpay_cells")
rows = [
    {"store_code": "S1", "close_date": "2026-08-01", "epay_on_cash": 100.0, "epay_on_credit": 20.0},
    {"store_code": "S1", "close_date": "2026-08-01", "epay_on_cash": 50.0, "epay_on_credit": 0.0},
    {"store_code": "S1", "close_date": "2026-08-02", "epay_on_cash": 30.0, "epay_on_credit": 0.0},
    {"store_code": "S2", "close_date": "2026-08-01", "epay_on_cash": 10.0, "epay_on_credit": 5.0},
]
vers = {("S1", "2026-08-01"): {"verified": True, "dm_epay_cash": 120.0, "dm_epay_cc": 25.0},
        ("S2", "2026-08-01"): {"verified": False, "dm_epay_cash": 999.0}}   # unverified — ignored
cells, meta = billpay_pl.billpay_cells(rows, vers)
check("DM-verified correction replaces the store-day rep sum (150→120 cash, 20→25 cc)",
      cells["S1"] == {"cash": 150.0, "credit": 25.0}, str(cells))
check("unverified correction ignored", cells["S2"] == {"cash": 10.0, "credit": 5.0})
check("meta honesty", meta["dm_corrected_days"] == 1 and meta["store_days"] == 3
      and meta["total"] == round(150.0 + 25.0 + 15.0, 2))
check("no rows → empty", billpay_pl.billpay_cells([], {})[0] == {})

print("D. billpay_bookings")
cfg = {"presentation": "carveout", "settlement": "net_from_commission"}
bookings, label = billpay_pl.billpay_bookings(cells, cfg)
net = round(sum(a for _k, _s, a, _d in bookings), 2)
check("THE INVARIANT: collected + offset net to ZERO (net income can never move)", net == 0.0)
check("offset label per settlement convention (total style)",
      label == "Bill payments netted from carrier commission (pass-through)")
_b2, label2 = billpay_pl.billpay_bookings(cells, {"presentation": "carveout",
                                                  "settlement": "remit_separate"})
check("offset label per settlement convention (boost style)",
      label2 == "Bill payments remitted to processor (pass-through)")
col_sum = round(sum(a for k, _s, a, _d in bookings if k == billpay_pl.COLLECTED_KEY), 2)
off_sum = round(sum(a for k, _s, a, _d in bookings if k == billpay_pl.OFFSET_KEY), 2)
check("collected positive / offset negative, equal magnitude",
      col_sum == 190.0 and off_sum == -190.0)
check("per-store pairing (each store's offset = −its collected)", all(
    round(sum(a for k, s, a, _d in bookings if s == st and k == billpay_pl.COLLECTED_KEY)
          + sum(a for k, s, a, _d in bookings if s == st and k == billpay_pl.OFFSET_KEY), 2) == 0.0
    for st in {"S1", "S2"}))
check("'off' books nothing", billpay_pl.billpay_bookings(cells, {"presentation": "off"}) == ([], None))
check("cash/credit detail labels present", {d for k, _s, _a, d in bookings
                                            if k == billpay_pl.COLLECTED_KEY}
      == {"Bill payments on cash", "Bill payments on credit/card"})

print("E. PL_SPEC lines")
from app.modules.account import coa  # noqa: E402
spec = {k: (sec, kind, grain) for k, _l, sec, kind, grain in coa.PL_SPEC}
check("billpay_collected: revenue/auto_opt/store", spec.get("billpay_collected") == ("revenue", "auto_opt", "store"))
check("billpay_offset: revenue/auto_opt/store", spec.get("billpay_offset") == ("revenue", "auto_opt", "store"))

print()
if FAILS:
    print(f"❌ {len(FAILS)} failure(s): {FAILS}")
    sys.exit(1)
print("✅ harness_billpay_pl: ALL PASS")
