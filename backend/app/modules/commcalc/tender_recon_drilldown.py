"""THE X-REPORT vs CLOSING TILES NAME THEIR ROWS — the tender-recon declaration (§CORE-17.3a).

THE AUDIT RANKED THIS SECOND of the 93, and for a reason worth stating: *"six variance tiles plus
per-store POS-vs-closing deltas, no click target — while the sibling `/closing/tender-recon-3way`
already has `/closing/tender-drilldown`. Same question, one path drilled and one not."* One question
answered two ways, one of them with evidence and one without, is the duplicate defect the index rules
forbid wearing a hat — so the undrilled path is the one that moves.

A ROW HERE IS A STORE, NOT A STORE-DAY. The endpoint folds the X-report and the closing sheet to one
row per store over the chosen day or month, so a panel's rows are stores and its keys are the store
the figure is addressed by. Saying so matters: a reader who expects days would read the row count as
a coverage claim.

WHAT IT COMPUTES: NOTHING. Per tile it names the `totals` key, the field on the report's own rows,
the tables and the predicate; `core/report_drilldown.tiles` does the fold and the tie-out. The
variance tiles tie because Σ(POS) − Σ(closing) over a store set is Σ(POS − closing) over the same
set — and if that ever stops being true of the endpoint, the tie-out says so on screen rather than
quietly.
"""
from app.modules.core import report_drilldown

#: The POS side. The X-report's own tender rows are the actual, and are never overridden.
POS = ("pos_tender_summary",)
#: The CLOSING side names BOTH its homes: the rep's sheet, and the DM-verified correction that is the
#: authoritative closing figure where one exists (TKT-1030). Naming only the sheet would credit a
#: corrected figure to the row that was corrected.
CLOSING = ("daily_closing", "daily_closing_verification")


def _flag(field, want=False):
    """A COUNT tile. A store that matches contributes no number rather than a 0 — "this store is not
    one of them" is not a measurement of it."""
    return lambda r: (1 if bool((r or {}).get(field)) is want else None)


def catalogue():
    common = {"row_label": "store", "keys": ("store",), "schema": "commcalc",
              "fields": ("store", "in_pos", "in_closing", "match")}
    return (
        {"key": "pos_cash", "label": "Cash the X-report rang up",
         "total": "pos_cash", "value": "pos_cash", "tables": POS, "column": "amount", **common},
        {"key": "closing_cash", "label": "Cash the closing sheet declared",
         "total": "closing_cash", "value": "closing_cash", "tables": CLOSING,
         "column": "store_cash", **common},
        {"key": "pos_card", "label": "Card the X-report rang up",
         "total": "pos_card", "value": "pos_card", "tables": POS, "column": "amount", **common},
        {"key": "closing_card", "label": "Card the closing sheet declared",
         "total": "closing_card", "value": "closing_card", "tables": CLOSING,
         "column": "store_cc", **common},
        {"key": "cash_variance", "label": "Cash difference, POS less closing",
         "total": "cash_variance", "value": "cash_variance", "tables": POS + CLOSING,
         "note": ("Positive means the register rang up more cash than the sheet declared. A store "
                  "present on only ONE side still appears, because a missing side is the finding."),
         **common},
        {"key": "card_variance", "label": "Card difference, POS less closing",
         "total": "card_variance", "value": "card_variance", "tables": POS + CLOSING, **common},
        {"key": "mismatches", "label": "Stores outside the tolerance band",
         "total": "mismatches", "value": _flag("match", False),
         "tables": POS + CLOSING, "unit": report_drilldown.UNIT_COUNT,
         "in_figure": (lambda r: not (r or {}).get("match")), **common},
    )


def drilldowns(rows, totals, cap=None):
    return report_drilldown.tiles(catalogue(), rows, totals,
                                  cap=cap or report_drilldown.ROW_CAP)
