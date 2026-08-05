-- 278_commission_received_tx_rollup.sql — the VidaPay daily-transaction aggregate the
-- "Commission received breakout" needs (owner directive 2026-08-05).
--
-- OWNER DIRECTIVE 2026-08-05 (verbatim, in-chat):
--   "we need to see what we made in M1 and other months and how much is on ATU and how much is on
--    residual."
--
-- READ-ONLY / REPORTING ONLY. One STABLE aggregate function. No table, no column, no data change, no
-- grant, no policy, no backfill. It moves no payout, no rate, no tier and no calc input.
--
-- WHY IT EXISTS. `commcalc.raw_ma_daily_tx` is the only place a Total/VidaPay tenant's AIRTIME MARGIN
-- and POSTPAID RESIDUAL ORDERS live, and every existing consumer pages the raw rows into Python
-- (whatif._ma_carrier_income pages it 1000 rows at a time; router._compute_gp pulls up to 100k rows
-- just to sum one column). The breakout needs both figures for a 12-month window, per period, which
-- is a distinct-period × 2-column aggregate — a handful of numbers. AGGREGATE IN POSTGRES.
--
-- THE THREE FIGURES, AND WHY THEY ARE THREE (this is the honest part). Two shipped surfaces disagree
-- about what "residual" is for an MA-fed tenant and the disagreement has never been stated on a page:
--   • Gross Profit / P&L  → ATU column = SUM(merchant_discount) over EVERY row      -> airtime_all
--   • MA Overview recon / What-If → residual = SUM(retail_cost) over rows whose Order Type contains
--     the tenant's configured residual order type ('Postpaid Residual Order')        -> residual_orders
--   • …and What-If's airtime leg then sums merchant_discount over the NON-residual rows only, so the
--     overlap between the two readings is the merchant discount sitting ON residual-order rows
--                                                                  -> airtime_residual_orders
-- Returning all three lets the report show both definitions and NAME the overlap, instead of a module
-- silently picking one (which would move a number the owner reads).
--
-- SIGNS ARE NOT APPLIED HERE. Raw sums only — exactly the posture of migration 274's
-- `commission_leg_ma_rollup`. The tenant's configured sign (whatif_source_config.residual_sign,
-- default 'negate' for MA: the export posts money paid TO the dealer as negative) is applied in the
-- backend so the rule stays CONFIG, not SQL.
--
-- PERIOD SPELLING. `p_periods` is the caller's `_pvariants()` list, so 'June 2026' and '2026-06' both
-- match. A `= period` here would silently return zero rows — the recurring bug class in this module.
--
-- DEGRADES GRACEFULLY. Until this runs, `/commcalc/commission-received-breakout` falls back to a
-- bounded per-period read of raw_ma_daily_tx (most recent months only) and says so on the page. No
-- other surface reads this function, so an un-run 278 breaks nothing.

CREATE OR REPLACE FUNCTION commcalc.commission_received_tx_rollup(
    p_org_id uuid, p_periods text[], p_residual_pattern text DEFAULT 'Postpaid Residual Order')
RETURNS TABLE (period text,
               airtime_all numeric,
               airtime_residual_orders numeric,
               residual_orders numeric,
               n bigint,
               n_residual bigint)
LANGUAGE sql STABLE AS $$
  SELECT t.period,
         sum(coalesce(t.merchant_discount, 0)),
         sum(CASE WHEN t.order_type ILIKE '%' || coalesce(p_residual_pattern, 'Postpaid Residual Order') || '%'
                  THEN coalesce(t.merchant_discount, 0) ELSE 0 END),
         sum(CASE WHEN t.order_type ILIKE '%' || coalesce(p_residual_pattern, 'Postpaid Residual Order') || '%'
                  THEN coalesce(t.retail_cost, 0) ELSE 0 END),
         count(*),
         count(*) FILTER (WHERE t.order_type ILIKE '%' || coalesce(p_residual_pattern, 'Postpaid Residual Order') || '%')
    FROM commcalc.raw_ma_daily_tx t
   WHERE t.org_id = p_org_id
     AND t.period = ANY (p_periods)
   GROUP BY 1;
$$;

COMMENT ON FUNCTION commcalc.commission_received_tx_rollup(uuid, text[], text) IS
  'VidaPay/master-agent daily transactions rolled up per period for the Commission Received breakout: airtime_all = SUM(merchant_discount) over every row (the SAME figure the Gross Profit report''s ATU column shows for an MA-fed org); residual_orders = SUM(retail_cost) over rows whose Order Type matches the tenant''s residual order type (the /ma-overview-recon + What-If basis); airtime_residual_orders = the overlap between the two readings. RAW sums — the backend applies the tenant''s configured sign. Reporting only; never an input to a payout.';
