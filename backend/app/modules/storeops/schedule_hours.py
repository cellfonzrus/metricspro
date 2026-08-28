"""Scheduled-hours aggregation for the StoreOps schedule page (owner request 2026-08-20).

PURE, I/O-FREE math — every function here is fed plain shift dicts and returns plain dicts, so the
whole thing is provable offline (harness_schedule_hours.py). The router does the I/O (fetch shifts,
apply the RBAC store keyset / market / store / employee filters) and hands the surviving rows here.

Three product features are served off this one aggregation:
  1. Total scheduled hours (footer) — `total_hours()`; the page also sums client-side for the live
     week, this is the server-side authority used by the trend/analysis reads.
  2. Week-over-week & month-over-month report with drill-down — `hours_trend()` returns hours
     bucketed by week and by calendar month, each bucket carrying its per-store and per-employee
     breakdown AND the period-over-period delta per store / per employee (the drill-down: WHY the
     total moved, and which stores/employees drove it).
  3. "Who's increasing" analysis — `increasing_ranking()` ranks (employee, store) pairs whose hours
     rose this period vs the prior one, factual and coaching-toned, largest increase first.

HOURS MATH — the one rule that matters: scheduled hours per shift = end - start, and an OVERNIGHT
shift whose end wraps past midnight (end < start, e.g. 18:00 -> 00:30) adds a day. This mirrors the
`_scheduled_end_for_punch` / `_biz_dt_utc` overnight guard in router.py. A zero-length row
(start == end) counts as 0, never a spurious 24h. Deleted shifts (is_deleted) must be excluded by
the caller OR are skipped here defensively.
"""
from datetime import date, timedelta


def shift_hours(start_time, end_time) -> float:
    """Scheduled hours for one shift from 'HH:MM' start/end, wrapping past midnight.

    end < start  → overnight, add 24h (18:00->00:30 = 6.5h; 22:00->06:00 = 8h).
    end == start → 0.0 (a zero-length / blank row, NOT a 24h shift).
    Bad/missing values → 0.0 (never raises)."""
    def _mins(t):
        parts = str(t).strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    try:
        s = _mins(start_time)
        e = _mins(end_time)
    except Exception:
        return 0.0
    if e == s:
        return 0.0
    d = e - s
    if d < 0:
        d += 24 * 60
    return round(d / 60.0, 4)


def _shift_h(sh) -> float:
    """Hours for a shift dict — prefers recomputing from start/end (overnight-safe); falls back to a
    stored `scheduled_hours` only when times are absent."""
    st, en = sh.get("start_time"), sh.get("end_time")
    if st and en:
        return shift_hours(st, en)
    try:
        return round(float(sh.get("scheduled_hours") or 0), 4)
    except Exception:
        return 0.0


def _live(shifts):
    """Non-deleted shifts only (defensive — the caller already filters, but never double-count a
    soft-deleted row if a raw list is handed in)."""
    return [s for s in (shifts or []) if not s.get("is_deleted")]


def total_hours(shifts) -> float:
    """Grand total scheduled hours over the given (already-filtered) shifts."""
    return round(sum(_shift_h(s) for s in _live(shifts)), 2)


def _emp_name(sh) -> str:
    return (sh.get("employee_name") or "").strip() or (str(sh.get("employee_id") or "").strip()) or "—"


# ── date bucketing ────────────────────────────────────────────────────────────────────────────────
def _d(x) -> date:
    return date.fromisoformat(str(x)[:10])


def week_start_of(d: date, week_start_dow: int = 0) -> date:
    """Monday-anchored by default (week_start_dow 0=Mon..6=Sun, matching Python date.weekday())."""
    delta = (d.weekday() - (week_start_dow or 0)) % 7
    return d - timedelta(days=delta)


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _month_label(key: str) -> str:
    y, m = key.split("-")
    names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{names[int(m)]} {y}"


def _accumulate(shifts, keyfn):
    """{bucket_key: {'total', 'by_store', 'by_employee', 'by_emp_store'}} over shifts."""
    out = {}
    for s in _live(shifts):
        sd = s.get("shift_date")
        if not sd:
            continue
        try:
            k = keyfn(_d(sd))
        except Exception:
            continue
        h = _shift_h(s)
        if h <= 0:
            # keep the bucket present (so an empty week still shows) but nothing to add
            b = out.setdefault(k, {"total": 0.0, "by_store": {}, "by_employee": {}, "by_emp_store": {}})
            continue
        store = (s.get("store_code") or "—").strip() or "—"
        emp = _emp_name(s)
        b = out.setdefault(k, {"total": 0.0, "by_store": {}, "by_employee": {}, "by_emp_store": {}})
        b["total"] += h
        b["by_store"][store] = b["by_store"].get(store, 0.0) + h
        b["by_employee"][emp] = b["by_employee"].get(emp, 0.0) + h
        b["by_emp_store"][(emp, store)] = b["by_emp_store"].get((emp, store), 0.0) + h
    return out


def _round_map(m):
    return {k: round(v, 2) for k, v in m.items()}


def _delta_map(cur, prev):
    """Per-key delta cur-prev over the union of keys, dropping ~0 deltas."""
    out = {}
    for k in set(cur) | set(prev):
        dv = round(cur.get(k, 0.0) - prev.get(k, 0.0), 2)
        if abs(dv) >= 0.01:
            out[k] = dv
    return out


def _series(acc, ordered_keys, label_fn):
    """Turn an accumulator into an ordered list of bucket dicts with prev-period deltas + drill-down
    per-store / per-employee deltas."""
    buckets = []
    prev = None
    for k in ordered_keys:
        b = acc.get(k, {"total": 0.0, "by_store": {}, "by_employee": {}, "by_emp_store": {}})
        by_store = _round_map(b["by_store"])
        by_emp = _round_map(b["by_employee"])
        prev_store = _round_map(prev["by_store"]) if prev else {}
        prev_emp = _round_map(prev["by_employee"]) if prev else {}
        prev_total = round(prev["total"], 2) if prev else None
        total = round(b["total"], 2)
        buckets.append({
            "key": k,
            "label": label_fn(k),
            "total": total,
            "prev_total": prev_total,
            "delta": (round(total - prev_total, 2) if prev_total is not None else None),
            "by_store": by_store,
            "by_employee": by_emp,
            # drill-down: which stores / employees drove the move vs the prior bucket
            "store_deltas": _delta_map(by_store, prev_store),
            "employee_deltas": _delta_map(by_emp, prev_emp),
        })
        prev = b
    return buckets


def increasing_ranking(cur_acc_bucket, prev_acc_bucket, *, limit=10, min_delta=0.5):
    """Rank (employee, store) pairs whose scheduled hours ROSE from prev to cur. Factual, coaching
    tone: names the store and the size of the increase, largest first. Fed the raw accumulator
    buckets (with 'by_emp_store'), so it stays pure."""
    cur = (cur_acc_bucket or {}).get("by_emp_store", {})
    prev = (prev_acc_bucket or {}).get("by_emp_store", {})
    rows = []
    for (emp, store) in set(cur) | set(prev):
        c = round(cur.get((emp, store), 0.0), 2)
        p = round(prev.get((emp, store), 0.0), 2)
        dv = round(c - p, 2)
        if dv >= min_delta:
            rows.append({"employee": emp, "store": store, "current": c, "prior": p, "delta": dv})
    rows.sort(key=lambda r: (-r["delta"], r["employee"], r["store"]))
    return rows[:limit]


def hours_trend(shifts, *, anchor, weeks=8, months=6, week_start_dow=0):
    """The whole payload for features 2 & 3.

    `anchor` is the 'today' the report ends on (a date or 'YYYY-MM-DD'). Returns:
      { total, weeks:[bucket...], months:[bucket...],
        increasing: { week:[...], month:[...] } }
    Each bucket: key/label/total/prev_total/delta/by_store/by_employee/store_deltas/employee_deltas.
    Buckets are chronological (oldest first); the LAST is the current (partial) period."""
    if isinstance(anchor, str):
        anchor = _d(anchor)
    live = _live(shifts)

    # WEEK axis — `weeks` consecutive weeks ending on the anchor's week.
    cur_ws = week_start_of(anchor, week_start_dow)
    week_keys = [(cur_ws - timedelta(days=7 * (weeks - 1 - i))).isoformat() for i in range(weeks)]
    week_acc = _accumulate(live, lambda d: week_start_of(d, week_start_dow).isoformat())

    def _week_label(iso):
        ws = _d(iso)
        we = ws + timedelta(days=6)
        return f"{ws.month}/{ws.day}–{we.month}/{we.day}"
    weeks_series = _series(week_acc, week_keys, _week_label)

    # MONTH axis — `months` consecutive calendar months ending on the anchor's month.
    month_keys = []
    y, m = anchor.year, anchor.month
    for _ in range(months):
        month_keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    month_keys.reverse()
    month_acc = _accumulate(live, _month_key)
    months_series = _series(month_acc, month_keys, _month_label)

    # "Who's increasing" — current bucket vs the immediately prior one, for both axes.
    def _empty():
        return {"by_emp_store": {}}
    wk_inc = increasing_ranking(week_acc.get(week_keys[-1], _empty()),
                                week_acc.get(week_keys[-2], _empty()) if weeks >= 2 else _empty())
    mo_inc = increasing_ranking(month_acc.get(month_keys[-1], _empty()),
                                month_acc.get(month_keys[-2], _empty()) if months >= 2 else _empty())

    return {
        "total": total_hours(live),
        "weeks": weeks_series,
        "months": months_series,
        "increasing": {"week": wk_inc, "month": mo_inc},
        "window": {"week_start": week_keys[0], "anchor": anchor.isoformat(),
                   "weeks": weeks, "months": months, "week_start_dow": week_start_dow},
    }
