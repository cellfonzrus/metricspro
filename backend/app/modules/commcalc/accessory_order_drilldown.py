"""THE DRILL-DOWN DECLARATION for the Accessory Order Plan (§CORE-17.3z, report §COMMCALC-49).

A DECLARATION, NOT A FOLD. Every row here names a figure the report already produced and says which
rows and which relations are behind it; the folding, the row cap, the tie-out and the panel shape are
`core/report_drilldown`'s, dereferenced. Nothing in this file computes a number.

════════════════════════════════════════════════════════════════════════════════════════════════════
WHAT IT CLAIMS, AND WHAT IT REFUSES TO CLAIM
════════════════════════════════════════════════════════════════════════════════════════════════════
CLAIMED — four COUNTS of device units, each folded over the report's own model rows:

  · `units_in_window`              every model row, its units over the look-back
  · `units_declared`               the rows a human has ruled on
  · `units_awaiting_declaration`   the rows still waiting, which is the figure that says how much of
                                   the order is not yet plannable
  · `projected_devices`            the ruled-current rows, at their projected units — the figure the
                                   whole order multiplies

Each row names the sales relations the look-back was read out of, through
`data_lineage_registry.SALES_DISPLAY_SOURCES` rather than by restating a table name here (§CORE-17.3k).

REFUSED, each for a stated reason:

  · `declared_share` and `projected_devices_per_month` are RATES (§CORE-17.3h) — a share and a
    per-month figure are not sums of anything, and a panel under one would be a second rendering of
    the figures it divides, which are claimed above.
  · THE ACCESSORY QUANTITIES are compositions at the TOTAL (§CORE-17.3k): a case quantity is the
    projected device total times a declared rate, so its rows are not accessory rows at all — they
    are the device rows already opened by `projected_devices`. Serving them again under a different
    tile would look like independent evidence for the same fold.
  · THE COLOUR SPLIT is a declared percentage of a quantity, so it has no rows of its own either.
  · A model AWAITING A RULING carries no projection, and its row is served with its reason rather
    than counted as a projected zero — the honesty rule the report itself rests on.
"""
from app.modules.commcalc import data_lineage_registry as _lin
from app.modules.core import report_drilldown as _dd

#: Where a model row's units came from: the display union every sales report reads. Named once.
SALES_TABLES = tuple(_lin.SALES_DISPLAY_SOURCES)

#: Why a model row carries no projected figure. The words are the reader's, not the field name's.
REASONS = {
    "awaiting_declaration": "nobody has said yet whether this model is current or end of life, so "
                            "projecting it would be the report making that call",
    "end_of_life": "this model was ruled end of life, so it is deliberately not being ordered for",
}


def _row_label(r):
    """A model row named the way the table above the panel names it."""
    return f"#{r.get('rank')} {r.get('model')} — {r.get('lifecycle_label')}"


def _projected(r):
    """The projected units, or None for a model nobody has ruled on — which is what makes the panel
    show a reason instead of a zero."""
    return r.get("projected_units")


def _projection_reason(r):
    basis = r.get("projection_basis")
    return basis if basis in REASONS else None


#: ONE declaration table. A tile is added by adding a ROW here, never by writing another fold.
TILES = (
    {"key": "units_in_window", "total": "units_in_window", "unit": _dd.UNIT_COUNT,
     "label": "Device units sold in the look-back", "row_label": _row_label,
     "value": "units_total", "tables": SALES_TABLES, "keys": ("store", "product_desc"),
     "column": "product_desc",
     "note": "One row per device model, counted over the look-back months the filters selected. The "
             "model is read out of the product description by the one home that owns that parse "
             "(index §62)."},
    {"key": "units_declared", "total": "units_declared", "unit": _dd.UNIT_COUNT,
     "label": "Units on models that have been ruled on", "row_label": _row_label,
     "value": "units_total", "tables": SALES_TABLES, "keys": ("store", "product_desc"),
     "column": "product_desc",
     "in_figure": lambda r: r.get("lifecycle") in ("current", "eol"),
     "note": "The models a human has marked current or end of life. The rows still waiting stay in "
             "the panel as the context that makes the figure readable."},
    {"key": "units_awaiting_declaration", "total": "units_awaiting_declaration",
     "unit": _dd.UNIT_COUNT, "label": "Units still waiting on a ruling", "row_label": _row_label,
     "value": "units_total", "tables": SALES_TABLES, "keys": ("store", "product_desc"),
     "column": "product_desc",
     "in_figure": lambda r: r.get("lifecycle") == "undeclared",
     "note": "Demand that is real and not yet plannable. This is the figure to act on: every row "
             "here is a model somebody has to mark current or end of life."},
    {"key": "projected_devices", "total": "projected_devices", "unit": _dd.UNIT_COUNT,
     "label": "Devices projected over the planning months", "row_label": _row_label,
     "value": _projected, "reason": _projection_reason,
     "tables": SALES_TABLES, "keys": ("store", "product_desc"), "column": "product_desc",
     "in_figure": lambda r: r.get("projected_units") is not None,
     "note": "Each model's own monthly average over the months it was selling, carried across the "
             "planning months. A model nobody has ruled on is shown with its reason rather than as "
             "a projected zero — the whole order rests on that distinction."},
)


def plan_drilldowns(payload, *, schema="commcalc"):
    """PURE: the panels for one Accessory Order Plan payload.

    Folded over the payload's OWN model rows, so a panel can only ever contain rows the caller was
    already served (the filters are query parameters, so the server-side fold is the whole answer)."""
    rows = list((payload or {}).get("models") or ())
    totals = {k: (payload or {}).get(k) for k in
              ("units_in_window", "units_declared", "units_awaiting_declaration",
               "projected_devices")}
    # A figure the report could not produce is skipped rather than served with a zero — `tiles`
    # enforces that for an absent key, and a None total is dropped the same way.
    return _dd.tiles(TILES, rows, {k: v for k, v in totals.items() if v is not None}, schema=schema)


def served(payload):
    """PURE: which tiles can honestly be explained for this payload — the shared refusal rule, not a
    second copy of it (`report_drilldown.served_tiles`)."""
    rows = list((payload or {}).get("models") or ())
    totals = {k: (payload or {}).get(k) for k in
              ("units_in_window", "units_declared", "units_awaiting_declaration",
               "projected_devices")}
    return _dd.served_tiles(TILES, rows, totals)
