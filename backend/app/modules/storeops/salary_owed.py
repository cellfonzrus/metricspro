"""storeops Daily Salary Owed + Cash Advances engine (mod-people, EEP package, owner directive
2026-08-04 — docs/specs/envelope-expense-payout.md, migration 419).

OWNER RULE (verbatim intent): salary paid in cash from the daily-closing envelope NEVER changes what
payroll counts — "the total salary will still be counted as what is as per the real clock in". The
existing GET /storeops/payroll / 'payroll_gross' P&L line stays the ONLY wages truth. Cash payments
recorded here are ADVANCES against that truth. Only the EXCESS of cumulative cash paid over cumulative
earned posts to the P&L, as a SEPARATE 'Additional Payroll' line (never folded into payroll_gross).

WHAT THIS MODULE IS THE ONE SHARED IMPLEMENTATION POINT FOR: pure (no DB) math over already-fetched
shift/timelog/ledger rows — router.py does all the I/O and calls these functions exactly like
payroll_expenses.py / pto_accrual.py / payroll_salary.py already do for their own features.

┌─ HOURS BASIS — REUSES /storeops/payroll's rules, does NOT invent a third one ──────────────────────┐
│ `daily_hours_for_employee` reproduces, day-by-day, the EXACT rules GET /storeops/payroll's legacy    │
│ Python path (and GET /payroll/actual-hours-detail) already apply, so a day computed here can never   │
│ diverge from what /payroll counts for the SAME employee/range:                                       │
│   - a shift's effective hours = actual_hours if >0, else scheduled_hours (the "act==0 -> scheduled   │
│     fallback"); for an INACTIVE employee a phantom (act==0) shift contributes NOTHING at all (matches │
│     router.py's `_inactive_activity_rows` — the day stays open for a punch instead).                 │
│   - a timelog punch only counts if CLOSED (clock_out set, hours not null) AND its day is not already  │
│     covered by a (real, for inactive) shift — the same no-double-count invariant /payroll applies.    │
│   - `basis` on a day is 'actual' when the hours came from a genuinely clocked source (shift.actual_   │
│     hours>0 or a counted punch), 'scheduled' only for the shift act==0 fallback (active employees     │
│     only — a skipped inactive phantom day never appears at all, so it can never be mislabeled).       │
│ harness_salary_owed.py cross-checks this against /storeops/payroll's OWN legacy aggregation (via the  │
│ same in-memory FakeClient pattern harness_payroll_rpc_equivalence.py already uses) so this can never   │
│ silently drift from the report it must reconcile to.                                                  │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘

┌─ RATE / OWED — hourly vs "daily equivalent" for a salaried pay_basis ───────────────────────────────┐
│ hourly:  rate = employees.pay_rate (unchanged); owed_total = round(Σ day hours × rate, 2) — the SAME  │
│          single-rounding-at-the-end /payroll's own `actual_pay = round(actual_hours * pay_rate, 2)`   │
│          uses, so a request spanning EXACTLY /payroll's own range totals to the SAME cents. Each      │
│          day's `owed` is hours × rate with the cents remainder from that single rounding fixed onto   │
│          the LAST hour-bearing day (same technique as payroll_salary.allocate_across_stores), so      │
│          Σ day.owed == owed_total to the penny, never drifting apart under independent per-day        │
│          rounding.                                                                                    │
│ salaried (weekly/monthly/annual): rate = "daily equivalent" = period_pay / period_length_days, where   │
│          period_pay is EXACTLY payroll_salary.convert_to_period_pay's own conversion table (so a       │
│          monthly salary on a weekly-pay tenant is (monthly × 12/52) / 7 — the SAME weekly period_pay   │
│          /payroll itself derives, just divided down to a day). owed_total is NOT re-derived from this  │
│          daily rate — it is payroll_salary.derive_salary_pay(...)['amount'], the EXACT AUTHORITATIVE   │
│          figure GET /payroll shows for this employee over this exact range (calendar-day-prorated      │
│          against both the employee's employment window and the request range, per payroll_salary's     │
│          own module docstring) — so a salaried employee's owed_total can NEVER diverge from their       │
│          /payroll row for the same [start,end]. days[] is a DISPLAY-ONLY split of that authoritative    │
│          total across the days inside the employment window (flat daily-equivalent rate, remainder-on- │
│          last-window-day fixup — same technique as above) so it always foots exactly to owed_total.     │
│          `hours`/`basis` shown per day for a salaried employee are still the REAL clocked/scheduled     │
│          figures (informational only — pay is not hours-derived for them, matching payroll_salary's     │
│          own "hours columns still display" convention for /payroll-by-store).                           │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘

┌─ ADDITIONAL PAYROLL (the P&L-facing number, `additional_payroll_excess`) ────────────────────────────┐
│ excess = max(0, cumulative cash paid to date − cumulative earned to date), computed PER EMPLOYEE as of │
│ the end of a given period ('YYYY-MM'), then summed per STORE for that period (store = the advance's    │
│ own store_code, falling back to the employee's home_store — see router.py's `_additional_payroll_      │
│ store_for`). "To date" is bounded to `EARNED_LOOKBACK_DAYS` before the period end (or the employee's    │
│ hire_date if later/known) — a documented, defensive cap (mirrors payroll_salary.MAX_PERIODS_WALKED)     │
│ so a recompute is never an unbounded full-history shift/timelog scan; any tenant whose true liability    │
│ predates that window gets a `note` flagging it rather than a silent undercount.                          │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘

HOURLY BYTE-IDENTICAL-TO-/payroll GUARANTEE for the owed_total figure specifically: for an hourly
employee, owed_total is nothing more than /payroll's own `round(actual_hours * pay_rate, 2)` computed
over hours built the identical way — there is no second, competing hours/rate computation anywhere in
this module.
"""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.modules.storeops import payroll_salary as _sal

# Defensive cap on how far back "earned to date" looks when an employee has no hire_date on file
# (payroll_salary.MAX_PERIODS_WALKED is the sibling cap for the pay-period walk; this is the analogous
# guard for the day-by-day shift/timelog scan). 366 days is a full year of coverage — plenty for the
# Additional-Payroll excess use case (advances outrunning earnings), never a realistic full-history need.
EARNED_LOOKBACK_DAYS = 366


def _money(x) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _date_range(lo: date, hi: date):
    out = []
    d = lo
    while d <= hi:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def daily_hours_for_employee(shifts, timelog, is_inactive: bool) -> dict:
    """(day -> {"hours": float, "basis": 'actual'|'scheduled'}) for ONE employee, over ALREADY-FILTERED
    (org, employee[, date-range]) shift + timelog rows. See module docstring for the exact rule this
    reproduces from GET /storeops/payroll's legacy path. `shifts` should already exclude soft-deleted
    rows (is_deleted=false), matching every /payroll query. Returns only days that have SOME hours
    (>0) or an explicit shift row — a day with no shift and no counted punch simply never appears."""
    days: dict = {}
    shift_days_covered = set()   # days a shift row (any, for an active emp; only a REAL one for inactive) covers
    for s in shifts or ():
        d = str(s.get("shift_date") or "")[:10]
        if not d:
            continue
        sched = float(s.get("scheduled_hours") or 0)
        act = float(s.get("actual_hours") or 0)
        if is_inactive and act <= 0:
            continue   # phantom shift for an inactive rep never counts (matches _inactive_activity_rows)
        eff, basis = (act, "actual") if act > 0 else (sched, "scheduled")
        row = days.setdefault(d, {"date": d, "hours": 0.0, "basis": basis})
        row["hours"] += eff
        if basis == "actual":
            row["basis"] = "actual"   # a mixed day (>1 shift row) stays 'actual' if ANY contributor was
        shift_days_covered.add(d)

    for t in timelog or ():
        d = str(t.get("work_date") or "")[:10]
        if not d or d in shift_days_covered:
            continue   # already represented by a shift that day -> never double-count (matches /payroll)
        if not (t.get("clock_out") and t.get("hours") is not None):
            continue   # only CLOSED punches count (matches /payroll-raw's own rule)
        hrs = float(t.get("hours") or 0)
        row = days.setdefault(d, {"date": d, "hours": 0.0, "basis": "actual"})
        row["hours"] += hrs
        row["basis"] = "actual"

    for row in days.values():
        row["hours"] = round(row["hours"], 2)
    return days


def apply_lunch_deduction(day_hours: dict, deduct_by_day: dict) -> dict:
    """Subtracts lunch-break minutes from each day's hours (same figures GET /storeops/payroll's own
    lunch-deduction block nets off `actual_hours`, so a caller who wires the SAME
    lunch_deduction.compute_lunch_deduction_from_rows(...)['days'] (grouped to {work_date: deduct_hours}
    for closed-day, applied rows) reconciles exactly). Never takes a day below 0 (negative-hours guard,
    matches every other lunch-deduction call site in this codebase). Returns a NEW dict; `day_hours`
    itself is left untouched."""
    if not deduct_by_day:
        return day_hours
    out = {}
    for d, row in day_hours.items():
        ded = float(deduct_by_day.get(d) or 0)
        nr = dict(row)
        if ded > 0:
            nr["hours"] = round(max(0.0, nr["hours"] - ded), 2)
        out[d] = nr
    return out


def _remainder_fixup_series(all_days, eligible_days, per_day_amount_fn, total: float):
    """Shared "flat/variable per-day amount, cents remainder fixed onto the LAST eligible day" builder
    — the same technique payroll_salary.allocate_across_stores uses for store splits, applied here to a
    per-day split so Σ owed ALWAYS foots exactly to `total` regardless of independent per-day rounding.
    `per_day_amount_fn(day) -> float` returns the (unrounded-ok) amount for a day BEFORE the fixup;
    ignored for the last eligible day, which instead gets `total - running`. `eligible_days` must be a
    subset of `all_days` (order preserved); a day not in `eligible_days` gets 0.0."""
    total_dec = Decimal(str(_money(total)))
    last = eligible_days[-1] if eligible_days else None
    out = {}
    running = Decimal("0.00")
    for d in all_days:
        if d not in (eligible_days or ()):
            out[d] = 0.0
            continue
        if d == last:
            out[d] = float(total_dec - running)
        else:
            amt = _money(per_day_amount_fn(d))
            out[d] = amt
            running += Decimal(str(amt))
    return out


def build_employee_salary_owed(emp: dict, settings: dict, lo: date, hi: date, day_hours: dict) -> dict:
    """(days:list, owed_total:float, basis:str) for ONE employee across [lo, hi] inclusive — see the
    module docstring's RATE/OWED section for exactly how owed_total is derived per pay_basis. `emp`
    needs employee_id/pay_rate always, plus pay_basis/pay_amount/hire_date/termination_date when
    available (payroll_salary.PAY_FIELDS — absent columns degrade to the hourly path, same convention
    as every other payroll_salary consumer)."""
    all_days = _date_range(lo, hi)
    rate_hourly = float(emp.get("pay_rate") or 0)
    basis, amount = _sal.resolve_pay_basis(emp) if "pay_basis" in emp else ("hourly", None)

    if basis == "hourly" or amount is None or amount <= 0:
        total_hours = round(sum((day_hours.get(dd) or {}).get("hours", 0.0) for dd in all_days), 2)
        owed_total = _money(total_hours * rate_hourly)
        eligible = [dd for dd in all_days if (day_hours.get(dd) or {}).get("hours", 0.0) > 0]
        owed_by_day = _remainder_fixup_series(
            all_days, eligible, lambda dd: (day_hours.get(dd) or {}).get("hours", 0.0) * rate_hourly,
            owed_total)
        days_out = [{"date": dd, "hours": (day_hours.get(dd) or {}).get("hours", 0.0),
                     "basis": (day_hours.get(dd) or {}).get("basis", "actual"),
                     "rate": rate_hourly, "owed": owed_by_day[dd]} for dd in all_days]
        return {"days": days_out, "owed_total": owed_total, "basis": "hourly"}

    # salaried — owed_total is the SAME authoritative figure /payroll shows via payroll_salary.
    hire = _sal.parse_date(emp.get("hire_date"))
    term = _sal.parse_date(emp.get("termination_date"))
    derived = _sal.derive_salary_pay(basis, amount, settings, lo, hi, hire, term)
    owed_total = _money((derived or {}).get("amount") or 0.0)
    plen = _sal.period_length_days(settings.get("pay_period_type"))
    period_pay = _sal.convert_to_period_pay(basis, amount, settings.get("pay_period_type")) or 0.0
    daily_rate = _money(period_pay / plen) if plen else 0.0
    window = [dd for dd in all_days
              if (not hire or date.fromisoformat(dd) >= hire) and (not term or date.fromisoformat(dd) <= term)]
    owed_by_day = _remainder_fixup_series(all_days, window, lambda dd: daily_rate, owed_total)
    days_out = [{"date": dd, "hours": (day_hours.get(dd) or {}).get("hours", 0.0),
                 "basis": (day_hours.get(dd) or {}).get("basis", "actual"),
                 "rate": daily_rate, "owed": owed_by_day[dd]} for dd in all_days]
    return {"days": days_out, "owed_total": owed_total, "basis": basis}


def additional_payroll_excess(cash_paid_to_date: float, earned_to_date: float) -> float:
    """max(0, cash_paid_to_date - earned_to_date) — the ONLY figure that ever posts to the P&L from
    this whole feature (see module docstring's Additional Payroll section). A cash-paid total AT or
    BELOW what's been earned is 0 — never negative, and never itself reduces payroll_gross."""
    return _money(max(0.0, float(cash_paid_to_date or 0) - float(earned_to_date or 0)))


def earned_lookback_start(hire_date, period_end: date) -> date:
    """The start date for a cumulative "earned to date" scan through `period_end` — the employee's own
    hire_date when known (exact, no approximation), else a defensive EARNED_LOOKBACK_DAYS cap before
    period_end (see module docstring)."""
    cap = period_end - timedelta(days=EARNED_LOOKBACK_DAYS)
    if hire_date and hire_date > cap:
        return hire_date
    return cap
