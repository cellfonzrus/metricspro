"""WHERE DID THIS FIGURE COME FROM — the declaration for **Declared vs Captured** (§CORE-17).

  /closing/declared-vs-captured   what each employee declared at closing against what the sales feed
                                  captured, per person per store per day (§CLOSING-02).

HOW MANY GRAINS — MEASURED OFF THE FIGURES, NOT ASSUMED (§CORE-17.3l)
────────────────────────────────────────────────────────────────────
**TWO**, and for the reason §CORE-17.3m gives: the two figures are Σ over the SAME row out of
DIFFERENT feeds. That is this whole report's subject, so one catalogue over both would assert that a
rep's declaration produced the sales rows it is being checked against.

  1. **DECLARED** — what a person typed at closing, out of `commcalc.closing_metric_line` (a
     category carrying money or a person) and `commcalc.daily_closing` (a store-level tally). Both,
     because `closing/metric_catalog.declared` folds the two physical stores and either may have
     supplied a given row.
  2. **CAPTURED** — what the platform read from the sales pair side the day resolved to, through
     `commcalc/financing_report.vendor_units` for a financing counterpart or `_b2b_day`'s counts for
     a B2B one. The feed is PASSED IN rather than named here, so the panel names the side that
     actually answered and never the pair it could have (§CORE-17.3c).

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **THE THREE VERDICT COUNTS** (agree / disagree / could not check) — the table below them IS that
    list, one row per finding. A panel there would print the same rows twice (§CORE-17.3h's sibling,
    and the ruling `byod_upgrade_drilldown` already took on its case count).
  · **THE VARIANCE** — it is the subtraction of the two figures that DO have panels, so a reader who
    opens either lands on the rows the difference is made of. A third panel over the same rows would
    be the fold printed again, and a variance of None (a check that could not run) is not a figure
    at all.
  · **A $0.00 OVER NO COUNTED ROWS** — refused by the one home's own `served_tiles`, which this
    module dereferences rather than copies.

NOTHING IS WITHHELD WHEN A SIDE IS ABSENT, AND THE REASON IS THE ROW'S OWN
─────────────────────────────────────────────────────────────────────────
The captured panel is served even where the feed produced nothing, because its rows then NAME each
declaration and say in words which absence this is. The reason is NOT re-derived here: every row
already carries the verdict and the sentence `closing/declared_feed_recon` wrote for it, and that
module is the one home for distinguishing the four absences. A second copy of that judgement here
could blame a rep for a feed we never loaded, which is the fake-zero class in words.

A PANEL EXPLAINS THE FIGURE, NOT THE TABLE (§CORE-17.3o)
───────────────────────────────────────────────────────
`/closing/declared-vs-captured` is **SERVER-FOLDED and NOT narrowed**. Its tiles print the server's
own totals as served; the "only the ones that disagree" tick filters the table in the browser and
does not move them. So each panel stays WHOLE and its note says it covers the range — a narrowed
panel would make a tile read a different number under a filter, which is a figure moved.
"""
from app.modules.core import report_drilldown

SCHEMA = "commcalc"

#: The two physical stores a declaration may have come from. Both are named because
#: `metric_catalog.declared` folds them and a given row may have been supplied by either; naming one
#: would be a provenance claim nothing checked.
DECLARED_TABLES = ("closing_metric_line", "daily_closing")


def _declared(row):
    """PURE: a finding's contribution to the declared figure."""
    return (row or {}).get("amount_declared")


def _captured(row):
    """PURE: a finding's contribution to the captured figure. None — never 0.00 — where the check
    could not run, so the one home renders a row with its reason instead of a measurement."""
    r = row or {}
    if r.get("verdict") == "pending":
        return None
    return r.get("amount_captured")


def captured_reason(row):
    """Why a finding carries no captured figure — the row's OWN sentence, written by
    `declared_feed_recon` when it decided which of the four absences this is. Dereferenced, never
    re-derived: deciding it again here is how one surface would start blaming a rep for a feed
    nobody loaded."""
    r = row or {}
    if r.get("verdict") != "pending" and r.get("amount_captured") is not None:
        return None
    return r.get("reason")


def _row_label(row):
    """PURE: how one finding names itself in a panel — the address a manager reads it at."""
    r = row or {}
    return " · ".join(str(b) for b in (
        r.get("store_code") or "no store",
        str(r.get("date") or "?")[:10],
        r.get("rep") or "(store-level)",
        r.get("label") or r.get("metric_key") or "?",
    ))


def _fields(row):
    """PURE: the facts a reader needs to find this finding again on either side."""
    r = row or {}
    return {"day": r.get("date"), "store": r.get("store_code"),
            "employee": r.get("rep") or "(store-level)",
            "category": r.get("label"),
            "checked against": r.get("counterpart"),
            "declared #": r.get("units_declared"), "captured #": r.get("units_captured"),
            "verdict": r.get("verdict")}


_DECLARED_NOTE = ("What the people who closed these days typed in, read out of the closing sheets "
                  "themselves. Covers the whole range — filtering the table below does not move it.")
_CAPTURED_NOTE = ("What the sales feed actually carried for the same category, same person, same "
                  "day, read through the same derivation the Financing report and the payout use. "
                  "A finding with no figure is one the platform could not check — the day's feed was "
                  "never loaded, or the category's counterpart is not set up — and it is listed with "
                  "that reason rather than counted as nothing.")


def catalogue(*, sales_tables=()):
    """PURE: this report's two panels, declared once.

    `sales_tables` is the pair side(s) the range's reads ACTUALLY drew rows from, passed in by the
    endpoint so the captured panel names the feed the figure was read from rather than the pair it
    might have been.
    """
    common = {"row_label": _row_label, "keys": ("date", "store_code", "rep", "metric_key"),
              "fields": _fields, "unit": report_drilldown.UNIT_MONEY, "schema": SCHEMA}
    return (
        {"key": "amount_declared", "total": "amount_declared", "label": "Declared at closing",
         "value": _declared, "tables": DECLARED_TABLES, "column": "amount",
         "note": _DECLARED_NOTE, **common},
        {"key": "amount_captured", "total": "amount_captured", "label": "Captured by the system",
         "value": _captured, "tables": tuple(t for t in (sales_tables or ()) if t),
         "column": "ext_price", "reason": captured_reason, "note": _CAPTURED_NOTE, **common},
    )


def panels(rows, totals, *, sales_tables=(), cap=report_drilldown.ROW_CAP):
    """The served panels — `{tile key: panel}`, through the one home's own rules. `served_tiles`
    decides which figures may be explained at all, so this module keeps no second copy of it."""
    return report_drilldown.tiles(catalogue(sales_tables=sales_tables), list(rows or ()),
                                  dict(totals or {}), schema=SCHEMA, cap=cap)
