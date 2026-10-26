#!/usr/bin/env python3
"""DB-FREE proof for `commcalc/inventory_stock_age.py` — the ONE home for "when did this device
arrive on the shelf, and how long has it sat there" (§COMMCALC-32).

Pins, in order:
  A. The receipt date: the feed's own date wins; otherwise `as_of_date − days_in_stock`; otherwise
     NOT MEASURED, never a silent zero.
  B. The regression the owner reported: a 10-day Luxelink window over a device received 16 days ago
     reads as 6 days OVER, and the same device under the old 12-day window read as 4 over — so the
     config change is what moves the number, and no literal in code does.
  C. The stale-snapshot defect `device_history` shipped: a device last seen in a July file does NOT
     report its July age in October.
  D. The ghost rule: `on_hand=true` on a superseded snapshot is NOT on the shelf.
  E. The window resolution ladder, including the two honest refusals.
  F. The summary never folds an unknown age or an unknown window into "fine".
"""
import sys, os
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from app.modules.commcalc import inventory_stock_age as isa
from app.modules.commcalc import device_aging_policy as ap

FAILED = []


def ok(cond, label):
    print(("PASS  " if cond else "FAIL  ") + label)
    if not cond:
        FAILED.append(label)


TODAY = date(2026, 10, 26)
LUX10 = ap.resolve_policy({"sell_within_days": 10, "label": "Total / MA"})
LUX12 = ap.resolve_policy({"sell_within_days": 12, "label": "Total / MA"})
BOOST = ap.resolve_policy({"sell_within_days": 60, "label": "Boost / VIP"})

# A real Luxelink row, copied from production 2026-10-26 (imei 89148000008588593198): the feed gives
# an age and no received date, which is the whole reason this module exists.
REAL = {"imei": "89148000008588593198", "item": "Total by Verizon SIM Kit", "store": "2414 W Cermak Rd",
        "unit_cost": 3.0, "received_date": None, "days_in_stock": 116, "as_of_date": "2026-10-26",
        "on_hand": True}

print("── A. the receipt date, one derivation ──")
ok(isa.received_on(REAL) == "2026-07-02",
   "as_of 2026-10-26 minus 116 days in stock -> received 2026-07-02")
ok(isa.received_basis(REAL) == isa.BASIS_AGE_AT_SNAPSHOT,
   "and it says so: basis age_at_snapshot, not a claimed receipt document")
ok(isa.received_on({**REAL, "received_date": "2026-08-01"}) == "2026-08-01"
   and isa.received_basis({**REAL, "received_date": "2026-08-01"}) == isa.BASIS_RECEIVED,
   "a feed that DOES date the receipt wins over the derivation")
ok(isa.received_on({"as_of_date": "2026-10-26", "days_in_stock": None}) is None
   and isa.received_on({"as_of_date": None, "days_in_stock": 5}) is None
   and isa.received_on({}) is None,
   "no age and no date -> None, never today and never epoch")
ok(isa.received_basis({}) == isa.BASIS_NOT_MEASURED,
   "and that is reported as not_measured")
ok(isa.received_on({"as_of_date": "2026-10-26", "days_in_stock": -3}) is None,
   "a negative age is refused rather than dating the receipt in the future")
ok(isa.days_on_shelf({}, TODAY) is None and isa.days_left({}, LUX10, TODAY) is None,
   "an unknown age yields an unknown age and an unknown deadline, not 0")

print("\n── B. the owner's regression: 10 days, from receipt ──")
D16 = {"days_in_stock": 16, "as_of_date": "2026-10-26", "on_hand": True, "unit_cost": 599.99,
       "item": "Samsung Galaxy S25 TO", "imei": "350135001237635"}
ok(isa.days_on_shelf(D16, TODAY) == 16, "received 16 days ago -> 16 days on the shelf")
ok(isa.days_left(D16, LUX10, TODAY) == -6, "under the declared 10-day window: 6 days OVER")
ok(isa.days_left(D16, LUX12, TODAY) == -4,
   "under the PREVIOUS declared 12 it was 4 over — the config row moves it, not code")
ok(isa.days_left(D16, BOOST, TODAY) == 44,
   "and Boost's own 60-day window is untouched by Luxelink's number")
D10 = {"days_in_stock": 10, "as_of_date": "2026-10-26"}
ok(isa.days_left(D10, LUX10, TODAY) == 0 and isa.shelf_row(D10, LUX10, TODAY)["overdue"] is False,
   "the last day inside the window is due TODAY, not yet overdue")
ok(isa.days_left({"days_in_stock": 11, "as_of_date": "2026-10-26"}, LUX10, TODAY) == -1,
   "the day after is 1 over — the edge is not off by one")
ok(isa.shelf_row(D16, LUX10, TODAY)["aging_bucket"] == ap.BUCKET_MISSED
   and isa.shelf_row(D10, LUX10, TODAY)["aging_bucket"] == ap.BUCKET_WARN
   and isa.shelf_row({"days_in_stock": 3, "as_of_date": "2026-10-26"}, LUX10, TODAY)["aging_bucket"]
       == ap.BUCKET_FRESH,
   "the ladder is device_aging_policy's, so the card cannot bucket a device differently")

print("\n── C. a stale snapshot does not report a stale age ──")
JULY = {"days_in_stock": 9, "as_of_date": "2026-07-25", "on_hand": True}
ok(isa.received_on(JULY) == "2026-07-16", "received 2026-07-16 per the July file")
ok(isa.days_on_shelf(JULY, TODAY) == 102,
   "aged to TODAY: 102 days, not the 9 the file recorded (the device_history defect)")
ok(isa.days_on_shelf(JULY, TODAY) != JULY["days_in_stock"],
   "explicitly: days_on_shelf is never days_in_stock read off the file")

print("\n── D. the ghost rule: a stale on_hand row is not a phone ──")
ROWS = [REAL, JULY, {"as_of_date": "2026-10-26", "on_hand": False, "days_in_stock": 2}]
SNAP = isa.current_snapshot(ROWS)
ok(SNAP == "2026-10-26", "the current snapshot is the newest as_of_date in the org's rows")
ok(isa.on_shelf(REAL, SNAP) is True, "on_hand at the current snapshot IS on the shelf")
ok(isa.on_shelf(JULY, SNAP) is False,
   "on_hand=true on a SUPERSEDED snapshot is NOT (balance_sheet's ~$129k of ghosts)")
ok(isa.on_shelf({**REAL, "on_hand": False}, SNAP) is False, "off_hand is not on the shelf")
ok(isa.on_shelf(REAL, None) is False,
   "with no snapshot established nothing is claimed to be on the shelf")
ok(isa.current_snapshot([]) is None and isa.current_snapshot([{"as_of_date": None}]) is None,
   "no dated rows -> no snapshot, rather than today")

print("\n── E. which window applies, and the two honest refusals ──")
LUXP = {"c-total": LUX10}
HOUSEP = {"c-boost": BOOST, "c-total": LUX12}
pol, why = isa.resolve_window("c-total", LUXP)
ok(pol is LUX10 and why is None, "a device whose carrier is known takes that carrier's window")
pol, why = isa.resolve_window(None, LUXP)
ok(pol is LUX10 and why is None,
   "carrier unknown but the org declares ONE window -> that window (not a guess: all agree)")
pol, why = isa.resolve_window(None, HOUSEP)
ok(pol is None and why == isa.NO_WINDOW_MIXED,
   "carrier unknown and the org's windows DIFFER -> no deadline, reason carrier_windows_differ")
pol, why = isa.resolve_window(None, {})
ok(pol is None and why == isa.NO_WINDOW_NO_CARRIER,
   "no map at all -> no deadline, reason carrier_not_identified")
pol, why = isa.resolve_window("c-unmapped", HOUSEP)
ok(pol is None and why == isa.NO_WINDOW_MIXED,
   "a carrier the org has no map for falls to the same ladder, never to someone else's window")
ok(isa.resolve_window(None, HOUSEP)[0] is None,
   "THE POINT: an unresolved device never inherits the house 60 on a rep's card")
r = isa.shelf_row(D16, None, TODAY, reason=isa.NO_WINDOW_MIXED)
ok(r["days_left"] is None and r["sell_within_days"] is None
   and r["no_window_reason"] == isa.NO_WINDOW_MIXED and r["days_on_shelf"] == 16,
   "with no window the AGE is still shown and the missing deadline names its reason")

print("\n── F. the summary folds nothing unknown into 'fine' ──")
CARD = [
    isa.shelf_row(D16, LUX10, TODAY),                       # 6 over, $599.99
    isa.shelf_row({"days_in_stock": 20, "as_of_date": "2026-10-26", "unit_cost": 100}, LUX10, TODAY),
    isa.shelf_row(D10, LUX10, TODAY),                       # due today
    isa.shelf_row({"days_in_stock": 2, "as_of_date": "2026-10-26"}, LUX10, TODAY),   # fine
    isa.shelf_row({"as_of_date": None, "days_in_stock": None}, LUX10, TODAY),        # undated
    isa.shelf_row(D16, None, TODAY, reason=isa.NO_WINDOW_MIXED),                     # no window
]
S = isa.summarise(CARD)
ok(S["devices"] == 6, "every device is counted")
ok(S["overdue"] == 2, "two overdue")
ok(S["due_today"] == 1, "one due today, counted apart from overdue")
ok(S["undated"] == 1 and S["no_window"] == 1,
   "an unknown AGE and an unknown WINDOW are counted separately — different gaps, different fixes")
ok(abs(S["value_overdue"] - 699.99) < 0.005, "the overdue value sums only the overdue devices")
ok(S["overdue"] + S["due_today"] + S["undated"] + S["no_window"] < S["devices"],
   "and the counts do not double-count: the remainder is the devices that are genuinely fine")
ok(isa.summarise([])["devices"] == 0 and isa.summarise(None)["overdue"] == 0,
   "an empty shelf summarises without raising")

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED"))
for f in FAILED:
    print("  FAILED:", f)
sys.exit(1 if FAILED else 0)
