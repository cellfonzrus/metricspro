"""IS THIS STORED PAY BREAKDOWN A FIGURE, OR A BLANK THAT LOOKS LIKE $0.00?

OWNER REPORT 2026-10-15 (verbatim): *"rep incentive report for luxelink shopws ACC GP as zero but
calculates the commission when you click on teh linke 0 if the source for the data is teh same then
why is the index not updateiong all areas when one is claculated , check for all such things in the
commission module and the sales module so everywhere the same data is visible"*.

WHAT HE SAW, MEASURED. The Rep Incentive table reads the STORED `commcalc.rep_commissions` row
(`router.get_commissions`); the drill-down link RE-COMPUTES from the plan engine. Both are right about
their own source, and the sources were written at different TIMES: the accessory slice of a plan-mode
row has only been written since the 2026-09-17 itemisation, and Luxelink's earlier months were last
calculated on 2026-09-10. So August's row carries `plan_comm = 957.36` beside `subtotal = 0` and
`acc_comm = 0` — a correct total next to a breakdown that was never computed.

Measured read-only on 2026-10-15: of 192 stored Luxelink rows, **102 contradict themselves this way**
(August 45/46, July 42/44, June 10/10, May 3/8, March 2/8), holding $23,440.15 of correct total pay
beside a $0.00 breakdown. The house (Boost) org: 0 of 379 rows, because the slices there are written by
`calculator.py` and always have been.

THE CLASS, not the instance (owner directive 2026-09-20). The instance is "August's accessory number is
blank". The class is **a surface cannot tell a measured zero from a figure nobody ever computed, and
prints both as $0.00** — which is the §66 ruling (`carrier_dollar_class.component_is_evidence`) applied
to a stored row instead of a classified dollar: a value is evidence only when something is known to have
computed it. Recomputing August is not the fix (and is the owner's own button: it moves money). The fix
is that every surface reading these slices asks THIS module first, so a never-computed figure reads as
**not recorded** rather than as zero dollars earned.

HOW A NEVER-WRITTEN SLICE IS KNOWN, with no migration and no new column. In plan mode the writer
(`router._apply_new_engines`) sets `subtotal` and `plan_comm` to the SAME figure, in the same
statement, and `tier_source` to 'plan'. A plan-mode row where `plan_comm` is positive and `subtotal` is
zero therefore CONTRADICTS ITSELF: it says the plan paid money and that the subtotal of that pay was
nothing. Only a row written before the itemisation can say both. That is the whole predicate, and it
reads two columns that already exist.

WHY NOT JUST BACKFILL. A backfill would be a write to stored pay for five closed months, which is money
and the owner's call, and it would still leave the next surface unable to tell. The registry comes
first; backfilling afterwards is then visible rather than silent, and this module reports every row it
withholds so the operator is told which months to recalculate.

ALLOWLIST POSTURE (§66). `is_evidence` is an ALLOWLIST: only a basis this module names as recorded is
evidence, so a basis it has never heard of — including one missing entirely — reads as NOT evidence. A
caller that forgets to resolve the basis therefore shows a blank, never a wrong number.

PURE. No I/O, no org, carrier, tenant or product name (RULE TWO). Lock: `harness_payout_slice_evidence.py`.
"""

#: `tier_source` of a row paid by the plan engine. The Boost calculator stores its own tier label, so a
#: Boost row is never plan mode and is never withheld by this module.
PLAN_MODE_TIER_SOURCE = "plan"

#: The named slices of a stored pay row — the columns a plan-mode writer fills in beside the total. These
#: are the figures that read as $0.00 when they were never computed, and the only ones this module speaks
#: for. `total_payout`, `plan_comm`, `final_payout` and the installment columns are NOT here: they are
#: written by every writer in every mode and were never blank.
NAMED_SLICES = (
    "subtotal", "acc_comm", "setup_fee_comm", "premium_comm", "byod_comm", "upgrade_comm",
    "trade_in_comm", "acima_comm", "custom_comm",
)

#: The slices were computed by the engine that wrote the row. The only basis that is evidence.
BASIS_RECORDED = "recorded"
#: The row contradicts itself (plan mode, the plan paid, the subtotal of that pay is zero), so its
#: slices were never written. The figure is UNKNOWN — which is not the same as zero earned.
BASIS_NOT_RECORDED = "not_recorded"

#: Only this basis is evidence. Everything else, named or not, is not — see the allowlist note above.
_EVIDENCE = (BASIS_RECORDED,)

#: The field the read side stamps on a row, and the one every surface should consult.
BASIS_FIELD = "payout_slice_basis"
REASON_FIELD = "payout_slice_reason"


def _f(v):
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def is_plan_mode(row):
    """True when this stored row was paid by the plan engine rather than the Boost calculator."""
    return str((row or {}).get("tier_source") or "").strip().lower() == PLAN_MODE_TIER_SOURCE


def basis(row):
    """How this row's named slices should be read: BASIS_RECORDED or BASIS_NOT_RECORDED.

    DERIVED from the row, never stored, so a row read by a caller that has not been through the read
    side still gets the right answer. A row that is not plan mode is always RECORDED — the Boost
    calculator has always written every slice, and that is what keeps the house org byte-identical.
    """
    row = row if isinstance(row, dict) else {}
    if not is_plan_mode(row):
        return BASIS_RECORDED
    if _f(row.get("plan_comm")) > 0 and _f(row.get("subtotal")) == 0:
        return BASIS_NOT_RECORDED
    return BASIS_RECORDED


def is_evidence(basis_value):
    """Is this basis one that may be read as a figure? ALLOWLIST — an unknown basis is not."""
    return str(basis_value or "").strip().lower() in _EVIDENCE


def slices_are_evidence(row):
    """Convenience: may this row's named slices be read as figures?"""
    return is_evidence(basis(row))


def slice_value(row, field):
    """The slice as a number, or **None** when it was never computed.

    None is the whole point: a surface that renders None as a dash tells the truth, and one that
    renders 0.0 tells the rep they earned nothing. A field outside NAMED_SLICES is returned as it
    stands — this module does not speak for columns it does not own.
    """
    row = row if isinstance(row, dict) else {}
    if field not in NAMED_SLICES:
        return _f(row.get(field))
    return None if not slices_are_evidence(row) else _f(row.get(field))


def reason(row):
    """One plain sentence for the screen, or '' when the slices are figures.

    No month, tenant or figure is named: the row knows its own period and the caller already shows it.
    """
    if slices_are_evidence(row):
        return ""
    return ("This month's pay breakdown was never computed — the total is correct, but the "
            "itemisation predates it. Run Calculation for this month to fill it in.")


def annotate(row):
    """The same row with its basis and reason stamped on, and NOT ONE FIGURE CHANGED.

    The read side calls this so the table, the month range, the exports and the emailed report all
    receive one answer instead of each deciding. Deliberately non-destructive: the stored numbers stay
    exactly as stored, because a surface that wants the raw column (a reconciliation, an audit) must
    still be able to see what the database holds.
    """
    row = row if isinstance(row, dict) else {}
    b = basis(row)
    row[BASIS_FIELD] = b
    row[REASON_FIELD] = reason(row)
    return row


def sum_slice(rows, field):
    """Sum a named slice across rows WITHOUT adding unknowns in as zero.

    Returns {'total', 'rows_counted', 'rows_not_recorded', 'complete'}. A caller that wants one number
    can read `total`, but it can no longer do so without being handed the count it excluded — which is
    what a month range summing August beside September needs, because adding a never-computed slice to
    a real one prints a total that is wrong and says nothing.
    """
    total, counted, missing = 0.0, 0, 0
    for r in rows or []:
        if slices_are_evidence(r):
            total += _f((r or {}).get(field))
            counted += 1
        else:
            missing += 1
    return {"total": round(total, 2), "rows_counted": counted,
            "rows_not_recorded": missing, "complete": missing == 0}


def periods_not_recorded(rows):
    """The stored periods whose slices were never computed, in first-seen order — the list an operator
    needs to know WHICH months to recalculate. Reports the defect; never repairs it."""
    out = []
    for r in rows or []:
        if not slices_are_evidence(r):
            p = str((r or {}).get("period") or "").strip()
            if p and p not in out:
                out.append(p)
    return out
