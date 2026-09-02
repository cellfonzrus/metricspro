"""Envelope report — PURE logic (owner directive 2026-09-02, item 2; mig 936).

The owner's words, verbatim: "a new report when all the envelopes can be filtered by using the
standard filters... user can put their comments after counting the actual cash marking it short
or over and if it is short then checkmark for assigning it to the sales rep as a chargeback if
the cash is coming back as short - all comments chargebacks or any discrepancy over or short must
be filterable with the date range with all our filters."

WHAT AN "ENVELOPE" IS HERE: one `commcalc.daily_closing` row (one rep, one store, one day — the
grain the envelope photo + declared cash already live at). The management count lands in
`commcalc.envelope_count` (mig 936, one row per closing row): counted amount, over/short status,
comment, and — when short and management ticks the checkbox — a CHARGEBACK against the sales rep.

CHARGEBACK WIRING — the EXISTING mechanism, never a parallel one: the assignment inserts a PARENT
row into `commcalc.ops_chargeback` (mig 504) with reason **'envelope_short'**, `applied_to`
'commission', `amount` = the actual shortage (not a flat policy fee — the cash that is missing).
From there everything downstream is the machinery that already exists:
  • the reason surfaces automatically in the Ops Chargeback Amounts policy editor
    (`ops_chargebacks.get_policy` unions "reasons in the wild");
  • decide (post/waive) goes through `ops_chargebacks.decide_chargeback` (management-gated);
  • POSTED commission-applied rows are settled by the commission module's
    `_settle_ops_chargebacks` / `_ops_chargeback_deductions` cascade (commission-agent domain —
    this module only ever creates parent rows, per the mig-504 contract).
Idempotency rides the mig-504 parent unique key (org, employee, store, reason, incident_date) —
one envelope-short chargeback per rep per store-day, matching the envelope grain exactly.

Everything here is PURE (rows in, dicts out) — proof: backend/harness_envelope_report.py.
"""

ENVELOPE_SHORT_REASON = "envelope_short"

STATUSES = ("short", "over", "match")


def _f(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def expected_cash(closing_row):
    """The cash this envelope SHOULD hold: the row's declared cash — `t_cash` (canonical tender
    column) falling back to legacy `store_cash`, the SAME rule _cash_position_core applies."""
    r = closing_row or {}
    v = _f(r.get("t_cash"))
    return v if v else _f(r.get("store_cash"))


def count_fields(expected, counted, tolerance=0.0):
    """PURE: variance + over/short/match status for a counted envelope. variance = counted −
    expected (negative ⇒ short). |variance| ≤ tolerance ⇒ 'match' (tolerance 0 by default — a
    counted envelope either ties out to the cent or it doesn't)."""
    exp, cnt = _f(expected), _f(counted)
    var = round(cnt - exp, 2)
    tol = abs(_f(tolerance))
    if abs(var) <= tol:
        status = "match"
    elif var < 0:
        status = "short"
    else:
        status = "over"
    return {"expected_amount": exp, "counted_amount": cnt, "variance": var, "status": status}


def shortage_amount(variance):
    """The chargeback dollar for a short envelope: the missing cash itself, positive. 0 for an
    over/match variance — an overage is never anyone's chargeback."""
    v = _f(variance)
    return round(-v, 2) if v < 0 else 0.0


def chargeback_parent_row(org_id, closing_row, employee_id, employee_name, amount):
    """PURE: the ops_chargeback PARENT insert dict for an envelope shortage assigned to the sales
    rep. status 'pending' — management still decides post/waive through the existing decide flow;
    applied_to 'commission' (the rep's commissionable pay; the mig-504 cascade handles overflow
    per the org's policy). Never called with amount ≤ 0 (guarded here anyway)."""
    amt = _f(amount)
    if amt <= 0:
        return None
    r = closing_row or {}
    return {
        "org_id": org_id,
        "employee_id": str(employee_id or ""),
        "employee_name": employee_name or r.get("employee_name"),
        "store_code": r.get("store_code") or "",
        "reason": ENVELOPE_SHORT_REASON,
        "incident_date": str(r.get("close_date") or "")[:10],
        "amount": amt,
        "status": "pending",
        "applied_to": "commission",
        "notes": "Envelope counted short at management count",
    }


def report_row(closing_row, count_row, chargeback, ver_row, market):
    """PURE: one envelope-report line — the closing row's identity + declared money + envelope
    photo ref, the management count (when one exists), and the linked chargeback status."""
    r = closing_row or {}
    c = count_row or {}
    cb = chargeback or {}
    v = ver_row or {}
    counted = c.get("counted_amount")
    out = {
        "closing_row_id": r.get("id"),
        "close_date": str(r.get("close_date") or "")[:10],
        "store_code": r.get("store_code"),
        "store_address": r.get("store_address") or r.get("store_name") or r.get("store_code"),
        "market": market or "(no market)",
        "employee_name": r.get("employee_name"),
        "declared_cash": expected_cash(r),
        "envelope_picture": r.get("envelope_picture"),
        "remarks": r.get("remarks"),
        "dm_verified": bool(v.get("verified")),
        "counted": counted is not None,
        "counted_amount": _f(counted) if counted is not None else None,
        "expected_amount": _f(c.get("expected_amount")) if c.get("expected_amount") is not None else None,
        "variance": _f(c.get("variance")) if c.get("variance") is not None else None,
        "status": c.get("status") or "uncounted",
        "comment": c.get("comment"),
        "counted_by": c.get("counted_by"),
        "counted_at": c.get("counted_at"),
        "chargeback_id": c.get("chargeback_id"),
        "chargeback_status": cb.get("status"),
        "chargeback_amount": _f(cb.get("amount")) if cb.get("amount") is not None else None,
    }
    return out


def status_filter(rows, status):
    """PURE: the report's discrepancy filter — 'short' | 'over' | 'match' | 'uncounted' |
    'discrepancy' (short OR over) | 'commented' (any comment) | 'chargeback' (one assigned).
    Unknown/blank ⇒ rows unchanged (never a silent drop on a typo'd filter)."""
    s = str(status or "").strip().lower()
    if s in STATUSES or s == "uncounted":
        return [r for r in rows if r.get("status") == s]
    if s == "discrepancy":
        return [r for r in rows if r.get("status") in ("short", "over")]
    if s == "commented":
        return [r for r in rows if (r.get("comment") or "").strip()]
    if s == "chargeback":
        return [r for r in rows if r.get("chargeback_id")]
    return rows


def totals(rows):
    """PURE: the report's summary tiles."""
    out = {"envelopes": len(rows), "counted": 0, "short": 0, "over": 0, "match": 0,
           "short_total": 0.0, "over_total": 0.0, "chargebacks": 0, "chargeback_total": 0.0}
    for r in rows:
        st = r.get("status")
        if r.get("counted"):
            out["counted"] += 1
        if st in ("short", "over", "match"):
            out[st] += 1
        v = r.get("variance")
        if st == "short" and v is not None:
            out["short_total"] = round(out["short_total"] + (-_f(v)), 2)
        if st == "over" and v is not None:
            out["over_total"] = round(out["over_total"] + _f(v), 2)
        if r.get("chargeback_id"):
            out["chargebacks"] += 1
            out["chargeback_total"] = round(out["chargeback_total"] + _f(r.get("chargeback_amount")), 2)
    return out
