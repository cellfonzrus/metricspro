"""THE sales-tax precedence — store > market > org-wide — in ONE place (index §POS-14).

This rule already had a home: `pos/router._resolve_tax`, written when migration 741 added the
`market` column. It had a SECOND, SHORTER copy in the register, which read store then org-wide and
skipped the market rung altogether. So a rate set on a market was shown as `effective` by the
Settings grid (which asks the server) and was NOT charged at the till (which asked itself): the
customer paid the org-wide rate, or nothing at all where no org-wide rate was declared.

Extracted here so there is one rule, reachable without a database — the router dereferences it, and
the browser reaches it through `GET /pos/tax-codes/resolve` rather than re-deriving it. The percent
to fraction conversion the register used to do for `sale_items.tax_rate` comes with it, because a
second place that turns 8.875 into 0.08875 is a second place that can turn it into 0.8875.

PURE: `codes` are `pos.tax_codes` rows, already fetched and org-scoped by the caller.
"""
from __future__ import annotations

SCOPE_STORE, SCOPE_MARKET, SCOPE_ORG, SCOPE_NONE = "store", "market", "org", "none"

#: Said to a cashier, never to a customer. A taxable sale that charged nothing is not recoverable
#: after the customer has left, so `none` is always explicit rather than a silent 0.
NO_RATE_WARNING = "No tax rate applies to this store — a taxable sale would charge $0."


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def pick(codes, store_code, market):
    """The winning row and which rung won it: (row, scope). Inactive rows never win.

    `is_active` is NULLABLE, so it is tested IS NOT FALSE — the platform's standing rule; `== True`
    silently drops every row whose flag was never set.
    """
    live = [c for c in (codes or []) if (c or {}).get("is_active") is not False]
    sc = str(store_code or "").strip()
    mk = str(market or "").strip().lower()
    if sc:
        hit = next((c for c in live if str(c.get("store_code") or "").strip().upper() == sc.upper()), None)
        if hit:
            return hit, SCOPE_STORE
    if mk:
        hit = next((c for c in live if str(c.get("market") or "").strip().lower() == mk), None)
        if hit:
            return hit, SCOPE_MARKET
    hit = next((c for c in live if not str(c.get("store_code") or "").strip()
                and not str(c.get("market") or "").strip()), None)
    return (hit, SCOPE_ORG) if hit else (None, SCOPE_NONE)


def resolve(codes, store_code, market) -> dict:
    """The rate that applies at ONE store, and why.

    `rate` is a PERCENT as the tenant declared it (8.875). `fraction` is what a sale line stores
    (0.08875) and is 0.0 — not None — when nothing applies, so arithmetic at a caller cannot raise
    on it; `scope == "none"` and `warning` are how a caller knows there was no rate.
    """
    hit, scope = pick(codes, store_code, market)
    rate = _num((hit or {}).get("rate"))
    return {"store_code": str(store_code or "").strip() or None,
            "market": str(market or "").strip() or None,
            "scope": scope if rate is not None else (scope if hit else SCOPE_NONE),
            "rate": rate, "fraction": (rate / 100.0) if rate is not None else 0.0,
            "tax_code": hit,
            "warning": NO_RATE_WARNING if rate is None else None}
