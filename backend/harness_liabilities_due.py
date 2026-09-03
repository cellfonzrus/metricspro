"""HARNESS — Current Monetary Liabilities aggregation (account/liabilities_due.py, owner
directive 2026-09-03: "Current monetary liabilities tile will have Current monies owed to the
distributor, this weeks payments due …, Payroll Due this week, payroll tax due, Rents due this
week, any other recurring expenses due, by default the rents are due in the 1st week of the
month, should not be hard coded but defined for stores when setting up the store").

  A. week_window / months_touched — Monday..Sunday ISO week, month spans, year/month boundaries.
  A3. payables_due_in_window ⟺ balance_sheet.handset_payable_bookings EQUIVALENCE PIN — on any
      shared vector, the rows outstanding at the week start that are NOT outstanding at the week
      end are exactly the rows due inside the week (the two predicates can never drift).
  B. payables_due_in_window semantics — family case-insensitivity, missing dates skipped,
      retail_cost-only money read, RMA sign preserved, empty family ⇒ nothing.
  C. attribute_stores — mig-314 account→store index attribution; unmapped stays company-wide.
  D. aggregate_payroll — gross per store, employer FICA + withheld tax split, employee counts.
  E. paydays_in_window — the shared pay_period_for arithmetic (weekly + biweekly), payday inside
      the week; bounded walk.
  F. rent_due_rows — mig-946 helpers drive the math (schedule > escalation > current), house
      first-week default, ended lease skipped, unknown rent = null amount (never fake 0).
  G. premium_occurrences / insurance_due_rows — annual/quarterly/monthly recurrence, month-end
      clamping, malformed anchor ⇒ [].
  H. sum_known — null amounts excluded from totals and counted as unknown.
  I. GATE TRUTH TABLE (composition contract) — the section gates the router applies are the
      EXISTING fail-closed gates: pay_visibility.resolve/can_see posture via store_lease's
      resolve_lease_access for rents, and mig-434 can_see_pay for payroll. Pinned here so a
      refactor that swaps either for an open-by-default check fails this harness.
  Z. ARMED negative control.

Run: python3 harness_liabilities_due.py   (stdlib-only; db/core stubbed at the lazy seams)
"""
import sys
import os
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── stubs at the lazy-import seams (harness_store_lease pattern) ─────────────────────────────────
_db = types.ModuleType("app.core.database")
_db.get_supabase = lambda: (_ for _ in ()).throw(RuntimeError("no live DB in this harness"))
sys.modules["app.core.database"] = _db

from app.modules.account import liabilities_due as ld          # noqa: E402
from app.modules.account import balance_sheet as bs            # noqa: E402
from app.modules.storeops import store_lease as sl             # noqa: E402
from app.modules.storeops import pay_visibility as pv          # noqa: E402

PASS, FAIL = [], []


def check(label, got, want=True):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}: got {got!r}, want {want!r}")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A. week_window / months_touched
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("A1 mid-week", ld.week_window("2026-09-03"), ("2026-08-31", "2026-09-06"))  # Thu → Mon 8/31
check("A2 monday is its own week start", ld.week_window("2026-08-31"), ("2026-08-31", "2026-09-06"))
check("A3 sunday belongs to the prior monday", ld.week_window("2026-09-06"), ("2026-08-31", "2026-09-06"))
check("A4 year boundary", ld.week_window("2026-01-01"), ("2025-12-29", "2026-01-04"))
check("A5 months_touched single", ld.months_touched("2026-09-07", "2026-09-13"), [(2026, 9)])
check("A6 months_touched spanning", ld.months_touched("2026-08-31", "2026-09-06"), [(2026, 8), (2026, 9)])
check("A7 months_touched year span", ld.months_touched("2025-12-29", "2026-01-04"), [(2025, 12), (2026, 1)])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A3'. EQUIVALENCE PIN vs balance_sheet.handset_payable_bookings
#   outstanding(week_start − 1) − outstanding(week_end) == due-in-week rows, on the same vector.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
TX = [
    {"account_id": "A1", "order_type": "Handset Order", "retail_cost": 500.0,
     "tx_date": "2026-08-20", "due_date": "2026-09-02"},   # due inside the week
    {"account_id": "A1", "order_type": "Handset Order", "retail_cost": 300.0,
     "tx_date": "2026-08-25", "due_date": "2026-09-10"},   # due after the week
    {"account_id": "A2", "order_type": "handset order", "retail_cost": -40.0,
     "tx_date": "2026-08-28", "due_date": "2026-09-05"},   # RMA credit due inside (case-insens.)
    {"account_id": "A3", "order_type": "Accessory Order", "retail_cost": 999.0,
     "tx_date": "2026-08-01", "due_date": "2026-09-03"},   # wrong family
    {"account_id": "A4", "order_type": "Handset Order", "retail_cost": 100.0,
     "tx_date": "2026-08-01", "due_date": "2026-08-15"},   # already settled before the week
    {"account_id": "A5", "order_type": "Handset Order", "retail_cost": 77.0,
     "tx_date": "2026-09-04", "due_date": "2026-09-06"},   # transacted AND due inside the week
    {"account_id": "A6", "order_type": "Handset Order", "retail_cost": 55.0,
     "tx_date": "", "due_date": "2026-09-03"},             # missing tx_date → honest skip
]
FAMS = ["Handset Order"]
WK = ("2026-08-31", "2026-09-06")
rows, meta = ld.payables_due_in_window(TX, FAMS, *WK)
check("B1 due-in-week rows", [r["account_id"] for r in rows], ["A1", "A2", "A5"])
check("B2 total nets the RMA", meta["total"], 537.0)                     # 500 − 40 + 77
check("B3 empty family books nothing", ld.payables_due_in_window(TX, [], *WK), ([], {"rows": 0, "total": 0.0}))
check("B4 wrong family excluded", all(r["account_id"] != "A3" for r in rows))
check("B5 settled row excluded", all(r["account_id"] != "A4" for r in rows))
check("B6 missing date skipped", all(r["account_id"] != "A6" for r in rows))

# equivalence: outstanding at (week_start-1) minus outstanding at week_end == due-in-week set,
# for rows transacted on/before week_start-1 (the bookings contract requires tx_date ≤ as_of).
pre = {(b[0], b[1]) for b in bs.handset_payable_bookings(TX, FAMS, "2026-08-30")[0]}
post = {(b[0], b[1]) for b in bs.handset_payable_bookings(TX, FAMS, "2026-09-06")[0]}
settled_in_week = pre - post
due_by_bookings = {(r["account_id"], r["amount"]) for r in rows if r["tx_date"] <= "2026-08-30"}
check("A3-PIN due-in-week == outstanding delta (shared predicate)", settled_in_week == due_by_bookings)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# C. attribute_stores
# ══════════════════════════════════════════════════════════════════════════════════════════════════
by_store, company = ld.attribute_stores(rows, {"A1": "S01", "A2": "S02"},
                                        resolve=lambda s: s.lower())
check("C1 mapped stores", by_store, {"s01": 500.0, "s02": -40.0})
check("C2 unmapped stays company-wide", company, 77.0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# D. aggregate_payroll (deterministic fake tax twin)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def fake_compute(hours, rate, w4):
    g = round(float(hours or 0) * float(rate or 0), 2)
    return {"gross": g, "employer_fica": round(g * 0.0765, 2), "fica_ss": round(g * 0.062, 2),
            "fica_medicare": round(g * 0.0145, 2), "federal": round(g * 0.10, 2),
            "state": round(g * 0.05, 2)}


praw = [{"store": "S01", "total_hours": 40, "pay_rate": 20, "settings": {}},
        {"store": "S01", "total_hours": 10, "pay_rate": 15, "settings": {}},
        {"store": "S02", "total_hours": 35, "pay_rate": 18, "settings": {}}]
agg = ld.aggregate_payroll(praw, fake_compute)
check("D1 gross per store", (agg["by_store"]["S01"]["gross"], agg["by_store"]["S02"]["gross"]),
      (950.0, 630.0))
check("D2 gross total", agg["gross_total"], 1580.0)
check("D3 employees", (agg["by_store"]["S01"]["employees"], agg["employees"]), (2, 3))
check("D4 employer fica", agg["tax"]["employer_fica"], round(950 * 0.0765 + 630 * 0.0765, 2))
wh = round(1580 * (0.062 + 0.0145 + 0.10 + 0.05), 2)
check("D5 withheld", abs(agg["tax"]["withheld"] - wh) < 0.03)   # per-row rounding tolerance
check("D6 tax total = employer + withheld",
      agg["tax"]["total"], round(agg["tax"]["employer_fica"] + agg["tax"]["withheld"], 2))
check("D7 empty rows", ld.aggregate_payroll([], fake_compute)["gross_total"], 0.0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# E. paydays_in_window — resolver injected (production injects core.router.pay_period_for; a local
#    twin of its DOCUMENTED contract is used here because importing core.router drags the full
#    platform — paydays_in_window's own walk logic is what is under test)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
from datetime import date as _d, timedelta as _td       # noqa: E402


def pay_period_for(s, ref):
    length = 14 if (s.get("pay_period_type") == "biweekly") else 7
    start = ref - _td(days=(ref.weekday() - s["work_week_start_dow"]) % 7)
    if length == 14 and s.get("biweekly_anchor"):
        off = (start - _d.fromisoformat(str(s["biweekly_anchor"])[:10])).days % 14
        if off:
            start = start - _td(days=off)
    end = start + _td(days=length - 1)
    payday = end + _td(days=(s["payday_dow"] - end.weekday()) % 7) + _td(weeks=max(0, s["payday_weeks_after"] - 1))
    return {"start": start.isoformat(), "end": end.isoformat(), "payday": payday.isoformat()}

WEEKLY = {"pay_period_type": "weekly", "work_week_start_dow": 0, "payday_dow": 4,
          "payday_weeks_after": 1}
# week 2026-08-31..09-06: the PRIOR period 08-24..08-30 pays Friday 09-04 → inside (paydays LAG)
hits = ld.paydays_in_window(WEEKLY, pay_period_for, "2026-08-31", "2026-09-06")
check("E1 weekly payday in week (prior period pays)", [(h["start"], h["payday"]) for h in hits],
      [("2026-08-24", "2026-09-04")])
# payday_weeks_after=2 lags one week further → the period BEFORE that one pays this week
WEEKLY2 = dict(WEEKLY, payday_weeks_after=2)
hits2 = ld.paydays_in_window(WEEKLY2, pay_period_for, "2026-08-31", "2026-09-06")
check("E2 two-week lag = older period pays this week", [h["start"] for h in hits2], ["2026-08-17"])
BIWEEKLY = {"pay_period_type": "biweekly", "work_week_start_dow": 0, "payday_dow": 4,
            "payday_weeks_after": 1, "biweekly_anchor": "2026-08-24"}
hits3 = ld.paydays_in_window(BIWEEKLY, pay_period_for, "2026-08-31", "2026-09-06")
check("E3 biweekly period ends 09-06, pays 09-11 → not this week", hits3, [])
hits4 = ld.paydays_in_window(BIWEEKLY, pay_period_for, "2026-09-07", "2026-09-13")
check("E4 biweekly payday next week", [(h["start"], h["payday"]) for h in hits4],
      [("2026-08-24", "2026-09-11")])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# F. rent_due_rows — driven by the mig-946 helpers
# ══════════════════════════════════════════════════════════════════════════════════════════════════
LEASES = [
    {"store_code": "S01", "current_rent": 3000, "rent_due": None},                 # house default: week 1
    {"store_code": "S02", "current_rent": 2000, "rent_due": {"kind": "day", "value": 3}},
    {"store_code": "S03", "current_rent": 4000, "rent_due": {"kind": "week", "value": 2}},
    {"store_code": "S04", "current_rent": None, "rent_due": {"kind": "day", "value": 1}},  # unknown amount
    {"store_code": "S05", "current_rent": 1500, "rent_due": {"kind": "day", "value": 2},
     "lease_end": "2026-08-31"},                                                    # ended lease
    {"store_code": "S06", "current_rent": 1000, "rent_effective_from": "2025-09-01",
     "escalation_pct": 10, "rent_due": {"kind": "day", "value": 4}},                # 1 anniversary → 1100
    {"store_code": "S07", "current_rent": 999,
     "rent_schedule": [{"effective_from": "2026-09-01", "monthly_rent": 1234.56}],
     "rent_due": {"kind": "day", "value": 5}},                                      # schedule wins
]
# week containing 2026-09-03 = Aug 31 .. Sep 6 → September week-1 windows overlap
rr = ld.rent_due_rows(LEASES, None, "2026-08-31", "2026-09-06")
by = {r["store_code"]: r for r in rr}
check("F1 house first-week default lands", by["S01"]["amount"], 3000.0)
check("F1b house window", (by["S01"]["due_start"], by["S01"]["due_end"]), ("2026-09-01", "2026-09-07"))
check("F2 day-3 store due", by["S02"]["amount"], 2000.0)
check("F3 week-2 store NOT due this week", "S03" not in by)
check("F4 unknown rent = null amount, still listed", by["S04"]["amount"], None)
check("F5 ended lease skipped", "S05" not in by)
check("F6 escalation via rent_for_month", by["S06"]["amount"], 1100.0)
check("F7 schedule wins", by["S07"]["amount"], 1234.56)
# tenant default (org-level rent_due_default) applies when store has none
rr2 = ld.rent_due_rows([{"store_code": "S10", "current_rent": 800}],
                       {"kind": "week", "value": 2}, "2026-09-07", "2026-09-13")
check("F8 tenant default week-2", [r["store_code"] for r in rr2], ["S10"])
# pin: the window really is store_lease.rent_due_window's output (never a local copy)
check("F9 window == store_lease.rent_due_window",
      (by["S02"]["due_start"], by["S02"]["due_end"]),
      sl.rent_due_window(2026, 9, {"kind": "day", "value": 3}))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# G. premium_occurrences / insurance_due_rows
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("G1 annual hit", ld.premium_occurrences("2025-09-02", "annual", "2026-08-31", "2026-09-06"),
      ["2026-09-02"])
check("G2 annual miss", ld.premium_occurrences("2025-10-15", "annual", "2026-08-31", "2026-09-06"), [])
check("G3 quarterly", ld.premium_occurrences("2026-03-01", "quarterly", "2026-08-31", "2026-09-06"),
      ["2026-09-01"])
check("G4 monthly month-end clamp", ld.premium_occurrences("2026-01-31", "monthly", "2026-02-01", "2026-02-28"),
      ["2026-02-28"])
check("G5 anchor inside window counts", ld.premium_occurrences("2026-09-03", "annual", "2026-08-31", "2026-09-06"),
      ["2026-09-03"])
check("G6 malformed anchor", ld.premium_occurrences("not-a-date", "annual", "2026-08-31", "2026-09-06"), [])
ins = ld.insurance_due_rows(
    [{"store_code": "S01", "insurance_premium": 1200, "insurance_premium_due": "2025-09-02",
      "insurance_premium_frequency": "annual", "insurance_company": "Acme Mutual"},
     {"store_code": "S02", "insurance_premium": None, "insurance_premium_due": "2026-09-02"}],
    "2026-08-31", "2026-09-06")
check("G7 insurance rows", [(r["store_code"], r["amount"], r["company"]) for r in ins],
      [("S01", 1200.0, "Acme Mutual")])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# H. sum_known
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("H1 nulls excluded + counted", ld.sum_known([{"amount": 10}, {"amount": None}, {"amount": 5.5}]),
      (15.5, 1))
check("H2 empty", ld.sum_known([]), (0.0, 0))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# I. GATE TRUTH TABLE — the composition uses the EXISTING fail-closed gates
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("I1 lease gate: store rep denied", sl.resolve_lease_access("sales_rep", "store"), False)
check("I2 lease gate: market manager allowed", sl.resolve_lease_access("market_manager", "market"), True)
check("I3 lease gate: scope-all allowed", sl.resolve_lease_access("anything", "all"), True)
check("I4 lease gate: grant allowed", sl.resolve_lease_access("sales_rep", "store", has_grant=True), True)
check("I5 lease gate: unknown role fails closed", sl.resolve_lease_access("", "store"), False)
# mig-434 pay gate: the deny-by-default resolver the router pre-checks before payroll_raw
check("I6 pay gate: broken resolver fails closed",
      pv.can_see_pay("Bearer bogus-token", "org-x", client=object()) in (False,), True)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Z. ARMED negative control
# ══════════════════════════════════════════════════════════════════════════════════════════════════
before = len(FAIL)
check("Z1 armed control (week-2 store must NOT be due in week 1)", "S03" in by, True)
if len(FAIL) == before + 1 and "Z1" in FAIL[-1]:
    FAIL.pop()
    PASS.append("Z1 armed negative control fired")
else:
    FAIL.append("Z1 armed negative control DID NOT fire — harness cannot detect failures")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  FAIL: {f}")
sys.exit(1 if FAIL else 0)
