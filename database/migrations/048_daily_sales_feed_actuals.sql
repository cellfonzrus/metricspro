-- 048_daily_sales_feed_actuals.sql — sibling of commcalc.daily_sales_actuals over the DAILY FEED.
-- Run this in the Supabase SQL editor (Claude cannot run SQL). Idempotent (CREATE OR REPLACE).
--
-- THEME 5(2) — intra-month MTD freshness for Daily Targets.
-- daily_sales_actuals (013) aggregates the AUTHORITATIVE monthly file (commcalc.raw_sales). During the
-- open month that file lags (it's re-uploaded periodically), so the Daily Targets MTD "achieved" can be
-- stale. This function is an EXACT copy of daily_sales_actuals with the source table swapped to the daily
-- B2B feed (commcalc.daily_sales_feed, migration 047) — identical RETURNS TABLE shape and identical
-- aggregation, so the router can merge the two per (store, rep, day) with the feed winning intra-month.
--
-- Additive + safe: nothing reads this until the router's _fetch_actuals calls it for the OPEN month, and
-- that call is wrapped in try/except — if this function is absent, targets behave exactly as before.

CREATE OR REPLACE FUNCTION commcalc.daily_sales_feed_actuals(
  p_org_id UUID,
  p_period TEXT
)
RETURNS TABLE (
  store_code     TEXT,
  store          TEXT,
  rep_name       TEXT,
  login          TEXT,
  trans_date     DATE,
  prem_count     BIGINT,
  byod_count     BIGINT,
  upg_count      BIGINT,
  acc_gp         NUMERIC,
  box_count      BIGINT,
  billpay_count  BIGINT
)
LANGUAGE sql
STABLE
AS $$
  WITH s AS (
    SELECT
      rs.trans_date,
      rs.trans_id,
      rs.contract_type,
      rs.department,
      rs.product_desc,
      rs.gp,
      COALESCE(sm.store_code, rs.store)                                AS store_code,
      rs.store                                                          AS store,
      rs.user_login                                                     AS login,
      COALESCE(NULLIF(nm.storeops_name, ''), rs.salesperson)            AS rep_name
    FROM commcalc.daily_sales_feed rs
    LEFT JOIN commcalc.store_mapping sm
      ON sm.org_id = rs.org_id
     AND LOWER(TRIM(sm.store_address)) = LOWER(TRIM(rs.store))
    LEFT JOIN commcalc.name_map nm
      ON nm.org_id = rs.org_id
     AND LOWER(TRIM(nm.epay_login)) = LOWER(TRIM(rs.user_login))
    WHERE rs.org_id = p_org_id
      AND rs.period = p_period
      AND rs.trans_date IS NOT NULL
      AND COALESCE(UPPER(TRIM(rs.voided)), '') <> 'YES'
      AND COALESCE(TRIM(rs.trans_type), '') <> 'Return'
      AND COALESCE(TRIM(rs.salesperson), '') <> ''
      AND LOWER(TRIM(rs.salesperson)) <> 'admin'
  )
  SELECT
    s.store_code,
    MAX(s.store) AS store,
    s.rep_name,
    MAX(s.login) AS login,
    s.trans_date,
    COUNT(DISTINCT s.trans_id) FILTER (WHERE s.contract_type = ANY(ARRAY[
        'Activation','Port-In','Add A Line','Port-In Add A Line',
        'Eligible Port-In Activation','Activation Add A Line','Eligible Port-In Add A Line',
        'PML Ineligible Port In Activation']))   AS prem_count,
    COUNT(DISTINCT s.trans_id) FILTER (WHERE s.contract_type = ANY(ARRAY[
        'BYOD','BYOD Port-In','BYOD Add A Line','BYOD Port-In Add A Line',
        'BYOD Swap','BYOD Eligible Port-In']))   AS byod_count,
    COUNT(DISTINCT s.trans_id) FILTER (WHERE s.contract_type = ANY(ARRAY[
        'Upgrade','Upgrade Port-In','Device Upgrade']))    AS upg_count,
    COALESCE(SUM(s.gp) FILTER (WHERE s.department = 'Ondigo'), 0)                            AS acc_gp,
    COUNT(*) FILTER (WHERE s.department = ANY(ARRAY['Android - XP','IPHONE - XP','TABLET - XP'])) AS box_count,
    COUNT(DISTINCT s.trans_id) FILTER (
        WHERE s.product_desc ILIKE '%Boost RTR%'
           OR s.product_desc ILIKE '%Xfinity Prepaid Refill%')                              AS billpay_count
  FROM s
  GROUP BY s.store_code, s.rep_name, s.trans_date;
$$;

GRANT EXECUTE ON FUNCTION commcalc.daily_sales_feed_actuals(UUID, TEXT) TO anon, authenticated;
