"""
Daily Sales Targets engine — pure functions, no I/O.

Given a store's monthly targets (defined at the start of the month), the StoreOps
schedule (scheduled hours per rep per day), and the daily sales actuals, this
reverse-calculates a schedule-weighted per-day target for the store and for each
rep, then applies the daily catch-up rule:

  base[c][d]    = monthly[c] * (hours_on_day_d / total_scheduled_hours)
  shortfall     = max(0, cum_base_through_yesterday - cum_achieved_through_yesterday)
  today_target  = base[c][today] + shortfall      # overage never reduces it
  need          = max(0, monthly[c] - achieved_to_date)
  pace          = need / open_days_remaining_incl_today

Categories: activations (premium+BYOD acts), upgrades, byod (KPI-derived),
accessories ($ GP). Counts for the first three, dollars for accessories.
"""
import re
from datetime import date, timedelta

from app.modules.commcalc.calculator import safe_float
# WHICH NUMBER ANSWERS A DECLARED CATEGORY is one fact with one home. `metric_catalog` is
# pure (no imports of its own, no I/O), so dereferencing it keeps this engine pure too — and
# keeps a target's UNIT and its ACHIEVED figure reading the same rule.
from app.modules.closing.metric_catalog import figure_unit as _figure_unit
# WHO IS THIS PERSON is one fact with one home (§CORE-08) — pure, so this engine stays pure.
from app.modules.core import person_identity as _person_identity


def name_key(name) -> str:
    """WHO IS THIS PERSON, word order and all thrown away — one fact with one home:
    `core.person_identity.token_key` (§CORE-08). The name is kept because this engine's callers read
    it; the rule, and the 2026-09-02 finding that motivated it, live in the home. PURE."""
    return _person_identity.token_key(name)


# Display order; accessories is a dollar amount, the rest are transaction counts.
#
# THESE FOUR ARE THE BUILT-INS, NOT THE WHOLE AXIS (owner directive 2026-10-10, §COMMCALC-02).
# They are the categories the SALES FEED answers, they each have a physical column on
# commcalc.targets (byod is derived from byod_pct), and they are kept as module constants because
# two surfaces outside this engine already dereference them by name — the DM visit-priority rule and
# GET /storevisit/visit-priority-options (§47.17). A tenant that sells services rather than
# activations declares its own categories on Daily Closing -> Count Fields, and those arrive through
# `target_categories()` / `target_units()` below. NEVER re-list these four anywhere else: ask this module.
BUILTIN_CATEGORIES = ['activations', 'upgrades', 'byod', 'accessories']
BUILTIN_UNITS = {'activations': 'count', 'upgrades': 'count', 'byod': 'count',
                 'accessories': 'dollars'}

# Back-compatible aliases. Existing dereferencers read these; they are the BUILT-IN axis, so a
# caller that wants the tenant's real axis calls `target_categories(catalog)` instead.
CATEGORIES = BUILTIN_CATEGORIES
UNITS = BUILTIN_UNITS

# Which fact answers a category's ACHIEVED figure. The built-ins come from the processed sales feed
# (_compute_feed_actuals_py); a declared category comes from the store-day declarations that
# closing/metric_catalog.py folds. A surface that guesses the source would read a drop-off count off
# the sales feed and get a silent zero, which is why the source travels WITH the category.
SOURCE_SALES_FEED = 'sales_feed'
SOURCE_DECLARED = 'declared'

# The grains a target can be set at. The engine has always been monthly, spread over scheduled days;
# `week` and `day` are windows OVER that same arithmetic, never a second calculation of it.
GRAINS = ('day', 'week', 'month')

# ── WHERE A BUILT-IN'S TARGET PHYSICALLY LIVES ───────────────────────────────────────────────────
# One home for the column names, because three surfaces need them and each kept its own list: the
# fold below, the carry-forward suggestion in commcalc/router, and the settings screen. `byod` is
# absent on purpose — it has no column of its own, it is DERIVED from activations x byod_pct, and
# listing it here would invite a caller to look for a column that does not exist. A DECLARED
# category is absent too: its target is a commcalc.target_line row (mig 1074), which is what
# `target_store` answers.
BUILTIN_TARGET_COLUMN = {
    'activations': 'activations_monthly',
    'upgrades': 'upgrades_monthly',
    'accessories': 'accessories_monthly',
}


def target_store(cat: str) -> str:
    """Which physical store holds this category's target: 'column' on commcalc.targets, 'derived'
    (byod, from activations x byod_pct), or 'line' (a commcalc.target_line row).

    TWO PHYSICAL STORES, ONE READER. A caller that needs to WRITE a target asks this instead of
    guessing from the category name, so adding a declared category never needs a schema change and
    never writes a built-in to the wrong store."""
    if cat in BUILTIN_TARGET_COLUMN:
        return 'column'
    if cat in BUILTIN_UNITS:
        return 'derived'
    return 'line'


def target_categories(catalog: list[dict] | None = None) -> list[str]:
    """The target axis for THIS tenant: the four built-ins, then every declared category that
    carries revenue. ONE answer, so a target screen, a report card and a payout rule cannot
    disagree about what is targetable.

    `catalog` is closing/metric_catalog.build() output. Passing None gives the built-ins alone,
    which is what every pre-2026-10 caller already assumed."""
    out = list(BUILTIN_CATEGORIES)
    for m in (catalog or []):
        key = (m or {}).get('key')
        if not key or key in out:
            continue
        # Only a revenue-bearing metric is a sales target. A category declared purely as a tally
        # (how many drop-offs) is still targetable because its units are the thing being targeted —
        # what it must NOT do is silently become a dollar figure, which `units()` decides.
        out.append(key)
    return out


def target_units(catalog: list[dict] | None = None) -> dict:
    """The unit per category — 'count' or 'dollars'. A declared category's unit is the one the
    admin chose on Count Fields (`value_kind`), never inferred here: a category measured in both
    is targeted on its MONEY, because that is the figure a target conversation is about."""
    out = dict(BUILTIN_UNITS)
    for m in (catalog or []):
        key = (m or {}).get('key')
        if not key or key in out:
            continue
        out[key] = _figure_unit((m or {}).get('value_kind'))
    return out


# The words a person reads for a built-in category. Three surfaces kept their own copy of this map
# (the action-plan router, the manager report card, the targets screen), so a declared category
# showed up on one of them as a raw key. It lives here with the axis it labels.
BUILTIN_LABELS = {
    'activations': 'Activations',
    'upgrades': 'Upgrades',
    'byod': 'BYOD',
    'accessories': 'Accessories',
}


def target_labels(catalog: list[dict] | None = None) -> dict:
    """The label per category. A DECLARED category is labelled with the words the admin typed on
    Count Fields — never a key prettified by a surface, because the admin's own wording is the
    thing a manager will recognise. Falls back to the key only when a catalog entry carries no
    label at all."""
    out = dict(BUILTIN_LABELS)
    for m in (catalog or []):
        key = (m or {}).get('key')
        if not key or key in out:
            continue
        out[key] = str((m or {}).get('label') or '').strip() or key
    return out


def target_axis(catalog: list[dict] | None = None) -> list[dict]:
    """The whole axis as a surface wants it: [{key, label, unit, store, source}] in order.

    ONE call, so a screen, a report card and a payout-rule dropdown cannot disagree about what is
    targetable, what it is called or what unit it is in. Every consumer that used to keep its own
    list of four categories reads this instead."""
    _u, _l = target_units(catalog), target_labels(catalog)
    return [{'key': c, 'label': _l.get(c, c), 'unit': _u.get(c, 'count'),
             'store': target_store(c), 'source': source_of(c, catalog)}
            for c in target_categories(catalog)]


def _axis_of_scope(scope_result: dict | None) -> list[str]:
    """The category axis a compute_scope result already carries, in its own order. Reading it back
    is how a downstream consumer avoids keeping a second list of categories."""
    cats = ((scope_result or {}).get('categories') or {})
    return list(cats.keys())


def _axis_of_rows(rows: list[dict]) -> tuple[list[str], dict]:
    """The union of the category axes a set of store rows carry, built in first-seen order, with
    each category's unit as the rows themselves reported it. Two stores on different catalogs (a
    tenant mid-rollout) therefore aggregate over the union rather than over whichever came first."""
    axis: list[str] = []
    units_seen: dict = {}
    for r in (rows or []):
        for cat, m in ((r or {}).get('categories') or {}).items():
            if cat not in units_seen:
                axis.append(cat)
            # A later row never overwrites a unit already seen: the first answer wins, so the
            # aggregate's unit is stable regardless of store order.
            units_seen.setdefault(cat, ((m or {}).get('unit') or 'count'))
    return axis, units_seen


def source_of(cat: str, catalog: list[dict] | None = None) -> str:
    """Which fact answers this category's achieved figure."""
    return SOURCE_SALES_FEED if cat in BUILTIN_UNITS else SOURCE_DECLARED

# Conversion = boxes sold ÷ bill-payments (walk-ins), as a %. A ratio measured against a
# fixed threshold (not a catch-up category), computed separately per store and per rep.
CONVERSION_TARGET = 30.0  # %


def _as_date(v):
    """Coerce a value to a date. Accepts date objects or 'YYYY-MM-DD...' strings."""
    if isinstance(v, date):
        return v
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def achieved_for_cat(cat: str, prem: float, byod: float, upg: float, acc: float,
                     declared: dict | None = None) -> float:
    """What this category actually ACHIEVED on a day.

    The four built-ins are answered from the sales feed exactly as they always were — the four
    branches below are byte-for-byte the original, so no wireless tenant's number moves. A DECLARED
    category is answered from `declared`, the store-day figures closing/metric_catalog folds; the
    value is already in the category's own unit (units for a count metric, dollars for a money one),
    because the catalog decided that when the admin declared it.

    `declared` is keyword-last and defaults to None, so every existing positional caller is
    unchanged. An unknown category still returns 0.0 — never a guess."""
    if cat == 'activations':
        return prem + byod
    if cat == 'byod':
        return byod
    if cat == 'upgrades':
        return upg
    if cat == 'accessories':
        return acc
    if declared:
        return safe_float(declared.get(cat))
    return 0.0


def scope_hours_by_day(shifts: list[dict], store_code: str, rep_name: str | None) -> dict:
    """Sum scheduled hours per day for a scope. rep_name=None → whole store."""
    out: dict[date, float] = {}
    sc = (store_code or '').strip().upper()
    rn = name_key(rep_name)
    for s in shifts:
        if s.get('is_deleted'):
            continue
        if (s.get('store_code') or '').strip().upper() != sc:
            continue
        if rn and name_key(s.get('employee_name')) != rn:
            continue
        d = _as_date(s.get('shift_date'))
        if not d:
            continue
        h = safe_float(s.get('scheduled_hours'))
        if h <= 0:
            continue
        out[d] = out.get(d, 0.0) + h
    return out


def scope_actuals_by_day(actuals: list[dict], store_code: str, rep_name: str | None) -> dict:
    """Aggregate RPC actual rows per day for a scope → {date: {prem,byod,upg,acc}}."""
    out: dict[date, dict] = {}
    sc = (store_code or '').strip().upper()
    rn = name_key(rep_name)
    for a in actuals:
        if (a.get('store_code') or '').strip().upper() != sc:
            continue
        if rn and name_key(a.get('rep_name')) != rn:
            continue
        d = _as_date(a.get('trans_date'))
        if not d:
            continue
        agg = out.setdefault(d, {'prem': 0.0, 'byod': 0.0, 'upg': 0.0, 'acc': 0.0,
                                 'setup': 0.0, 'box': 0.0, 'billpay': 0.0})
        agg['prem'] += safe_float(a.get('prem_count'))
        agg['byod'] += safe_float(a.get('byod_count'))
        agg['upg'] += safe_float(a.get('upg_count'))
        # `acc` (acc_gp) ALREADY includes the device set-up fee (folded in _compute_feed_actuals_py so the
        # accessory attainment counts it); `setup` carries the same set-up fee SEPARATELY for reporting.
        agg['acc'] += safe_float(a.get('acc_gp'))
        agg['setup'] += safe_float(a.get('setup_fee'))
        agg['box'] += safe_float(a.get('box_count'))
        agg['billpay'] += safe_float(a.get('billpay_count'))
    return out


def merge_declared_actuals(actuals_by_day: dict, declared_by_day: dict | None) -> dict:
    """Attach what the store DECLARED each day to that day's sales actuals, on the `declared` key.

    THE MERGE LIVES HERE, not in the router. A declared category's achievement arrives from a
    different source than the sales feed — reps type it on Submit Daily Closing, and
    closing/router._declared_by_day is its one reader — but it has to reach exactly the same
    per-day arithmetic as a sale, or the catch-up shortfall and the pace would be computed from the
    feed alone and a drop-off target would never register progress. Doing the merge in each caller
    is how three callers would come to disagree about what a day with declarations but no sales
    means.

    A day that was DECLARED but had no feed sale still counts: it gets a zero sales row carrying the
    declarations, because for a UPS-store tenant that is a completely ordinary trading day, not a
    missing one. `declared_by_day` is keyed by date STRING (as the closing reader returns it); the
    actuals are keyed by `date`, and this is the one place that reconciles the two.

    Non-destructive: the input dict is not mutated, so a caller can still read the pure feed
    figures (conversion, box counts) off its own copy."""
    out = {d: dict(v) for d, v in (actuals_by_day or {}).items()}
    for key, vals in (declared_by_day or {}).items():
        d = _as_date(key)
        if not d or not vals:
            continue
        row = out.get(d)
        if row is None:
            row = {'prem': 0.0, 'byod': 0.0, 'upg': 0.0, 'acc': 0.0,
                   'setup': 0.0, 'box': 0.0, 'billpay': 0.0}
            out[d] = row
        merged = dict(row.get('declared') or {})
        for k, v in vals.items():
            merged[k] = safe_float(merged.get(k)) + safe_float(v)
        row['declared'] = merged
    return out


def scope_conversion(actuals: list[dict], store_code: str, rep_name: str | None = None,
                     today=None) -> dict:
    """MTD conversion for a store (rep_name=None) or a rep within it.

    conversion = boxes sold (device-dept lines) ÷ bill-payments (walk-in recharges) × 100.
    Target = CONVERSION_TARGET%. Rows after `today` are excluded so it stays month-to-date."""
    sc = (store_code or '').strip().upper()
    rn = name_key(rep_name)
    boxes = billpays = 0.0
    for a in actuals:
        if (a.get('store_code') or '').strip().upper() != sc:
            continue
        if rn and name_key(a.get('rep_name')) != rn:
            continue
        if today is not None:
            d = _as_date(a.get('trans_date'))
            if d and d > today:
                continue
        boxes += safe_float(a.get('box_count'))
        billpays += safe_float(a.get('billpay_count'))
    rate = (boxes / billpays * 100.0) if billpays > 0 else 0.0
    return {
        'boxes': int(boxes),
        'billpays': int(billpays),
        'rate': round(rate, 1),
        'target': CONVERSION_TARGET,
        'meets_target': rate >= CONVERSION_TARGET,
    }


def scope_achieved_mtd(actuals: list[dict], store_code: str, rep_name: str | None,
                       today=None) -> dict:
    """Sum a scope's MTD achieved (activations / upgrades / byod / accessory $)
    across every day up to `today`. Used for the per-rep store breakdown."""
    by_day = scope_actuals_by_day(actuals, store_code, rep_name)
    prem = byod = upg = acc = setup = 0.0
    for d, a in by_day.items():
        if today is not None and d > today:
            continue
        prem += a['prem']; byod += a['byod']; upg += a['upg']; acc += a['acc']
        setup += a.get('setup', 0.0)
    # `accessories` INCLUDES the device set-up fee (attainment basis); `accessory_setup_fee` is the set-up
    # fee portion of that, reported separately so nothing is silently blended.
    return {'activations': round(prem + byod, 1), 'byod': round(byod, 1),
            'upgrades': round(upg, 1), 'accessories': round(acc, 2),
            'accessory_setup_fee': round(setup, 2)}


def project_future_hours(hours_by_day: dict, today: date, month_end: date) -> dict:
    """Fill not-yet-scheduled future days from the scope's weekly open pattern.

    StoreOps schedules are entered week-by-week, so `hours_by_day` typically only
    reaches ~today. Left alone, the remaining-days denominator collapses to 1 and every
    store is told to hit its whole monthly balance today. This looks at which weekdays
    the scope has concrete hours on this period, takes the average hours on each such
    weekday, and assigns that to every matching weekday from `today` through `month_end`
    that has no concrete shift yet. Future-only; never overwrites a concrete day; a scope
    with no shifts at all observes no pattern and projects nothing → {}."""
    if not hours_by_day or not month_end:
        return {}
    by_wd: dict[int, list] = {}
    for d, h in hours_by_day.items():
        if h > 0:
            by_wd.setdefault(d.weekday(), []).append(h)
    if not by_wd:
        return {}
    avg_by_wd = {wd: sum(v) / len(v) for wd, v in by_wd.items()}
    out: dict[date, float] = {}
    d = today
    while d <= month_end:
        if d not in hours_by_day and d.weekday() in avg_by_wd:
            out[d] = avg_by_wd[d.weekday()]
        d += timedelta(days=1)
    return out


def compute_scope(
    monthly_by_cat: dict,
    hours_by_day: dict,
    actuals_by_day: dict,
    today: date,
    round_counts: bool = False,
    month_end: date | None = None,
    catalog: list[dict] | None = None,
):
    """Compute per-category target numbers + a day-by-day calendar for one scope.

    When `month_end` is given and the concrete StoreOps schedule (`hours_by_day`) doesn't
    reach it, future open days are PROJECTED from the scope's weekly pattern (see
    project_future_hours) so the per-day base and the remaining-days denominator reflect
    the whole month, not just the days entered so far. Projection is additive only;
    concrete days win, and a scope with no shifts at all projects nothing."""
    projected = project_future_hours(hours_by_day, today, month_end) if month_end else {}
    eff_hours = dict(hours_by_day)
    eff_hours.update(projected)

    total_hours = sum(eff_hours.values())
    open_days = sorted(eff_hours.keys())

    def base_for(cat: str, d: date) -> float:
        if total_hours <= 0:
            return 0.0
        return float(monthly_by_cat.get(cat, 0) or 0) * (eff_hours.get(d, 0.0) / total_hours)

    def achieved_on(cat: str, d: date) -> float:
        a = actuals_by_day.get(d)
        if not a:
            return 0.0
        return achieved_for_cat(cat, a['prem'], a['byod'], a['upg'], a['acc'],
                                a.get('declared'))

    # The tenant's own axis and units, resolved ONCE per call. `_unit` is asked rather than read off
    # a module constant, so a declared money category rounds like money and a declared tally rounds
    # like a count.
    _axis = target_categories(catalog)
    _units = target_units(catalog)

    def maybe_round(cat: str, v: float) -> float:
        if round_counts and _units.get(cat) == 'count':
            return round(v, 1)
        return round(v, 2)

    # WHICH DAYS `achieved_mtd` IS THE SUM OF — recorded in the SAME loop that sums it, at full
    # precision (the `base_raw` reasoning below, for the same reason). ADDITIVE and read-only: the
    # drill-down (§CORE-17.3m) opens an achieved figure to these days, and the evidence has to be
    # the days the figure was ACTUALLY summed over — which is not the calendar, because the calendar
    # is the scope's OPEN days while this counts every day with sales, scheduled or not. Folding it
    # anywhere else would be a second answer to "which days is this".
    _evidence: dict = {}

    categories = {}
    for cat in _axis:
        monthly = float(monthly_by_cat.get(cat, 0) or 0)
        cum_base_yday = sum(base_for(cat, d) for d in open_days if d < today)
        cum_ach_yday = sum(achieved_on(cat, d) for d in open_days if d < today)
        shortfall = max(0.0, cum_base_yday - cum_ach_yday)

        scheduled_today = today in eff_hours
        base_today = base_for(cat, today) if scheduled_today else 0.0
        # Only give a target on a day this scope is actually scheduled. A rep on
        # their day off gets 0 today; the unmet shortfall is carried by `pace`
        # onto the days they do work, not dumped onto a day they aren't here.
        today_target = (base_today + shortfall) if scheduled_today else 0.0

        # MTD achieved counts every day with actuals up to today, including sales
        # on non-scheduled days (walk-ins / coverage shifts not in the schedule).
        achieved_mtd = 0.0
        for d in actuals_by_day:
            if d <= today:
                _a = achieved_on(cat, d)
                achieved_mtd += _a
                _evidence.setdefault(d, {})[cat] = _a

        need = max(0.0, monthly - achieved_mtd)
        days_left = sum(1 for d in open_days if d >= today)
        pace = (need / days_left) if days_left > 0 else need

        categories[cat] = {
            'unit': _units[cat],
            'monthly': maybe_round(cat, monthly),
            'achieved_mtd': maybe_round(cat, achieved_mtd),
            'need': maybe_round(cat, need),
            'base_today': maybe_round(cat, base_today),
            'today_target': maybe_round(cat, today_target),
            'pace': maybe_round(cat, pace),
            'open_days_left': days_left,
        }

    # Device SET-UP FEE (MTD) — the set-up-fee portion of the accessories achieved, exposed SEPARATELY
    # (the accessory attainment above already COUNTS it; this reports it on its own line/column so nothing
    # is silently blended). Same MTD date-cut as achieved_mtd.
    if 'accessories' in categories:
        setup_mtd = 0.0
        for d in actuals_by_day:
            if d <= today:
                setup_mtd += (actuals_by_day.get(d) or {}).get('setup', 0.0)
        categories['accessories']['setup_fee_mtd'] = round(setup_mtd, 2)

    # Day-by-day calendar (combined across categories)
    calendar = []
    for d in open_days:
        a = actuals_by_day.get(d)
        has_actual = a is not None and d <= today
        row = {
            'date': d.isoformat(),
            'hours': round(eff_hours.get(d, 0.0), 2),
            # FULL PRECISION, same reasoning as `base_raw` below: the hours drill-down (§CORE-17.3m)
            # adds these up against `scheduled_hours_total`, which is one round over the raw sum.
            'hours_raw': eff_hours.get(d, 0.0),
            'is_today': d == today,
            'is_past': d < today,
            'projected': d in projected,  # estimated from the weekly pattern, not yet scheduled
            'cats': {},
        }
        for cat in _axis:
            _b = base_for(cat, d)
            _a = achieved_on(cat, d)
            row['cats'][cat] = {
                'base': round(_b, 2),
                'achieved': round(_a, 2) if has_actual else None,
                # FULL PRECISION, for summing only. A week window that added up the DISPLAYED
                # per-day figures drifted 0.15 off a 600 monthly target over 31 days, so the weeks
                # on screen would not have added up to the month — the exact disagreement
                # grain_windows exists to make impossible. Rounding happens once, per window.
                'base_raw': _b,
                'achieved_raw': _a if has_actual else None,
            }
        calendar.append(row)

    concrete_hours_total = sum(hours_by_day.values())
    return {
        # Real StoreOps hours actually on the schedule for this scope.
        'scheduled_hours_total': round(concrete_hours_total, 2),
        # Concrete + projected — the denominator the per-day base and pace spread over.
        'effective_hours_total': round(total_hours, 2),
        'projected_hours': round(sum(projected.values()), 2),
        'projected_open_days': len(projected),
        'concrete_open_days': len(hours_by_day),
        'open_days_total': len(open_days),
        'has_schedule': concrete_hours_total > 0,  # False → no schedule to weight or project from
        'today': today.isoformat(),
        'categories': categories,
        'calendar': calendar,
        # ADDITIVE (§CORE-17.3m): the days `achieved_mtd` was summed over, per category, at full
        # precision. Recorded by the same loop, so it cannot be a different set of days.
        'achieved_days': [{'date': d.isoformat(),
                           'scheduled': d in eff_hours,
                           'cats': dict(_evidence[d])}
                          for d in sorted(_evidence)],
    }


def derive_monthly_by_cat(target_row: dict, byod_pct_default: float,
                          target_lines: list[dict] | None = None,
                          catalog: list[dict] | None = None) -> dict:
    """Build the monthly target dict. THE ONE FOLD from stored rows to a per-category target.

    TWO PHYSICAL STORES, ONE READER — the same shape as closing/metric_catalog.storage_for and the
    envelope_report.is_modern_row precedent (§29.7). The four built-ins keep living in their own
    columns on commcalc.targets, read byte-for-byte as before, so no existing tenant's target moves
    and no migration is needed to keep them working. A DECLARED category's target lives in a row of
    commcalc.target_line (mig 1074). No caller reads either store directly: everything downstream —
    compute_scope's per-day base, the catch-up shortfall, `need` and `pace` — consumes this dict and
    therefore works for a declared category the day it is declared, with no new arithmetic.

    BYOD target = activations_monthly * byod_pct/100 (KPI-derived; row override wins).

    `target_lines` are the month-grain rows for ONE store and period. A category with no row targets
    0, which reads as "no target set" rather than as a guess."""
    _row = target_row or {}
    # The column names are DEREFERENCED from BUILTIN_TARGET_COLUMN, not spelled here: this fold and
    # the carry-forward writer must agree about which column holds which category, and a second
    # spelling is how they would stop agreeing.
    activations = safe_float(_row.get(BUILTIN_TARGET_COLUMN['activations']))
    upgrades = safe_float(_row.get(BUILTIN_TARGET_COLUMN['upgrades']))
    accessories = safe_float(_row.get(BUILTIN_TARGET_COLUMN['accessories']))
    byod_pct = _row.get('byod_pct')
    byod_pct = safe_float(byod_pct) if byod_pct is not None else safe_float(byod_pct_default)
    out = {
        'activations': activations,
        'upgrades': upgrades,
        'byod': activations * byod_pct / 100.0,
        'accessories': accessories,
    }
    # The declared half. A row naming a built-in is IGNORED rather than allowed to override the
    # column: two homes for one number is the defect this fold exists to prevent, and the writer
    # refuses such a row at save so one can only arrive by hand.
    by_key = {}
    for r in (target_lines or []):
        key = (r or {}).get('metric_key')
        if not key or key in BUILTIN_UNITS:
            continue
        by_key[key] = safe_float((r or {}).get('target_value'))
    for cat in target_categories(catalog):
        if cat in out:
            continue
        out[cat] = by_key.get(cat, 0.0)
    return out


def grain_windows(scope_result: dict, grain: str = 'week', week_starts: int = 0) -> list[dict]:
    """Roll a compute_scope result up into day / week / month windows.

    THE OWNER ASKED FOR TARGETS PER DAY, WEEK AND MONTH. There is exactly one piece of arithmetic
    in this engine and it is monthly, spread over the scope's scheduled hours. A week target is
    therefore a WINDOW over that same spread — the sum of the per-day bases of the days in the week
    — and never a second calculation that could disagree with the month. That is the whole reason
    this reads `scope_result['calendar']` instead of recomputing anything: a second derivation of
    "what should this week be" is the duplicate defect, and it would drift the first time the
    schedule changed.

    Each window carries, per category: `base` (what the schedule says this window is worth),
    `achieved` (summed over the days that have actuals), `need` (what is still owed on the MONTH
    after this window — asked of the month figure, because a month's shortfall is not a property of
    a week) and `remaining` (base not yet achieved inside the window, floored at zero).

    `week_starts` is 0 for Monday through 6 for Sunday, matching date.weekday(). A retail week that
    starts on Sunday is a tenant preference, not a fact about arithmetic, so it is a parameter with
    a default rather than a constant somebody has to patch.

    Days the scope is not scheduled are absent from the calendar, so a window with no scheduled days
    is returned with zero base rather than omitted — "nothing was planned this week" is an answer,
    and dropping the row would read as "this week does not exist"."""
    if grain not in GRAINS:
        raise ValueError(f"grain must be one of {GRAINS}, got {grain!r}")
    calendar = list((scope_result or {}).get('calendar') or [])
    cats = ((scope_result or {}).get('categories') or {})
    axis = _axis_of_scope(scope_result)

    def key_of(d: date):
        if grain == 'day':
            return d
        if grain == 'month':
            return (d.year, d.month)
        # The window's own first day, so the label is a real date rather than an ISO week number
        # nobody reads off a calendar.
        return d - timedelta(days=(d.weekday() - week_starts) % 7)

    buckets: dict = {}
    order: list = []
    for row in calendar:
        d = _as_date(row.get('date'))
        if d is None:
            continue
        k = key_of(d)
        if k not in buckets:
            buckets[k] = {'days': [], 'cats': {c: {'base': 0.0, 'achieved': 0.0,
                                                   'has_actual': False} for c in axis}}
            order.append(k)
        b = buckets[k]
        b['days'].append(d)
        for c in axis:
            cell = (row.get('cats') or {}).get(c) or {}
            # Sum at full precision (see base_raw in compute_scope), round once per window below.
            b['cats'][c]['base'] += safe_float(cell.get('base_raw', cell.get('base')))
            ach = cell.get('achieved')
            if ach is not None:
                b['cats'][c]['achieved'] += safe_float(cell.get('achieved_raw', ach))
                b['cats'][c]['has_actual'] = True

    out = []
    for k in order:
        b = buckets[k]
        days = sorted(b['days'])
        win = {
            'grain': grain,
            'start': days[0].isoformat(),
            'end': days[-1].isoformat(),
            'open_days': len(days),
            'cats': {},
        }
        for c in axis:
            cell = b['cats'][c]
            base = round(cell['base'], 2)
            ach = round(cell['achieved'], 2) if cell['has_actual'] else None
            win['cats'][c] = {
                'unit': (cats.get(c) or {}).get('unit', 'count'),
                'base': base,
                'achieved': ach,
                'remaining': round(max(0.0, base - (ach or 0.0)), 2),
                # The month's outstanding need, carried onto the window so a weekly screen can say
                # "this week is worth 140 and the month still owes 577" without doing its own sum.
                'month_need': (cats.get(c) or {}).get('need', 0.0),
            }
        out.append(win)
    return out


def backward_from_goal(goal: float, achieved: float, open_days_left: int,
                       open_days_per_week: float = 0.0) -> dict:
    """"What do I need to reach X?" — the owner's backward calculation, stated once.

    `goal` is the figure being aimed at, which is NOT always the stored target: the owner asked to
    be able to ask the question of an arbitrary level ("what would it take to hit 1,200?"). So the
    goal is a parameter and the stored target is just the usual value passed in.

    Returns `need` (what is still owed), `per_day` and `per_week` (the flat rate that closes it over
    the days actually left). With no open days left, `per_day` is None rather than the need itself or
    an infinity: the honest answer to "what per day do I need with no days remaining" is that there
    is no such rate, and a caller showing the need as a daily figure would be stating something
    false. Over-achievement returns a need of 0 and rates of 0, never a negative."""
    need = max(0.0, safe_float(goal) - safe_float(achieved))
    days = int(open_days_left or 0)
    per_day = round(need / days, 2) if days > 0 else None
    per_week = None
    if per_day is not None and (open_days_per_week or 0) > 0:
        per_week = round(per_day * safe_float(open_days_per_week), 2)
    return {'goal': round(safe_float(goal), 2), 'achieved': round(safe_float(achieved), 2),
            'need': round(need, 2), 'open_days_left': days,
            'per_day': per_day, 'per_week': per_week}


def reps_in_scope(shifts: list[dict], actuals: list[dict], store_code: str) -> list[str]:
    """Distinct rep names that either worked or sold at a store (for rep breakdown)."""
    sc = (store_code or '').strip().upper()
    # Keyed by name_key so 'Diana Antunez' (schedule) and 'Antunez, Diana' (POS) list ONCE — the
    # schedule spelling wins the display because shifts are scanned first.
    names: dict[str, str] = {}
    for s in shifts:
        if s.get('is_deleted'):
            continue
        if (s.get('store_code') or '').strip().upper() != sc:
            continue
        n = (s.get('employee_name') or '').strip()
        if n:
            names.setdefault(name_key(n), n)
    for a in actuals:
        if (a.get('store_code') or '').strip().upper() != sc:
            continue
        n = (a.get('rep_name') or '').strip()
        if n:
            names.setdefault(name_key(n), n)
    return sorted(names.values(), key=lambda x: x.upper())


# ── Action Plan: turn the computed numbers into prioritized focus areas ────────
SEV_RANK = {'critical': 0, 'warning': 1, 'good': 2}
_CAT_LABEL = {'activations': 'Activations', 'upgrades': 'Upgrades',
              'byod': 'BYOD', 'accessories': 'Accessories'}


def _fmt_metric(v: float, unit: str) -> str:
    return f"${v:,.0f}" if unit == 'dollars' else f"{v:,.1f}"


def build_action_items(scope_result: dict, conversion: dict | None,
                       include_categories: bool = True,
                       rep_below_store: bool | None = None) -> list[dict]:
    """Translate a compute_scope result + conversion dict into prioritized,
    human-readable focus areas. Pure (no I/O). Each item is
    {severity: critical|warning|good, metric, title, detail}.

    Store scope gets per-category catch-up items (behind-pace vs the even daily
    rate) + conversion; rep scope gets conversion only — reps have no standalone
    per-category monthly target, so a per-category catch-up would compare the rep
    against the whole store number, which is misleading."""
    items: list[dict] = []

    if include_categories:
        cats = scope_result.get('categories', {}) or {}
        open_days_total = scope_result.get('open_days_total', 0) or 0
        # The axis comes off the scope result itself, never from a list kept here: compute_scope
        # already resolved this tenant's categories, so re-listing them would be the second copy
        # the lock fails the build on — and would silently skip every declared category.
        for cat in _axis_of_scope(scope_result):
            m = cats.get(cat)
            if not m:
                continue
            monthly = m.get('monthly', 0) or 0
            if monthly <= 0:
                continue  # no target set → nothing to be behind on
            unit = m.get('unit', 'count')
            need = m.get('need', 0) or 0
            pace = m.get('pace', 0) or 0
            achieved = m.get('achieved_mtd', 0) or 0
            days_left = m.get('open_days_left', 0) or 0
            label = _CAT_LABEL.get(cat, cat)
            if need <= 0:
                items.append({'severity': 'good', 'metric': cat,
                              'title': f'{label} target met',
                              'detail': f'{_fmt_metric(achieved, unit)} of '
                                        f'{_fmt_metric(monthly, unit)} — done for the month.'})
                continue
            # "Behind" = the pace needed for the rest of the month exceeds the even
            # daily rate (monthly spread over all open days). The bigger the ratio,
            # the more urgent.
            base_daily = (monthly / open_days_total) if open_days_total > 0 else 0
            ratio = (pace / base_daily) if base_daily > 0 else 0
            need_txt = f'{_fmt_metric(need, unit)} to go'
            day_word = 'day' if days_left == 1 else 'days'
            pace_txt = f'{_fmt_metric(pace, unit)}/day over {days_left} open {day_word} left'
            if ratio >= 1.5:
                items.append({'severity': 'critical', 'metric': cat,
                              'title': f'Behind on {label}',
                              'detail': f'{need_txt}; must do {pace_txt} — '
                                        f'{ratio:.1f}× the normal daily pace.'})
            elif ratio >= 1.15:
                items.append({'severity': 'warning', 'metric': cat,
                              'title': f'Behind on {label}',
                              'detail': f'{need_txt}; {pace_txt} ({ratio:.1f}× normal).'})
            else:
                items.append({'severity': 'good', 'metric': cat,
                              'title': f'{label} on track',
                              'detail': f'{need_txt}; {pace_txt}.'})

    # Conversion needs a denominator; skip when there are no bill-payments to measure against.
    if conversion and (conversion.get('billpays', 0) or 0) > 0:
        rate = conversion.get('rate', 0) or 0
        target = conversion.get('target', CONVERSION_TARGET) or CONVERSION_TARGET
        boxes = conversion.get('boxes', 0) or 0
        bp = conversion.get('billpays', 0) or 0
        if rate < target:
            sev = 'critical' if rate < target * 0.7 else 'warning'
            tail = ' and under the store average' if rep_below_store else ''
            items.append({'severity': sev, 'metric': 'conversion',
                          'title': f'Lift conversion{tail}',
                          'detail': f'{rate}% vs {target:.0f}% target ({boxes} boxes / '
                                    f'{bp} bill-pays) — convert more walk-ins into box sales.'})
        elif rep_below_store:
            items.append({'severity': 'warning', 'metric': 'conversion',
                          'title': 'Conversion below store',
                          'detail': f'{rate}% is under the store average — keep it above the store line.'})
        else:
            items.append({'severity': 'good', 'metric': 'conversion',
                          'title': 'Conversion on target',
                          'detail': f'{rate}% ≥ {target:.0f}% target ({boxes}/{bp}).'})

    items.sort(key=lambda it: SEV_RANK.get(it['severity'], 9))
    return items


# ── AREA / SPAN ROLL-UP (owner directive 2026-08-03) ──────────────────────────
# "under my targets the dm should have a collective target for all the stores assigned to them with a
# drill down for each area assigning them per store."
#
# A district manager's number is the SUM of the stores in their span — nothing new is derived, nothing
# is re-weighted, and no per-store target is changed. `aggregate_stores` is deliberately a plain sum of
# the SAME per-store rows the drill-down renders, so the invariant the harness asserts —
# collective[cat][metric] == Σ store[cat][metric] — holds BY CONSTRUCTION rather than by luck. That
# matters because the alternative (recomputing the aggregate from the raw shifts/actuals with the span
# as one big scope) would legitimately disagree with the store rows it sits above, and a DM cannot act
# on a total that does not reconcile to its own drill-down.
#
# PURE (no I/O). The caller decides WHICH stores are in scope (the scope_keyset span) — this function
# never sees a permission, a token or an org.
def attainment_pct(monthly, achieved):
    """How much of a target has been achieved, as a PERCENT, or None when there is no target to be a
    percent OF. THE ONE FORMULA (owner directive 2026-09-20, "one fact, one home"): the area roll-up
    below and the DM visit plan (`storevisit/visit_plan`, which ranks stores by how far under target
    they are) both read it here, so "attainment" cannot mean two things in two places. A zero or
    missing monthly target returns None — never 0% (a store nobody set a target for is not a store
    failing its target) and never 100%. PURE."""
    m = safe_float(monthly)
    if not m or m <= 0:
        return None
    return round(100.0 * safe_float(achieved) / m, 1)


def attainment_fraction(monthly, achieved):
    """`attainment_pct` as a 0–1 fraction (1.0 = on target), or None. Same single formula — the visit
    plan scores on a fraction and must not re-divide. PURE."""
    pct = attainment_pct(monthly, achieved)
    return None if pct is None else pct / 100.0


def aggregate_stores(store_rows: list[dict]) -> dict:
    """Collective (area-level) target roll-up over already-computed per-store summary rows.

    `store_rows` = the `stores` list `/targets/{period}/summary` returns: each row carries
    `categories[cat] = {unit, monthly, achieved_mtd, need, today_target, pace, base_today, ...}`.

    Returns {stores, store_codes, categories, conversion, trending} where every category metric is the
    straight SUM across the input rows. `attainment_pct` is the only derived number (achieved ÷
    monthly), and `need_net` is reported ALONGSIDE the summed `need` — not instead of it — because the
    two answer different questions: Σneed is "what my stores collectively still owe" (a store already
    over target contributes 0), while need_net nets an over-performing store against a short one. Both
    are shown so nobody has to guess which definition a total used.
    """
    rows = list(store_rows or [])
    cats: dict = {}
    # Same reasoning as build_action_items: the axis is whatever the store rows carry, in the order
    # compute_scope produced, so an aggregate cannot omit a category the stores reported.
    _agg_axis, _agg_units = _axis_of_rows(rows)
    for cat in _agg_axis:
        acc = {'unit': _agg_units.get(cat, 'count'), 'monthly': 0.0, 'achieved_mtd': 0.0, 'need': 0.0,
               'today_target': 0.0, 'pace': 0.0, 'base_today': 0.0, 'setup_fee_mtd': 0.0,
               'stores_with_target': 0, 'stores_on_track': 0}
        for r in rows:
            m = ((r.get('categories') or {}).get(cat) or {})
            if not m:
                continue
            monthly = safe_float(m.get('monthly'))
            achieved = safe_float(m.get('achieved_mtd'))
            acc['monthly'] += monthly
            acc['achieved_mtd'] += achieved
            acc['need'] += safe_float(m.get('need'))
            acc['today_target'] += safe_float(m.get('today_target'))
            acc['pace'] += safe_float(m.get('pace'))
            acc['base_today'] += safe_float(m.get('base_today'))
            acc['setup_fee_mtd'] += safe_float(m.get('setup_fee_mtd'))
            if monthly > 0:
                acc['stores_with_target'] += 1
                if achieved >= monthly:
                    acc['stores_on_track'] += 1
        # HOW MANY DECIMALS, asked of the unit THESE ROWS reported (`_agg_units`, resolved above)
        # rather than of the built-in map. This line was `UNITS[cat]`, and `UNITS` is the BUILT-IN
        # axis — so a tenant with a category declared on Count Fields raised `KeyError` here and
        # took the whole area roll-up with it (and with it every manager report card, which rolls up
        # through this same function). Found 2026-10-25 by §CORE-17.3m, whose team-total fold calls
        # this for the declared axis too. NO FIGURE MOVES for a built-in: `_agg_units[cat]` IS
        # `BUILTIN_UNITS[cat]` for all four, because that is where `compute_scope` read it from —
        # asserted by harness_batch13_drilldown §Z13.
        dec = 1 if _agg_units.get(cat, 'count') == 'count' else 2
        for k in ('monthly', 'achieved_mtd', 'need', 'today_target', 'pace', 'base_today'):
            acc[k] = round(acc[k], dec)
        acc['setup_fee_mtd'] = round(acc['setup_fee_mtd'], 2)
        acc['need_net'] = round(max(0.0, acc['monthly'] - acc['achieved_mtd']), dec)
        acc['attainment_pct'] = attainment_pct(acc['monthly'], acc['achieved_mtd'])
        cats[cat] = acc

    # Area conversion = Σboxes ÷ Σbill-pays across the span (a ratio of sums, never a mean of ratios —
    # averaging store percentages would let a 2-transaction store outvote a 400-transaction one).
    boxes = sum(safe_float(((r.get('conversion') or {}).get('store') or {}).get('boxes')) for r in rows)
    billpays = sum(safe_float(((r.get('conversion') or {}).get('store') or {}).get('billpays')) for r in rows)
    target = CONVERSION_TARGET
    for r in rows:
        t = ((r.get('conversion') or {}).get('store') or {}).get('target')
        if t is not None:
            target = safe_float(t)
            break
    conv = {'boxes': int(boxes), 'billpays': int(billpays), 'target': target,
            'rate': round(100.0 * boxes / billpays, 1) if billpays > 0 else 0.0}

    return {
        'stores': len(rows),
        'store_codes': [str(r.get('store_code') or '') for r in rows if r.get('store_code')],
        'categories': cats,
        'conversion': conv,
        'trending': {
            'acc_sales': round(sum(safe_float(r.get('trending_acc_sales')) for r in rows), 2),
            'acc_target': round(sum(safe_float(r.get('trending_acc_target')) for r in rows), 2),
            'box': int(sum(safe_float(r.get('trending_box')) for r in rows)),
        },
        'scheduled_hours_total': round(sum(safe_float(r.get('scheduled_hours_total')) for r in rows), 2),
    }
