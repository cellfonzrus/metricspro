"""Distributor-invoice (VIP) report filtering — ONE selector both the summary and the list read.

RULE FIVE (§3d) retrofit, owner request 2026-10-03: *"add date range and market with standard
filters for distributor invoices"*. The screen had a month / distributor-spelling / status trio of
its own; it now carries the shared core set (date RANGE + market + store multi-select) and keeps
those three as appended module facets.

WHY A MODULE AND NOT TWO MORE `if` BRANCHES IN THE ROUTER. `/vip/summary` (tiles, fees-by-type,
fees-by-store) and `/vip/invoices` (the table + its export) answer the SAME question — "which
invoices are in scope?" — and they used to answer it with two separately-written PostgREST filter
chains. A second date or market condition written into only one of them renders a table whose rows
do not add up to the tiles above them, confidently. So the selection is computed ONCE here, over
one fetched row set, and both endpoints read it.

THE MARKET HALF IS DEREFERENCED, NEVER RE-DERIVED. `commcalc.vip_invoices.location` is a store
ADDRESS in the distributor's own spelling, and the table has no `market` column — so a market
selection has to be resolved through the org's store vocabulary. That vocabulary has exactly one
home (`core.scope.market_index`, read through `account.statement_filter.resolve_store_matcher`,
which the P&L store/market filter already reads) and this module READS it. It deliberately does not
carry its own spelling rules: if the vocabulary learns a new spelling, this report learns it in the
same instant, and it can never resolve a spelling to one store while the P&L resolves it to
another. `harness_vip_invoice_filter.py` fails the build if a sibling matcher appears here.

UNRESOLVED SPELLINGS ARE REPORTED, NOT HIDDEN. A distributor spelling the vocabulary cannot bind
(measured on production 2026-10-03: 3 of 27 — `1 S 60th St`, `1598 Mt Ephraim Ave`,
`228 N Wood Ave`) is EXCLUDED by a market/store selection, because guessing it into a market would
be an invented number. `summarize` therefore counts those rows and the screen says so, so a
missing store reads as a mapping row to add rather than as money that is not there.
"""
from app.modules.commcalc.calculator import safe_float

# The invoice money buckets that make up "fees". One list, read by the totals, the fees-by-type
# panel and the per-store breakdown, so a new bucket cannot reach one of the three and miss two.
FEE_COLS = ['shipping', 'discount', 'other_cost', 'other_deductions', 'tax']

# Columns the summary selector fetches. `created_on` is in the list because the DATE WINDOW reads
# it: `in_date_window` is fail-closed on a row with no date, so a column list that omits it makes
# every window return zero invoices — which is exactly what the first live run of this module did.
# Any caller that passes its own `cols` must include the date field for the same reason.
SUMMARY_COLS = ("location,created_on,sub_total,shipping,discount,other_cost,"
                "other_deductions,tax,grand_total")


def _day(v) -> str:
    """A row timestamp reduced to its YYYY-MM-DD day, '' when there is none. `created_on` is a
    timestamptz ('2026-06-03T08:49:34+00:00'), and a date-range filter is stated in days, so the
    comparison is made on days — never on a timestamp against a bare date, which silently drops
    everything booked after midnight on the `to` day."""
    s = str(v or "").strip()
    return s[:10] if len(s) >= 10 else ""


def in_date_window(row, date_from: str = "", date_to: str = "", field: str = "created_on") -> bool:
    """PURE: is this invoice inside the [date_from, date_to] window, both ends INCLUSIVE?

    An empty end is open. A row with NO date is excluded once a window is set (the same
    fail-closed convention `standard-filters.ts periodOk` uses on the client, so the screen and the
    server agree about a dateless row instead of each dropping a different set)."""
    f, t = _day(date_from), _day(date_to)
    if not f and not t:
        return True
    d = _day(row.get(field) if hasattr(row, "get") else None)
    if not d:
        return False
    if f and d < f:
        return False
    if t and d > t:
        return False
    return True


def select(rows, *, store_matcher=None, date_from: str = "", date_to: str = "",
           date_field: str = "created_on"):
    """PURE: the invoices in scope, and the ones a store/market selection could not resolve.

    `store_matcher` is `statement_filter.build_store_matcher`'s predicate over a store address —
    None means no store/market selection is active, in which case nothing is excluded and
    `unresolved` is empty (an unbindable spelling is only a problem for a selection that has to
    bind it).

    Returns (kept, unresolved): `unresolved` is the subset of date-window rows the matcher rejected,
    so a caller can SAY how many invoices a market selection dropped instead of quietly shrinking
    a total."""
    kept, unresolved = [], []
    for r in (rows or []):
        if not in_date_window(r, date_from, date_to, date_field):
            continue
        if store_matcher is None or store_matcher(r.get("location")):
            kept.append(r)
        else:
            unresolved.append(r)
    return kept, unresolved


def summarize(rows, unresolved=()):
    """PURE: the totals, the fees-by-type panel and the per-store breakdown over `rows`.

    Shape is unchanged from the pre-2026-10-03 `/vip/summary` (a byte-for-byte equivalence the
    harness pins) plus `unresolved`, which reports what a store/market selection could not bind:
    {invoices, grand_total, locations} — never silently folded into the totals."""
    totals = {"invoices": 0, "sub_total": 0.0, "grand_total": 0.0, **{c: 0.0 for c in FEE_COLS}}
    by_store: dict = {}
    for r in (rows or []):
        loc = r.get("location") or "—"
        s = by_store.setdefault(loc, {"location": loc, "invoices": 0, "sub_total": 0.0,
                                      "grand_total": 0.0, **{c: 0.0 for c in FEE_COLS}})
        s["invoices"] += 1
        totals["invoices"] += 1
        for col in ("sub_total", "grand_total", *FEE_COLS):
            v = safe_float(r.get(col))
            s[col] += v
            totals[col] += v
    totals["fees_total"] = sum(totals[c] for c in FEE_COLS)
    unres = list(unresolved or ())
    return {
        "totals": totals,
        "fees_by_type": {c: totals[c] for c in FEE_COLS},
        "by_store": sorted(by_store.values(), key=lambda x: x["grand_total"], reverse=True),
        "unresolved": {
            "invoices": len(unres),
            "grand_total": sum(safe_float(r.get("grand_total")) for r in unres),
            "locations": sorted({r.get("location") for r in unres if r.get("location")}),
        },
    }
