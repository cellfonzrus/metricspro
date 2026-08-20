"""HARNESS — scheduled-hours aggregation for the schedule page (schedule_hours.py).

Owner request 2026-08-20: total scheduled hours that respects the active filter; week-over-week &
month-over-month trend with per-store/per-employee drill-down + deltas; and a "who's increasing"
ranking. This proves the PURE math (schedule_hours) that all three features read from:

  A. shift_hours — normal, overnight-wrap (end < start), zero-length, bad input.
  B. total_hours — respects the (already-filtered) row set; overnight shifts counted right.
  C. week bucketing — WoW buckets land on the right week; deltas vs prior week correct.
  D. month bucketing — MoM buckets + deltas correct.
  E. drill-down — per-store / per-employee deltas name who drove the change.
  F. increasing ranking — ranks (employee, store) whose hours rose, largest first; store named.

Run: python3 harness_schedule_hours.py     (pure — functions are fed rows directly, no DB)
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.storeops import schedule_hours as sh

PASS, FAIL = [], []


def check(label, got, want):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def section(t):
    print(f"\n── {t}")


def S(store, emp, d, start, end, **extra):
    r = {"store_code": store, "employee_name": emp, "employee_id": emp[:1].upper(),
         "shift_date": d, "start_time": start, "end_time": end}
    r.update(extra)
    return r


# ── A. shift_hours ─────────────────────────────────────────────────────────────────────────────
section("A. shift_hours (overnight wrap, zero-length, bad input)")
check("A1 normal 10:00-18:00 = 8h", sh.shift_hours("10:00", "18:00"), 8.0)
check("A2 overnight 18:00-00:30 = 6.5h", sh.shift_hours("18:00", "00:30"), 6.5)
check("A3 overnight 22:00-06:00 = 8h", sh.shift_hours("22:00", "06:00"), 8.0)
check("A4 zero-length 10:00-10:00 = 0 (not 24h)", sh.shift_hours("10:00", "10:00"), 0.0)
check("A5 half hour 09:15-09:45 = 0.5h", sh.shift_hours("09:15", "09:45"), 0.5)
check("A6 bad input -> 0", sh.shift_hours("", None), 0.0)
check("A7 near-midnight 23:30-00:15 = 0.75h", sh.shift_hours("23:30", "00:15"), 0.75)

# ── B. total_hours respects the row set; overnight counted right ─────────────────────────────────
section("B. total_hours")
rows_all = [
    S("S1", "Ann", "2026-08-17", "10:00", "18:00"),   # 8
    S("S1", "Ann", "2026-08-18", "22:00", "06:00"),   # 8 overnight
    S("S2", "Bob", "2026-08-17", "09:00", "17:00"),   # 8
    S("S2", "Bob", "2026-08-19", "10:00", "10:00"),   # 0 zero-length
]
check("B1 grand total = 24h", sh.total_hours(rows_all), 24.0)
# a soft-deleted row must not count even if handed in raw
rows_del = rows_all + [S("S1", "Ann", "2026-08-17", "08:00", "20:00", is_deleted=True)]
check("B2 deleted row excluded", sh.total_hours(rows_del), 24.0)
# "filter respected" — feeding only S1 rows (what the caller's market/store filter would leave)
check("B3 filter to S1 only = 16h", sh.total_hours([r for r in rows_all if r["store_code"] == "S1"]), 16.0)
# stored scheduled_hours fallback when no times present
check("B4 fallback to scheduled_hours",
      sh.total_hours([{"store_code": "S1", "employee_name": "X", "shift_date": "2026-08-17",
                       "scheduled_hours": 5}]), 5.0)

# ── Build a multi-week / multi-month dataset ─────────────────────────────────────────────────────
# Weeks are Monday-anchored. 2026-08-17 is a Monday. Prior week Mon = 2026-08-10.
#   Week of 08-10: Ann@S1 8h (Mon)                         -> week total 8
#   Week of 08-17: Ann@S1 8h + Ann@S1 8h(overnight) + Bob@S2 8h  -> week total 24
trend_rows = [
    S("S1", "Ann", "2026-08-10", "10:00", "18:00"),   # prior week, 8h
    S("S1", "Ann", "2026-08-17", "10:00", "18:00"),   # cur week, 8h
    S("S1", "Ann", "2026-08-18", "22:00", "06:00"),   # cur week, 8h overnight
    S("S2", "Bob", "2026-08-17", "09:00", "17:00"),   # cur week, 8h
]
tr = sh.hours_trend(trend_rows, anchor="2026-08-19", weeks=4, months=3, week_start_dow=0)

section("C. week-over-week buckets + deltas")
wk = {b["key"]: b for b in tr["weeks"]}
check("C1 four week buckets", len(tr["weeks"]), 4)
check("C2 current week (08-17) total = 24", wk["2026-08-17"]["total"], 24.0)
check("C3 prior week (08-10) total = 8", wk["2026-08-10"]["total"], 8.0)
check("C4 WoW delta on current week = +16", wk["2026-08-17"]["delta"], 16.0)
check("C5 current week by_store", wk["2026-08-17"]["by_store"], {"S1": 16.0, "S2": 8.0})
check("C6 current week by_employee", wk["2026-08-17"]["by_employee"], {"Ann": 16.0, "Bob": 8.0})
check("C7 empty older week total = 0", wk["2026-08-03"]["total"], 0.0)

section("D. drill-down deltas (which store / employee drove the change)")
check("D1 store_deltas current week", wk["2026-08-17"]["store_deltas"], {"S1": 8.0, "S2": 8.0})
check("D2 employee_deltas current week", wk["2026-08-17"]["employee_deltas"], {"Ann": 8.0, "Bob": 8.0})

section("E. month-over-month")
# All rows fall in 2026-08, so August total = 8+8+8+8 = 32; prior months empty.
mo = {b["key"]: b for b in tr["months"]}
check("E1 three month buckets", len(tr["months"]), 3)
check("E2 August total = 32", mo["2026-08"]["total"], 32.0)
check("E3 July total = 0", mo["2026-07"]["total"], 0.0)
check("E4 August by_store", mo["2026-08"]["by_store"], {"S1": 24.0, "S2": 8.0})

section("F. who's-increasing ranking")
# Ann@S1 rose 8 -> 16 (+8); Bob@S2 is new this week (0 -> 8, +8). Ranked largest first, tie broken
# by employee name (Ann before Bob).
inc = tr["increasing"]["week"]
check("F1 two increasers", len(inc), 2)
check("F2 top increaser Ann@S1 +8", (inc[0]["employee"], inc[0]["store"], inc[0]["delta"]), ("Ann", "S1", 8.0))
check("F3 Bob@S2 new +8", (inc[1]["employee"], inc[1]["store"], inc[1]["delta"]), ("Bob", "S2", 8.0))
check("F4 current/prior recorded", (inc[0]["current"], inc[0]["prior"]), (16.0, 8.0))

# Negative control — someone who DROPPED hours must not appear as increasing.
drop_rows = [
    S("S3", "Cy", "2026-08-10", "08:00", "20:00"),   # prior week 12h
    S("S3", "Cy", "2026-08-17", "10:00", "14:00"),   # cur week 4h  (dropped 8)
]
tr2 = sh.hours_trend(drop_rows, anchor="2026-08-19", weeks=3, months=3)
check("F5 decreaser absent from increasing", tr2["increasing"]["week"], [])

# min_delta guard — a +0.25h wiggle is below the 0.5 floor, so it is not surfaced as "increasing".
wiggle = [
    S("S4", "Di", "2026-08-10", "10:00", "18:00"),        # 8h prior
    S("S4", "Di", "2026-08-17", "10:00", "18:15"),        # 8.25h cur (+0.25)
]
tr3 = sh.hours_trend(wiggle, anchor="2026-08-19", weeks=3, months=3)
check("F6 sub-0.5h wiggle not flagged", tr3["increasing"]["week"], [])

# ── week_start_dow (tenant work-week alignment, e.g. Luxelink Thursday=3) ────────────────────────
section("G. tenant work-week start alignment")
# With week_start_dow=3 (Thursday), 2026-08-17 (Mon) belongs to the week starting Thu 2026-08-13.
check("G1 Thursday-anchored week start",
      sh.week_start_of(date(2026, 8, 17), 3).isoformat(), "2026-08-13")

# ── report ───────────────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
for f in FAIL:
    print(f"FAIL: {f}")
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
