"""Pure staffing heat-map math — no DB / no FastAPI, so it's unit-testable.

Buckets three things into a store-local weekday(0=Mon..6=Sun) × hour(0..23) grid:
  • demand    — transaction counts (from raw_sales/daily_sales_feed.trans_ts, mig 854)
  • scheduled — heads on the schedule (from storeops.shifts start/end times)
  • actual    — heads actually present (from storeops.timelog clock_in/out)
…and turns transaction demand into "staff required per hour" via a tunable transactions-per-labor-hour
capacity. The endpoint fetches rows, converts every timestamp to the store's local zone, and calls
build_grid(); all timezone work happens there so this stays pure.
"""
import math

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def hhmm_to_hours(start, end):
    """Integer hours [start..end) an 'HH:MM'..'HH:MM' shift covers. Handles overnight (end<=start → +1 day),
    matching attendance_exceptions' shift-window convention."""
    def _m(s):
        try:
            p = str(s).split(":")
            return int(p[0]) * 60 + int(p[1])
        except Exception:
            return None
    a, b = _m(start), _m(end)
    if a is None or b is None:
        return []
    if b <= a:
        b += 24 * 60
    out, h = [], a // 60
    while h * 60 < b:
        out.append(h % 24)
        h += 1
    return out


def required_staff(txn_per_day, capacity):
    """Heads needed to serve `txn_per_day` transactions within the hour at `capacity` txns per labor-hour.
    Ceil so a partial head rounds up; 0 when there's no demand or no capacity set."""
    try:
        cap = float(capacity)
    except (TypeError, ValueError):
        cap = 0
    if cap <= 0 or txn_per_day <= 0:
        return 0
    return int(math.ceil(txn_per_day / cap))


def build_grid(demand, scheduled, actual, occurrences, capacity):
    """demand/scheduled/actual: {(weekday, hour): total_over_period}. occurrences: {weekday: n_days of that
    weekday in the period} (so totals average to a typical day). capacity: txns per labor-hour.
    Returns the full 7×24 grid, each cell with per-typical-day averages, required staff, and the gap."""
    grid = []
    for wd in range(7):
        occ = max(1, int(occurrences.get(wd, 0) or 0))
        for hr in range(24):
            txn = float(demand.get((wd, hr), 0) or 0)
            sched = float(scheduled.get((wd, hr), 0) or 0)
            act = float(actual.get((wd, hr), 0) or 0)
            txn_per_day = txn / occ
            req = required_staff(txn_per_day, capacity)
            sched_avg = sched / occ
            grid.append({
                "weekday": wd, "weekday_label": WEEKDAYS[wd], "hour": hr,
                "txn": round(txn_per_day, 1), "txn_total": int(txn),
                "required": req,
                "scheduled": round(sched_avg, 1),
                "actual": round(act / occ, 1),
                "gap": round(sched_avg - req, 1),   # +overstaffed vs demand, −understaffed
            })
    return grid


if __name__ == "__main__":
    assert hhmm_to_hours("09:00", "12:00") == [9, 10, 11]
    assert hhmm_to_hours("22:00", "02:00") == [22, 23, 0, 1]     # overnight
    assert hhmm_to_hours("10:30", "13:15") == [10, 11, 12, 13]   # partial hours count
    assert hhmm_to_hours("bad", "x") == []
    assert required_staff(20, 10) == 2 and required_staff(21, 10) == 3
    assert required_staff(0, 10) == 0 and required_staff(5, 0) == 0
    g = build_grid(demand={(0, 12): 40, (0, 13): 10}, scheduled={(0, 12): 4},
                   actual={(0, 12): 2}, occurrences={0: 4}, capacity=10)
    noon = next(c for c in g if c["weekday"] == 0 and c["hour"] == 12)
    assert noon["txn"] == 10.0 and noon["required"] == 1 and noon["scheduled"] == 1.0 and noon["gap"] == 0.0, noon
    one = next(c for c in g if c["weekday"] == 0 and c["hour"] == 13)
    assert one["txn"] == 2.5 and one["required"] == 1, one
    assert len(g) == 7 * 24
    print("staffing_heatmap self-test OK")
