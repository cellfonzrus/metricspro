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
