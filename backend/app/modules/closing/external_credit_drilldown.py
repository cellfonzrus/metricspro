"""THE CARD-SETTLEMENT TILES NAME THEIR STORE-DAYS — the drill-down declaration for
/closing/external-credit-recon.

§CORE-17 (owner ask 2026-10-24). This report's six tiles are the hardest of the day recons to take on
trust, because two of its statuses mean *we do not know*: `no_processor_data` (the portal scrape has
not landed for that day) and `dm_merged` (the district manager corrected the card total without
stating the external split). `external_credit_recon.totals` already refuses to let those rows reach a
dollar sum — they count only. The drill-down's job is to make that refusal VISIBLE: a gap row appears
in the panel, carrying its reason, un-counted, so the reader can see that a tile reading $0.00 short
is not the same thing as a balanced day.

THIS FILE COMPUTES NOTHING. The fold, the tie-out and the provenance roll-up are
`core/report_drilldown.tiles`.

THE SETTLED LEG'S TABLE IS CONFIG, NOT A CONSTANT. Which table holds the processor's settled totals is
a mig-207 registry row (`_settlement_feed_spec`), so the caller hands the resolved table name in. When
no feed is registered the settled tile's rows carry NO origin — which is the honest answer, and the
one the `feeds.registered` banner on the screen already gives in words.
"""
from app.modules.core import report_drilldown
#: The statuses that mean "we do not know", not "zero". Dereferenced from the report's own module so
#: a status added there cannot silently start reading as a measured zero here.
from .external_credit_recon import GAP_STATUSES

#: The keys addressing one row of this report: a store-day, per processor role. Three keys, because
#: one store-day carries one tally line PER ROLE and a two-key address would be ambiguous.
ROW_KEYS = ("store_code", "close_date", "processor_role")


def _label(r):
    return " · ".join(str(x) for x in (
        (r or {}).get("store_address") or (r or {}).get("store_code") or "?",
        (r or {}).get("close_date") or "?",
        (r or {}).get("role_title") or (r or {}).get("processor_role") or "?",
    ))


def _row_fields(r):
    return {"store_code": (r or {}).get("store_code"),
            "market": (r or {}).get("market"),
            "processor": (r or {}).get("role_title") or (r or {}).get("processor_role"),
            "status": (r or {}).get("status"),
            "basis": (r or {}).get("declared_basis")}


def _reason(r):
    """Why a row has no number. The report's own status IS the reason, so this reads it rather than
    re-deriving one — and a status the report calls a gap is never counted."""
    st = (r or {}).get("status")
    return st if st in GAP_STATUSES else None


def _money(field):
    """A gap row contributes NOTHING, not 0.00 — the same refusal `totals` makes, read off the same
    status so the two can never disagree."""
    def read(r):
        if (r or {}).get("status") in GAP_STATUSES:
            return None
        return (r or {}).get(field)
    return read


def _short(r):
    v = _money("variance")(r)
    return abs(v) if (r or {}).get("status") == "short" and v is not None else None


def _over(r):
    v = _money("variance")(r)
    return v if (r or {}).get("status") == "over" and v is not None else None


def _status_count(want):
    def read(r):
        return 1 if (r or {}).get("status") == want else None
    return read


def _cell(r):
    """Every tally line counts toward `cells`, gaps included — it is a count of lines, not of money,
    and the report's own total counts them the same way."""
    return 1


def catalogue(*, settlement_table=None):
    DC = ("daily_closing", "daily_closing_verification")
    SET = tuple(t for t in (settlement_table,) if t)
    common = {"row_label": _label, "keys": ROW_KEYS, "schema": "commcalc",
              "fields": _row_fields, "reason": _reason}
    return (
        {"key": "cells", "label": "Store-days tallied",
         "total": "cells", "value": _cell, "tables": DC + SET,
         "unit": report_drilldown.UNIT_COUNT, "in_figure": (lambda r: True), **common},
        {"key": "declared_total", "label": "Card settlement the stores declared",
         "total": "declared_total", "value": _money("declared_amount"), "tables": DC,
         "note": ("A district manager's merged card total is NOT counted here: it states one figure "
                  "for both card legs, so attributing part of it to this one would be a guess."),
         **common},
        {"key": "settled_total", "label": "What the processor settled",
         "total": "settled_total", "value": _money("settled_amount"), "tables": SET, **common},
        {"key": "short_total", "label": "Short — the processor settled less than the store declared",
         "total": "short_total", "value": _short, "tables": DC + SET,
         "in_figure": (lambda r: (r or {}).get("status") == "short"), **common},
        {"key": "over_total", "label": "Over — the processor settled more than the store declared",
         "total": "over_total", "value": _over, "tables": DC + SET,
         "in_figure": (lambda r: (r or {}).get("status") == "over"), **common},
        {"key": "short", "label": "Store-days short",
         "total": "short", "value": _status_count("short"), "tables": DC + SET,
         "unit": report_drilldown.UNIT_COUNT,
         "in_figure": (lambda r: (r or {}).get("status") == "short"), **common},
        {"key": "over", "label": "Store-days over",
         "total": "over", "value": _status_count("over"), "tables": DC + SET,
         "unit": report_drilldown.UNIT_COUNT,
         "in_figure": (lambda r: (r or {}).get("status") == "over"), **common},
        {"key": "match", "label": "Store-days that agree",
         "total": "match", "value": _status_count("match"), "tables": DC + SET,
         "unit": report_drilldown.UNIT_COUNT,
         "in_figure": (lambda r: (r or {}).get("status") == "match"), **common},
    )


def drilldowns(rows, totals, *, settlement_table=None, cap=None):
    return report_drilldown.tiles(catalogue(settlement_table=settlement_table), rows, totals,
                                  cap=cap or report_drilldown.ROW_CAP)
