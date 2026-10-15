"""PER-STORE month-of-life commission trend — the store axis on the existing leg trend.

OWNER ASK, verbatim (2026-10-15, Store Takeover thread, on a takeover prospect's M1..M12 rungs shown
side by side for two months): *"this is a good insight , this means we need to see these numbers as a
report sisde by sie as shown above for each store to analyze , create one"*.

WHAT HE IS ASKING OF IT. The prospect table showed that 96% of a 41% fall in carrier commission was
M2-M5 TRAILING legs paying on an activation cohort that had collapsed. The same shape, per store,
answers: *which of my stores is running down, and in which rung* — because a store whose M1 holds but
whose M4-M6 are falling is a store whose cohort shrank four to six months ago, and a store whose M1 is
falling is one shrinking right now. Those are different problems with different fixes, and the rung is
the only place the difference is visible.

WHAT THIS MODULE IS, AND IS NOT. It is PURE. It computes NO money. It PIVOTS the already-computed
per-period / per-store leg decomposition that `router._commission_leg_trend_compute` produces — the
same single read, the same classifier, the same `commission_legs` ladder convention — into store-major
rows with the months side by side, and derives the plain-English "running down" read-out from them.
This is the §4c precedent (a multi-month P&L LOOPS the single-month read; it computes no line) applied
to the leg trend: one money path, two shapes.

DUPLICATE CHECK (house build gate), stated here because this file is the thing that was nearly a
sibling derivation:
  • Per-store rungs already existed for ONE month at a time — `gp_report.calc_gp_report` puts
    `comm_ladder {rung: $}` on every store row. NOT re-derived; this is the trend's money, not that
    read.
  • The ladder already existed ACROSS months with NO store axis — `GET /commcalc/commission-leg-trend`.
    EXTENDED: that endpoint's compute now accumulates a `by_store` grain in the SAME pass over the SAME
    rows, and this module only reshapes it. There is no second query, no second classifier and no
    second rung convention.
  • The rung convention is dereferenced from `commission_legs` (`ladder_key` / `ladder_sort_key` /
    `months_present` / `ladder_column` / `ladder_label`). NOTHING here writes `range(1, 13)`, a month
    COUNT, or a rung name. A rung the feed carries is a column the day it arrives; a rung it does not
    carry is never invented.

HONESTY RULES THIS FILE ENFORCES (they are the report, not decoration):
  1. A PARTIAL month is marked. The window ends on the page's own period, which is normally the
     CURRENT calendar month, and a half-collected month looks exactly like a store falling off a
     cliff. `partial_months` names it from the caller's clock so the owner never reads 15 days as a
     collapse, and every derived delta SKIPS a partial month rather than quietly dividing by it.
  2. Money with NO store is shown as itself. A carrier row whose address resolves to no store keeps
     the honest '' key and is reported as the named `unattributed` row — never spread across stores,
     never dropped. Its presence is a mapping finding.
  3. A rung that is NEGATIVE is not hidden. Carrier withholding/clawback lands in whichever rung its
     label states (in the house org's own Sep-2026 data, "Commission Withholding" is -$1,335.76 of
     Unlabelled). The read-out reports a decline by magnitude and never flips a sign to make a store
     look better.
  4. Nothing is asserted about a store the window has no data for. `first`/`last` are the first and
     last COMPLETE months in which the store appears at all; a store present in one month only gets
     `delta = None`, not 0.
"""
from app.modules.commcalc import commission_legs as _legs

# The store key for carrier money that matched NO store. '' is `_leg_store_index`'s own miss value and
# `ma_store_pnl.ma_earned_month_ladder`'s, so this file does not invent a third spelling for "nobody".
UNATTRIBUTED_KEY = ""
UNATTRIBUTED_LABEL = "(No store matched)"


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def partial_months(labels, now_year, now_month):
    """PURE: the labels in `labels` that are NOT a finished month, given the caller's clock.

    The current calendar month and anything after it. A window anchored on the page's own period
    normally ENDS on the current month, so without this the newest column is a part-month compared
    against full ones — which reads as a collapse at every store at once. `router` passes its own
    `_datetime.now(_timezone.utc)`; no clock is read here, so the rule is testable."""
    out = []
    cut = int(now_year) * 12 + (int(now_month) - 1)
    for lab in labels or []:
        ym = _legs._period_ym(lab)
        if not ym:
            continue
        if ym[0] * 12 + (ym[1] - 1) >= cut:
            out.append(lab)
    return out


def _blank_cell(label):
    return {"period": label, "m1": 0.0, "m2_12": 0.0, "unsplit": 0.0, "total": 0.0, "ladder": {}}


def pivot(labels, series, store_meta=None, partials=()):
    """PURE: store-major rows from the trend's per-period `by_store` grain.

    `labels`  — the window, oldest first (the trend's own `months`).
    `series`  — the trend's per-period rows; each carries `by_store` = {store key: cell}, where a cell
                is the same {m1, m2_12, unsplit, total, ladder} shape the company row has.
    `store_meta` — {store key: {'store','store_code','market'}} from `router._leg_store_index`, the
                ONE home for store identity. A key it does not know keeps its own text as the name;
                '' becomes the named unattributed row. No identity is decided here.
    `partials` — labels that are not finished months (see `partial_months`); their money is REPORTED
                in the row but excluded from every derived delta.

    Returns (rows, ladder_keys). Each row:
      store, store_code, market, unattributed
      months   {label: cell}                     every label present, $0 cells included
      ladder   {rung key: $}                     the store's rungs summed across the COMPLETE months
      total / complete_total                     all months / complete months only
      first / last                               first and last COMPLETE month the store appears in
      delta / delta_pct                          last vs first, total commission
      trailing_* / m1_*                          the same for the M2-M12 legs and the 1st-month leg
      worst_rung                                 the rung that fell hardest first -> last, or None
    """
    partial = {str(p) for p in (partials or ())}
    meta = dict(store_meta or {})
    cells = {}
    for per in series or []:
        lab = str(per.get("period") or "")
        for skey, cell in (per.get("by_store") or {}).items():
            cells.setdefault(str(skey), {})[lab] = cell
    ladder_keys = _legs.months_present(
        *[c.get("ladder") or {} for by in cells.values() for c in by.values()])
    rows = []
    for skey, by in cells.items():
        months, ladder = {}, {}
        total = complete_total = 0.0
        present = []
        for lab in labels:
            cell = by.get(lab) or _blank_cell(lab)
            months[lab] = {"period": lab,
                           "m1": round(_f(cell.get("m1")), 2),
                           "m2_12": round(_f(cell.get("m2_12")), 2),
                           "unsplit": round(_f(cell.get("unsplit")), 2),
                           "total": round(_f(cell.get("total")), 2),
                           "partial": lab in partial,
                           "ladder": {_legs.ladder_key(k): round(_f(v), 2)
                                      for k, v in (cell.get("ladder") or {}).items()}}
            total = round(total + months[lab]["total"], 2)
            if lab in partial:
                continue
            complete_total = round(complete_total + months[lab]["total"], 2)
            if lab in by:
                present.append(lab)
            for k, v in months[lab]["ladder"].items():
                ladder[k] = round(ladder.get(k, 0.0) + v, 2)
        ent = meta.get(skey) or {}
        row = {"store_key": skey,
               "store": str(ent.get("store") or skey or UNATTRIBUTED_LABEL),
               "store_code": str(ent.get("store_code") or ""),
               "market": str(ent.get("market") or ""),
               "unattributed": skey == UNATTRIBUTED_KEY,
               "months": months, "ladder": ladder,
               "total": total, "complete_total": complete_total,
               "first": present[0] if present else None,
               "last": present[-1] if len(present) > 1 else None}
        rows.append(_derive(row, ladder_keys))
    rows.sort(key=lambda r: (r["unattributed"], -abs(r["complete_total"]), r["store"].lower()))
    return rows, ladder_keys


def _delta(first_cell, last_cell, key):
    """PURE: (delta, pct) for one measure between two cells, or (None, None) when there is no pair.

    pct is None — never 0, never inf — when the baseline is $0, because "up from nothing" has no
    percentage and printing one would invent a trend."""
    if first_cell is None or last_cell is None:
        return None, None
    a, b = _f(first_cell.get(key)), _f(last_cell.get(key))
    d = round(b - a, 2)
    return d, (round(d / abs(a) * 100, 1) if a else None)


def _derive(row, ladder_keys):
    """PURE: the "is this store running down, and in which rung" read-out, from the row's own months.

    Compares the first and last COMPLETE month the store appears in — a partial month is never an
    endpoint of a trend (honesty rule 1). `worst_rung` is the rung whose dollars fell the most between
    those two months, which is the question the owner is actually asking: a store losing M4-M6 lost
    its cohort months ago, one losing M1 is losing it now."""
    f_lab, l_lab = row.get("first"), row.get("last")
    fc = row["months"].get(f_lab) if f_lab else None
    lc = row["months"].get(l_lab) if l_lab else None
    for measure in ("total", "m1", "m2_12", "unsplit"):
        d, p = _delta(fc, lc, measure)
        row["delta" if measure == "total" else f"{measure}_delta"] = d
        row["delta_pct" if measure == "total" else f"{measure}_delta_pct"] = p
    worst = None
    if fc is not None and lc is not None:
        for k in ladder_keys:
            a = _f((fc.get("ladder") or {}).get(k))
            b = _f((lc.get("ladder") or {}).get(k))
            d = round(b - a, 2)
            if d >= 0:
                continue
            if worst is None or d < worst["delta"]:
                worst = {"key": k, "label": _legs.ladder_label(k), "delta": d,
                         "from": round(a, 2), "to": round(b, 2),
                         "delta_pct": (round(d / abs(a) * 100, 1) if a else None)}
    row["worst_rung"] = worst
    return row


def totals_row(labels, rows, ladder_keys, partials=()):
    """PURE: the all-stores row, summed from `rows` — so the grid's own footer cannot disagree with
    the grid. (The trend's `company` series stays the authority for the company figure; this is the
    arithmetic of what is DISPLAYED, and `ties_to` reports whether the two agree.)"""
    partial = {str(p) for p in (partials or ())}
    months = {}
    for lab in labels:
        cell = _blank_cell(lab)
        cell["partial"] = lab in partial
        for r in rows:
            m = r["months"].get(lab) or {}
            for k in ("m1", "m2_12", "unsplit", "total"):
                cell[k] = round(cell[k] + _f(m.get(k)), 2)
            for k, v in (m.get("ladder") or {}).items():
                cell["ladder"][k] = round(cell["ladder"].get(k, 0.0) + _f(v), 2)
        months[lab] = cell
    row = {"store_key": None, "store": "All stores", "store_code": "", "market": "",
           "unattributed": False, "months": months,
           "ladder": {k: round(sum(_f(months[l]["ladder"].get(k)) for l in labels
                                   if l not in partial), 2) for k in ladder_keys},
           "total": round(sum(_f(months[l]["total"]) for l in labels), 2),
           "complete_total": round(sum(_f(months[l]["total"]) for l in labels
                                       if l not in partial), 2),
           "first": next((l for l in labels if l not in partial), None),
           "last": next((l for l in reversed(labels) if l not in partial), None)}
    return _derive(row, ladder_keys)


def ties_to(labels, totals, series):
    """PURE: per label, whether the displayed all-stores total equals the trend's COMPANY total.

    A mismatch is a finding and is reported as one (the grid says so on the month), never smoothed
    over: it means money reached the company series that the store axis could not place — today the
    only such money is the VidaPay/MA fallback on a month whose processor account resolves to no
    store, and the router names that case in `notes`."""
    comp = {str(p.get("period") or ""): _f(p.get("total")) for p in (series or [])}
    out = {}
    for lab in labels:
        shown = _f((totals.get("months") or {}).get(lab, {}).get("total"))
        want = comp.get(lab, 0.0)
        out[lab] = {"shown": round(shown, 2), "company": round(want, 2),
                    "ok": abs(round(shown - want, 2)) < 0.01,
                    "gap": round(shown - want, 2)}
    return out


def ladder_public(ladder_keys):
    """PURE: the column names and labels for the rungs, from the ONE home — so the CSV header, the
    grid header and the payload cannot spell a rung three ways."""
    return {"ladder_months": list(ladder_keys),
            "ladder_labels": {k: _legs.ladder_label(k) for k in ladder_keys},
            "ladder_columns": {k: _legs.ladder_column("comm", k) for k in ladder_keys},
            "ladder_unknown_key": _legs.LADDER_UNKNOWN}
