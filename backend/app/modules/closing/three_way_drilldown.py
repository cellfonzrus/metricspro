"""THE 3-WAY RECON'S SELECTION TOTALS NAME THEIR STORE-DAYS — declaration + the one fold (§CORE-17.3d).

The 3-way tender recon captures the same tenders three independent ways per store-day — the rep's
closing sheet, the POS X-report, the sales transactions — and the endpoint deliberately returns one
block per day rather than a summed total, because netting a +$50 day against a -$50 day reads as a
clean $0 (owner's explicit caution, 2026-08-03). The screen then shows a "Selection totals" strip over
whatever the reader has filtered down to.

THAT STRIP WAS THE THIRD INSTANCE OF §CORE-17.3d's class: eleven figures folded in TypeScript over
client-filtered rows, with no way to the store-days behind any of them. It is fixed the way the class
is fixed — the server folds the figures ONCE here, the screen narrows the served panels to its own
selection, and each tile reads its number off its narrowed panel. The page's own reduce survives only
as the fallback for a figure no panel was served for.

THE FLATTENING IS ALSO ONE HOME. `flat_rows(days)` is the store-day list the strip is folded over, and
`totals(rows)` is the strip. The screen flattens the same payload for its table, so these two must
agree — which they do because the TILE is read off this fold rather than recomputed beside it.

GROSS IS NOT NETTED, AND THAT IS THE POINT. `gross_cx` / `gross_xs` sum |difference| per store-day, so
offsetting errors cannot hide in them. They are declared as their own tiles with their own value
readers; a reader who opens one sees the absolute contribution of each store-day, not a net.
"""
from app.modules.core import report_drilldown

#: The keys addressing one row of this report in every table it names: a store-DAY.
ROW_KEYS = ("store_code", "close_date")

#: The three legs' tables. The sales leg is the pair, resolved by the caller (§CORE-17.3c) — the
#: X-report and closing legs are single tables and named directly.
CLOSING = ("daily_closing",)
XREPORT = ("pos_tender_summary",)


def flat_rows(days):
    """THE STORE-DAY LIST the selection strip is folded over — one row per (store, day), carrying the
    day it belongs to so a drill row can address it.

    The endpoint returns `days` (one block per calendar date), each with its own `stores`. Flattening
    is done HERE rather than in each consumer because the screen flattens the same payload for its
    table and the two must describe the same population."""
    out = []
    for day in (days or []):
        d = str((day or {}).get("date") or "")[:10]
        for s in ((day or {}).get("stores") or []):
            if not s:
                continue
            t = (s.get("totals") or {})
            out.append({
                "store_code": s.get("store_code"),
                "store_address": s.get("store_address"),
                "close_date": d,
                "closing": t.get("closing"),
                "x_report": t.get("x_report"),
                "sales": t.get("sales"),
                "mismatch": any(not (x or {}).get("match") for x in (s.get("tenders") or [])),
                "x_report_unmapped": ((s.get("x_report_unmapped") or {}).get("amount")
                                      if s.get("x_report_unmapped") else None),
                "sales_unmapped": ((s.get("sales_unmapped") or {}).get("amount")
                                   if s.get("sales_unmapped") else None),
            })
    return out


def _f(v):
    return round(float(v or 0), 2)


def totals(rows):
    """THE SELECTION STRIP, folded once. Net AND gross for each comparison, because net alone hides
    offsetting errors across stores and days — the owner's caution, kept as two figures rather than a
    footnote on one."""
    rows = [r for r in (rows or []) if r]
    out = {
        "closing": round(sum(float(r.get("closing") or 0) for r in rows), 2),
        "x_report": round(sum(float(r.get("x_report") or 0) for r in rows), 2),
        "sales": round(sum(float(r.get("sales") or 0) for r in rows), 2),
        "gross_cx": round(sum(abs(float(r.get("x_report") or 0) - float(r.get("closing") or 0))
                              for r in rows), 2),
        "gross_xs": round(sum(abs(float(r.get("sales") or 0) - float(r.get("x_report") or 0))
                              for r in rows), 2),
        "mismatch_store_days": sum(1 for r in rows if r.get("mismatch")),
        "store_days": len(rows),
        "x_report_unmapped": round(sum(float(r.get("x_report_unmapped") or 0) for r in rows), 2),
        "sales_unmapped": round(sum(float(r.get("sales_unmapped") or 0) for r in rows), 2),
    }
    # The two NET figures are differences of figures above them, folded per row so each is drillable
    # in its own right rather than being arithmetic the screen does on two other tiles.
    out["net_cx"] = round(out["x_report"] - out["closing"], 2)
    out["net_xs"] = round(out["sales"] - out["x_report"], 2)
    return out


def _label(r):
    return f"{(r or {}).get('store_address') or (r or {}).get('store_code') or '?'} · " \
           f"{(r or {}).get('close_date') or '?'}"


def _row_fields(r):
    return {"store_code": (r or {}).get("store_code"),
            "close_date": (r or {}).get("close_date"),
            "verdict": ("mismatch" if (r or {}).get("mismatch") else "agrees")}


def _diff(a, b):
    """x − y for one store-day, or None when neither leg reported anything for it."""
    def read(r):
        x, y = (r or {}).get(a), (r or {}).get(b)
        if x is None and y is None:
            return None
        return round(float(x or 0) - float(y or 0), 2)
    return read


def _absdiff(a, b):
    inner = _diff(a, b)

    def read(r):
        v = inner(r)
        return None if v is None else abs(v)
    return read


def _count(field):
    def read(r):
        return 1 if (r or {}).get(field) else None
    return read


def catalogue(*, sales_tables=()):
    SALES = tuple(t for t in (sales_tables or ()) if t)
    common = {"row_label": _label, "keys": ROW_KEYS, "schema": "commcalc", "fields": _row_fields}
    return (
        {"key": "closing", "label": "Tenders the closing sheet declared",
         "total": "closing", "value": "closing", "tables": CLOSING, **common},
        {"key": "x_report", "label": "Tenders the POS X-report rang up",
         "total": "x_report", "value": "x_report", "tables": XREPORT, **common},
        {"key": "sales", "label": "Tenders in the sales transactions",
         "total": "sales", "value": "sales", "tables": SALES, **common},
        {"key": "net_cx", "label": "Net difference, X-report less closing",
         "total": "net_cx", "value": _diff("x_report", "closing"), "tables": XREPORT + CLOSING,
         "note": ("Netted, so a store-day over and another short can cancel here. The gross figure "
                  "beside it is the one that cannot."), **common},
        {"key": "net_xs", "label": "Net difference, sales less X-report",
         "total": "net_xs", "value": _diff("sales", "x_report"), "tables": SALES + XREPORT,
         **common},
        {"key": "gross_cx", "label": "Gross difference, X-report against closing",
         "total": "gross_cx", "value": _absdiff("x_report", "closing"), "tables": XREPORT + CLOSING,
         "note": ("Every store-day's difference counted at its absolute size, so offsetting errors "
                  "cannot hide in it."), **common},
        {"key": "gross_xs", "label": "Gross difference, sales against the X-report",
         "total": "gross_xs", "value": _absdiff("sales", "x_report"), "tables": SALES + XREPORT,
         **common},
        {"key": "mismatch_store_days", "label": "Store-days where the three legs disagree",
         "total": "mismatch_store_days", "value": _count("mismatch"),
         "tables": CLOSING + XREPORT + SALES, "unit": report_drilldown.UNIT_COUNT,
         "in_figure": (lambda r: bool((r or {}).get("mismatch"))), **common},
        {"key": "x_report_unmapped",
         "label": "X-report money under a tender label this tenant does not recognise",
         "total": "x_report_unmapped", "value": "x_report_unmapped", "tables": XREPORT,
         "note": ("Not a variance — money the mapping could not bucket, which is why it is shown "
                  "rather than absorbed into a leg."), **common},
        {"key": "sales_unmapped",
         "label": "Sales money under a tender label this tenant does not recognise",
         "total": "sales_unmapped", "value": "sales_unmapped", "tables": SALES, **common},
    )


def drilldowns(rows, tots, *, sales_tables=(), cap=None):
    return report_drilldown.tiles(catalogue(sales_tables=sales_tables), rows, tots,
                                  cap=cap or report_drilldown.ROW_CAP)
