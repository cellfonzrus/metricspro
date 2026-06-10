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
from datetime import date

from app.modules.commcalc.calculator import safe_float

# Display order; accessories is a dollar amount, the rest are transaction counts.
CATEGORIES = ['activations', 'upgrades', 'byod', 'accessories']
UNITS = {'activations': 'count', 'upgrades': 'count', 'byod': 'count', 'accessories': 'dollars'}


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
        agg = out.setdefault(d, {'prem': 0.0, 'byod': 0.0, 'upg': 0.0, 'acc': 0.0})
        agg['prem'] += safe_float(a.get('prem_count'))
        agg['byod'] += safe_float(a.get('byod_count'))
        agg['upg'] += safe_float(a.get('upg_count'))
        agg['acc'] += safe_float(a.get('acc_gp'))
    return out


def compute_scope(
    monthly_by_cat: dict,
    hours_by_day: dict,
    actuals_by_day: dict,
    today: date,
    round_counts: bool = False,
):
    """Compute per-category target numbers + a day-by-day calendar for one scope."""
    total_hours = sum(hours_by_day.values())
    open_days = sorted(hours_by_day.keys())

    def base_for(cat: str, d: date) -> float:
        if total_hours <= 0:
            return 0.0
        return float(monthly_by_cat.get(cat, 0) or 0) * (hours_by_day.get(d, 0.0) / total_hours)

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

        base_today = base_for(cat, today) if today in hours_by_day else 0.0
        today_target = base_today + shortfall

        achieved_mtd = sum(achieved_on(cat, d) for d in open_days if d <= today)
        # include any actuals that may exist on non-scheduled days too
        achieved_all = 0.0
        for d in actuals_by_day:
            if d <= today:
                achieved_all += achieved_on(cat, d)
        achieved_mtd = max(achieved_mtd, achieved_all)

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
            'hours': round(hours_by_day.get(d, 0.0), 2),
            'is_today': d == today,
            'is_past': d < today,
            'cats': {},
        }
        for cat in CATEGORIES:
            row['cats'][cat] = {
                'base': round(base_for(cat, d), 2),
                'achieved': round(achieved_on(cat, d), 2) if has_actual else None,
            }
        calendar.append(row)

    return {
        'scheduled_hours_total': round(total_hours, 2),
        'open_days_total': len(open_days),
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
