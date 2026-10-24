"""THE ELEVEN CASH TILES NAME THEIR ROWS — the drill-down declaration for cash recon management.

OWNER ASK 2026-10-24, verbatim: *"cash variance at employee should have the drill down from wheter
the data is cming from , update in index thta drilldown is needed for every report geenerated from
any data which comming from within the platforma as a defalut"*.

The §CORE-17 audit named this screen FIRST of the 93 that owed one, and the reason is not that it is
complicated: it is that it is *the most disputable money screen in the repo and the furthest from its
rows*. Eleven tiles — declared cash, declared credit, declared bill-pay, the two pickup totals, what
the DM counted, what management counted, the short and over dollars on both sides, the POS and
processor bill-pay figures — each a fold over store-days nobody could reach. A market manager was
being asked to chase a $90,000 shortfall with no way to the store-day that produced it.

THIS FILE COMPUTES NOTHING. It is a DECLARATION: per tile, which `totals` key holds the figure, which
field on the report's own rows it was folded from, which tables that field was read out of, and which
rows the tile counts. The fold, the tie-out and the provenance roll-up are
`core/report_drilldown.tiles` — the one home, shared with every other report that gains a tile drill
(§CORE-17). No figure is recomputed here, because a drill-down that recomputed its own figure would
be a second opinion about money rather than evidence for the first one.

WHY A CATALOGUE AND NOT ELEVEN FUNCTIONS. A tile is added by adding a ROW. `harness_cash_recon_drilldown.py`
reads this catalogue against the endpoint's own `totals` keys and FAILS THE BUILD when a money tile on
the screen has no declaration — so the screen cannot grow a twelfth undrillable tile, which is exactly
how it grew to eleven.

THE PROVENANCE OF TWO LEGS IS RESOLVED AT RUNTIME, NOT ASSUMED. The processor bill-payment figure and
the sales-transaction figure each come from whichever feed `metric_source_of_truth` resolved for the
range, so the caller hands those table names in rather than this file guessing. A guess here would be
a provenance CLAIM nothing checked, which is worse than the blank it replaces.
"""
from app.modules.core import report_drilldown

#: The keys addressing one row of this report in every table it names: a store-day.
ROW_KEYS = ("store_code", "day")

#: What the reader wants beside the number on every row of every tile. Purely presentational — the
#: tiles differ in their figure, not in what identifies a store-day.
ROW_FIELDS = ("store_name", "market", "reps", "day")


def _label(r):
    return f"{(r or {}).get('store_name') or (r or {}).get('store_code') or '?'} · {(r or {}).get('day') or '?'}"


def _epay_declared(r):
    """The declared bill-pay figure is the two legs the rep declares separately, which is how the
    tile sums it. Both absent is not $0.00 — a store-day with no closing has no declaration."""
    a, b = (r or {}).get("epay_cash_declared"), (r or {}).get("epay_credit_declared")
    if a is None and b is None:
        return None
    return round(float(a or 0) + float(b or 0), 2)


def _mgmt_reason(r):
    """Management counted the envelope but nothing scored it. That is NOT a tie and NOT a zero: it is
    a count no run has compared against its expected figure, and the screen says so."""
    if (r or {}).get("mgmt_counted") is not None and (r or {}).get("mgmt_variance") is None:
        return "not_computed"
    if (r or {}).get("mgmt_counted") is None:
        return "not_recorded"
    return None


def _dm_reason(r):
    """A pickup whose basis refuses the comparison (§75). The two figures count different cash, so
    the difference would be a definition rather than a variance."""
    if (r or {}).get("cash_picked_comparable") is False:
        return "basis_mismatch"
    if (r or {}).get("cash_picked_actual") is None:
        return "not_recorded"
    return None


def _over(field, tol):
    return lambda r: (r or {}).get(field) is not None and (r or {}).get(field) > abs(tol)


def _short(field, tol):
    return lambda r: (r or {}).get(field) is not None and (r or {}).get(field) < -abs(tol)


def _flag(field, want):
    """A COUNT tile: each matching store-day contributes exactly 1, and a row that does not match
    carries no number rather than a 0 — "this day is not one of them" is not a measurement of it."""
    return lambda r: (1 if (r or {}).get(field) == want else None)


def catalogue(tolerance=1.0, *, processor_table=None, sales_table=None):
    """The eleven tiles, declared. `tolerance` is the screen's own band (a request parameter, never a
    constant here); the two feed tables are whatever the range resolved to."""
    tol = abs(float(tolerance or 0))
    DC = ("daily_closing",)
    # A DM-verified correction wins at store-day grain, so the declared legs are reachable in two
    # places and BOTH are named: the rep's sheet and the verification that overrode it.
    DCV = ("daily_closing", "daily_closing_verification")
    CP = ("cash_pickup",)
    BP = ("billpay_pickup",)
    EC = ("envelope_count", "daily_closing")
    PROC = tuple(t for t in (processor_table,) if t)
    SALES = tuple(t for t in (sales_table,) if t)
    # `row_label` names each ROW; each tile's own `label` names the tile. Two words on purpose —
    # see `report_drilldown.tile_panel`, where sharing one name cost every tile its title.
    common = {"row_label": _label, "keys": ROW_KEYS, "fields": ROW_FIELDS, "schema": "commcalc"}
    return (
        {"key": "cash_declared", "label": "Cash declared by the reps",
         "total": "cash_declared", "value": "cash_declared", "tables": DCV,
         "column": "t_cash", **common},
        {"key": "credit_declared", "label": "Credit declared by the reps",
         "total": "credit_declared", "value": "credit_declared", "tables": DCV,
         "column": "t_credit", **common},
        {"key": "epay_declared", "label": "Bill payments declared by the reps",
         "total": "epay_declared", "value": _epay_declared, "tables": DCV,
         "column": "epay_on_cash", **common},
        {"key": "cash_pickup", "label": "Cash collected on pickup",
         "total": "cash_pickup", "value": "cash_pickup", "tables": CP,
         "column": "amount", **common},
        {"key": "billpay_pickup", "label": "Bill-payment cash collected on pickup",
         "total": "billpay_pickup", "value": "billpay_pickup", "tables": BP,
         "column": "amount", **common},
        {"key": "cash_picked_actual", "label": "Cash the district manager counted at pickup",
         "total": "cash_picked_actual", "value": "cash_picked_actual", "tables": CP,
         "column": "actual_picked_amount", "reason": _dm_reason, **common},
        {"key": "mgmt_counted", "label": "Cash management counted",
         "total": "mgmt_counted", "value": "mgmt_counted", "tables": EC,
         "column": "counted_amount", "reason": _mgmt_reason, **common},
        {"key": "mgmt_short_amount", "label": "Short against what management counted",
         "total": "mgmt_short_amount", "value": "mgmt_variance", "tables": EC,
         "column": "variance", "in_figure": _short("mgmt_variance", tol),
         "reason": _mgmt_reason,
         "note": ("Each envelope was scored against ITS OWN expected figure when it was counted, on "
                  "the basis the counter chose — this is that stored score, not a fresh subtraction "
                  "against the whole drawer."), **common},
        {"key": "mgmt_over_amount", "label": "Over against what management counted",
         "total": "mgmt_over_amount", "value": "mgmt_variance", "tables": EC,
         "column": "variance", "in_figure": _over("mgmt_variance", tol),
         "reason": _mgmt_reason, **common},
        {"key": "dm_short_amount", "label": "Short against what the district manager counted",
         "total": "dm_short_amount", "value": "cash_picked_variance", "tables": CP,
         "in_figure": _short("cash_picked_variance", tol), "reason": _dm_reason, **common},
        {"key": "dm_over_amount", "label": "Over against what the district manager counted",
         "total": "dm_over_amount", "value": "cash_picked_variance", "tables": CP,
         "in_figure": _over("cash_picked_variance", tol), "reason": _dm_reason, **common},
        {"key": "pos_billpay", "label": "Bill payments the processor reported",
         "total": "pos_billpay", "value": "pos_billpay", "tables": PROC, **common},
        {"key": "sales_billpay", "label": "Bill payments in the sales transactions",
         "total": "sales_billpay", "value": "sales_billpay", "tables": SALES, **common},
        {"key": "sales_billpay_card",
         "label": "Of those bill payments, the share taken on card",
         "total": "sales_billpay_card", "value": "sales_billpay_card", "tables": SALES,
         "note": ("The tender split of the same sales rows as the figure above it, so these are the "
                  "same store-days read on a different column — not a second population."), **common},
        {"key": "mismatched_store_days",
         "label": "Store-days where the declared and reported bill payments disagree",
         "total": "mismatched_store_days", "value": _flag("billpay_status", "mismatch"),
         "tables": DC + PROC, "unit": report_drilldown.UNIT_COUNT,
         "in_figure": (lambda r: (r or {}).get("billpay_status") == "mismatch"), **common},
        {"key": "three_way_mismatched",
         "label": "Store-days where the three bill-payment legs disagree",
         "total": "three_way_mismatched", "value": _flag("three_way_status", "mismatch"),
         "tables": DC + SALES + PROC, "unit": report_drilldown.UNIT_COUNT,
         "in_figure": (lambda r: (r or {}).get("three_way_status") == "mismatch"), **common},
    )


def drilldowns(rows, totals, tolerance=1.0, *, processor_table=None, sales_table=None, cap=None):
    """{tile key: panel} for every tile the report actually produced a figure for.

    A tile whose figure the report did not produce — the processor feed never resolved for the range,
    so there is no number — is ABSENT rather than served as a panel asserting $0.00 behind nothing.
    """
    return report_drilldown.tiles(
        catalogue(tolerance, processor_table=processor_table, sales_table=sales_table),
        rows, totals, cap=cap or report_drilldown.ROW_CAP)
