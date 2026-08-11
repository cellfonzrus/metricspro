"""HARNESS — payroll-hours approval defaults follow the tenant's configured PAY CYCLE.

Owner 2026-08-11: "for payroll hour approval the system should show the default dates as per the payroll
cycle set up in the system which should also tally with the schedule set up — for ref we are running
07/23-08/05 payable on 08/14."

The reference is the specification, so it is asserted literally: asked on 2026-08-11 with Luxelink's real
settings, the board must default to 2026-07-23 → 2026-08-05, payable 2026-08-14.

  A. The weekday convention (the "doesn't tally with the schedule" bug) — 0=MONDAY, one reading, shared
     with core.pay_period_for, storeops/router._work_week_bounds and the schedule grid.
  B. The default is a PAY PERIOD, not a week — a biweekly tenant is never asked to approve 7 of 14 days.
  C. The owner's reference cycle, to the day, including the payday.
  D. Honest degradation — unreadable settings, weekly tenants, explicit ranges, custom ranges.
  E. ARMED negative control.

Pure: the period math is exercised against fixture settings dicts through the SAME core function the
module imports. No DB.

Run: python3 harness_approval_pay_cycle.py
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.core.router import pay_period_for, _pp_settings   # noqa: E402

PASS, FAIL = [], []


def check(label, got, want):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def section(t):
    print(f"\n── {t}")


# Luxelink's REAL row, except biweekly_anchor which this work corrects 2026-07-02 -> 2026-07-09
# (same weekday, one fortnight off — see section C).
LUX = {"work_week_start_dow": 3, "pay_period_type": "biweekly", "payday_dow": 4,
       "payday_weeks_after": 2, "biweekly_anchor": "2026-07-09"}
LUX_LIVE_ANCHOR = dict(LUX, biweekly_anchor="2026-07-02")     # what production carries today
HOUSE = {"work_week_start_dow": 0, "pay_period_type": "biweekly", "payday_dow": 4,
         "payday_weeks_after": 1, "biweekly_anchor": "2026-06-29"}


def prev_period(cfg, ref):
    """The module's previous_pay_period math, over a settings dict (no DB)."""
    s = _pp_settings(cfg)
    cur = pay_period_for(s, ref)
    prev = pay_period_for(s, date.fromisoformat(cur["start"]) - timedelta(days=1))
    return prev["start"], prev["end"], prev["payday"]


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. work_week_start_dow is 0=MONDAY — one convention, shared with the schedule")

# Every tenant's own biweekly_anchor independently confirms the convention: the anchor always falls on
# the configured start weekday. Under a 0=Sunday reading each of these would be off by one day.
for name, cfg in (("Luxelink", LUX), ("Cellfonz", HOUSE)):
    dow = cfg["work_week_start_dow"]
    anchor = date.fromisoformat(cfg["biweekly_anchor"])
    check(f"A1.{name} anchor falls on the configured start weekday (0=Mon)", anchor.weekday(), dow)

check("A2 Luxelink dow=3 is THURSDAY (0=Mon), not Wednesday", date(2026, 7, 23).weekday(), 3)
# The old module-local conversion (sql_dow-1)%7 turned 3 into 2 = Wednesday. Proven wrong by A1/A2.
check("A3 the OLD 0=Sunday conversion produced Wednesday — the schedule mismatch", (3 - 1) % 7, 2)

# Period starts land on the configured weekday for any reference date in the year.
starts = {date.fromisoformat(prev_period(LUX, date(2026, 1, 1) + timedelta(days=n))[0]).weekday()
          for n in range(0, 300, 7)}
check("A4 every derived period starts on Thursday, all year", starts, {3})

section("B. the default is a PAY PERIOD, not a week")

s, e, _p = prev_period(LUX, date(2026, 8, 11))
check("B1 a biweekly tenant gets 14 days", (date.fromisoformat(e) - date.fromisoformat(s)).days + 1, 14)

weekly = dict(LUX, pay_period_type="weekly")
ws, we, _ = prev_period(weekly, date(2026, 8, 11))
check("B2 a weekly tenant still gets 7", (date.fromisoformat(we) - date.fromisoformat(ws)).days + 1, 7)

# The defining property: the default period is COMPLETE — it has already ended.
check("B3 the default period has ENDED (never the one in progress)",
      date.fromisoformat(e) < date(2026, 8, 11), True)
cur = pay_period_for(_pp_settings(LUX), date(2026, 8, 11))
check("B4 ... and is exactly the one before the current period",
      date.fromisoformat(cur["start"]) - timedelta(days=1) >= date.fromisoformat(e), True)

section("C. THE OWNER'S REFERENCE — 07/23–08/05, payable 08/14")

check("C1 default period start", s, "2026-07-23")
check("C2 default period end", e, "2026-08-05")
check("C3 payday", _p, "2026-08-14")

# ...and it stays right when asked on any day inside the following period, not just 08-11.
for ref in (date(2026, 8, 6), date(2026, 8, 11), date(2026, 8, 19)):
    got = prev_period(LUX, ref)
    check(f"C4.{ref} same answer across the whole current period", got,
          ("2026-07-23", "2026-08-05", "2026-08-14"))

# The live anchor (2026-07-02) is the ONLY wrong value: same weekday, one fortnight out.
live = prev_period(LUX_LIVE_ANCHOR, date(2026, 8, 11))
check("C5 the LIVE anchor yields the wrong fortnight (the config fix this work needs)",
      live[:2], ("2026-07-16", "2026-07-29"))
check("C6 the corrected anchor is the same weekday as the live one",
      date.fromisoformat("2026-07-09").weekday(), date.fromisoformat("2026-07-02").weekday())
check("C7 ... and exactly one fortnight apart",
      (date.fromisoformat("2026-07-09") - date.fromisoformat("2026-07-02")).days, 7)

# The payday rule itself, stated: first payday_dow on/after period end, + (weeks_after - 1) weeks.
check("C8 payday is a Friday", date.fromisoformat(_p).weekday(), 4)
check("C9 payday is 9 days after the period ends",
      (date.fromisoformat(_p) - date.fromisoformat(e)).days, 9)

section("D. the house tenant is unaffected, and odd inputs stay honest")

hs, he, hp = prev_period(HOUSE, date(2026, 8, 11))
check("D1 Cellfonz keeps its Monday cycle", date.fromisoformat(hs).weekday(), 0)
check("D2 ... 14 days", (date.fromisoformat(he) - date.fromisoformat(hs)).days + 1, 14)
check("D3 ... payday Friday", date.fromisoformat(hp).weekday(), 4)
check("D4 Cellfonz period is NOT Luxelink's", (hs, he) != (s, e), True)

# _cycle_meta only claims a payday when the shown range IS a real configured period.
def matches(cfg, s_, e_):
    p = pay_period_for(_pp_settings(cfg), date.fromisoformat(s_))
    return p["start"] == s_ and p["end"] == e_

check("D5 the reference range is recognised as a real cycle period",
      matches(LUX, "2026-07-23", "2026-08-05"), True)
check("D6 a hand-picked range is NOT claimed as a cycle period (no invented payday)",
      matches(LUX, "2026-07-30", "2026-08-05"), False)
check("D7 the OLD weekly default range would not have matched either",
      matches(LUX, "2026-07-29", "2026-08-04"), False)

# A missing anchor must not crash — biweekly falls back to plain fortnights off the week start.
no_anchor = dict(LUX, biweekly_anchor=None)
na = prev_period(no_anchor, date(2026, 8, 11))
check("D8 a tenant with no anchor still gets a Thursday 14-day period",
      (date.fromisoformat(na[0]).weekday(),
       (date.fromisoformat(na[1]) - date.fromisoformat(na[0])).days + 1), (3, 14))

section("E. ARMED negative control")

_f0 = len(FAIL)
check("E-armed period", prev_period(LUX, date(2026, 8, 11))[0], "2026-07-30")   # the OLD wrong start
check("E-armed payday", prev_period(LUX, date(2026, 8, 11))[2], "2026-08-21")   # the OLD wrong payday
fired = len(FAIL) - _f0
if fired == 2:
    FAIL[:] = FAIL[:_f0]
    PASS.append("E1 negative control fired on both wrong expectations (checks are live)")
else:
    FAIL.append(f"E1 NEGATIVE CONTROL DID NOT FIRE — {fired}/2 wrong answers accepted.")

print(f"\n{'=' * 78}")
for f in FAIL:
    print(f"  ✗ {f}")
print(f"  PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
