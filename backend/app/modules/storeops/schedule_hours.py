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


def employee_labels(shifts, attributor):
    """PURE: `{canonical key: display label}` for every employee the given rows touch.

    The label is the ROSTER spelling (the live one) — never the row's stale `employee_name` copy, see
    §STOREOPS-01. Two different people whose roster names are identical are disambiguated by their
    employee id rather than silently merged into one display row, and the unattributed bucket gets
    its own standing label.

    This function exists because the by_employee/by_emp_store maps this module emits are keyed by a
    DISPLAY string (the report's own payload shape). The GROUPING is still the canonical key: the
    label is resolved once, over all the rows, so a bucket can never be labelled one way in one week
    and another way in the next.
    """
    from app.modules.storeops import payroll_identity as _pi
    seen = {}
    for sh in _live(shifts):
        a = attributor(sh.get("employee_id"), sh.get("employee_name"))
        if a["key"] == _pi.UNATTRIBUTED_KEY:
            seen.setdefault(a["key"], _pi.UNATTRIBUTED_LABEL)
            continue
        seen.setdefault(a["key"], (str(a["name"] or "").strip() or a["key"]))
    # Disambiguate colliding labels with the employee id, deterministically.
    counts = {}
    for lbl in seen.values():
        counts[lbl] = counts.get(lbl, 0) + 1
    return {k: (f"{v} ({k})" if counts.get(v, 0) > 1 else v) for k, v in seen.items()}


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


def _accumulate(shifts, keyfn, attributor, labels):
    """{bucket_key: {'total', 'by_store', 'by_employee', 'by_emp_store'}} over shifts.

    The employee axis groups on the CANONICAL employee key from `attributor` (§STOREOPS-01) and is
    emitted under the stable display label from `employee_labels` — never on the row's own
    `employee_name` copy, which a rename leaves stale. A row that names nobody still counts, under
    the unattributed label, so this axis always sums to the store axis.
    """
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
        a = attributor(s.get("employee_id"), s.get("employee_name"))
        emp = labels.get(a["key"]) or (str(a["name"] or "").strip() or "—")
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


def hours_trend(shifts, *, anchor, employees, weeks=8, months=6, week_start_dow=0):
    """The whole payload for features 2 & 3.

    `anchor` is the 'today' the report ends on (a date or 'YYYY-MM-DD'). `employees` is the org's
    roster and is REQUIRED, not defaulted: the employee axis groups on a canonical employee key
    dereferenced from it (§STOREOPS-01), and a caller who has not got the roster must say so (pass
    `[]`, which puts every row in the visible unattributed bucket) rather than silently fall back to
    the stale `employee_name` copy this whole mechanism exists to stop depending on. Returns:
      { total, weeks:[bucket...], months:[bucket...],
        increasing: { week:[...], month:[...] } }
    Each bucket: key/label/total/prev_total/delta/by_store/by_employee/store_deltas/employee_deltas.
    Buckets are chronological (oldest first); the LAST is the current (partial) period."""
    if isinstance(anchor, str):
        anchor = _d(anchor)
    live = _live(shifts)
    # ONE employee identity for the whole report (§STOREOPS-01): resolved once, from the roster, and
    # dereferenced by every bucket. `employees=None` means nobody is on the roster, which puts every
    # row in the unattributed bucket — visibly, and still adding up.
    from app.modules.storeops import payroll_identity as _pi
    attribute = _pi.build_shift_attributor(employees)
    labels = employee_labels(live, attribute)

    # WEEK axis — `weeks` consecutive weeks ending on the anchor's week.
    cur_ws = week_start_of(anchor, week_start_dow)
    week_keys = [(cur_ws - timedelta(days=7 * (weeks - 1 - i))).isoformat() for i in range(weeks)]
    week_acc = _accumulate(live, lambda d: week_start_of(d, week_start_dow).isoformat(), attribute, labels)

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
    month_acc = _accumulate(live, _month_key, attribute, labels)
    months_series = _series(month_acc, month_keys, _month_label)

    # "Who's increasing" — current bucket vs the immediately prior one, for both axes.
    def _empty():
        return {"by_emp_store": {}}
    wk_inc = increasing_ranking(week_acc.get(week_keys[-1], _empty()),
                                week_acc.get(week_keys[-2], _empty()) if weeks >= 2 else _empty())
    mo_inc = increasing_ranking(month_acc.get(month_keys[-1], _empty()),
                                month_acc.get(month_keys[-2], _empty()) if months >= 2 else _empty())

    # The axes payload for the SAME rows, so the report and the grid can never disagree: one fact,
    # one home, both totals out of one row walk (§STOREOPS-01).
    axes = scheduled_axes(live, employees)
    return {
        "total": total_hours(live),
        "weeks": weeks_series,
        "months": months_series,
        "increasing": {"week": wk_inc, "month": mo_inc},
        "window": {"week_start": week_keys[0], "anchor": anchor.isoformat(),
                   "weeks": weeks, "months": months, "week_start_dow": week_start_dow},
        "axes": axes,
        "identity": {"unattributed_hours": axes["unattributed"]["hours"],
                     "unattributed_shifts": axes["unattributed"]["shifts"],
                     "name_divergence": axes["name_divergence"],
                     "invariant": axes["invariant"]},
    }


# ════════════════════════════════════════════════════════════════════════════════════════════════
# THE TWO AXES ARE ONE FACT (owner report 2026-10-12, index §STOREOPS-01)
# ════════════════════════════════════════════════════════════════════════════════════════════════
# THE DEFECT, named as a class. "Scheduled hours by store" and "scheduled hours by employee" were
# TWO derivations over the same rows. The store axis kept every row (it groups on `store_code`, a
# column); the employee axis was an INNER JOIN of `shifts.employee_name` against the roster's `name`
# — string equality on a DENORMALIZED COPY that `PATCH /storeops/employees/{id}` does not maintain.
# Rename an employee and every shift they already have is orphaned: still counted by store, silently
# dropped by employee. The two totals then differ by exactly the orphaned hours, with nothing saying
# so, and it will happen again at the next rename for the next tenant.
#
# THE HOME. `scheduled_axes` builds BOTH axes once, from one row walk, over one canonical employee
# key dereferenced from `payroll_identity.build_shift_attributor` (which in turn is the one home for
# "which employee id is which" and "which spelling is which employee"). Its contract is an
# INVARIANT, not a convention:
#
#     sum(by_store) == sum(by_employee, incl. the unattributed bucket) == total
#
# A row that cannot be attributed to a roster employee is NOT dropped — it rides the explicit
# `__unattributed__` bucket with its reason, so the employee axis always adds up to the store axis
# and "we do not know whose hours these are" is a visible answer instead of a missing one.
# `harness_schedule_axes.py` proves the invariant on every fixture (including the live 208-vs-164
# regression) and `harness_schedule_identity_lock.py` fails the build if a caller stops dereferencing
# this function or a second employee-axis derivation appears.


def employee_axis_key(sh, attributor):
    """PURE: the canonical employee bucket key for one shift row. The ONE answer; never a name."""
    return attributor(sh.get("employee_id"), sh.get("employee_name"))


def scheduled_axes(shifts, employees=None):
    """BOTH axes of scheduled hours over already-filtered shift rows, from one row walk.

    `shifts`    the rows the caller has already scoped (org, period, RBAC keyset, market/store
                filter). Soft-deleted rows are skipped defensively.
    `employees` the org's roster rows (`id`, `employee_id`, `name`). Omitted/empty means NOBODY is
                on the roster, so every row lands in the unattributed bucket — which is correct and
                still adds up, rather than a silent zero on one axis.

    Returns:
      { total, by_store: {code: hours},
        by_employee: [ {key, employee_id, name, hours, shifts, stored_names:[...], basis, binds,
                        reason, by_store:{code: hours}} ],  # unattributed bucket LAST
        unattributed: {hours, shifts, rows:[...]},
        name_divergence: [ {employee_id, roster_name, stored_name, rows, hours} ],
        invariant: {store_total, employee_total, agrees} }

    `name` is always the ROSTER spelling. `stored_names` lists every spelling the rows themselves
    carry, so a divergence is shown rather than hidden by the fix.
    """
    from app.modules.storeops import payroll_identity as _pi
    attribute = _pi.build_shift_attributor(employees)
    live = _live(shifts)

    by_store = {}
    buckets = {}
    order = []
    unattributed = {"hours": 0.0, "shifts": 0, "rows": []}
    div_rows = []

    for s in live:
        h = _shift_h(s)
        store = (str(s.get("store_code") or "").strip() or "—")
        by_store[store] = round(by_store.get(store, 0.0) + h, 4)
        a = attribute(s.get("employee_id"), s.get("employee_name"))
        div_rows.append({"employee_id": s.get("employee_id"), "employee_name": s.get("employee_name"),
                         "hours": h})
        k = a["key"]
        if k not in buckets:
            buckets[k] = {"key": k, "employee_id": a["employee_id"], "name": a["name"],
                          "hours": 0.0, "shifts": 0, "stored_names": [], "basis": a["basis"],
                          "binds": a["binds"], "reason": a["reason"], "by_store": {}}
            order.append(k)
        b = buckets[k]
        b["hours"] = round(b["hours"] + h, 4)
        b["shifts"] += 1
        b["by_store"][store] = round(b["by_store"].get(store, 0.0) + h, 4)
        sn = a["shift_name"]
        if sn and sn not in b["stored_names"]:
            b["stored_names"].append(sn)
        if not a["binds"]:
            unattributed["hours"] = round(unattributed["hours"] + h, 4)
            unattributed["shifts"] += 1
            unattributed["rows"].append({"shift_id": s.get("id"), "store_code": store,
                                         "shift_date": s.get("shift_date"),
                                         "employee_id": s.get("employee_id"),
                                         "employee_name": s.get("employee_name"),
                                         "hours": round(h, 2), "basis": a["basis"],
                                         "reason": a["reason"]})

    # The unattributed bucket is always LAST, so a UI that renders the list in order shows the named
    # people first and the open question at the bottom where it reads as a question.
    named = [buckets[k] for k in order if k != _pi.UNATTRIBUTED_KEY]
    named.sort(key=lambda b: (-b["hours"], (b["name"] or "")))
    rest = [buckets[k] for k in order if k == _pi.UNATTRIBUTED_KEY]
    rows = named + rest
    for b in rows:
        b["hours"] = round(b["hours"], 2)
        b["by_store"] = _round_map(b["by_store"])

    store_total = round(sum(by_store.values()), 2)
    emp_total = round(sum(b["hours"] for b in rows), 2)
    return {
        "total": store_total,
        "by_store": _round_map(by_store),
        "by_employee": rows,
        "unattributed": {"hours": round(unattributed["hours"], 2), "shifts": unattributed["shifts"],
                         "rows": unattributed["rows"]},
        "name_divergence": _pi.name_divergences(div_rows, attribute),
        "invariant": {"store_total": store_total, "employee_total": emp_total,
                      "agrees": abs(store_total - emp_total) < 0.01},
    }
