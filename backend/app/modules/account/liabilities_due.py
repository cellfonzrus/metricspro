"""Current Monetary Liabilities — pure composition logic (owner directive 2026-09-03).

OWNER DIRECTIVE (verbatim excerpts): "Current monetary liabilities tile will have Current monies
owed to the distributor, this weeks payments due with standard filters, Payroll Due this week,
payroll tax due, Rents due this week, any other recurring expenses due, by default the rents are
due in the 1st week of the month, should not be hard coded but defined for stores when setting up
the store."

WHAT LIVES HERE (the endpoint glue lives in account/router.py `GET /account/liabilities-due`):
pure, stdlib-only window math and per-section aggregation over rows OTHER, EXISTING derivations
produce — this module never invents a data path (duplicate-check gate, CLAUDE.md):

  • Distributor payables  — the SAME raw_ma_daily_tx rows `statement_engine._fetch_outstanding_tx`
    fetches for the mig-933 `handset_payable` Balance-Sheet line, filtered by the SAME
    `account_config.handset_payable_order_types` families. `payables_due_in_window` is the
    due-THIS-WEEK sibling of `balance_sheet.handset_payable_bookings` (same family matching, same
    `retail_cost`-only money read — equivalence pinned in harness_liabilities_due.py §A3 so the
    two predicates can never drift). Boost-side device money stays the `owed_vip` line of the
    STORED Balance-Sheet snapshot (read in the router — one math path, never recomputed here).
  • Payroll due / payroll tax due — aggregation over the rows `storeops.router.payroll_raw`
    returns (the /storeops/payroll-tax page's one data path) with the tax twin
    `storeops/payroll_tax_estimate.compute_pay`; the mig-434 pay gate is applied by the ROUTER
    (fail closed: gate denied ⇒ the section reports allowed=False and carries NO dollars).
  • Rents / insurance due this week — computed FROM the mig-946 `storeops.store_lease` helpers
    (`store_lease.rent_for_month` / `resolve_rent_due` / `rent_due_window` — the documented read
    contract; imported, never re-derived). Gated whole by `store_lease.can_see_lease` in the
    router (same fail-closed posture).

LEAF MODULE: stdlib only at import time (store_lease itself imports pay_visibility lazily-safe);
provable offline by backend/harness_liabilities_due.py.
"""
import calendar
from datetime import date, timedelta

from app.modules.storeops import store_lease as _lease

# ── money rounding (the account-module convention) ────────────────────────────────────────────────
def _round(x):
    try:
        return round(float(x or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _safe_float(v):
    try:
        f = float(v)
        return f if f == f else 0.0          # NaN guard
    except (TypeError, ValueError):
        return 0.0


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Week window — "this week" = the Monday..Sunday ISO week containing `today`
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def week_window(today):
    """'YYYY-MM-DD' (or date) → (monday_iso, sunday_iso) of the calendar week containing it."""
    d = today if isinstance(today, date) else date.fromisoformat(str(today)[:10])
    mon = d - timedelta(days=d.weekday())
    return mon.isoformat(), (mon + timedelta(days=6)).isoformat()


def months_touched(start_iso, end_iso):
    """[(year, month)] for every calendar month the inclusive [start, end] window overlaps."""
    s, e = date.fromisoformat(start_iso[:10]), date.fromisoformat(end_iso[:10])
    out, y, m = [], s.year, s.month
    while (y, m) <= (e.year, e.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _overlap(a_start, a_end, b_start, b_end):
    return a_start <= b_end and b_start <= a_end


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 1. Distributor payables due in the window (sibling of balance_sheet.handset_payable_bookings)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def payables_due_in_window(tx_rows, order_types, start, end):
    """raw_ma_daily_tx rows → the handset-payable rows whose vendor due_date falls INSIDE
    [start, end] (inclusive). Family matching + money read are IDENTICAL to
    `balance_sheet.handset_payable_bookings` (case-insensitive order_type family; `retail_cost`
    ONLY; rows missing either date skipped honestly; negative rows — RMA credits — kept, sign
    preserved). A row transacted after `end` cannot be due yet and is skipped.

    Returns (rows [{account_id, order_type, tx_date, due_date, amount}], meta {rows, total})."""
    fams = {str(t).strip().lower() for t in (order_types or []) if str(t).strip()}
    if not fams or not start or not end:
        return [], {"rows": 0, "total": 0.0}
    lo, hi = str(start)[:10], str(end)[:10]
    out, total = [], 0.0
    for r in tx_rows or []:
        r = r or {}
        ot = str(r.get("order_type") or "").strip()
        if ot.lower() not in fams:
            continue
        tx_d = str(r.get("tx_date") or "")[:10]
        due_d = str(r.get("due_date") or "")[:10]
        if not tx_d or not due_d:
            continue
        if due_d < lo or due_d > hi or tx_d > hi:
            continue
        amt = _safe_float(r.get("retail_cost"))
        if not amt:
            continue
        out.append({"account_id": (str(r.get("account_id") or "").strip() or None),
                    "order_type": ot, "tx_date": tx_d, "due_date": due_d,
                    "amount": _round(amt)})
        total = round(total + amt, 2)
    out.sort(key=lambda x: (x["due_date"], x["account_id"] or ""))
    return out, {"rows": len(out), "total": _round(total)}


def attribute_stores(rows, acct_store, resolve=None):
    """Fold payable rows to store grain via the mig-314 account→store index (the SAME attribution
    the P&L / BS use). Unmapped accounts stay company-wide (honest). Returns
    (by_store {store: amt}, company_wide)."""
    by_store, company = {}, 0.0
    for r in rows or []:
        st = (acct_store or {}).get(r.get("account_id"))
        if st and resolve:
            st = resolve(st) or st
        if st:
            by_store[st] = _round(by_store.get(st, 0.0) + r["amount"])
        else:
            company = _round(company + r["amount"])
    return by_store, company


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 2. Payroll due / payroll tax due (aggregation over payroll_raw rows + the tax twin)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def aggregate_payroll(raw_rows, compute_pay):
    """PURE: /storeops/payroll-raw rows + the payroll_tax_estimate twin → per-store gross plus the
    tax liability split. `payroll_tax_due` = employer FICA + every withheld amount (SS, Medicare,
    federal, state) — the dollars owed to tax authorities for the period; `gross` is what is owed
    to employees before withholding. Returns the section dict (no gate logic here — the router
    fails closed before calling)."""
    by_store, gross = {}, 0.0
    employer_fica = withheld = 0.0
    for r in raw_rows or []:
        p = compute_pay(r.get("total_hours"), r.get("pay_rate"), r.get("settings") or {})
        g = _safe_float(p.get("gross"))
        st = str(r.get("store") or "").strip() or "—"
        cell = by_store.setdefault(st, {"gross": 0.0, "employees": 0})
        cell["gross"] = _round(cell["gross"] + g)
        cell["employees"] += 1
        gross = round(gross + g, 2)
        employer_fica = round(employer_fica + _safe_float(p.get("employer_fica")), 2)
        withheld = round(withheld + _safe_float(p.get("fica_ss")) + _safe_float(p.get("fica_medicare"))
                         + _safe_float(p.get("federal")) + _safe_float(p.get("state")), 2)
    return {"by_store": by_store, "gross_total": _round(gross),
            "tax": {"employer_fica": _round(employer_fica), "withheld": _round(withheld),
                    "total": _round(employer_fica + withheld)},
            "employees": sum(c["employees"] for c in by_store.values())}


def paydays_in_window(pay_settings, pay_period_for, start, end, lookback_days=63):
    """The pay period(s) whose PAYDAY falls inside [start, end] — 'payroll due this week' in the
    literal sense. A payday LAGS its period's end (first payday_dow on/after the end, plus
    payday_weeks_after−1 weeks), so the walk starts `lookback_days` BEFORE the window and steps
    period-by-period forward until the period start passes the window end (bounded).
    `pay_period_for` is core.router.pay_period_for (injected — the ONE shared period resolver,
    never a copy). Returns [{start, end, payday}] ascending."""
    lo, hi = str(start)[:10], str(end)[:10]
    out, seen = [], set()
    cur = pay_period_for(pay_settings, date.fromisoformat(lo) - timedelta(days=lookback_days))
    for _ in range(24):                                  # bounded: 63d lookback / ≥7d periods
        key = cur["start"]
        if key in seen:
            break
        seen.add(key)
        if lo <= cur["payday"] <= hi:
            out.append(dict(cur))
        if key > hi:
            break
        cur = pay_period_for(pay_settings, date.fromisoformat(cur["end"]) + timedelta(days=1))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 3. Rents due in the window (mig-946 helpers — imported, never re-derived)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def rent_due_rows(lease_rows, tenant_default, start, end):
    """storeops.store_lease rows → the stores whose rent-due window overlaps [start, end], with
    the month's rent per `store_lease.rent_for_month` (schedule > escalation > current; None =
    amount unknown, reported as null — never a fake 0). One row per (store, month) whose due
    window overlaps. Rows without any rent basis AND no due override still surface (amount null)
    so a configured store is never silently missing from a liabilities view."""
    lo, hi = str(start)[:10], str(end)[:10]
    out = []
    for r in lease_rows or []:
        r = r or {}
        sc = str(r.get("store_code") or "").strip()
        if not sc:
            continue
        due = _lease.resolve_rent_due(r.get("rent_due"), tenant_default)
        for (y, m) in months_touched(lo, hi):
            w_start, w_end = _lease.rent_due_window(y, m, due)
            if not _overlap(w_start, w_end, lo, hi):
                continue
            amt = _lease.rent_for_month(y, m, r.get("current_rent"), r.get("rent_effective_from"),
                                        r.get("escalation_pct"), r.get("rent_schedule"))
            # a lease that has ENDED before this month owes nothing (honest skip, reported)
            lease_end = str(r.get("lease_end") or "")[:10]
            if lease_end and lease_end < f"{y:04d}-{m:02d}-01":
                continue
            out.append({"store_code": sc, "month": f"{y:04d}-{m:02d}",
                        "due_start": w_start, "due_end": w_end,
                        "amount": (None if amt is None else _round(amt)),
                        "due_rule": due})
    out.sort(key=lambda x: (x["due_start"], x["store_code"]))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 4. Insurance premiums (and other lease-recorded recurring expenses) due in the window
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_FREQ_MONTHS = {"annual": 12, "semiannual": 6, "quarterly": 3, "monthly": 1}


def _add_months(d, n):
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def premium_occurrences(first_due, frequency, start, end, cap=64):
    """Recurrence dates of an insurance premium (anchor `first_due`, repeating every
    `insurance_premium_frequency`) that land inside [start, end]. Anchor-day arithmetic clamps to
    month end (Jan 31 quarterly → Apr 30). Unknown frequency → treated 'annual' (the mig-946
    column default). Malformed anchor → [] (nothing known, nothing invented)."""
    try:
        anchor = date.fromisoformat(str(first_due)[:10])
    except (TypeError, ValueError):
        return []
    step = _FREQ_MONTHS.get(str(frequency or "").strip().lower(), 12)
    lo, hi = date.fromisoformat(str(start)[:10]), date.fromisoformat(str(end)[:10])
    out, d, n = [], anchor, 0
    # walk forward from the anchor (or backward-compatible: anchor may already be past `start`)
    while d < lo and n < cap:
        n += 1
        d = _add_months(anchor, step * n)
    while d <= hi and n < cap:
        if d >= lo:
            out.append(d.isoformat())
        n += 1
        d = _add_months(anchor, step * n)
    return out


def insurance_due_rows(lease_rows, start, end):
    """storeops.store_lease rows → insurance premiums due inside [start, end] per the mig-946
    recurrence columns (`insurance_premium` on `insurance_premium_due` repeating per
    `insurance_premium_frequency`). One row per occurrence."""
    out = []
    for r in lease_rows or []:
        r = r or {}
        sc = str(r.get("store_code") or "").strip()
        prem = r.get("insurance_premium")
        if not sc or prem in (None, ""):
            continue
        for d in premium_occurrences(r.get("insurance_premium_due"),
                                     r.get("insurance_premium_frequency"), start, end):
            out.append({"store_code": sc, "due_date": d, "amount": _round(prem),
                        "company": (str(r.get("insurance_company") or "").strip() or None),
                        "frequency": (str(r.get("insurance_premium_frequency") or "annual")
                                      .strip().lower() or "annual")})
    out.sort(key=lambda x: (x["due_date"], x["store_code"]))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 5. Assembly — the payload skeleton (totals never invent a number: null amounts excluded + counted)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def sum_known(rows, key="amount"):
    """(total of non-null amounts, count of null/unknown rows) — an unknown rent must show as
    'unknown', never as $0 inside a total."""
    total, unknown = 0.0, 0
    for r in rows or []:
        v = r.get(key)
        if v is None:
            unknown += 1
        else:
            total = round(total + _safe_float(v), 2)
    return _round(total), unknown
