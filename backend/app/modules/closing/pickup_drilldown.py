"""THE CASH-PICKUP FOOTERS NAME THEIR ENVELOPES — the drill-down declaration for /closing/pickup
(§CORE-17).

A DM reads a bottom line — "$41,200 declared, $38,900 actually counted, 11 envelopes with no count" —
and acts on the gap. Until now there was no way from any of those figures to the envelopes behind it.

WHY THIS IS THE PLAIN TILE SHAPE AND NOT THE NARROWED ONE (§CORE-17.3d). §CORE-17.3b named this screen
as a sibling of the client-folded-tile class, on the strength of its own comment — "footer totals over
the envelopes CURRENTLY ON SCREEN". That reading was half wrong, and checking it is why this is a short
file: every filter on this page (date, store, market, employee, DM) is a QUERY PARAMETER, so the server
already returns exactly the envelopes on screen. The footers fold over a server-filtered list, which is
the same population the panels are folded over — no narrowing is needed and none is applied.

THE ACTUAL-PICKED FIGURES COVER ONLY THE ENVELOPES THAT CARRY A COUNT, and that is the whole point of
them: adding a declared amount into an "actual" total would manufacture agreement, and treating an
unrecorded envelope as $0.00 would manufacture a 100% shortfall. The endpoint already refuses both, and
the declaration reads that refusal off the same fields — a collected envelope with no count appears in
the panel with its reason, un-counted, so a reader can see what the figure does and does not cover.
"""
from app.modules.core import report_drilldown

#: The keys addressing one envelope: a store-day, per the person who filed it. Two envelopes for one
#: store-day are two people's declarations, so the employee is part of the address.
ROW_KEYS = ("store_code", "close_date", "employee_name")

#: The envelope's declaration is a closing row; what was actually counted at pickup is the pickup
#: record. Which of the two a figure came from is the difference between "what the store said" and
#: "what the district manager found", so the legs are never named together on a one-leg figure.
DECLARED = ("daily_closing",)
PICKED = ("cash_pickup",)


def _label(r):
    return " · ".join(str(x) for x in (
        (r or {}).get("store_name") or (r or {}).get("store_code") or "?",
        str((r or {}).get("close_date") or "?")[:10],
        (r or {}).get("employee_name") or "(no name)",
    ))


def _row_fields(r):
    return {"store_code": (r or {}).get("store_code"),
            "employee_name": (r or {}).get("employee_name"),
            "state": ("collected" if (r or {}).get("picked_up") else "waiting"),
            "counted": (None if (r or {}).get("actual_picked_amount") is None
                        else (r or {}).get("actual_picked_amount"))}


def _collected(field):
    """A figure that only covers the COLLECTED envelopes carrying a count. An envelope that is still
    waiting, or was collected with no count recorded, reads None here — never its declared amount and
    never $0.00."""
    def read(r):
        if not (r or {}).get("picked_up"):
            return None
        if (r or {}).get("actual_picked_amount") is None:
            return None
        return (r or {}).get(field)
    return read


def _counted_variance(r):
    if not (r or {}).get("picked_up") or (r or {}).get("actual_picked_amount") is None:
        return None
    return round(float((r or {}).get("actual_picked_amount") or 0)
                 - float((r or {}).get("cash") or 0), 2)


def _reason(r):
    """Why an envelope has no number on a counted-side figure. Both answers are facts about the
    envelope, not about the figure, so they read the same on every tile that excludes it."""
    if not (r or {}).get("picked_up"):
        return "not_collected"
    if (r or {}).get("actual_picked_amount") is None:
        return "not_recorded"
    return None


def _state(want_picked):
    def read(r):
        return 1 if bool((r or {}).get("picked_up")) is want_picked else None
    return read


def _cash_when(want_picked):
    def read(r):
        return (r or {}).get("cash") if bool((r or {}).get("picked_up")) is want_picked else None
    return read


def _flag(r):
    return 1 if (r or {}).get("deposit_flagged") else None


def catalogue():
    common = {"row_label": _label, "keys": ROW_KEYS, "schema": "commcalc", "fields": _row_fields}
    counted = dict(common, reason=_reason)
    COUNT = report_drilldown.UNIT_COUNT
    return (
        {"key": "total_cash", "label": "Cash the stores declared",
         "total": "total_cash", "value": "cash", "tables": DECLARED, **common},
        {"key": "ready_cash", "label": "Declared cash still waiting to be collected",
         "total": "ready_cash", "value": _cash_when(False), "tables": DECLARED,
         "in_figure": (lambda r: not (r or {}).get("picked_up")), **common},
        {"key": "collected_cash", "label": "Declared cash on the envelopes collected",
         "total": "collected_cash", "value": _cash_when(True), "tables": DECLARED,
         "note": ("The DECLARED amount on a collected envelope — the figure a short pickup does not "
                  "change, which is why the counted figures below it exist."),
         "in_figure": (lambda r: bool((r or {}).get("picked_up"))), **common},
        {"key": "collected_actual", "label": "Cash actually counted at pickup",
         "total": "collected_actual", "value": _collected("actual_picked_amount"),
         "tables": PICKED,
         "in_figure": (lambda r: (r or {}).get("picked_up")
                       and (r or {}).get("actual_picked_amount") is not None), **counted},
        {"key": "collected_actual_variance",
         "label": "Difference between what was counted and what was declared",
         "total": "collected_actual_variance", "value": _counted_variance,
         "tables": PICKED + DECLARED,
         "note": ("Only over the envelopes that carry a count. A collected envelope with no count "
                  "is shown below with its reason and is NOT treated as $0.00."),
         "in_figure": (lambda r: (r or {}).get("picked_up")
                       and (r or {}).get("actual_picked_amount") is not None), **counted},
        {"key": "total_cash_sales", "label": "Of the declared cash, the merchandise share",
         "total": "total_cash_sales", "value": "cash_sales", "tables": DECLARED, **common},
        {"key": "total_cash_gross", "label": "Declared cash before any bill-payment netting",
         "total": "total_cash_gross",
         "value": (lambda r: (r or {}).get("cash_gross") if (r or {}).get("cash_gross") is not None
                   else (r or {}).get("cash")),
         "tables": DECLARED,
         "note": ("The gross column: an envelope with no separate gross figure reads as the amount "
                  "being collected, which is what it is."), **common},
        {"key": "ready", "label": "Envelopes waiting to be collected",
         "total": "ready", "value": _state(False), "tables": DECLARED, "unit": COUNT,
         "in_figure": (lambda r: not (r or {}).get("picked_up")), **common},
        {"key": "collected", "label": "Envelopes collected",
         "total": "collected", "value": _state(True), "tables": PICKED, "unit": COUNT,
         "in_figure": (lambda r: bool((r or {}).get("picked_up"))), **common},
        {"key": "collected_actual_envelopes", "label": "Collected envelopes that carry a count",
         "total": "collected_actual_envelopes",
         "value": (lambda r: 1 if ((r or {}).get("picked_up")
                                   and (r or {}).get("actual_picked_amount") is not None) else None),
         "tables": PICKED, "unit": COUNT,
         "in_figure": (lambda r: (r or {}).get("picked_up")
                       and (r or {}).get("actual_picked_amount") is not None), **counted},
        {"key": "collected_actual_missing", "label": "Collected envelopes with no count recorded",
         "total": "collected_actual_missing",
         "value": (lambda r: 1 if ((r or {}).get("picked_up")
                                   and (r or {}).get("actual_picked_amount") is None) else None),
         "tables": PICKED, "unit": COUNT,
         "note": ("These are the envelopes every counted figure above excludes — the reason it "
                  "covers less than the whole pickup."),
         "in_figure": (lambda r: (r or {}).get("picked_up")
                       and (r or {}).get("actual_picked_amount") is None), **common},
        {"key": "flagged", "label": "Envelopes flagged on deposit",
         "total": "flagged", "value": _flag, "tables": PICKED, "unit": COUNT,
         "in_figure": (lambda r: bool((r or {}).get("deposit_flagged"))), **common},
    )


def drilldowns(rows, totals, cap=None):
    return report_drilldown.tiles(catalogue(), rows, totals, cap=cap or report_drilldown.ROW_CAP)
