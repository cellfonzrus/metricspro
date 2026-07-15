"""storeops PTO accrual engine — pure, DB-free functions.

"Paid Leave Accumulated" (PTO accrual): a per-employee, per-store COST computed from hours worked
(accrual) and time-off taken (draw-down), layered on a per-org / per-role / per-employee CONFIG
(RULE TWO — nothing hard-coded, see migration 403). Everything here is pure so it is unit-testable
without a database (see harness_pto_accrual.py) — the router (router.py) is the only place that
touches Supabase; it fetches rows and calls these functions.

This module NEVER changes a wage/payout number. It only produces an ADDITIVE cost line (Paid Leave
Accumulated) that the router hands to mod-commission's Store Expenses "system-line" endpoint.

Formula
-------
accrued_hours_raw = hours_worked * accrual_rate           (accrual_rate default 0.0385 ~= 80hr/2080hr)
balance_after_taken = prior_balance - taken_hours          (this period's usage draws the bank first)
accrued_hours = capped at max_accrual_hours (if set) so payable_balance never exceeds the cap;
                excess accrual is FORFEITED (use-it-or-lose-it), not carried anywhere
payable_balance = balance_after_taken + accrued_hours
cost  = accrued_hours * hourly_rate     when mode == 'accrue'  (book the expense as it accrues)
      = taken_hours   * hourly_rate     when mode == 'on_use'  (book the expense only when taken)

Per-store attribution
----------------------
'accrue' mode: accrued cost follows WHERE the labor happened — split across the stores an employee
worked this period, proportional to each store's share of their hours (a floater's cost lands where
they worked, same attribution /payroll-by-store already uses for wages).
'on_use' mode: the expense is booked only when time is actually taken, which has no work-store — it
lands at the employee's home store.
"""
from datetime import date
from calendar import monthrange
from typing import Dict, List, Optional

DEFAULT_CONFIG = {
    "enabled": True,
    "accrual_rate": 0.0385,          # PTO hours earned per hour worked (~80 hrs / 2080 hrs per year)
    "mode": "accrue",                # 'accrue' | 'on_use'
    "cost_basis": "payscale_rate",   # only basis implemented today; a config knob for future bases
    "max_accrual_hours": None,       # None = no cap
    "hours_per_pto_day": 8.0,        # calendar-day -> hours conversion for taken PTO
    "counts_as_pto_types": ["PTO"],  # time_off_requests.type values that draw the bank
}

_CFG_FIELDS = tuple(DEFAULT_CONFIG.keys())


# ── config layering: employee override > role override > org default > code default ───────────────
def resolve_effective_config(org_row: Optional[dict] = None, role_row: Optional[dict] = None,
                              employee_row: Optional[dict] = None) -> dict:
    """Merge the 3 config layers into one effective config dict. A layer's field is applied only when
    it is present AND not None (None on an override row means "inherit from the next layer down");
    the org layer falls back to DEFAULT_CONFIG for anything it doesn't set (e.g. the org row hasn't
    been seeded yet — the feature must degrade gracefully per contract §5)."""
    eff = dict(DEFAULT_CONFIG)
    for layer in (org_row, role_row, employee_row):
        if not layer:
            continue
        for f in _CFG_FIELDS:
            v = layer.get(f)
            if v is not None:
                eff[f] = v
    # normalize types defensively (rows come from JSON/DB, could be strings)
    eff["accrual_rate"] = float(eff["accrual_rate"])
    eff["hours_per_pto_day"] = float(eff["hours_per_pto_day"])
    eff["max_accrual_hours"] = None if eff["max_accrual_hours"] in (None, "") else float(eff["max_accrual_hours"])
    eff["mode"] = eff["mode"] if eff["mode"] in ("accrue", "on_use") else "accrue"
    eff["enabled"] = bool(eff["enabled"])
    types = eff.get("counts_as_pto_types") or ["PTO"]
    if isinstance(types, str):
        types = [types]
    eff["counts_as_pto_types"] = [str(t).strip().lower() for t in types if str(t).strip()]
    return eff


# ── period helpers ──────────────────────────────────────────────────────────────────────────────
def month_bounds(period: str) -> (date, date):
    """'YYYY-MM' -> (first_day, last_day) of that calendar month. Pure date math (no timezone
    parsing of a JS-style string), so the JS `new Date('YYYY-MM-DD')` UTC-parse pitfall doesn't
    apply here — this only ever runs in Python."""
    y, m = str(period).split("-")[:2]
    y, m = int(y), int(m)
    last_day = monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last_day)


def _parse_date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


# ── worked hours from shifts (same basis as GET /payroll-by-store: actual > scheduled) ─────────────
def hours_worked_from_shifts(shifts: List[dict]) -> Dict[str, Dict[str, float]]:
    """employee_id -> {store_code: hours}. A floater's hours land at the store they actually
    worked (the shift's own store_code), matching /payroll-by-store's attribution."""
    out: Dict[str, Dict[str, float]] = {}
    for s in shifts or []:
        eid = s.get("employee_id")
        store = (s.get("store_code") or "").strip()
        if not eid or not store:
            continue
        sched = float(s.get("scheduled_hours") or 0)
        act = float(s.get("actual_hours") or 0)
        hrs = act if act > 0 else sched
        out.setdefault(eid, {})
        out[eid][store] = out[eid].get(store, 0.0) + hrs
    return out


# ── taken PTO hours from time_off_requests, prorated to the period ─────────────────────────────────
def taken_hours_from_time_off(time_off_rows: List[dict], period_start: date, period_end: date,
                               pto_types: List[str], hours_per_pto_day: float) -> Dict[str, float]:
    """employee_id -> hours taken THIS period. Only 'approved' rows whose type is in pto_types
    (case-insensitive) count. A block spanning a month boundary is prorated: only the days that
    fall inside [period_start, period_end] count toward this period."""
    types = {str(t).strip().lower() for t in (pto_types or [])}
    out: Dict[str, float] = {}
    for r in time_off_rows or []:
        if str(r.get("status") or "").lower() != "approved":
            continue
        if str(r.get("type") or "").strip().lower() not in types:
            continue
        eid = r.get("employee_id")
        s = _parse_date(r.get("start_date"))
        e = _parse_date(r.get("end_date"))
        if not eid or not s or not e or e < s:
            continue
        lo = max(s, period_start)
        hi = min(e, period_end)
        if hi < lo:
            continue
        days = (hi - lo).days + 1
        out[eid] = out.get(eid, 0.0) + days * hours_per_pto_day
    return out


# ── main engine ──────────────────────────────────────────────────────────────────────────────────
def compute_pto(hours_by_employee_store: Dict[str, Dict[str, float]],
                 taken_by_employee: Dict[str, float],
                 rates: Dict[str, float],
                 cfg_by_employee: Dict[str, dict],
                 home_store_by_employee: Optional[Dict[str, str]] = None,
                 prior_balance_by_employee: Optional[Dict[str, float]] = None,
                 employee_names: Optional[Dict[str, str]] = None) -> dict:
    """Compute per-employee PTO accrual + a per-store cost rollup for one period.

    hours_by_employee_store: {employee_id: {store: hours_worked}}  (this period's worked hours)
    taken_by_employee:       {employee_id: hours_taken}            (this period's paid time off taken)
    rates:                   {employee_id: hourly_rate}            (payscale_rate — employees.pay_rate)
    cfg_by_employee:         {employee_id: effective_config}       (see resolve_effective_config)
    home_store_by_employee:  {employee_id: store}                  (attributes on_use-mode cost + any
                              taken-only employee who worked 0 hours this period to a store)
    prior_balance_by_employee: {employee_id: hours}                (running balance carried in; 0 default)

    Returns:
      {"employees": {eid: {..., "by_store": {store: {"accrued_hours","taken_hours","cost"}}}},
       "stores":    {store: {"accrued_hours","taken_hours","cost"}}}      (sum of every employee's by_store)

    An employee whose effective config has enabled=False is EXCLUDED entirely (no accrual, no cost,
    no ledger row) — "PTO accrual off" means this engine does nothing for them.
    """
    home_store_by_employee = home_store_by_employee or {}
    prior_balance_by_employee = prior_balance_by_employee or {}
    employee_names = employee_names or {}

    all_eids = set(hours_by_employee_store) | set(taken_by_employee) | set(cfg_by_employee)
    employees: Dict[str, dict] = {}
    stores: Dict[str, dict] = {}

    def _bump_store(store: str, accrued: float, taken: float, cost: float):
        if not store:
            return
        d = stores.setdefault(store, {"store": store, "accrued_hours": 0.0, "taken_hours": 0.0, "cost": 0.0})
        d["accrued_hours"] += accrued
        d["taken_hours"] += taken
        d["cost"] += cost

    for eid in sorted(all_eids):
        cfg = cfg_by_employee.get(eid) or DEFAULT_CONFIG
        if not cfg.get("enabled", True):
            continue
        by_store_hours = hours_by_employee_store.get(eid) or {}
        hours_worked = sum(by_store_hours.values())
        taken_hours = float(taken_by_employee.get(eid, 0.0))
        rate = float(rates.get(eid, 0.0))
        prior_balance = float(prior_balance_by_employee.get(eid, 0.0))
        home_store = home_store_by_employee.get(eid, "")

        accrual_rate = float(cfg["accrual_rate"])
        accrued_raw = hours_worked * accrual_rate
        balance_after_taken = prior_balance - taken_hours
        cap = cfg.get("max_accrual_hours")
        if cap is not None:
            room = max(0.0, cap - balance_after_taken)
            accrued_hours = min(accrued_raw, room)
        else:
            accrued_hours = accrued_raw
        capped = (accrued_raw - accrued_hours) > 1e-9
        payable_balance = balance_after_taken + accrued_hours

        mode = cfg.get("mode", "accrue")
        cost = (taken_hours * rate) if mode == "on_use" else (accrued_hours * rate)

        # ── per-store split (single source of truth for both the employee record and the rollup) ──
        emp_by_store: Dict[str, dict] = {}

        def _emp_store(store: str) -> dict:
            return emp_by_store.setdefault(store, {"accrued_hours": 0.0, "taken_hours": 0.0, "cost": 0.0})

        if mode == "accrue":
            if hours_worked > 0:
                for store, hrs in by_store_hours.items():
                    share = hrs / hours_worked
                    d = _emp_store(store)
                    d["accrued_hours"] += accrued_hours * share
                    d["cost"] += cost * share
            elif home_store and accrued_hours != 0:
                d = _emp_store(home_store)
                d["accrued_hours"] += accrued_hours
                d["cost"] += cost
            if taken_hours and home_store:
                _emp_store(home_store)["taken_hours"] += taken_hours   # informational only in accrue mode
        else:  # on_use — cost only realizes where the time is actually taken (home store)
            if taken_hours or cost:
                if home_store:
                    d = _emp_store(home_store)
                    d["taken_hours"] += taken_hours
                    d["cost"] += cost
            if hours_worked > 0 and accrued_hours != 0:
                for store, hrs in by_store_hours.items():   # informational only — no cost in on_use mode
                    share = hrs / hours_worked
                    _emp_store(store)["accrued_hours"] += accrued_hours * share

        for store, d in emp_by_store.items():
            _bump_store(store, d["accrued_hours"], d["taken_hours"], d["cost"])

        employees[eid] = {
            "employee_id": eid,
            "name": employee_names.get(eid, ""),
            "store": home_store,
            "hours_worked": round(hours_worked, 4),
            "accrued_hours": round(accrued_hours, 4),
            "accrued_hours_precap": round(accrued_raw, 4),
            "capped": capped,
            "taken_hours": round(taken_hours, 4),
            "rate": round(rate, 4),
            "mode": mode,
            "prior_balance": round(prior_balance, 4),
            "payable_balance": round(payable_balance, 4),
            "cost": round(cost, 2),
            "by_store": {s: {"accrued_hours": round(d["accrued_hours"], 4),
                              "taken_hours": round(d["taken_hours"], 4),
                              "cost": round(d["cost"], 2)} for s, d in emp_by_store.items()},
        }

    for d in stores.values():
        d["accrued_hours"] = round(d["accrued_hours"], 4)
        d["taken_hours"] = round(d["taken_hours"], 4)
        d["cost"] = round(d["cost"], 2)

    return {"employees": employees, "stores": stores}


# ── ledger row shaping (pure — the router just inserts what this returns) ──────────────────────────
def ledger_rows(org_id: str, period: str, result: dict, run_by: Optional[str] = None) -> List[dict]:
    """One ledger row per (employee, store) touched this period (from `emp["by_store"]`), each
    carrying that employee's OVERALL payable_balance as of this period (balance is per-employee, not
    per-store, so every row for the same employee repeats the same balance_hours — intentional, it's
    the running total the company owes that person). An employee with zero activity this period
    (nothing accrued, nothing taken) produces no rows — nothing changed, nothing to persist; the next
    period's prior-balance lookup sums all history so skipping a no-op period is safe."""
    rows: List[dict] = []
    for eid, emp in (result.get("employees") or {}).items():
        by_store = emp.get("by_store") or {}
        if not by_store:
            continue
        for store, d in by_store.items():
            if d["accrued_hours"] == 0 and d["taken_hours"] == 0 and d["cost"] == 0:
                continue
            rows.append({
                "org_id": org_id, "period": period, "employee_id": eid, "store": store or None,
                "accrued_hours": d["accrued_hours"], "taken_hours": d["taken_hours"], "cost": d["cost"],
                "balance_hours": emp["payable_balance"], "mode": emp["mode"], "run_by": run_by,
            })
    return rows


def expense_cells_from_stores(stores: Dict[str, dict]) -> List[dict]:
    """Store rollup -> the `cells` payload for POST .../expenses/{period}/system-line. Includes every
    store the rollup touched even at cost 0 (so a store that had a cost last run but none this run
    gets its system line cleared to 0 on the idempotent upsert, not left stale)."""
    return [{"store": s, "amount": d["cost"]} for s, d in sorted(stores.items())]
