"""Envelope Expense + Payout (EEP) math — migrations 506/507.

Everything DB-reading here is try/except-guarded (missing table/migration -> empty dict, never a
raise) so the netting below degrades to `gross_cash` unchanged (today's exact behaviour) until 506/507
are applied — the "empty config == today's behaviour" guarantee the tender/count config modules already
established for this file.

The selection algorithm (`select_envelopes`) and the cadence due-logic (`cadence_due`) are PURE
functions with no DB access — unit-tested by scratchpad/prove_envelope.py (see the retail-ops handoff).
"""
from dateutil import parser as dateparser
import calendar


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ── Reading approved expense / withdrawal totals (org-scoped, degrade-to-empty) ────────────────────
def approved_expense_totals(client, org_id, date_from=None, date_to=None, store_codes=None):
    """(by_row, by_store_day) sums of APPROVED commcalc.closing_expense amounts — ALL kinds
    (payroll/commission/expense) count against envelope cash on hand; only 'expense'-kind rolls to
    the P&L separately. by_row keys on closing_row_id (for per-envelope netting); by_store_day keys on
    (store_code, close_date-as-str) and is the SUPERSET (includes null-closing_row_id lines too) — the
    aggregate a store-level/day-level figure (bank-deposit basis, cash-position) must use."""
    by_row, by_store_day = {}, {}
    try:
        q = (client.schema("commcalc").table("closing_expense")
             .select("closing_row_id,store_code,close_date,amount,status")
             .eq("org_id", org_id).eq("status", "approved"))
        if date_from:
            q = q.gte("close_date", date_from)
        if date_to:
            q = q.lte("close_date", date_to)
        if store_codes:
            q = q.in_("store_code", list(store_codes))
        rows = q.limit(200000).execute().data or []
    except Exception:
        return by_row, by_store_day
    for r in rows:
        amt = _f(r.get("amount"))
        if not amt:
            continue
        rid = r.get("closing_row_id")
        if rid:
            by_row[rid] = round(by_row.get(rid, 0.0) + amt, 2)
        key = (r.get("store_code") or "", str(r.get("close_date") or ""))
        by_store_day[key] = round(by_store_day.get(key, 0.0) + amt, 2)
    return by_row, by_store_day


def withdrawal_totals(client, org_id, date_from=None, date_to=None, store_codes=None):
    """Same shape as approved_expense_totals but over commcalc.envelope_withdrawal (mig 507) — cash
    already taken out for a commission/salary/expense/other payout."""
    by_row, by_store_day = {}, {}
    try:
        q = (client.schema("commcalc").table("envelope_withdrawal")
             .select("closing_row_id,store_code,close_date,amount")
             .eq("org_id", org_id))
        if date_from:
            q = q.gte("close_date", date_from)
        if date_to:
            q = q.lte("close_date", date_to)
        if store_codes:
            q = q.in_("store_code", list(store_codes))
        rows = q.limit(200000).execute().data or []
    except Exception:
        return by_row, by_store_day
    for r in rows:
        amt = _f(r.get("amount"))
        if not amt:
            continue
        rid = r.get("closing_row_id")
        if rid:
            by_row[rid] = round(by_row.get(rid, 0.0) + amt, 2)
        key = (r.get("store_code") or "", str(r.get("close_date") or ""))
        by_store_day[key] = round(by_store_day.get(key, 0.0) + amt, 2)
    return by_row, by_store_day


# ── Pure netting (no DB) ─────────────────────────────────────────────────────────────────────────
def net_row(gross_cash, row_id, exp_by_row, wd_by_row) -> float:
    """envelope_available for ONE daily_closing row (one rep's envelope): gross tender cash minus this
    row's own approved closing_expense lines minus any envelope_withdrawal taken specifically against
    THIS row. Never floored — a negative result is a real signal (over-withdrawn envelope)."""
    out = _f(gross_cash) - exp_by_row.get(row_id, 0.0) - wd_by_row.get(row_id, 0.0)
    return round(out, 2)


def net_store_day(gross_cash, store_code, close_date, exp_by_store_day, wd_by_store_day) -> float:
    """envelope_available aggregated for a (store, day) — the basis _bank_deposit_declared /
    cash_position use. exp_by_store_day / wd_by_store_day are the SUPERSET dicts from
    approved_expense_totals/withdrawal_totals (include null-closing_row_id lines), so this is the
    single source of truth for 'how much is really left to deposit/pick up' at store-day grain."""
    key = (store_code or "", str(close_date or ""))
    out = _f(gross_cash) - exp_by_store_day.get(key, 0.0) - wd_by_store_day.get(key, 0.0)
    return round(out, 2)


# ── Fewest-envelopes selection (pure, deterministic) ────────────────────────────────────────────────
def select_envelopes(envelopes, required_amount):
    """envelopes: [{closing_row_id, store_code, close_date, employee_name, available}], available =
    that envelope's CURRENT net cash on hand (already netted via net_row, > 0 to be eligible).

    Algorithm (spec §mod-retail-ops item 11 / Q15):
      1. If any single envelope's `available` >= required_amount, pick the SMALLEST such envelope
         (fewest envelopes AND least cash disturbed) — ties broken by OLDEST close_date first.
      2. Else greedy LARGEST-first until the requirement is covered — ties broken by OLDEST
         close_date first (drains stale cash preferentially).
    Deterministic (stable sort keys only — never depends on input order or dict iteration order).
    Returns {required, picks:[{...envelope fields, take}], total_taken, shortfall}."""
    req = round(max(_f(required_amount), 0.0), 2)
    pool = [e for e in envelopes if round(_f(e.get("available")), 2) > 0]
    if req <= 0:
        return {"required": req, "picks": [], "total_taken": 0.0, "shortfall": 0.0}

    def _dkey(e):
        return str(e.get("close_date") or "")

    asc = sorted(pool, key=lambda e: (round(_f(e.get("available")), 2), _dkey(e)))
    single = next((e for e in asc if round(_f(e.get("available")), 2) >= req), None)
    if single is not None:
        pick = {**single, "available": round(_f(single.get("available")), 2), "take": req}
        return {"required": req, "picks": [pick], "total_taken": req, "shortfall": 0.0}

    desc = sorted(pool, key=lambda e: (-round(_f(e.get("available")), 2), _dkey(e)))
    picks, remaining = [], req
    for e in desc:
        if remaining <= 0:
            break
        avail = round(_f(e.get("available")), 2)
        take = round(min(avail, remaining), 2)
        if take <= 0:
            continue
        picks.append({**e, "available": avail, "take": take})
        remaining = round(remaining - take, 2)
    total_taken = round(sum(p["take"] for p in picks), 2)
    shortfall = round(max(req - total_taken, 0.0), 2)
    return {"required": req, "picks": picks, "total_taken": total_taken, "shortfall": shortfall}


# ── Cadence "is X due on this date" (pure) ──────────────────────────────────────────────────────────
def cadence_due(cadence, anchor, anchor_date, as_of, unpaid_balance):
    """(is_due: bool, amount_due: float). daily -> always due. weekly -> due on `anchor` weekday
    (0=Mon..6=Sun; default today's weekday if unset — i.e. "due every week on today's weekday" the
    first time it's asked, which a management config then pins down). monthly -> due on `anchor`
    day-of-month, CLAMPED to the month's real last day (so anchor=31 fires on Feb 28/29). biweekly ->
    due every 14 days counted from `anchor_date` (no anchor_date configured -> never due, since there's
    no reference point to count from). Amount due = the full unpaid_balance when due, else 0 — this
    module never computes a partial/prorated payout."""
    cadence = (str(cadence or "daily").strip().lower())
    try:
        d = dateparser.parse(str(as_of)).date()
    except Exception:
        return False, 0.0
    due = False
    if cadence == "daily":
        due = True
    elif cadence == "weekly":
        try:
            wd = int(anchor) if anchor is not None and str(anchor).strip() != "" else d.weekday()
        except (TypeError, ValueError):
            wd = d.weekday()
        wd = max(0, min(6, wd))
        due = (d.weekday() == wd)
    elif cadence == "monthly":
        try:
            dom = int(anchor) if anchor is not None and str(anchor).strip() != "" else 1
        except (TypeError, ValueError):
            dom = 1
        last_day = calendar.monthrange(d.year, d.month)[1]
        dom = max(1, min(dom, last_day))
        due = (d.day == dom)
    elif cadence == "biweekly":
        if anchor_date:
            try:
                ad = dateparser.parse(str(anchor_date)).date()
            except Exception:
                ad = None
            if ad:
                delta_days = (d - ad).days
                due = (delta_days >= 0 and delta_days % 14 == 0)
    else:
        due = True   # unknown cadence string -> safest default is "always due" (never silently withholds pay)
    amt = round(_f(unpaid_balance), 2) if due else 0.0
    return due, amt
