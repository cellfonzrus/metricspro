-- 302_asset_market_gap_rpc.sql — mod-asset · settings-audit package (2026-07-26)
--
-- WHY: commcalc.asset_ledger.market is backfilled at upload time (router.py's _backfill_market) by
-- matching the raw `store` text VIP sent (its own "Billing Address 1" column) against
-- commcalc.store_mapping (exact match) or the router's hardcoded MARKET_OVERRIDES dict. A store
-- string that matches NEITHER is silently left with market = NULL — the row still exists and still
-- carries a real $ owed/reimbursement figure, but it silently drops out of every market-filtered
-- asset report (Charges Dashboard, RMA, Aging, Owed-Weekly all accept a `market` query param).
-- Nothing today tells an admin this happened.
--
-- WHAT THIS IS: one cheap, org-scoped Postgres aggregate — total ledger rows, how many have no
-- market, and a short example list of the offending store strings — so an admin-attention provider
-- (backend/app/modules/asset/attention.py) can surface it WITHOUT pulling the 43k+-row ledger into
-- Python (per AGENT_CONTRACT §6 / CLAUDE.md: aggregate in Postgres, not Python).
--
-- SAFE: purely additive (CREATE OR REPLACE), idempotent, read-only (never writes). Degrades
-- gracefully if unrun — attention.py's provider treats a missing-function error as "nothing to
-- report" and stays silent (contract §5); no other asset page calls this function, so nothing else
-- can break.

CREATE OR REPLACE FUNCTION commcalc.asset_market_gap(p_org_id uuid)
RETURNS TABLE (total_rows bigint, unmapped_rows bigint, unmapped_stores text[])
LANGUAGE sql
STABLE
AS $$
  WITH base AS (
    SELECT store, market
    FROM commcalc.asset_ledger
    WHERE org_id = p_org_id
  ),
  examples AS (
    SELECT DISTINCT store
    FROM base
    WHERE store IS NOT NULL AND btrim(store) <> ''
      AND (market IS NULL OR btrim(market) = '')
    ORDER BY store
    LIMIT 5
  )
  SELECT
    (SELECT count(*) FROM base)                                            AS total_rows,
    (SELECT count(*) FROM base
       WHERE market IS NULL OR btrim(market) = '')                         AS unmapped_rows,
    (SELECT coalesce(array_agg(store), ARRAY[]::text[]) FROM examples)     AS unmapped_stores;
$$;

GRANT EXECUTE ON FUNCTION commcalc.asset_market_gap(uuid) TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT '302 complete — commcalc.asset_market_gap(uuid) aggregate for the market-backfill-gap admin check' AS status;
