"""REP INCENTIVE — A MONTH RANGE ON ONE PAGE (owner 2026-09-25).

Owner, verbatim: *"al for rep incentive report the range for multiple months should be there to display the
commission for teh months selected in different rowqn on one page"*.

WHAT THIS IS — AND IS NOT. The Rep Incentive report (`/commcalc/reports`) reads ONE month through
`GET /commcalc/commissions/{period}` (`router.get_commissions`: the stored `rep_commissions` rows, the
chargeback and ops-chargeback deductions, the market stamp, the caller's self / span scope). A range is that
SAME per-month read, LOOPED over the months `account/_period.month_range` enumerates — never a second
calculation. This module only lays the per-month answers side by side: one row per rep per month, a
subtotal per month, a total per rep across the range, and a grand total. Every number is copied from the
per-month payload; the only arithmetic is summing, rounded to the cent. PURE (no I/O).

The cap is `MAX_MONTHS` = 12: each month is one full per-month read (a rep_commissions query, the
chargeback reads and the scope resolution), so a year is the widest window that stays one page.
"""

MAX_MONTHS = 12

# WHETHER A NAMED SLICE MAY BE SUMMED AT ALL (owner 2026-10-15, §COMMCALC-09). A range is where this
# bites hardest: `subtotal` and `acc_comm` are only written on a plan-mode row by the engine that
# itemises it, so a window covering a month calculated before that itemisation added a never-computed
# figure to a real one and printed one total. The month's own total is still exact (`total_payout` has
# always been written) -- it is the SLICES that are unknown, and this says so instead of adding zero.
#
# The verdict is INJECTED, not imported: this module imports nothing (harness_rep_incentive_range D5
# locks that, and the lock is right -- an assembler that can reach the app can grow a read of its own).
# `assemble` therefore REQUIRES the `evidence` module and has no fallback: a caller that stops passing
# commcalc/payout_slice_evidence raises rather than silently reverting to adding zeros (§19.18).

# the money fields a range sums (every one is a field the single-month payload already carries)
MONEY_FIELDS = ("subtotal", "total_payout", "chargeback_deduction", "ops_chargeback_deduction", "final_payout",
                "plan_comm", "installment_comm_sale", "residual_installment_comm", "acc_comm")
COUNT_FIELDS = ("premium_acts", "byod_acts", "upgrade_acts")


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _r2(x):
    return round(x + 0.0, 2)


def rep_key(row):
    """The rep a row belongs to across months — the roster name when present, else the POS name, upper."""
    r = row or {}
    return (str(r.get("storeops_name") or "").strip() or str(r.get("epay_salesperson") or "").strip()).upper()


def assemble(months, per_month, evidence):
    """`months` = the canonical month list (oldest first); `per_month` = {month: [row, …]} — each list
    EXACTLY what the single-month read returned for that month. `evidence` = the
    commcalc/payout_slice_evidence module, handed in by the caller (this module reaches for nothing) —
    the ONE home for whether a stored row's named slices are figures at all. Returns

      {"months": [...],
       "rows": [row + {"range_month": month}, …]     — month order, then the per-month order kept,
       "month_totals": [{"period", <money>…, <counts>…, "reps"}, …],
       "rep_totals": [{"rep", "store", "months", <money>…, <counts>…}, …]   — biggest payout first,
       "grand_total": {<money>…, <counts>…, "reps", "months_with_rows"}}

    Rows are COPIED (a shallow dict copy + a `range_month` key); no field of a per-month row changes."""
    rows, month_totals, reps = [], [], {}
    grand = {k: 0.0 for k in MONEY_FIELDS}
    grand.update({k: 0 for k in COUNT_FIELDS})
    with_rows = 0
    for m in months:
        mrows = list(per_month.get(m) or [])
        if mrows:
            with_rows += 1
        mt = {"period": m, "reps": len(mrows)}
        for k in MONEY_FIELDS:
            if k in evidence.NAMED_SLICES:
                _agg = evidence.sum_slice(mrows, k)
                mt[k] = _agg["total"]
                mt[k + "_complete"] = _agg["complete"]
                mt[k + "_rows_not_recorded"] = _agg["rows_not_recorded"]
            else:
                mt[k] = _r2(sum(_f(r.get(k)) for r in mrows))
        for k in COUNT_FIELDS:
            mt[k] = int(sum(int(_f(r.get(k))) for r in mrows))
        month_totals.append(mt)
        for r in mrows:
            d = dict(r)
            d["range_month"] = m
            rows.append(d)
            key = rep_key(r)
            t = reps.setdefault(key, {"rep": r.get("storeops_name") or r.get("epay_salesperson"),
                                      "store": r.get("store"), "months": 0,
                                      **{k: 0.0 for k in MONEY_FIELDS}, **{k: 0 for k in COUNT_FIELDS}})
            t["months"] += 1
            _r_ev = evidence.slices_are_evidence(r)
            if not _r_ev:
                # counted once per month, not once per field: this rep's across-the-range slice totals
                # are short by this many months, and the number says so.
                t["months_not_recorded"] = t.get("months_not_recorded", 0) + 1
            for k in MONEY_FIELDS:
                if k in evidence.NAMED_SLICES and not _r_ev:
                    continue          # never added as zero
                t[k] = _r2(t[k] + _f(r.get(k)))
            for k in COUNT_FIELDS:
                t[k] += int(_f(r.get(k)))
        for k in MONEY_FIELDS:
            grand[k] = _r2(grand[k] + mt[k])
            if k in evidence.NAMED_SLICES:
                grand[k + "_rows_not_recorded"] = (grand.get(k + "_rows_not_recorded", 0)
                                                   + mt.get(k + "_rows_not_recorded", 0))
        for k in COUNT_FIELDS:
            grand[k] += mt[k]
    grand["reps"] = len(reps)
    grand["months_with_rows"] = with_rows
    for k in MONEY_FIELDS:
        if k in evidence.NAMED_SLICES:
            grand[k + "_complete"] = not grand.get(k + "_rows_not_recorded", 0)
    # WHICH MONTHS to recalculate, named once for the whole window rather than left for the reader to
    # work out from a blank column. Reported, never repaired: a recalculation moves money.
    grand["periods_not_recorded"] = evidence.periods_not_recorded(rows)
    return {"months": list(months), "rows": rows, "month_totals": month_totals,
            "rep_totals": sorted(reps.values(), key=lambda t: (-t["total_payout"], str(t["rep"] or ""))),
            "grand_total": grand, "max_months": MAX_MONTHS}
