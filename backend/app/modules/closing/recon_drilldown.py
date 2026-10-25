"""THE CLOSE RECON'S ACCUSATIONS NAME THEIR EVIDENCE — the declaration for /closing/recon (§CORE-17).

Every row of this report is an accusation in dollars: *this rep declared $400 cash and the POS says
$380, so the close is blocked.* The audit's finding was that the row dead-ends there — a manager
reading it cannot see which closing row the declared figure came from, which feed the counterpart
came from, or what rule turned the difference into a block rather than a flag. A blocked close is a
person's day; it needs its evidence attached to it.

THE FOUR FIGURES ARE COUNTS OF ACCUSATIONS, so each one's panel is the accusations themselves —
blocked, flagged, pending, and all of them — and every row carries:

- **where the declared figure lives** (`daily_closing`, keyed by the day, the store and the rep), and
- **where the counterpart came from**: the pair side the sales read ACTUALLY answered from, passed in
  by the endpoint (`_b2b_day`'s `sales_tables`, §CORE-17.3c). Never both tables as a provenance claim
  nothing checked, and never a constant.

A PENDING ROW IS THE INTERESTING ONE. It is an accusation the report refuses to make: the feed was
not loaded, or the rep could not be matched, so there IS no counterpart. Its counterpart origin is
therefore untraceable and its variance is None — reported with its reason, never as a $0.00 variance,
which would read as "we checked and it agreed".

THE RULE THAT MADE IT A BLOCK TRAVELS WITH THE ROW. `router._money_issues` is the one home for that
rule (cash short blocks, cash over flags; credit over blocks, credit under flags, each outside the
tenant's tolerance) and it already writes its verdict into the row as `severity` and `reason`. This
declaration SHOWS those two fields rather than restating the thresholds — a second copy of the rule
here would be a rule that could drift from the one actually applied.
"""
from app.modules.core import report_drilldown

#: The keys addressing one accusation: a rep's day at a store, for one metric. The metric is part of
#: the address because a rep-day can be blocked on cash and flagged on credit at the same time, and
#: those are two different accusations about two different figures.
ROW_KEYS = ("date", "store_code", "rep", "metric")

#: Where a declared figure lives. The counterpart's table is the sales pair side that answered, and
#: is passed in rather than named here.
DECLARED = ("daily_closing",)


def _label(r):
    r = r or {}
    return " · ".join(str(x) for x in (
        r.get("store_address") or r.get("store_code") or "?",
        str(r.get("date") or "?")[:10],
        r.get("rep") or "(store-level)",
        r.get("metric") or "?",
    ))


def _row_fields(r):
    """What identifies and explains one accusation. `severity` and `reason` are read off the row the
    report already built, so the rule that produced them has exactly one home (`_money_issues`)."""
    r = r or {}
    return {"store_code": r.get("store_code"),
            "rep": r.get("rep"),
            "metric": r.get("metric"),
            "severity": r.get("severity"),
            "verdict": r.get("reason"),
            "declared": r.get("declared"),
            "counterpart": r.get("b2b")}


def _one(r):
    """A count figure's contribution: one accusation."""
    return 1 if r else None


def _severity(want):
    def read(r):
        return (r or {}).get("severity") == want
    return read


def _reason(r):
    """Why a row carries no variance. A pending row is an accusation the report REFUSES to make: the
    feed was not loaded for that day, or the rep could not be matched to it, so there is no
    counterpart — which is not the same as a counterpart of $0.00."""
    r = r or {}
    if r.get("severity") != "pending":
        return None
    return "not_computed" if r.get("status") == "not_computed" else "no_sales_feed_match"


def _tables(sales_tables):
    """A row's provenance, PER ROW: the closing row the declared figure was filed on, and the feed
    the counterpart was read from. A pending row has no counterpart, so its second leg is named as
    untraceable (`None`) rather than claiming a feed that answered nothing — which is what the
    shared fold turns into a stated, countable provenance gap."""
    SALES = tuple(t for t in (sales_tables or ()) if t)

    def read(r):
        if (r or {}).get("severity") == "pending" or (r or {}).get("b2b") is None:
            return DECLARED + (None,)
        return DECLARED + (SALES or (None,))
    return read


def catalogue(*, sales_tables=()):
    read_tables = _tables(sales_tables)
    common = {"row_label": _label, "keys": ROW_KEYS, "schema": "commcalc", "fields": _row_fields,
               "tables": read_tables, "unit": report_drilldown.UNIT_COUNT, "reason": _reason}
    return (
        {"key": "blocks", "label": "Closes the recon is blocking",
         "total": "blocks", "value": _one, "in_figure": _severity("block"),
         "note": ("A block is the rule in `_money_issues` applied to this row — cash short of the "
                  "feed, or credit over it, by more than the tenant's tolerance. Each row states "
                  "which."), **common},
        {"key": "flags", "label": "Closes flagged for someone to look at",
         "total": "flags", "value": _one, "in_figure": _severity("flag"), **common},
        {"key": "pending", "label": "Reps the recon cannot judge yet",
         "total": "pending", "value": _one, "in_figure": _severity("pending"),
         "note": ("Not a clean close and not a discrepancy: the feed was not loaded for the day, or "
                  "the rep could not be matched to it, so no comparison was made at all."),
         **common},
        {"key": "total", "label": "Rows this recon produced",
         "total": "total", "value": _one, **common},
    )


def drilldowns(rows, totals, *, sales_tables=(), cap=None):
    return report_drilldown.tiles(catalogue(sales_tables=sales_tables), rows, totals,
                                  cap=cap or report_drilldown.ROW_CAP)


# ── THE DASHBOARD'S TWO CASH TILES — the same accusation, folded as money ─────────────────────────
# `/closing`'s "Cash short" and "Cash over" tiles are not a second mechanism and must not get a
# second declaration. They are `_money_issues`' CASH verdict for a rep-day — the identical fact the
# four counts above are counting — carried on the submissions row as `cash_short_amount` /
# `cash_over_amount` and summed. The recon counts the accusations; the dashboard adds up their
# dollars. One fact, one home, two folds, so the declaration lives here beside its sibling rather
# than in a file of its own (the shape `pickup_drilldown.billpay_catalogue` already set).
#
# THE GRAIN IS THE REP-DAY, NOT THE ACCUSATION. A submissions row carries one cash figure, not one
# per metric, so `metric` is no part of its address — which is exactly why it needs its own keys and
# its own label and cannot simply reuse the catalogue above.
#
# REPORTED, NOT PAPERED OVER: a rep-day the recon could not judge — the sales feed was not loaded for
# that day, or the rep could not be matched to it — contributes 0.00 to BOTH tiles today. The sum is
# right; the presentation is not, because "Cash short $0.00" then reads as "no cash is missing" when
# the truth is that nothing was compared. These rows are shown in both panels, NOT counted, each
# saying which blank it is. The tiles' figures are unchanged: a figure is never moved to make a
# screen read better.

#: A rep-day's address on the submissions report.
SUBMISSION_KEYS = ("close_date", "store_code", "employee_name")

#: The states in which no comparison was made at all, so there is no variance to count.
UNJUDGED = ("recon_pending", "not_computed")


def _submission_label(r):
    r = r or {}
    return " · ".join(str(x) for x in (
        r.get("store_address") or r.get("store_code") or "?",
        str(r.get("close_date") or "?")[:10],
        r.get("employee_name") or "(no name)",
    ))


def _submission_fields(r):
    r = r or {}
    return {"store_code": r.get("store_code"),
            "rep": r.get("employee_name"),
            "metric": "cash",
            "severity": r.get("gate_status"),
            "verdict": ", ".join(r.get("gate_reasons") or []) or None,
            "declared": r.get("t_cash"),
            "counterpart": r.get("b2b_cash")}


def _unjudged(r):
    return (r or {}).get("gate_status") in UNJUDGED


def _submission_reason(r):
    """Why a rep-day carries no variance. Same two blanks as the recon's pending rows, named with the
    same two reasons, because it is the same read that came back empty."""
    if not _unjudged(r):
        return None
    return ("not_computed" if (r or {}).get("gate_status") == "not_computed"
            else "no_sales_feed_match")


def _cash_variance(field):
    def read(r):
        r = r or {}
        if _unjudged(r):
            return None
        v = float(r.get(field) or 0)
        return v if v else None
    return read


def _submission_tables(sales_tables):
    SALES = tuple(t for t in (sales_tables or ()) if t)

    def read(r):
        if _unjudged(r) or (r or {}).get("b2b_cash") is None:
            return DECLARED + (None,)
        return DECLARED + (SALES or (None,))
    return read


_UNJUDGED_NOTE = ("A rep-day the recon could not judge — the sales feed was not loaded for that day, "
                  "or the rep could not be matched to it — is shown here and not counted. It "
                  "contributes nothing to the figure, which is not the same as contributing $0.00.")


def submission_catalogue(*, sales_tables=()):
    common = {"row_label": _submission_label, "keys": SUBMISSION_KEYS, "schema": "commcalc",
              "fields": _submission_fields, "tables": _submission_tables(sales_tables),
              "reason": _submission_reason, "note": _UNJUDGED_NOTE}
    return (
        {"key": "cash_short", "label": "Cash short of the sales feed", "total": "cash_short",
         "value": _cash_variance("cash_short_amount"),
         "in_figure": (lambda r: not _unjudged(r) and float((r or {}).get("cash_short_amount") or 0) > 0),
         **common},
        {"key": "cash_over", "label": "Cash over the sales feed", "total": "cash_over",
         "value": _cash_variance("cash_over_amount"),
         "in_figure": (lambda r: not _unjudged(r) and float((r or {}).get("cash_over_amount") or 0) > 0),
         **common},
    )


def submission_totals(rows):
    """PURE: the two figures the dashboard's tiles show. Summed, NEVER netted against each other —
    a rep $50 short one day and $50 over the next has twice handed over the wrong cash, which is the
    whole reason the owner asked for two tiles rather than one (2026-08-03)."""
    # Seeded with a bare 0, not 0.0: the batch-6 lock (§E2) bans every float literal in this file so
    # that a tolerance or a rate can never be copied in beside the rule that owns it, and an
    # accumulator seed has no business being the exception that opens it.
    out = {"cash_short": 0, "cash_over": 0}
    for r in (rows or []):
        if _unjudged(r):
            continue
        out["cash_short"] = round(out["cash_short"] + float((r or {}).get("cash_short_amount") or 0), 2)
        out["cash_over"] = round(out["cash_over"] + float((r or {}).get("cash_over_amount") or 0), 2)
    return out


def submission_drilldowns(rows, totals, *, sales_tables=(), cap=None):
    return report_drilldown.tiles(submission_catalogue(sales_tables=sales_tables), rows, totals,
                                  cap=cap or report_drilldown.ROW_CAP)
