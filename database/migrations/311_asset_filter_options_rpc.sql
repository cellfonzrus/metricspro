-- 311_asset_filter_options_rpc.sql — mod-asset · filter-options-aggregate hardening (2026-07-30)
--
-- WHY: GET /asset/filter-options (backend/app/modules/asset/router.py, get_filter_options) derives
-- the distinct store/market dropdown values by pulling the ENTIRE org's asset_ledger (store,market)
-- columns into Python via a sequential .range() pagination loop — up to 100 x 1000-row round trips
-- against a 43k+-row table just to compute a handful of distinct values. This violates the project
-- convention (CLAUDE.md / AGENT_CONTRACT §6: "aggregate in Postgres, never fetch-all-then-filter" —
-- a full asset scan in Python is ~14s, an RPC is sub-second) and, per the 2026-07-30 failure_log
-- triage, each of those ~44 independent round trips is its own chance to hit the platform-wide
-- stale-pooled-connection failure class (one production 500 on 7/29 traced to exactly this
-- endpoint). This migration replaces the loop with ONE Postgres aggregate query.
--
-- WHAT THIS RETURNS (call-site contract — see backend/app/modules/asset/router.py,
-- _filter_options_via_rpc / get_filter_options, the only caller):
--   markets          jsonb array of every DISTINCT non-blank asset_ledger.market value for the org,
--                     across EVERY row — INDEPENDENT of whether that row's store is blank. This
--                     mirrors the old Python loop's `if r.get("market"): markets.add(r["market"])`,
--                     which ran unconditionally on market alone (there is no `and s` gate on that
--                     branch) — a row with a real market but a blank store still contributed to the
--                     set. Do not "fix" this to be store-scoped; it would silently diverge from the
--                     legacy fallback path and break the byte-identity guarantee.
--   stores            jsonb array of {"store","market","row_count"} — one entry per distinct
--                     non-blank asset_ledger.store value, "row_count" = total ledger rows for that
--                     store (any market), "market" = that store's assigned market (see tie-break
--                     note below).
--   no_market_count   count of ledger rows whose store is non-blank and whose market is NULL/blank
--                     (mirrors the old code's `elif s: no_market_count += 1`, only reached when
--                     store is present).
--
-- PER-STORE MARKET TIE-BREAK (the one intentional, documented behavior refinement): the old Python
-- loop did `store_to_market[s] = r.get("market")` unconditionally on EVERY row for that store, so
-- the final value was whichever row an UNORDERED .range() scan happened to return LAST for that
-- store — never a documented guarantee, just an accident of physical/scan order. This migration
-- instead picks, per store, the MOST FREQUENT non-null market value (ties broken alphabetically for
-- a fully deterministic result), falling back to NULL only when a store has no non-null market rows
-- at all. In real data this is a no-op difference: commcalc.asset_ledger.market is stamped by
-- router.py's _backfill_market, which always UPDATEs every row for a given store in one statement
-- (see market_filter.py / 302_asset_market_gap_rpc.sql precedent), so a store's own rows agree on
-- market (or are uniformly NULL) by construction — the only case this tie-break differs from the
-- old code is a genuine data-quality anomaly (one store literally carrying two different market
-- values across its own rows), where "most frequent, deterministic" is a strictly better answer
-- than an implementation-artifact "whichever row was scanned last".
--
-- SAFE: purely additive (CREATE OR REPLACE), idempotent, read-only (never writes to asset_ledger or
-- anywhere else). Degrades gracefully if unrun — get_filter_options feature-detects a missing-
-- function error (same _is_missing_schema_error() helper the asset-2 staging-swap package already
-- uses) and falls back to the exact pre-existing fetch-all-pages scan, so this endpoint NEVER 500s
-- for a not-yet-run migration; it just keeps paying the old ~44-round-trip cost until the owner runs
-- this file.
--
-- Per AGENT_CONTRACT §5 (2026-07-28 anon/authenticated grant lockdown): this migration grants EXECUTE
-- to service_role ONLY — the backend's service-role client is the sole caller, and the contract is
-- explicit that "all app data access goes through the backend's service role ... and needs no
-- [anon/authenticated] grants." (Earlier asset migrations 301/302/304/310 predate/precede that
-- lockdown being enforced and still grant anon/authenticated — not touched here, out of scope for
-- this package; flagged in the handoff for whoever next does a grants sweep.)

CREATE OR REPLACE FUNCTION commcalc.asset_filter_options(p_org_id uuid)
RETURNS TABLE (markets jsonb, stores jsonb, no_market_count bigint)
LANGUAGE sql
STABLE
AS $$
  WITH ledger AS (
    -- Mirrors the old Python loop's truthiness checks EXACTLY: a "blank" string is None or ''
    -- only (no whitespace-trimming) — `if r.get("store"):` / `if r.get("market"):` in Python are
    -- falsy for None or '' alone, so this deliberately does NOT btrim() either column (a
    -- whitespace-only store/market string would be "truthy" in Python and must stay truthy here).
    -- Every org row lands here (store presence is NOT filtered at this stage — see `markets`
    -- below, which must see rows even when their store is blank).
    SELECT store, nullif(market, '') AS market
    FROM commcalc.asset_ledger
    WHERE org_id = p_org_id
  ),
  store_base AS (
    -- store IS NOT NULL AND store <> '' -- mirrors the old loop's `if s:` gate, which guards
    -- store_to_market/store_counts AND the no_market_count increment (but NOT the markets.add
    -- branch -- that one stays unfiltered, computed straight from `ledger` above).
    SELECT store, market FROM ledger WHERE store IS NOT NULL AND store <> ''
  ),
  store_rows AS (
    SELECT store, count(*) AS row_count
    FROM store_base
    GROUP BY store
  ),
  store_market_counts AS (
    SELECT store, market, count(*) AS cnt
    FROM store_base
    GROUP BY store, market
  ),
  market_pick AS (
    -- One row per store: the (market IS NULL) ASC / cnt DESC / market ASC ordering means a
    -- non-null market always outranks NULL, the most-frequent non-null value wins among those,
    -- and ties break alphabetically -- see the tie-break note above.
    SELECT DISTINCT ON (store) store, market
    FROM store_market_counts
    ORDER BY store, (market IS NULL) ASC, cnt DESC, market ASC
  )
  SELECT
    (SELECT coalesce(jsonb_agg(DISTINCT market), '[]'::jsonb)
       FROM ledger WHERE market IS NOT NULL)                                  AS markets,
    (SELECT coalesce(jsonb_agg(jsonb_build_object(
              'store', sr.store, 'market', mp.market, 'row_count', sr.row_count
            )), '[]'::jsonb)
       FROM store_rows sr
       LEFT JOIN market_pick mp ON mp.store = sr.store)                       AS stores,
    (SELECT count(*) FROM store_base WHERE market IS NULL)                    AS no_market_count;
$$;

GRANT EXECUTE ON FUNCTION commcalc.asset_filter_options(uuid) TO service_role;

NOTIFY pgrst, 'reload schema';
SELECT '311 complete — commcalc.asset_filter_options(uuid) — one-query aggregate for GET /asset/filter-options (replaces a ~44-round-trip sequential fetch-all scan)' AS status;
