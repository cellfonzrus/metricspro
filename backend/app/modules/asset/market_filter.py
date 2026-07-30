"""Shared "(no market)" sentinel/bucket helper for the asset module (mod-asset).

WHY THIS FILE EXISTS (dedupe follow-up, 2026-07-29): the market-filter-dropdown package
(asset-13, `agent/asset/market-filter-dropdown`, landed on origin/main as `2ed44ba`/`cff89b0`)
and the On-Inventory 3-Way Recon package (asset-14, `agent/asset/oninv-3way-recon`, landed as
`358876f`/`48406d0`) were built concurrently in separate worktrees, each starting from a
pre-dedupe `origin/main`. Per AGENT_CONTRACT §7 ("each package stays independently
mergeable/rejectable"), asset-14 deliberately duplicated the tiny pieces of asset-13's
not-yet-merged "(no market)" convention it needed rather than importing in-flight code, and
BOTH packages' own docstrings/handoff entries flagged the duplication for a merge-time dedupe
pass once both landed. This file is that pass.

Every asset-module call site that filters `commcalc.asset_ledger.market` (or translates the
`market` query param for an RPC's `p_market`/`p_no_market_only` parameters) should import from
here rather than redefining its own copy. Nothing about the sentinel VALUE or the filtering
semantics changes versus either prior copy — this is a pure move/consolidation, verified
byte-identical via the pre-existing offline harnesses (`harness_asset_market_filter.py`,
`harness_asset_oninv_3way_recon.py`), which access these names via module attribute (e.g.
`router.NO_MARKET_SENTINEL`, `oninv_recon.NO_MARKET_SENTINEL`) — re-exporting an imported name
via `from ... import x` preserves that attribute access unchanged.

Do NOT confuse this with `retail-ops`'s own "(no market)" literal (a separate module, separate
file tree, separate call sites) — that is intentionally NOT consolidated here; see this
module's handoff for the cross-module convergence note filed instead of built.
"""

# Reserved market value (never a real market name) a caller passes to explicitly select rows
# whose market is NULL/blank — the "(no market)" bucket — instead of "" (which means "no filter"
# everywhere in this module) silently making those rows unreachable from every market filter.
NO_MARKET_SENTINEL = "__no_market__"


def _apply_market_filter(q, market: str):
    """Apply the `market` query param to a Supabase/PostgREST query builder against a table with
    a `market` text column (asset_ledger today). `market == NO_MARKET_SENTINEL` selects the "(no
    market)" bucket (NULL or blank) instead of silently returning nothing. Any other non-empty
    value is an ordinary exact match — safe because every asset market dropdown is sourced from
    GET /filter-options, built from the real distinct values already on the rows (pick-don't-type,
    RULE THREE), so what's offered always exactly matches what's stored; no case-folding needed on
    the read side (that risk lives entirely upstream, in how the value got INTO the column, which
    is what _backfill_market's normalization addresses)."""
    if market == NO_MARKET_SENTINEL:
        return q.or_("market.is.null,market.eq.")
    if market:
        return q.eq("market", market)
    return q


def _market_matches(row_market, market: str) -> bool:
    """Python-side equivalent of _apply_market_filter, for callers filtering an already-fetched
    list instead of a live query builder."""
    if not market:
        return True
    if market == NO_MARKET_SENTINEL:
        return not row_market
    return row_market == market


def _store_list(store: str):
    """Comma-separated multi-select store query param (RULE FIVE), same shape used by the Aging
    and On-Inventory-by-Store reports and the On-Inventory 3-Way Recon report. Empty string / no
    param = no store filter."""
    return [s.strip() for s in (store or "").split(",") if s.strip()]


def resolve_market_for_rpc(market: str):
    """Translate a `market` filter value (possibly NO_MARKET_SENTINEL) into the two pieces an
    RPC-backed endpoint needs: the ordinary market value to pass through to the RPC (None when
    not filtering OR when selecting the no-market bucket — an RPC's exact-match p_market can
    never select NULL/blank rows itself), and whether the caller explicitly asked for the "(no
    market)" bucket. Returns (p_market, is_no_market).

    Two prior call sites computed this exact same translation independently: router.py's
    get_charges_summary (bypasses the RPC's p_market when is_no_market, then Python-filters the
    returned aggregate rows to falsy-market only, rather than teaching the verified mig-304 SQL
    function a new NULL-handling branch) and oninv_recon.py's _call_recon_rpc (passes a dedicated
    p_no_market_only boolean the mig-310 RPC itself branches on). Only what each caller DOES with
    the two returned values differs — the translation math was byte-identical."""
    is_no_market = market == NO_MARKET_SENTINEL
    p_market = None if is_no_market else (market or None)
    return p_market, is_no_market
