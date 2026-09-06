"""Event planned-vs-actual — DERIVED from the ONE shared sales pass. Never a second derivation.

═══════════════════════════════════════════════════════════════════════════════════════════════════
THE READ CONTRACT (§14 discipline, the same one the lease/liabilities work followed)
═══════════════════════════════════════════════════════════════════════════════════════════════════
Activations and accessory dollars per (store, rep, day) already exist. They come out of
`commcalc/router.py::_sales_cell_agg` — THE single shared pass behind the Sales Report, Executive
MTD and Daily Targets (§3 of the index) — reached through `_compute_feed_actuals_py`, which is also
what applies the canonical store-code resolution and the period-spelling variants.

This module CALLS that function and adds up its output rows. It does not query `raw_sales`, does not
query `daily_sales_feed`, does not classify a contract type, does not decide what an accessory is,
and does not know what a void or a return looks like. If the shared pass changes its mind about any
of that, this module changes with it automatically — which is the entire point. Two paths answering
"how many activations" is a defect (CLAUDE.md build gate); there is one, and it is not here.

The pure half of this file (`aggregate_actual_rows`, `compare_windows`) consumes that function's
OUTPUT SHAPE and is provable with no database. The impure half (`event_actuals`) is a thin fetch.

═══════════════════════════════════════════════════════════════════════════════════════════════════
ATTRIBUTION — what this report claims, and what it refuses to claim
═══════════════════════════════════════════════════════════════════════════════════════════════════
Sales rung at a store on the day of an outside event are NOT all caused by the event. Some of them
would have happened anyway; some event activations get rung up the next day; a customer met at a
table on Saturday may walk into the store on Tuesday. Nothing in the sales data marks a line as
"came from the event", and inventing an attribution would produce a number that looks authoritative
and is not.

So this module reports exactly one thing, and says so on every response:

    STORE PERFORMANCE OVER THE EVENT WINDOW, ALONGSIDE THE SAME STORES' PERFORMANCE ON THE SAME
    WEEKDAYS IN RECENT WEEKS.

`lift_vs_baseline` is a DIFFERENCE BETWEEN TWO OBSERVATIONS, not an effect. Every response carries
an `attribution` block stating that in words, the API never names a field `event_activations` or
`incremental_*`, and the goal comparison is described as "goal vs what the stores did", because that
is what it is. A manager who wants to know whether the event worked gets the numbers plus the honest
caveat, which is more useful than a confident fiction.

Proof: `backend/harness_marketing_event.py` sections F and G.
"""
from app.modules.marketing import event_logic as L

# ═══════════════════════════════════════════════════════════════════════════════════════════════
# The metric map: a goal metric key -> the field of the SHARED pass that answers it.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
# Every value on the right is a key `_compute_feed_actuals_py` itself returns. This table is a
# POINTER at the shared derivation, never a copy of it — there is no arithmetic here that decides
# what an activation is.
#
# The mapping is ALSO carried per-option in `core.marketing_option.extra.field` (mig 987), so a
# tenant can point a metric they invented at one of these fields without a deploy. This dict is the
# fallback and the whitelist: a metric whose configured field is not in here is reported as
# NOT DERIVABLE rather than being silently summed from something that happened to have that name.
FIELD_SOURCES = {
    # key in our summary        -> (source field on the shared pass' row, unit, human label)
    "activations": ("prem_count", "count",
                    "Activations (the shared activation bucket used by the Sales Report and "
                    "Daily Targets)"),
    "byod": ("byod_count", "count", "BYOD activations"),
    "upgrades": ("upg_count", "count", "Upgrades"),
    "boxes": ("box_count", "count",
              "Total boxes (as Daily Targets counts them, including the configured activation "
              "buckets)"),
    "accessory_dollars": ("acc_gp", "money",
                          "Accessory $ — accessory revenue plus the device set-up fee, the same "
                          "'achieved' figure Daily Targets attainment uses"),
    "setup_fees": ("setup_fee", "money", "Device set-up fees"),
    "billpays": ("billpay_count", "count", "Bill payments"),
}

#: Free-standing metrics with no automatic source. Reported as "no automatic actual" — deliberately
#: NOT as zero, which would read as failure.
NOT_DERIVABLE_NOTE = ("No automatic source exists for this metric, so there is no computed actual — "
                      "it is tracked as a goal for the event lead to report against.")

ATTRIBUTION_HEADLINE = "Store performance over the event window — not sales attributed to the event"

ATTRIBUTION_DETAIL = (
    "These are the totals the event's store(s) rang during the event's calendar day(s), taken from "
    "the same shared sales figures as the Sales Report and Daily Targets. They are NOT sales caused "
    "by the event: nothing in the sales data marks a transaction as having come from an event, some "
    "of this business would have happened anyway, and event conversations often ring up days later "
    "at the store. The baseline is the same store(s) on the same weekday(s) in the preceding weeks, "
    "so the comparison is like-for-like — but a difference between two observations is not proof of "
    "an effect."
)

GRAIN_NOTE = (
    "The shared sales figures are per store, per person, per DAY and carry no time of day, so an "
    "event's window is read as whole calendar days. A four-hour event is compared against the whole "
    "day it fell on."
)


def resolve_metric_field(metric_key, option_extra):
    """(field, unit, label, derivable). Config wins: a tenant's option row may point a metric at any
    field in FIELD_SOURCES. An unknown field name is NOT trusted — it degrades to not-derivable,
    because summing an arbitrary key would be a silent second definition of a sales metric."""
    extra = option_extra if isinstance(option_extra, dict) else {}
    if extra.get("derivable") is False:
        return None, extra.get("unit") or "count", None, False
    field = extra.get("field") or metric_key
    src = FIELD_SOURCES.get(field)
    if not src:
        return None, extra.get("unit") or "count", None, False
    return src[0], (extra.get("unit") or src[1]), src[2], True


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# PURE — aggregation over the shared pass's output rows
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def aggregate_actual_rows(rows, store_codes=None, dates=None, rep_names=None):
    """Sum `_compute_feed_actuals_py` output rows over a window.

    `store_codes` / `dates` / `rep_names` are FILTERS on rows the shared pass already produced —
    the pass did every classification decision; this only decides which of its rows are in scope.
    Passing None for a filter means "no restriction on that axis".

    Store codes and rep names are compared case-insensitively because the shared pass upper-cases
    reps and the canonical store code's case is not guaranteed across the roster tables.
    """
    codes = {str(c).strip().upper() for c in (store_codes or []) if str(c or "").strip()} or None
    days = {str(d)[:10] for d in (dates or [])} or None
    reps = {str(r).strip().upper() for r in (rep_names or []) if str(r or "").strip()} or None

    totals = {f: 0.0 for f in {v[0] for v in FIELD_SOURCES.values()}}
    matched, seen_days, seen_stores, seen_reps = 0, set(), set(), set()
    for r in (rows or []):
        code = str(r.get("store_code") or "").strip().upper()
        day = str(r.get("trans_date") or "")[:10]
        rep = str(r.get("rep_name") or "").strip().upper()
        if codes is not None and code not in codes:
            continue
        if days is not None and day not in days:
            continue
        if reps is not None and rep not in reps:
            continue
        matched += 1
        seen_days.add(day)
        seen_stores.add(code)
        if rep:
            seen_reps.add(rep)
        for field in totals:
            v = r.get(field)
            try:
                totals[field] += float(v or 0)
            except (TypeError, ValueError):
                continue
    return {
        "totals": {k: round(v, 2) for k, v in totals.items()},
        "rows_matched": matched,
        "days_with_sales": len(seen_days),
        "stores_with_sales": sorted(seen_stores),
        "reps_with_sales": sorted(seen_reps),
    }


def compare_windows(event_agg, baseline_agg, event_day_count, baseline_day_count):
    """Event window vs baseline, per source field, on a PER-DAY basis.

    Per-day, not per-window: the baseline deliberately spans several weeks (four same-weekdays by
    default), so comparing its raw total against a single event day would show a fictional collapse.
    Dividing both by their day counts is what makes the two numbers the same kind of thing.

    `pct_change` is None rather than 0 or infinity when the baseline is zero — a store that sold
    nothing on the last four Saturdays and three today has not improved by "infinity percent", and
    printing a number there would be worse than printing "no baseline".
    """
    e_days = max(1, int(event_day_count or 0))
    b_days = int(baseline_day_count or 0)
    out = {}
    for field in sorted({v[0] for v in FIELD_SOURCES.values()}):
        e_total = float((event_agg or {}).get("totals", {}).get(field) or 0.0)
        b_total = float((baseline_agg or {}).get("totals", {}).get(field) or 0.0)
        e_per_day = e_total / e_days
        b_per_day = (b_total / b_days) if b_days else None
        diff = None if b_per_day is None else (e_per_day - b_per_day)
        pct = None
        if b_per_day is not None and b_per_day != 0:
            pct = round(100.0 * (e_per_day - b_per_day) / abs(b_per_day), 1)
        out[field] = {
            "event_total": round(e_total, 2),
            "event_per_day": round(e_per_day, 2),
            "baseline_total": round(b_total, 2),
            "baseline_per_day": (None if b_per_day is None else round(b_per_day, 2)),
            "diff_per_day": (None if diff is None else round(diff, 2)),
            "pct_change": pct,
            "has_baseline": b_days > 0,
        }
    return out


def build_goal_lines(goals, options, comparison):
    """Goal rows + the derived actual → what the screen renders, one line per goal.

    A goal whose metric has no automatic source keeps its target and reports `derivable: false` with
    the reason. It NEVER shows an actual of 0 — the difference between "they sold nothing" and "we
    do not measure this" is the difference between a useful report and a misleading one.
    """
    by_key = {o.get("key"): o for o in (options or [])}
    lines = []
    for g in (goals or []):
        mk = g.get("metric_key")
        opt = by_key.get(mk) or {}
        field, unit, src_label, derivable = resolve_metric_field(mk, opt.get("extra"))
        target = L._num(g.get("target_value"))
        line = {
            "metric_key": mk,
            "label": opt.get("label") or mk,
            "unit": unit,
            "target_value": target,
            "note": g.get("note"),
            "derivable": derivable,
            "source_field": field,
            "source_label": src_label,
        }
        if not derivable:
            line.update({"actual_value": None, "variance": None, "pct_of_goal": None,
                         "reason": NOT_DERIVABLE_NOTE})
            lines.append(line)
            continue
        c = (comparison or {}).get(field) or {}
        actual = c.get("event_total")
        line["actual_value"] = actual
        line["baseline_per_day"] = c.get("baseline_per_day")
        line["diff_per_day"] = c.get("diff_per_day")
        line["pct_change_vs_baseline"] = c.get("pct_change")
        if target is None:
            line.update({"variance": None, "pct_of_goal": None,
                         "reason": "No target was set for this metric."})
        else:
            line["variance"] = round((actual or 0.0) - target, 2)
            line["pct_of_goal"] = (round(100.0 * (actual or 0.0) / target, 1) if target else None)
        lines.append(line)
    return lines


def attribution_block(event_days, baseline_days, store_codes, derived_ok=True, source_note=None):
    """The honesty header carried by EVERY actuals response. Not optional, not collapsible in the
    UI, and phrased so that quoting it out of context still says the right thing."""
    return {
        "headline": ATTRIBUTION_HEADLINE,
        "detail": ATTRIBUTION_DETAIL,
        "grain_note": GRAIN_NOTE,
        "event_days": list(event_days or []),
        "baseline_days": list(baseline_days or []),
        "stores": list(store_codes or []),
        "baseline_method": ("The same store(s) on the same weekday(s) for the %d preceding week(s)."
                            % BASELINE_WEEKS),
        "source": ("commcalc shared sales aggregation (_sales_cell_agg via "
                   "_compute_feed_actuals_py) — the same figures as the Sales Report and Daily "
                   "Targets."),
        "derived": bool(derived_ok),
        "source_note": source_note,
    }


BASELINE_WEEKS = 4


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# IMPURE — the fetch. Everything decided above; this only gets the rows.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def _shared_pass_rows(client, org_id, periods):
    """Rows from commcalc's shared per-(store, rep, day) pass for the given YYYY-MM periods.

    The import is LOCAL and lazy on purpose: commcalc/router.py is a very large module that pulls in
    the whole commission stack at import time, and a marketing page must not pay that cost (nor make
    this module unimportable in a container where commcalc's dependencies are absent). A failure to
    reach it degrades to "actuals unavailable" with a stated reason — never a 500 on the event page,
    and never a silent zero that would read as a failed event.
    """
    try:
        from app.modules.commcalc.router import _compute_feed_actuals_py
    except Exception as e:                                   # pragma: no cover - import-environment
        return None, "The shared sales aggregation could not be loaded (%s)." % (str(e)[:120],)
    rows, errors = [], []
    for period in (periods or []):
        try:
            rows.extend(_compute_feed_actuals_py(client, org_id, period) or [])
        except Exception as e:
            errors.append("%s (%s)" % (period, str(e)[:80]))
    if errors and not rows:
        return None, "No sales figures could be read for %s." % ", ".join(errors)
    note = None
    if errors:
        note = "Sales figures were unavailable for %s; the totals below cover the rest." % \
               ", ".join(errors)
    return rows, note


def event_actuals(client, org_id, event, store_codes, goals, goal_options, rep_names=None,
                  baseline_weeks=BASELINE_WEEKS):
    """The full planned-vs-actual payload for ONE event.

    Reads the shared pass once for every month either window touches, then filters that single row
    set twice (event window, baseline window) — so the two comparisons can never come from
    differently-fetched data.
    """
    e_days = L.event_dates(event)
    b_days = L.baseline_dates(event, weeks=baseline_weeks)
    codes = [c for c in (store_codes or []) if c]

    if not e_days:
        return {"available": False,
                "reason": "This event has no start date, so there is no window to read.",
                "goals": build_goal_lines(goals, goal_options, {}),
                "attribution": attribution_block([], [], codes, derived_ok=False)}
    if not codes:
        return {"available": False,
                "reason": ("No store is attached to this event, so there is no store whose "
                           "performance can be read. Attach the store(s) working the event."),
                "goals": build_goal_lines(goals, goal_options, {}),
                "attribution": attribution_block(e_days, b_days, [], derived_ok=False)}

    periods = L.period_keys_for_dates(list(e_days) + list(b_days))
    rows, note = _shared_pass_rows(client, org_id, periods)
    if rows is None:
        return {"available": False, "reason": note,
                "goals": build_goal_lines(goals, goal_options, {}),
                "attribution": attribution_block(e_days, b_days, codes, derived_ok=False,
                                                 source_note=note)}

    event_agg = aggregate_actual_rows(rows, codes, e_days, rep_names)
    baseline_agg = aggregate_actual_rows(rows, codes, b_days, rep_names)
    comparison = compare_windows(event_agg, baseline_agg, len(e_days), len(b_days))
    return {
        "available": True,
        "goals": build_goal_lines(goals, goal_options, comparison),
        "window": event_agg,
        "baseline": baseline_agg,
        "comparison": comparison,
        "field_labels": {v[0]: v[2] for v in FIELD_SOURCES.values()},
        "attribution": attribution_block(e_days, b_days, codes, derived_ok=True, source_note=note),
    }
