"""storeops Salary Pay-Basis engine — pure, DB-free functions (mirrors payroll_expenses.py's own
"pure math, router does I/O" convention, unit-testable without a database — see
harness_payroll_salary.py).

OWNER DIRECTIVE 2026-07-27 (verbatim): "in hr module the payroll set upo is currently per hour, need
to have the option to set up flat weekly or monthly salary or annual salary for the employees and
then calculate what is payable per week or biweekly as set up for the company."

WHAT THIS MODULE IS THE ONE SHARED IMPLEMENTATION POINT FOR (so the mig-407 RPC payroll path and the
legacy Python payroll path — both in storeops/router.py's GET /payroll and GET /payroll-by-store —
can never disagree on a salaried employee's pay): the conversion table, the period-boundary walk, the
calendar-day proration, and the store-allocation split. Both router.py's hourly-aggregation branches
already converge into the SAME `rows` / `by_store` structures before either endpoint returns — the
functions below are called EXACTLY ONCE per request, AFTER that convergence, as a pure post-processing
step over already-computed hourly figures. They never touch hours math.

┌─ CONVERSION TABLE (RULE TWO, cents HALF_UP, `convert_to_period_pay`) ──────────────────────────────┐
│ pay_basis   | company pay_period_type = 'weekly'  | company pay_period_type = 'biweekly'           │
│ 'weekly'    | pay_amount × 1                       | pay_amount × 2                                │
│ 'monthly'   | pay_amount × 12 / 52                 | pay_amount × 12 / 26                           │
│ 'annual'    | pay_amount / 52                      | pay_amount / 26                                │
│ 'hourly'    | n/a — untouched, uses employees.pay_rate × hours exactly as before this feature       │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
The result is "period_pay": the amount payable for ONE full company pay period — the number the
setup-UI's live preview shows ("$52,000/yr = $1,000.00 per weekly period").

┌─ PARTIAL PERIODS (calendar-day proration, `derive_salary_pay`) ────────────────────────────────────┐
│ For a report range [lo, hi] (inclusive), walk every company pay period that overlaps it (via a      │
│ READ-ONLY IMPORT of core.router.pay_period_for/_pp_settings — never reimplemented; the SAME         │
│ established pattern storeops/router.py's own `_who_for_log` already uses for a core.router import,  │
│ and the SAME thing frontend/.../storeops/lib/pay-period.ts's docstring explicitly calls out as the   │
│ reason it does NOT reimplement the boundary/anchor math client-side). For each overlapping period,   │
│ clip it to BOTH the employee's employment window (hire_date..termination_date) AND the report range  │
│ itself, then pay period_pay × (days-in-the-clipped-window / days-in-the-full-period). This ONE rule  │
│ covers two cases the owner asked for with the same formula: a mid-period hire/termination, AND a     │
│ report range that isn't an integer number of pay periods (e.g. a calendar-month view — 28-31 days is │
│ never evenly divisible by 7 or 14, so a month view legitimately shows a proportional partial amount  │
│ at each edge, not a whole extra/missing period). Every period's rounded amount SUMS EXACTLY to the   │
│ returned total (cents HALF_UP per period, no further rounding on the sum) — a UI drill-down of the    │
│ `periods` detail always foots to `amount`, and Store Expenses' allocated total always foots to the    │
│ Payroll Report's per-employee total (see `allocate_across_stores`).                                   │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘

┌─ STORE ALLOCATION (documented default, owner-confirmable, `allocate_across_stores`) ────────────────┐
│ A salaried employee's derived period pay is split across the stores they actually worked in range,   │
│ proportional to their WORKED HOURS share there (same hourly-basis hours the report already computes  │
│ — a salaried employee who split time 60/40 between two stores gets their pay split 60/40 too). An     │
│ employee with ZERO recorded hours anywhere in range (pure salary, never punches/scheduled — plausible │
│ for e.g. a market manager) attributes 100% to their home_store; if even home_store is blank, the      │
│ whole amount lands in an explicit 'Unassigned' bucket rather than silently vanishing from Store        │
│ Expenses (Gate-1 F1 fix, 2026-07-27). This is the DOCUMENTED DEFAULT the dispatch asked to implement,  │
│ flagged owner-confirmable in docs/handoffs/people.md — not the only defensible choice (a flat          │
│ 100%-home-store rule for every salaried employee regardless of hours would be the other obvious one). │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘

┌─ ZERO-ACTIVITY SALARIED EMPLOYEES (Gate-1 F1 fix, 2026-07-27, `synthesize_zero_activity_rows`) ─────┐
│ A salaried employee who has NO shift and NO punch this period (e.g. a market manager who never        │
│ clocks in) still earns their salary — GET /payroll's `rows` come from `summary`, which is built ONLY  │
│ from activity (shifts/timelog), so such an employee had NO row for `apply_to_payroll_rows` to         │
│ override, and silently vanished from the Payroll Report while still appearing (correctly) in          │
│ GET /payroll-by-store and GET /compensation, which iterate the full employee roster instead. This      │
│ function SYNTHESIZES a 0-hours row (store = home_store, or 'Unassigned' if even that is blank) for     │
│ every salaried employee with a usable pay_amount who has no existing row — called AFTER               │
│ apply_to_payroll_rows, so the two never disagree on the derived figure (both go through the SAME       │
│ `_salary_pay_fields` helper).                                                                          │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘

HOURLY BYTE-IDENTICAL GUARANTEE: every function below either (a) returns `None`/leaves a row's dict
UNCHANGED (same object, not even a shallow copy) when `pay_basis == 'hourly'` or the employee's
`pay_basis` column isn't even present in the row passed in (pre-migration-416/417 database, or a
caller that hasn't widened its SELECT), or (b) is simply never called (`by_eid`/`salaried` maps come up
empty for an all-hourly tenant). A genuinely-hourly employee's row therefore never enters ANY new code
branch — the byte-identical claim is structural, not "we tried to leave it alone".
"""
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta

PAY_BASES = ("hourly", "weekly", "monthly", "annual")
SALARY_BASES = ("weekly", "monthly", "annual")

# The widened-SELECT column list every router.py/hr/router.py call site adds to its base `employees`
# fields (migrations 416 + 417). Centralized here so there is exactly ONE list to keep in sync.
#
# IMPORTANT (Gate-1 N1, 2026-07-27): this is ONE combined select — pay_basis/pay_amount (416) AND
# hire_date/termination_date (417, hire_date pre-existed 077 but is requested here alongside the
# other three) all in a SINGLE query string. PostgREST/Postgres reject the WHOLE select if ANY named
# column is missing, and every call site's try/except (see router.py `_employees_with_pay_fields`)
# falls back to the caller's base fields ALONE on that failure — meaning if migration 416 has run but
# 417 has NOT, the combined select still fails (termination_date doesn't exist yet), so pay_basis/
# pay_amount/hire_date are ALSO unavailable and the ENTIRE salary feature is a no-op. Hire-side
# proration does NOT "already work" off 416 alone — nothing in this feature is reachable until BOTH
# 416 AND 417 have run. (This corrects an earlier, incorrect claim in migration 416's own header and
# in docs/handoffs/people.md's PENDING SQL section that hire-side proration worked off 416 alone.)
PAY_FIELDS = "pay_basis,pay_amount,hire_date,termination_date"

# Defensive cap on how many pay periods derive_salary_pay will walk for one range (Gate-1 N2,
# 2026-07-27). The SHORTEST reach is the weekly (7-day) period type: 5000 * 7 = 35,000 days, ~96
# years — no real payroll report range should ever approach that (biweekly reaches ~192 years at the
# same period count). This exists only to bound a malformed/absurd date range (never crash, never
# spin forever), not as a realistic limit. See pay_periods_overlapping's `truncated` return value,
# propagated into derive_salary_pay's result and surfaced as a salary_note by callers — a truncated
# calculation is flagged, never silently under-summed.
MAX_PERIODS_WALKED = 5000


def _money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_date(v):
    """'YYYY-MM-DD...' (or a date object) -> date, or None. Never raises."""
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def resolve_pay_basis(emp: dict):
    """(basis, amount_or_None) off an employee row. Any unrecognized/blank pay_basis value degrades
    SAFELY to 'hourly' (never crashes on bad data, never invents a pay figure from garbage) —
    'hourly' is the universal always-safe default the rest of this module is a no-op for."""
    basis = str((emp or {}).get("pay_basis") or "hourly").strip().lower()
    if basis not in PAY_BASES:
        basis = "hourly"
    amount = (emp or {}).get("pay_amount")
    try:
        amount = float(amount) if amount not in (None, "") else None
    except (TypeError, ValueError):
        amount = None
    return basis, amount


def period_length_days(pay_period_type) -> int:
    """Matches core.router.pay_period_for's own `length = 14 if pay_period_type == 'biweekly' else 7`."""
    return 14 if pay_period_type == "biweekly" else 7


def convert_to_period_pay(pay_basis, pay_amount, pay_period_type):
    """The conversion table above, cents HALF_UP. None for pay_basis='hourly' (not applicable) or a
    non-positive/unparseable pay_amount (caller then leaves the employee's existing hourly-computed
    figure untouched rather than silently showing $0)."""
    if pay_basis not in SALARY_BASES:
        return None
    try:
        amt = Decimal(str(pay_amount))
    except Exception:
        return None
    if amt <= 0:
        return None
    biweekly = (pay_period_type == "biweekly")
    if pay_basis == "weekly":
        factor = Decimal(2) if biweekly else Decimal(1)
    elif pay_basis == "monthly":
        factor = (Decimal(12) / Decimal(26)) if biweekly else (Decimal(12) / Decimal(52))
    else:  # annual
        factor = (Decimal(1) / Decimal(26)) if biweekly else (Decimal(1) / Decimal(52))
    return float((amt * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def tenant_pay_period_settings(tenant_row):
    """Read-only wrap of core.router._pp_settings — the SAME real per-tenant pay-period resolution
    every other surface reads (GET /tenant-settings, storeops/lib/pay-period.ts's own documented
    reason for NOT reimplementing this client-side). `tenant_row` is a (possibly empty/None) dict off
    storeops.tenants; missing fields fall back to core's own defaults (weekly, Monday week)."""
    from app.modules.core.router import _pp_settings
    return _pp_settings(tenant_row or {})


def pay_periods_overlapping(settings, lo: date, hi: date, max_periods: int = MAX_PERIODS_WALKED):
    """Every FULL company pay period ({start,end,payday} ISO strings, from core.router.pay_period_for
    — imported, never reimplemented) that overlaps [lo, hi] inclusive, walking forward period-by-
    period from the period containing `lo`. Returns (periods, truncated) — `truncated` is True only if
    `max_periods` genuinely wasn't enough to reach `hi` (see MAX_PERIODS_WALKED's docstring — a
    defensive cap against a malformed/absurd range, not a realistic limit for a real payroll report),
    so a caller can surface that explicitly rather than silently under-summing a truncated result."""
    from app.modules.core.router import pay_period_for
    out = []
    cur = pay_period_for(settings, lo)
    n = 0
    while n < max_periods:
        out.append(cur)
        cur_end = date.fromisoformat(cur["end"])
        if cur_end >= hi:
            return out, False
        cur = pay_period_for(settings, cur_end + timedelta(days=1))
        n += 1
    return out, True


def derive_salary_pay(pay_basis, pay_amount, settings, lo: date, hi: date,
                       hire_date: date = None, termination_date: date = None):
    """The period-converted salary pay for [lo, hi] inclusive, calendar-day-prorated against both the
    employee's employment window and the report range itself (see module docstring). Returns None if
    pay_basis is 'hourly' or pay_amount is unusable — callers then leave the row's existing
    hours×rate figure untouched. Result includes `truncated` (see pay_periods_overlapping) — always
    False in practice, propagated for defense in depth."""
    period_pay = convert_to_period_pay(pay_basis, pay_amount, settings.get("pay_period_type"))
    if period_pay is None or lo is None or hi is None or lo > hi:
        return None
    period_pay_dec = Decimal(str(period_pay))   # re-Decimal'd for exact per-period proration math below
    plen = period_length_days(settings.get("pay_period_type"))
    periods, truncated = pay_periods_overlapping(settings, lo, hi)
    total = Decimal("0.00")
    detail = []
    any_prorated = False
    for p in periods:
        p_start, p_end = date.fromisoformat(p["start"]), date.fromisoformat(p["end"])
        eff_start = max(p_start, lo, hire_date) if hire_date else max(p_start, lo)
        eff_end = min(p_end, hi, termination_date) if termination_date else min(p_end, hi)
        if eff_start > eff_end:
            continue
        days = (eff_end - eff_start).days + 1
        prorated = days != plen
        amt = (period_pay_dec * Decimal(days) / Decimal(plen)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total += amt
        any_prorated = any_prorated or prorated
        detail.append({"period_start": p["start"], "period_end": p["end"], "days_counted": days,
                        "period_length_days": plen, "amount": float(amt), "prorated": prorated})
    return {"amount": float(total), "period_pay": period_pay,
            "pay_period_type": settings.get("pay_period_type") or "weekly",
            "periods": detail, "prorated": any_prorated, "truncated": truncated}


def allocate_across_stores(store_hours: dict, total_amount: float, home_store):
    """Proportional-to-worked-hours store allocation (see module docstring). Cents-HALF_UP per store
    with the remainder fixed up on the LAST (smallest-share) store so the allocation sums EXACTLY to
    total_amount — a Store Expenses reconciliation must never be a penny short/over vs. the Payroll
    Report total for the same employee. Zero recorded hours anywhere -> 100% to home_store; if even
    home_store is blank, the whole amount lands in an explicit 'Unassigned' bucket (Gate-1 F1 —
    the pay must land somewhere visible, never silently vanish)."""
    total = _money(total_amount)
    hours_total = sum(v for v in (store_hours or {}).values() if v)
    if hours_total <= 0:
        store = (home_store or "").strip() or "Unassigned"
        return {store: float(total)}
    items = sorted(((s, h) for s, h in (store_hours or {}).items() if h and s), key=lambda kv: -kv[1])
    out = {}
    running = Decimal("0.00")
    for i, (store, hrs) in enumerate(items):
        if i == len(items) - 1:
            amt = total - running
        else:
            amt = (total * Decimal(str(hrs)) / Decimal(str(hours_total))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        out[store] = float(amt)
        running += amt
    return out


def accumulate(d: dict, key, subkey, val: float):
    """Tiny shared bookkeeping helper: d[key][subkey] += val, creating either level as needed. Used by
    router.py's GET /payroll-by-store to gather per-(employee, store) hours AND dollars — but ONLY for
    the small subset of employees who are actually salaried (see apply_to_by_store), so this is a
    no-op cost center for an all-hourly tenant."""
    m = d.setdefault(key, {})
    m[subkey] = m.get(subkey, 0.0) + (val or 0.0)


def _salary_pay_fields(emp: dict, settings, lo: date, hi: date):
    """THE single point both apply_to_payroll_rows (existing rows) and synthesize_zero_activity_rows
    (missing rows) call, so they can never disagree on what a salaried employee's overlay looks like.
    Returns (basis, overlay) — `overlay` is a dict of fields to merge onto a payroll row:
      - basis == 'hourly': ({}), caller leaves the row alone entirely.
      - a non-hourly basis with no usable pay_amount (misconfigured): {pay_basis, pay_amount,
        salary_note} — no pay/hours fields touched, so the row's EXISTING pay figure (hourly-computed,
        likely $0 for a never-given-a-rate salaried employee) stands, never silently overwritten.
      - a non-hourly basis with a usable pay_amount: the full derived overlay (pay_basis, pay_amount,
        salary_period_pay, salary_pay_period_type, salary_periods, salary_prorated, scheduled_pay,
        actual_pay — the last two are what actually change what a rep is paid)."""
    basis, amount = resolve_pay_basis(emp)
    if basis == "hourly":
        return basis, {}
    if amount is None or amount <= 0:
        return basis, {
            "pay_basis": basis, "pay_amount": amount,
            "salary_note": (f"pay_basis is '{basis}' but no pay_amount is configured — showing "
                             f"the hourly-computed figure (likely $0 with no pay_rate set either) "
                             f"until a pay_amount is set."),
        }
    derived = derive_salary_pay(basis, amount, settings, lo, hi,
                                 parse_date(emp.get("hire_date")), parse_date(emp.get("termination_date")))
    if derived is None:
        return basis, {"pay_basis": basis, "pay_amount": amount}
    overlay = {
        "pay_basis": basis, "pay_amount": amount,
        "salary_period_pay": derived["period_pay"], "salary_pay_period_type": derived["pay_period_type"],
        "salary_periods": derived["periods"], "salary_prorated": derived["prorated"],
        "scheduled_pay": derived["amount"], "actual_pay": derived["amount"],
    }
    if derived.get("truncated"):
        overlay["salary_note"] = ("period calculation was truncated (report range spans an unusually "
                                   "large number of pay periods) — the shown figure may undercount.")
    return basis, overlay


def apply_to_payroll_rows(rows, employees, settings, lo: date, hi: date):
    """GET /payroll's post-aggregation salary override (Deliverable 3). For each row whose canonical
    employee has a non-hourly pay_basis, replaces scheduled_pay/actual_pay with the derived salary
    figure (hours are left exactly as the hourly-basis aggregation already computed — still
    displayed) and adds pay_basis/salary_* metadata fields (via `_salary_pay_fields`, the SAME helper
    `synthesize_zero_activity_rows` uses for a missing row). A row for an hourly (or unresolved)
    employee is returned as the SAME object, unmodified — the structural byte-identical guarantee
    (see module docstring). Does NOT add a row for a salaried employee with no existing activity —
    see synthesize_zero_activity_rows, always called immediately after this in router.py."""
    by_eid = {e.get("employee_id"): e for e in (employees or ()) if e.get("employee_id") and "pay_basis" in e}
    if not by_eid or lo is None or hi is None:
        return rows
    out = []
    for r in rows:
        emp = by_eid.get(r.get("employee_id"))
        if emp is None:
            out.append(r)
            continue
        basis, overlay = _salary_pay_fields(emp, settings, lo, hi)
        if basis == "hourly":
            out.append(r)
            continue
        nr = dict(r)
        nr.update(overlay)
        out.append(nr)
    return out


def synthesize_zero_activity_rows(rows, employees, settings, lo: date, hi: date):
    """Gate-1 F1 fix (2026-07-27, MAJOR): a salaried employee with NO shift/punch this period has NO
    row in `rows` for apply_to_payroll_rows to override (GET /payroll's rows come from activity-driven
    aggregation) — they still earn their salary, and were previously silently missing from the Payroll
    Report while correctly present in GET /payroll-by-store and GET /compensation (which iterate the
    full roster). Appends ONE synthesized row (0 hours/shifts, derived period pay via the SAME
    `_salary_pay_fields` helper apply_to_payroll_rows uses, store = home_store or 'Unassigned' if even
    that is blank — the pay must land somewhere VISIBLE) per salaried employee with a usable
    pay_amount who has no existing row. Never touches an existing row. No-op for an all-hourly tenant
    or a pre-migration database (same `"pay_basis" in e` gate as everywhere else in this module)."""
    if lo is None or hi is None:
        return rows
    existing_ids = {r.get("employee_id") for r in rows if r.get("employee_id")}
    out = list(rows)
    for e in employees or ():
        eid = e.get("employee_id")
        if not eid or eid in existing_ids or "pay_basis" not in e:
            continue
        basis, overlay = _salary_pay_fields(e, settings, lo, hi)
        if basis == "hourly":
            continue
        # A misconfigured (no usable pay_amount) zero-activity employee has nothing to synthesize —
        # they'd show $0/$0 either way (no hours, no rate basis) and adding a bare row with just a
        # salary_note but no real figures would be noise, not signal; apply_to_payroll_rows already
        # never invents a row for them either. Skip.
        if "scheduled_pay" not in overlay:
            continue
        row = {
            "employee_id": eid, "name": e.get("name") or eid,
            "store": (e.get("home_store") or "").strip() or "Unassigned",
            "pay_rate": float(e.get("pay_rate") or 0),
            "scheduled_hours": 0.0, "actual_hours": 0.0, "shifts": 0,
            "scheduled_pay": 0.0, "actual_pay": 0.0,
        }
        row.update(overlay)
        out.append(row)
    return out


def apply_to_by_store(by_store, employees, emp_store_hours, emp_store_dollars, settings, lo: date, hi: date):
    """GET /payroll-by-store's post-aggregation salary override (Deliverable 4). For each salaried
    employee with a USABLE derived figure: subtract their hourly-computed $ contribution from every
    store bucket it landed in (emp_store_dollars — gathered by router.py's own per-row loops, the
    SAME basis `by_store['hours']`/'amount' already used, so this nets to exactly zero drift for a
    non-salaried tenant), then add the derived period pay back in, allocated across those SAME stores
    proportional to hours worked there (or 100%/'Unassigned' at zero hours — see
    allocate_across_stores). An employee whose salary can't be derived (misconfigured — pay_basis set
    but no usable pay_amount) is left COMPLETELY UNTOUCHED here — their original hourly-computed $
    stands rather than being zeroed out with nothing to replace it. `hours` in by_store is never
    modified — still the hourly-basis figure, always displayed (owner directive: "hours columns still
    display"). Naturally covers a zero-activity salaried employee too (emp_store_hours/dollars are
    simply empty for them, so allocate_across_stores falls straight to the home_store/'Unassigned'
    branch) — this endpoint never had GET /payroll's F1 bug, since `salaried` here is built from the
    full roster, not from activity."""
    salaried = {}
    for e in (employees or ()):
        eid = e.get("employee_id")
        if not eid or "pay_basis" not in e:
            continue
        basis, _amount = resolve_pay_basis(e)
        if basis != "hourly":
            salaried[eid] = e
    if not salaried or lo is None or hi is None:
        return by_store
    out = {k: dict(v) for k, v in by_store.items()}
    for eid, emp in salaried.items():
        basis, amount = resolve_pay_basis(emp)
        if amount is None or amount <= 0:
            continue   # misconfigured — leave this employee's hourly-computed $ untouched
        derived = derive_salary_pay(basis, amount, settings, lo, hi,
                                     parse_date(emp.get("hire_date")), parse_date(emp.get("termination_date")))
        if derived is None:
            continue
        old_dollars = emp_store_dollars.get(eid, {})
        for store, amt in old_dollars.items():
            if store in out:
                out[store]["amount"] = round(out[store]["amount"] - amt, 2)
        alloc = allocate_across_stores(emp_store_hours.get(eid, {}), derived["amount"], emp.get("home_store"))
        for store, amt in alloc.items():
            d = out.setdefault(store, {"store_code": store, "hours": 0.0, "amount": 0.0})
            d["amount"] = round(d["amount"] + amt, 2)
    return out
