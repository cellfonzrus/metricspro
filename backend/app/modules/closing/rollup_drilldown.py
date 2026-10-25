"""A DASHBOARD TILE IS A STORE-DAY SUM, AND SAYS SO — the declaration for the Daily Closing
dashboard's tile strip (§CORE-17).

Twelve tiles sit at the top of `/closing`, and every one of them has been clickable since 2026-08-03
— a click switches to the submissions table and narrows it. That is a drill-down into the ROWS. What
no tile ever said is WHERE THE FIGURE CAME FROM, which on this report is not the obvious answer.

THE GRAIN IS THE STORE-DAY, AND THAT IS NOT A STYLE CHOICE. The tiles are not Σ(rep submissions).
A DM who corrects a store-day's money in DM Verify writes six `dm_*` figures onto
`daily_closing_verification`, and `verified_overlay` makes that correction authoritative FOR THE
STORE-DAY — its own docstring is explicit that it "is applied to store-day AGGREGATES only" and that
per-rep detail keeps each rep's raw submitted figures, because a store-day correction cannot be
attributed to one rep. So a drill panel of rep-days would NOT add up to the tile on any day a DM
corrected, and the discrepancy would be read as a defect in the report rather than as the design.
Store-days after the overlay are the one population that adds up to these figures.

WHICH MEANS TWO TABLES, ROW BY ROW:

  · `daily_closing`              — the reps' own submitted figures, summed for the store-day. Every
                                   row has these; they are what a store-day is.
  · `daily_closing_verification` — the DM's corrected total, on the store-days a DM actually
                                   corrected AND verified. Named ONLY on those rows, so a reader can
                                   see at a glance which days are the rep's numbers and which are
                                   the manager's.

THE COUNTS ARE NOT CORRECTED, AND THE PANEL SHOWS IT. A DM's correction covers the six money
figures; activations, upgrades and the submission count are the reps' own, overlay or no overlay. A
reader who sees `daily_closing_verification` against a day's cash and not against its activations is
reading something true.

NOTHING HERE RE-DERIVES A FIGURE. `cash` and `credit` are each the sum of the two columns the screen
has always added (`store_cash + epay_cash`, `store_cc + epay_cc`) and `activations` is the sum of the
two it has always added (`new_line_count + postpaid_count`) — read off the store-day row the endpoint
folded, never recomputed from the submissions. `days` has no panel here at all: it is a DISTINCT
count of dates, not a sum over rows, so there is no set of rows that adds up to it, and it is a
tile's sub-label rather than a figure.
"""
from app.modules.core import report_drilldown

#: One store-day's address.
ROW_KEYS = ("store_code", "close_date")

#: The reps' submitted figures. On every row.
CLOSING = ("daily_closing",)
#: The DM's corrected store-day total. Only on the store-days a DM corrected.
VERIFICATION = ("daily_closing_verification",)


def _label(r):
    r = r or {}
    return " · ".join(str(x) for x in (
        r.get("store_address") or r.get("store_name") or r.get("store_code") or "(no store)",
        str(r.get("close_date") or "?")[:10],
    ))


def _row_fields(r):
    r = r or {}
    return {"store_code": r.get("store_code"),
            "market": r.get("market"),
            # Whose numbers these are. The whole reason the two tables are named separately.
            "figures": "DM corrected" if r.get("dm_corrected") else "as submitted",
            "submissions": r.get("rows"),
            "dm_verified": bool(r.get("verified"))}


def _tables(r):
    """A MONEY figure's row: the closing sheet always, and the verification table only where a DM's
    correction actually answered for this store-day. Naming it on every row would claim an authority
    that was never exercised."""
    return CLOSING + (VERIFICATION if (r or {}).get("dm_corrected") else ())


def _count_tables(_r):
    """A COUNT figure's row: the closing sheet, always and only. A DM's correction covers the six
    money figures, so an activation or an upgrade on a corrected store-day is still the reps own
    number and must not be shown as the manager's. Caught by `harness_batch7_drilldown.py` §E4,
    which found the money reader being reused here."""
    return CLOSING


def _sum_of(*fields):
    """Σ of the columns the screen has always added for this tile, read off the store-day row."""
    def read(r):
        r = r or {}
        return round(sum(float(r.get(f) or 0) for f in fields), 2)
    return read


def _count_of(*fields):
    def read(r):
        r = r or {}
        return sum(int(r.get(f) or 0) for f in fields)
    return read


#: Said on the four money tiles the overlay can move: the figure on a corrected day is the manager's,
#: not the rep's, and the panel says which rows those are.
_DM_NOTE = ("On a store-day a district manager corrected and verified, the figure is the manager's "
            "corrected total for the whole store-day, not the reps' submitted sum — the rows say "
            "which days those are.")
#: Said on the count tiles, for the same reason read the other way.
_COUNT_NOTE = ("A district manager's correction covers the money figures only, so these counts are "
               "the reps' own on every day, including the days whose money was corrected.")


def catalogue(*, processor_label="Bill-pay"):
    """`processor_label` is the tenant's own name for the bill-payment processor, resolved by the
    caller from the `processor` report term — this file spells no carrier or processor name."""
    common = {"row_label": _label, "keys": ROW_KEYS, "schema": "commcalc",
              "fields": _row_fields, "tables": _tables}
    money = dict(common, note=_DM_NOTE)
    counts = dict(common, unit=report_drilldown.UNIT_COUNT, note=_COUNT_NOTE,
                  tables=_count_tables)
    return (
        {"key": "cash", "label": "Cash collected", "total": "cash",
         "value": _sum_of("store_cash", "epay_cash"), **money},
        {"key": "credit", "label": "Credit collected", "total": "credit",
         "value": _sum_of("store_cc", "epay_cc"), **money},
        {"key": "epay_on_cash", "label": f"{processor_label} cash", "total": "epay_on_cash",
         "value": _sum_of("epay_on_cash"),
         **dict(common, note=("Already counted inside Cash collected — a breakdown of that figure, "
                              "not a separate one. " + _DM_NOTE))},
        {"key": "epay_on_cc", "label": f"{processor_label} credit", "total": "epay_on_cc",
         "value": _sum_of("epay_on_cc"),
         **dict(common, note=("Already counted inside Credit collected — a breakdown of that figure, "
                              "not a separate one. " + _DM_NOTE))},
        {"key": "acc_sale", "label": "Accessory sales", "total": "acc_sale",
         "value": _sum_of("acc_sale"), **money},
        {"key": "other_account", "label": "Other tenders", "total": "other_account",
         "value": _sum_of("other_account"), **money},
        {"key": "activations", "label": "Activations", "total": "activations",
         "value": _count_of("new_line_count", "postpaid_count"), **counts},
        {"key": "upgrade_count", "label": "Upgrades", "total": "upgrade_count",
         "value": _count_of("upgrade_count"), **counts},
        {"key": "rows", "label": "Rep submissions", "total": "rows",
         "value": _count_of("rows"),
         **dict(counts, note=("One per rep-day closing filed. The store-days are the rows; a day "
                              "with three reps on it is one row carrying three submissions."))},
        # THE DM-VERIFIED COVERAGE PAIR. Two counts of store-days, so the store-day grain is the
        # figure's own grain here rather than a consequence of the overlay.
        {"key": "submitted_keys", "label": "Store-days with a closing filed", "total": "submitted_keys",
         "unit": report_drilldown.UNIT_COUNT,
         "value": (lambda r: 1 if (r or {}).get("store_code") else None),
         "in_figure": (lambda r: bool((r or {}).get("store_code"))),
         **dict(common, note=("A store-day with no store code at all cannot be matched to a "
                              "verification, so it is shown here and not counted."))},
        {"key": "verified_keys", "label": "Store-days a district manager verified",
         "total": "verified_keys", "unit": report_drilldown.UNIT_COUNT,
         "value": (lambda r: 1 if (r or {}).get("verified") else None),
         "in_figure": (lambda r: bool((r or {}).get("verified"))),
         **dict(common, note=("The unverified store-days are shown here, uncounted — they are the "
                              "work this figure is measuring."))},
    )


def totals(store_days, *, verified_keys=None, submitted_keys=None):
    """PURE: the figures the dashboard's own tiles show, folded from the SAME store-day rows the
    panels are built from.

    IT DOES NOT REPLACE THE ENDPOINT'S `totals`. The dashboard keeps reading `grand`; this exists so
    the drill-down's `tiles()` call has a figure to tie each panel against, and so the harness can
    assert the two folds agree. Two folds that must agree, checked, beat one fold nobody can check.
    """
    rows = store_days or []
    # Bare 0 seeds, for the reason the sibling declaration gives: the locks ban a float literal in a
    # declaration so a threshold cannot be copied in, and the two locks must not disagree about
    # whether a zero is one. `round(0 + x, 2)` is the same arithmetic.
    out = {"cash": 0, "credit": 0, "epay_on_cash": 0, "epay_on_cc": 0,
           "acc_sale": 0, "other_account": 0,
           "activations": 0, "upgrade_count": 0, "rows": 0}
    for r in rows:
        out["cash"] = round(out["cash"] + _sum_of("store_cash", "epay_cash")(r), 2)
        out["credit"] = round(out["credit"] + _sum_of("store_cc", "epay_cc")(r), 2)
        for k in ("epay_on_cash", "epay_on_cc", "acc_sale", "other_account"):
            out[k] = round(out[k] + _sum_of(k)(r), 2)
        out["activations"] += _count_of("new_line_count", "postpaid_count")(r)
        out["upgrade_count"] += _count_of("upgrade_count")(r)
        out["rows"] += _count_of("rows")(r)
    if submitted_keys is not None:
        out["submitted_keys"] = submitted_keys
    if verified_keys is not None:
        out["verified_keys"] = verified_keys
    return out


def drilldowns(store_days, figures, *, processor_label="Bill-pay", cap=None):
    return report_drilldown.tiles(catalogue(processor_label=processor_label), store_days, figures,
                                  cap=cap or report_drilldown.ROW_CAP)
