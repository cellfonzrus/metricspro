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
from datetime import date, timedelta

from app.modules.commcalc.calculator import safe_float

# Display order; accessories is a dollar amount, the rest are transaction counts.
CATEGORIES = ['activations', 'upgrades', 'byod', 'accessories']
UNITS = {'activations': 'count', 'upgrades': 'count', 'byod': 'count', 'accessories': 'dollars'}

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


def achieved_for_cat(cat: str, prem: float, byod: float, upg: float, acc: float) -> float:
    if cat == 'activations':
        return prem + byod
    if cat == 'byod':
        return byod
    if cat == 'upgrades':
        return upg
    if cat == 'accessories':
        return acc
    return 0.0


def scope_hours_by_day(shifts: list[dict], store_code: str, rep_name: str | None) -> dict:
    """Sum scheduled hours per day for a scope. rep_name=None → whole store."""
    out: dict[date, float] = {}
    sc = (store_code or '').strip().upper()
    rn = (rep_name or '').strip().upper()
    for s in shifts:
        if s.get('is_deleted'):
            continue
        if (s.get('store_code') or '').strip().upper() != sc:
            continue
        if rn and (s.get('employee_name') or '').strip().upper() != rn:
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
    rn = (rep_name or '').strip().upper()
    for a in actuals:
        if (a.get('store_code') or '').strip().upper() != sc:
            continue
        if rn and (a.get('rep_name') or '').strip().upper() != rn:
            continue
        d = _as_date(a.get('trans_date'))
        if not d:
            continue
        agg = out.setdefault(d, {'prem': 0.0, 'byod': 0.0, 'upg': 0.0, 'acc': 0.0,
                                 'box': 0.0, 'billpay': 0.0})
        agg['prem'] += safe_float(a.get('prem_count'))
        agg['byod'] += safe_float(a.get('byod_count'))
        agg['upg'] += safe_float(a.get('upg_count'))
        agg['acc'] += safe_float(a.get('acc_gp'))
        agg['box'] += safe_float(a.get('box_count'))
        agg['billpay'] += safe_float(a.get('billpay_count'))
    return out


def scope_conversion(actuals: list[dict], store_code: str, rep_name: str | None = None,
                     today=None) -> dict:
    """MTD conversion for a store (rep_name=None) or a rep within it.

    conversion = boxes sold (device-dept lines) ÷ bill-payments (walk-in recharges) × 100.
    Target = CONVERSION_TARGET%. Rows after `today` are excluded so it stays month-to-date."""
    sc = (store_code or '').strip().upper()
    rn = (rep_name or '').strip().upper()
    boxes = billpays = 0.0
    for a in actuals:
        if (a.get('store_code') or '').strip().upper() != sc:
            continue
        if rn and (a.get('rep_name') or '').strip().upper() != rn:
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
        return achieved_for_cat(cat, a['prem'], a['byod'], a['upg'], a['acc'])

    def maybe_round(cat: str, v: float) -> float:
        if round_counts and UNITS.get(cat) == 'count':
            return round(v, 1)
        return round(v, 2)

    categories = {}
    for cat in CATEGORIES:
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
                achieved_mtd += achieved_on(cat, d)

        need = max(0.0, monthly - achieved_mtd)
        days_left = sum(1 for d in open_days if d >= today)
        pace = (need / days_left) if days_left > 0 else need

        categories[cat] = {
            'unit': UNITS[cat],
            'monthly': maybe_round(cat, monthly),
            'achieved_mtd': maybe_round(cat, achieved_mtd),
            'need': maybe_round(cat, need),
            'base_today': maybe_round(cat, base_today),
            'today_target': maybe_round(cat, today_target),
            'pace': maybe_round(cat, pace),
            'open_days_left': days_left,
        }

    # Day-by-day calendar (combined across categories)
    calendar = []
    for d in open_days:
        a = actuals_by_day.get(d)
        has_actual = a is not None and d <= today
        row = {
            'date': d.isoformat(),
            'hours': round(eff_hours.get(d, 0.0), 2),
            'is_today': d == today,
            'is_past': d < today,
            'projected': d in projected,  # estimated from the weekly pattern, not yet scheduled
            'cats': {},
        }
        for cat in CATEGORIES:
            row['cats'][cat] = {
                'base': round(base_for(cat, d), 2),
                'achieved': round(achieved_on(cat, d), 2) if has_actual else None,
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
    }


def derive_monthly_by_cat(target_row: dict, byod_pct_default: float) -> dict:
    """Build the monthly target dict from a commcalc.targets row.
    BYOD target = activations_monthly * byod_pct/100 (KPI-derived; row override wins)."""
    activations = safe_float((target_row or {}).get('activations_monthly'))
    upgrades = safe_float((target_row or {}).get('upgrades_monthly'))
    accessories = safe_float((target_row or {}).get('accessories_monthly'))
    byod_pct = (target_row or {}).get('byod_pct')
    byod_pct = safe_float(byod_pct) if byod_pct is not None else safe_float(byod_pct_default)
    return {
        'activations': activations,
        'upgrades': upgrades,
        'byod': activations * byod_pct / 100.0,
        'accessories': accessories,
    }


def reps_in_scope(shifts: list[dict], actuals: list[dict], store_code: str) -> list[str]:
    """Distinct rep names that either worked or sold at a store (for rep breakdown)."""
    sc = (store_code or '').strip().upper()
    names: dict[str, str] = {}
    for s in shifts:
        if s.get('is_deleted'):
            continue
        if (s.get('store_code') or '').strip().upper() != sc:
            continue
        n = (s.get('employee_name') or '').strip()
        if n:
            names.setdefault(n.upper(), n)
    for a in actuals:
        if (a.get('store_code') or '').strip().upper() != sc:
            continue
        n = (a.get('rep_name') or '').strip()
        if n:
            names.setdefault(n.upper(), n)
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
        for cat in CATEGORIES:
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
