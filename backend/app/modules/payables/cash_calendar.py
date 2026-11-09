"""ONE home for the netting: expected money IN against money OWED out, day by day.

Index §COMMCALC-56. Lock: `backend/harness_cash_calendar.py`.

OWNER ASK, verbatim: *"the foloowing report will be how much is owed, which we already ahave and the
thent eh irs will be after offseting the payments what isteh cash flow"*.

WHAT THIS MODULE IS, AND WHAT IT DELIBERATELY IS NOT. It is the subtraction and nothing else. It reads
no table, resolves no store, dates no dollar and knows no carrier. BOTH of its inputs come from homes
that already existed or are declared elsewhere:

  · the OUT side is `payables/owed_read.dated_owed` — the SAME read the Daily Owed surface and the
    weekly roll-up share (§COMMCALC-34). The owner said that report already exists, and he is right;
    giving the calendar its own owed query would be the third path to "what do I owe" and the index
    rules forbid the second.
  · the IN side is `payables/expected_receipts.dated_receipts`, which reads each tenant's own declared
    source and places it through `payables/receipt_schedule`.

A NET IS FOLDED PER DAY, NEVER AS A DIFFERENCE OF TWO GRAND TOTALS. §CORE-17.3h's ruling: a figure
computed as A_total − B_total has no set of records adding up to it, so a tie-out against it is a
check that cannot fail. Each day carries its own in, its own out and its own net, and the headline
totals are Σ of the day rows — so the header cannot disagree with the grid, by construction.

AN UNMEASURED SIDE REFUSES TO NET, which is the whole reason this is a module and not four lines in a
router. A tenant with no declared payout cycle has an UNKNOWN inbound, and 0.00 is not unknown:
netting against it would print a confident cash position that is wrong by exactly the amount nobody
measured. So `expected_in`, `net` and `running` all go to None, the measured side is still shown in
full, and the surface says which half is missing. That is §COMMCALC-29 and §66 applied to a balance
rather than to a feed.

EVERY DAY IN THE SPAN IS PRESENT, so the page draws a calendar instead of inventing the gaps, and a
day with neither in nor out is a REAL zero (`quiet: True`) rather than an absence. Money dated outside
the span is reported as out-of-span — never dropped, and never clamped onto the edge day, which would
put a receipt on a date no cadence named.

ONE ASYMMETRY, STATED RATHER THAN PAPERED OVER (and it is not this module's choice). The IN side
reports a MEASURED $0.00 when a complete declaration simply found no rows — the declaration answered
and nothing was there. The OUT side does not: `owed_read` returns `source: None` whenever neither of
its sources produced a row, deliberately, so that an empty read is never read as "$0.00 is owed". So
under a filter that matches nothing the owed half reads NOT MEASURED, which is exactly what the Daily
Owed screen shows for the same filter. Overriding it here would be a SECOND opinion about whether the
business owes anything — the duplicate defect the house rules gate on — and it is worse than the
asymmetry. Fixing the class properly means changing that shared contract for all three of its
callers, which is a PR of its own. Index §COMMCALC-56.6.

PURE: no I/O, no framework, no clock of its own, no carrier/tenant/product name (RULE TWO).
"""
import datetime

#: A hard cap on the day rows one request may build. A calendar is read a month or a quarter at a
#: time; a decade of day cells is a mistake in a query string, not a question. Reached means the span
#: was TRUNCATED and the caller is told, rather than handed a silently short calendar.
MAX_DAYS = 400

TRUNCATED_NOTE = ("The date span asked for is longer than one calendar can carry, so it was cut "
                  "short. Narrow the dates to see the rest.")
IN_UNMEASURED_NOTE = ("Expected incoming money could not be measured, so no net position and no "
                      "running balance are shown. What is owed is still shown in full.")
OUT_UNMEASURED_NOTE = ("What is owed could not be measured, so no net position and no running "
                       "balance are shown. Expected incoming money is still shown in full.")


def _date(v):
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    s = str(v or "")[:10]
    if len(s) != 10:
        return None
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _fold(rows, date_key, amount_key):
    """Rows -> {iso date: {amount, count}}. Rows carrying no usable date are counted apart: money
    nobody could date belongs beside the calendar, not on a day of it."""
    out, undated_n, undated_amt = {}, 0, 0.0
    for r in rows or []:
        if not r:
            continue
        d = _date(r.get(date_key))
        amt = _f(r.get(amount_key))
        n = int(r.get("count") or 1)
        if d is None:
            undated_n += n
            undated_amt += amt
            continue
        e = out.setdefault(d.isoformat(), {"amount": 0.0, "count": 0})
        e["amount"] += amt
        e["count"] += n
    return out, undated_n, round(undated_amt, 2)


def build(expected_rows, owed_rows, *, start, end, opening_balance=0.0,
          in_measured=True, out_measured=True, in_date_key="date", in_amount_key="amount",
          out_date_key="due_date", out_amount_key="owed"):
    """PURE: the day-by-day cash calendar over [start, end].

    Returns {days, total_in, total_out, total_net, opening_balance, closing_balance, in_measured,
    out_measured, out_of_span_in, out_of_span_out, undated_in, undated_out, span_truncated, note}.

    Each day carries date / expected_in / owed_out / net / running / in_count / out_count / quiet.
    `expected_in`, `net` and `running` are None wherever the in side was not measured, and `owed_out`
    is None wherever the out side was not — never 0.00, because a figure nobody measured printed as
    zero is the one error this whole report exists to avoid making about the owner's cash.

    `start` and `end` are inclusive ISO dates. A reversed or unusable span yields no days rather than
    an exception: an empty calendar is a legible answer and a 500 is not.
    """
    s, e = _date(start), _date(end)
    netting = bool(in_measured and out_measured)

    in_by_day, undated_in_n, undated_in_amt = _fold(expected_rows, in_date_key, in_amount_key)
    out_by_day, undated_out_n, undated_out_amt = _fold(owed_rows, out_date_key, out_amount_key)

    base = {"days": [],
            "total_in": (0.0 if in_measured else None),
            "total_out": (0.0 if out_measured else None),
            "total_net": (0.0 if netting else None),
            "opening_balance": (round(_f(opening_balance), 2) if netting else None),
            "closing_balance": (round(_f(opening_balance), 2) if netting else None),
            "in_measured": bool(in_measured), "out_measured": bool(out_measured),
            "out_of_span_in": (0.0 if in_measured else None),
            "out_of_span_out": (0.0 if out_measured else None),
            "undated_in": undated_in_amt, "undated_out": undated_out_amt,
            "undated_in_rows": undated_in_n, "undated_out_rows": undated_out_n,
            "span_truncated": False, "start": None, "end": None, "note": None}
    if s is None or e is None or e < s:
        return base

    total_days = (e - s).days + 1
    truncated = total_days > MAX_DAYS
    if truncated:
        total_days = MAX_DAYS
        e = s + datetime.timedelta(days=MAX_DAYS - 1)

    days, running = [], _f(opening_balance)
    total_in, total_out = 0.0, 0.0
    for i in range(total_days):
        d = (s + datetime.timedelta(days=i)).isoformat()
        got_in = in_by_day.get(d) or {"amount": 0.0, "count": 0}
        got_out = out_by_day.get(d) or {"amount": 0.0, "count": 0}
        amt_in = round(got_in["amount"], 2)
        amt_out = round(got_out["amount"], 2)
        total_in += amt_in
        total_out += amt_out
        net = round(amt_in - amt_out, 2) if netting else None
        if netting:
            running = round(running + net, 2)
        days.append({
            "date": d,
            "expected_in": (amt_in if in_measured else None),
            "owed_out": (amt_out if out_measured else None),
            "net": net,
            "running": (running if netting else None),
            "in_count": (got_in["count"] if in_measured else None),
            "out_count": (got_out["count"] if out_measured else None),
            # A day with neither in nor out is a MEASURED nothing, which is a different fact from a
            # day whose figures were never measured — the page prints them differently.
            "quiet": bool(amt_in == 0.0 and amt_out == 0.0 and netting),
        })

    span = {d["date"] for d in days}
    note = TRUNCATED_NOTE if truncated else (
        IN_UNMEASURED_NOTE if not in_measured else (
            OUT_UNMEASURED_NOTE if not out_measured else None))
    return {"days": days,
            "total_in": (round(total_in, 2) if in_measured else None),
            "total_out": (round(total_out, 2) if out_measured else None),
            "total_net": (round(total_in - total_out, 2) if netting else None),
            "opening_balance": (round(_f(opening_balance), 2) if netting else None),
            "closing_balance": (running if netting else None),
            "in_measured": bool(in_measured), "out_measured": bool(out_measured),
            # Dated, real, and outside the window asked for. Reported so a reader can widen the dates
            # rather than conclude the money does not exist.
            "out_of_span_in": (round(sum(v["amount"] for k, v in in_by_day.items()
                                         if k not in span), 2) if in_measured else None),
            "out_of_span_out": (round(sum(v["amount"] for k, v in out_by_day.items()
                                          if k not in span), 2) if out_measured else None),
            "undated_in": undated_in_amt, "undated_out": undated_out_amt,
            "undated_in_rows": undated_in_n, "undated_out_rows": undated_out_n,
            "span_truncated": truncated, "start": days[0]["date"], "end": days[-1]["date"],
            "note": note}
