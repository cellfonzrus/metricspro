"""THE ONE HOME for "where must this store's YEAR land, and is it going to".

Owner 2026-11-17, verbatim: *"The goal is to make the store a platinum store which is 1 Million in
annual sale including the commission earned - make this user filled entry but fill it for me where
the targets have to be declared"*.

PURE — dicts in, dicts out, no DB, no network, no clock. The caller passes `today`.

WHY THIS IS NOT A FOURTH GRAIN ON `commcalc.target_line` (the CLAUDE.md duplicate check, stated).
`targets_engine` owns one piece of arithmetic: a MONTHLY figure spread over the scope's scheduled
hours. `target_line.grain` ('day' | 'week' | 'month') names a WINDOW over that spread — a smaller
slice of a month that is already known. A year is the opposite direction: it is the figure that
GOVERNS the months, not a slice of one. And a `target_line` row names exactly ONE `metric_key`,
while a goal is measured over a SET of categories, which is a shape that table's unique key cannot
express. So the goal is its own declaration (mig 1125) — but the subtraction and the division are
NOT a second arithmetic: `progress` dereferences `targets_engine.rate_to_close`, the same home
`backward_from_goal` dereferences, with the MONTH as its unit instead of the day.

WHAT COUNTS TOWARD THE GOAL IS DECLARED, NEVER DEFAULTED (owner directive 2026-10-30). The owner
said "including the commission earned", and there are at least two things that could mean for a
shipping store: the commission the STORE earns from the carrier on a shipment, and the commission
the store PAYS its reps. This module refuses to decide. The goal carries `metric_keys` — the
tenant's own declared categories (mig 1072) — and whichever reading is right is expressed by
ticking the category that holds it. A tenant whose "commission earned" has no category yet declares
one; no code here learns the word.

NOTHING DECLARED MEANS NOTHING MEASURED, AND THE SURFACE SAYS SO. A goal with no `metric_keys` does
not quietly measure every category, and it does not measure zero either: `achieved` returns
`measured: False` with a reason in words. Zero is the plausible-looking answer that hides this class
— a store that traded all year would read as having sold nothing, which is exactly the defect the
device-line report shipped with (index §COMMCALC-49.10).
"""
from app.modules.commcalc import targets_engine as _targets_engine

# The horizons a goal may be declared over. A year today; `quarter` exists because the same
# declaration answers it with no new arithmetic, and a tenant asking for one should not need a
# migration. Mirrors the CHECK in mig 1125.
GRAINS = ("year", "quarter")

# How many months each horizon holds. The denominator of the run-rate, in ONE place.
GRAIN_MONTHS = {"year": 12, "quarter": 3}


def _num(v, default=0.0):
    try:
        if v in (None, ""):
            return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _key(v):
    return str(v or "").strip()


def grain_named(grain):
    """The declared horizon, or 'year'. A spelling this module does not know is NOT guessed into a
    different horizon silently — it falls to the only one every tenant has, and `months_in` is the
    single place that turns a horizon into a count of months."""
    g = _key(grain).lower()
    return g if g in GRAINS else "year"


def months_in(grain):
    return GRAIN_MONTHS[grain_named(grain)]


def horizon_bounds(goal_row):
    """The horizon's first and last calendar day, as 'YYYY-MM-DD' strings, or (None, None) when the
    period cannot be read.

    Here rather than in the caller because every surface that measures a goal must measure the SAME
    span — a read that built its own bounds would be the second derivation of "which days are in
    this year", and it would disagree the first time a quarter was declared. PURE, and the only date
    arithmetic in this module besides `months_elapsed`, which reads the same two fields by the same
    rule."""
    start = _key((goal_row or {}).get("period"))
    total = months_in((goal_row or {}).get("grain"))
    try:
        if len(start) == 4:
            y, m = int(start), 1
        else:
            y, m = int(start[:4]), int(start[5:7])
        if not (1 <= m <= 12):
            return (None, None)
    except (ValueError, IndexError):
        return (None, None)
    em = m + total - 1
    ey, em = y + em // 12, em % 12 + 1
    if em == 1:                       # rolled exactly onto January; the month before it is December
        ey, em = ey - 1, 12
    else:
        em -= 1
    # The last day of the closing month, without a calendar library: the day before the first of the
    # month after it.
    ny, nm = (ey + 1, 1) if em == 12 else (ey, em + 1)
    import datetime as _dt
    last = _dt.date(ny, nm, 1) - _dt.timedelta(days=1)
    return ("%04d-%02d-01" % (y, m), last.isoformat())


def measure_keys(goal_row):
    """The declared categories this goal is measured over, de-duplicated, order kept.

    This is the ONLY reader of `metric_keys`. A caller that pulled the list off the row itself could
    not be stopped from treating an empty list as "everything", which is the defect the module
    docstring names."""
    out, seen = [], set()
    for k in ((goal_row or {}).get("metric_keys") or []):
        kk = _key(k)
        if kk and kk not in seen:
            seen.add(kk)
            out.append(kk)
    return out


def measure_named(goal_row, catalog=None):
    """The measure in the TENANT'S OWN WORDS, for a screen and for an explanation.

    `catalog` is `closing/metric_catalog.build()` output — the tenant's declarations. A key the
    catalog no longer carries is returned with `declared: False` rather than dropped, because a goal
    silently measuring one category fewer than it says is worse than a goal that says a category
    went away."""
    by_key = {_key(m.get("key")): m for m in (catalog or []) if _key(m.get("key"))}
    out = []
    for k in measure_keys(goal_row):
        m = by_key.get(k) or {}
        out.append({"key": k, "label": _key(m.get("label")) or k,
                    "declared": bool(m), "in_revenue": bool(m.get("in_revenue"))})
    return out


def achieved(goal_row, amount_by_key):
    """What the horizon has earned SO FAR against this goal's measure.

    `amount_by_key` is {metric_key: amount} already summed over the horizon's completed and
    in-flight months by the caller — this module does no period arithmetic, so it cannot disagree
    with whatever read produced those figures.

    Returns `measured` (False when the goal declares no category, or when NONE of the categories it
    declares appears in the figures at all), `amount`, the per-category breakdown, and `why` in
    words when it is not measured. A category that is declared and simply had no sales contributes
    0.00 and does NOT make the goal unmeasured — that is a real zero, not an absent one."""
    keys = measure_keys(goal_row)
    if not keys:
        return {"measured": False, "amount": 0.0, "by_key": {}, "missing": [],
                "why": "This goal names no category, so there is nothing to measure it against. "
                       "Tick the categories whose sales count toward it."}
    figs = dict(amount_by_key or {})
    present = [k for k in keys if k in figs]
    missing = [k for k in keys if k not in figs]
    if not present:
        return {"measured": False, "amount": 0.0, "by_key": {}, "missing": missing,
                "why": "None of the %d category(ies) this goal names has been measured yet, so the "
                       "progress toward it is unknown rather than zero." % len(keys)}
    by_key = {k: round(_num(figs.get(k)), 2) for k in present}
    return {"measured": True, "amount": round(sum(by_key.values()), 2), "by_key": by_key,
            "missing": missing,
            "why": ("%d of the %d category(ies) this goal names has no figure yet, so this total is "
                    "what the rest have earned." % (len(missing), len(keys))) if missing else ""}


def months_elapsed(goal_row, today):
    """Completed-or-in-flight months of the horizon up to `today`, and the months left.

    The month `today` falls in counts as ELAPSED and also as REMAINING: it is partly earned and
    still partly available, so the run-rate must divide the earnings by it, and the rate that closes
    the gap must still have it to spend. Counting it on one side only is how a December goal would
    either flatter the run-rate or declare itself unreachable a month early."""
    total = months_in((goal_row or {}).get("grain"))
    start = _key((goal_row or {}).get("period"))
    t = _key(today)
    # 'YYYY' (a year) or 'YYYY-MM' (a quarter's first month). Anything else and the horizon's
    # position is unknown, so nothing is assumed: the caller is told it could not be placed.
    try:
        if len(start) == 4:
            y0, m0 = int(start), 1
        else:
            y0, m0 = int(start[:4]), int(start[5:7])
        ty, tm = int(t[:4]), int(t[5:7])
    except (ValueError, IndexError):
        return {"placed": False, "elapsed": 0, "remaining": total, "total": total,
                "why": "The period %r could not be read as a year ('2027') or a first month "
                       "('2027-01'), so how far through the horizon today is cannot be said."
                       % start}
    offset = (ty - y0) * 12 + (tm - m0)
    if offset < 0:
        return {"placed": True, "elapsed": 0, "remaining": total, "total": total,
                "why": "This horizon has not started yet."}
    if offset >= total:
        return {"placed": True, "elapsed": total, "remaining": 0, "total": total,
                "why": "This horizon has closed."}
    return {"placed": True, "elapsed": offset + 1, "remaining": total - offset, "total": total,
            "why": ""}


def progress(goal_row, amount_by_key, today):
    """THE ANSWER: is this store going to reach its goal, and what would it take.

    `per_month` is `targets_engine.rate_to_close` with the MONTH as its unit — the same home the
    per-day backward calculation dereferences, so "what do I need" means one thing on this platform.

    `on_pace` is None, never False, when the goal cannot be measured or the horizon cannot be
    placed. A store told it is behind on a figure nobody measured would act on nothing."""
    goal = round(_num((goal_row or {}).get("goal_value")), 2)
    ach = achieved(goal_row, amount_by_key)
    win = months_elapsed(goal_row, today)
    closed = _targets_engine.rate_to_close(goal, ach["amount"] if ach["measured"] else 0.0,
                                           win["remaining"])
    run_rate = None
    pace_needed = None
    on_pace = None
    if ach["measured"] and win["placed"] and win["elapsed"] > 0:
        run_rate = round(ach["amount"] / win["elapsed"], 2)
        # What the whole horizon needs per month if it were spread flat — the yardstick the run-rate
        # is measured against. Not the same number as `per_month`, which only has the months LEFT.
        pace_needed = round(goal / win["total"], 2) if win["total"] else None
        if pace_needed is not None:
            on_pace = run_rate >= pace_needed
    return {
        "goal": goal, "grain": grain_named((goal_row or {}).get("grain")),
        "period": _key((goal_row or {}).get("period")),
        "label": _key((goal_row or {}).get("label")),
        "measured": ach["measured"], "achieved": ach["amount"], "by_key": ach["by_key"],
        "missing_keys": ach["missing"], "why": ach["why"] or win["why"],
        "months_elapsed": win["elapsed"], "months_remaining": win["remaining"],
        "months_total": win["total"], "placed": win["placed"],
        "need": closed["need"] if ach["measured"] else None,
        "per_month": closed["per_unit"] if ach["measured"] else None,
        "run_rate": run_rate, "pace_needed": pace_needed, "on_pace": on_pace,
        "pct": (round(100.0 * ach["amount"] / goal, 1) if (ach["measured"] and goal > 0) else None),
    }


def reconcile_monthly_targets(goal_row, monthly_target_sum, months_counted=0):
    """DOES THE MONTH-BY-MONTH PLAN ADD UP TO THE YEAR'S GOAL?

    The operational point of declaring a year: a store can carry twelve monthly targets that each
    look reasonable and sum to well under the goal, and nothing on the platform says so today.
    `monthly_target_sum` is what the stored monthly targets (mig 006 columns + mig 1074 rows, folded
    by `targets_engine.derive_monthly_by_cat`) add up to over this goal's measure.

    `months_counted` is how many months actually carry a saved target. It is reported, not corrected:
    a sum that is short BECAUSE eight months have no target set is a different conversation from one
    that is short because twelve months were each set too low, and a single gap figure cannot tell
    them apart. Returns `reconciles: None` when the goal names no category, for the same reason
    `on_pace` does."""
    goal = round(_num((goal_row or {}).get("goal_value")), 2)
    total = months_in((goal_row or {}).get("grain"))
    if not measure_keys(goal_row):
        return {"goal": goal, "planned": 0.0, "gap": None, "reconciles": None,
                "months_counted": int(months_counted or 0), "months_total": total,
                "months_without_target": total,
                "why": "This goal names no category, so there is no monthly target to add up."}
    planned = round(_num(monthly_target_sum), 2)
    counted = max(0, int(months_counted or 0))
    return {"goal": goal, "planned": planned, "gap": round(goal - planned, 2),
            "reconciles": planned >= goal, "months_counted": counted, "months_total": total,
            "months_without_target": max(0, total - counted),
            "why": ("%d of %d months carry no saved target, so the plan is short at least partly "
                    "because it is incomplete." % (total - counted, total)) if counted < total
                   else ""}
