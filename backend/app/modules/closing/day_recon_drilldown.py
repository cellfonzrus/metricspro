"""THE DAY RECONS NAME THEIR STORES — drill-down declarations for the ePay bank-deposit recon and the
accessory recon.

§CORE-17 (owner ask 2026-10-24: *"drilldown is needed for every report geenerated from any data which
comming from within the platforma as a defalut"*). Both of these screens ask a manager to act on a
single number — "$14,200 declared, $11,800 deposited" — and neither could say which store-day produced
it. They are declared together because they are the SAME SHAPE: a day's per-store recon of what the
reps declared at closing against what another feed actually recorded. One grain (store × day), one row
label, one set of row fields. Two catalogues, no second fold.

THIS FILE COMPUTES NOTHING. The fold, the tie-out and the provenance roll-up are
`core/report_drilldown.tiles` — the one home every report's tiles go through. A drill-down that
recomputed its own figure would be a second opinion about money rather than evidence for the first one.

PROVENANCE OF THE SALES LEG IS RESOLVED, NEVER ASSUMED. Both reports read their "actual" leg from the
SALES PAIR (the live daily feed and the monthly archive), and which side of the pair answered depends
on whether the day falls in an open month and on which stores each side happens to carry. So the
caller hands in the tables the read ACTUALLY drew rows from (`router._b2b_sales_rows_sourced`) rather
than this file naming both and hoping. Naming a feed that contributed nothing is a provenance CLAIM
nothing checked, which is worse than the blank it replaces.
"""
from app.modules.core import report_drilldown

#: The keys addressing one row of either report in every table it names: a store-DAY. The day is the
#: report's parameter, so both endpoints now stamp `close_date` on their rows — without it a drill-down
#: origin would read "daily_closing where store_code = B-117", which addresses every day there has
#: ever been and so is not an address at all.
ROW_KEYS = ("store_code", "close_date")

def _label(r):
    return str((r or {}).get("store_address") or (r or {}).get("store_code") or "?")


def _reps(r):
    """The people whose closing rows add up to this store's declaration. A manager chasing a variance
    wants the name before the number, and both reports already carry the per-rep breakdown."""
    names = [str((x or {}).get("employee_name") or "").strip()
             for x in ((r or {}).get("reps") or [])]
    return ", ".join([n for n in names if n]) or None


def _row_fields(r):
    """What identifies a row on either report: the store, and who filed its closing. One function
    because both reports fold over the same grain — a second copy would be a future divergence."""
    return {"store_address": (r or {}).get("store_address"),
            "store_code": (r or {}).get("store_code"),
            "rep_names": _reps(r)}


def _flagged(r):
    return 1 if (r or {}).get("flag") else None


def _counted(field):
    """A store counts toward a count-tile only when the report marked it, not when the field is
    merely non-zero."""
    return lambda r: bool((r or {}).get(field))


# ── ePay bank-deposit recon (/closing/epay-recon) ────────────────────────────────────────────────
def _declared_cash(r):
    return ((r or {}).get("declared") or {}).get("cash")


def _sales_cash(r):
    return ((r or {}).get("sales") or {}).get("cash")


def epay_catalogue(*, sales_tables=()):
    """The four tiles on the ePay bank-deposit recon.

    `sales_tables` is the sales pair side(s) the day's read actually answered from — empty when the
    sales read returned nothing, in which case the sales tile's rows carry NO origin rather than a
    table name that supplied none of it.
    """
    DC = ("daily_closing",)
    BANK = ("bank_deposit",)
    SALES = tuple(t for t in (sales_tables or ()) if t)
    common = {"row_label": _label, "keys": ROW_KEYS, "schema": "commcalc",
              "fields": _row_fields}
    return (
        {"key": "declared_cash", "label": "Bill-payment cash the reps declared",
         "total": "declared_cash", "value": _declared_cash, "tables": DC,
         "column": "epay_on_cash", **common},
        {"key": "sales_cash", "label": "Bill-payment cash in the sales transactions",
         "total": "sales_cash", "value": _sales_cash, "tables": SALES,
         "note": ("The bill-payment lines of the day's sales, bucketed to the cash tender — the same "
                  "transactions the sales report reads, not a separate tally."), **common},
        {"key": "bank_deposited", "label": "Cash deposited at the bank",
         "total": "bank_deposited", "value": "bank_deposited", "tables": BANK,
         "column": "amount", **common},
        {"key": "flagged", "label": "Stores whose declared cash and deposit disagree",
         "total": "flagged", "value": _flagged, "tables": DC + BANK,
         "unit": report_drilldown.UNIT_COUNT, "in_figure": _counted("flag"), **common},
    )


def epay_drilldowns(rows, totals, *, sales_tables=(), cap=None):
    return report_drilldown.tiles(epay_catalogue(sales_tables=sales_tables), rows, totals,
                                  cap=cap or report_drilldown.ROW_CAP)


# ── Accessory recon (/closing/accessory-recon) ───────────────────────────────────────────────────
def accessory_catalogue(*, sales_tables=()):
    """The four tiles on the accessory recon.

    The declared leg is the reps' `acc_sale`, OVERLAID by a verified district-manager correction where
    one exists (TKT-1030) — so the declared tile's origin names the verification table as well as the
    closing sheet. Stating only the closing sheet would make a DM's correction look like a rep's entry.
    """
    DC = ("daily_closing", "daily_closing_verification")
    SALES = tuple(t for t in (sales_tables or ()) if t)
    common = {"row_label": _label, "keys": ROW_KEYS, "schema": "commcalc",
              "fields": _row_fields}
    return (
        {"key": "declared", "label": "Accessory sales the reps declared",
         "total": "declared", "value": "declared", "tables": DC, "column": "acc_sale",
         "note": ("A verified district-manager correction is the authoritative declaration for the "
                  "store-day; the per-rep breakdown below stays as filed."), **common},
        {"key": "actual", "label": "Accessory sales in the sales transactions",
         "total": "actual", "value": "actual", "tables": SALES, **common},
        {"key": "variance", "label": "Net difference between declared and actual",
         "total": "variance", "value": "variance", "tables": DC + SALES,
         "note": ("Declared minus actual, folded over the same store-days as the two figures above "
                  "it — one fold, server-side, so the screen cannot show a different difference."),
         **common},
        {"key": "flagged", "label": "Stores whose declared and actual accessory sales disagree",
         "total": "flagged", "value": _flagged, "tables": DC + SALES,
         "unit": report_drilldown.UNIT_COUNT, "in_figure": _counted("flag"), **common},
    )


def accessory_drilldowns(rows, totals, *, sales_tables=(), cap=None):
    return report_drilldown.tiles(accessory_catalogue(sales_tables=sales_tables), rows, totals,
                                  cap=cap or report_drilldown.ROW_CAP)
