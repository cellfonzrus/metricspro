-- 013_conversion.sql — Conversion metric for Daily Targets.
-- Run this in the Supabase SQL editor (Claude cannot run SQL).
--
-- Conversion = boxes sold ÷ bill-payments (walk-ins), target 30%, per STORE and per REP.
--   boxes        = device-department line items (Android - XP / IPHONE - XP / TABLET - XP)
--   bill payments = transactions whose Product Desc is a recharge: "Boost RTR" or
--                   "Xfinity Prepaid Refill" (counted once per trans_id).
-- Extends commcalc.daily_sales_actuals (from 006_targets.sql) with box_count + billpay_count
-- per (store, rep, day). DROP+CREATE because the RETURNS TABLE signature changes.

DROP FUNCTION IF EXISTS commcalc.daily_sales_actuals(UUID, TEXT);

CREATE FUNCTION commcalc.daily_sales_actuals(
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
    FROM commcalc.raw_sales rs
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
    -- boxes = device-department line items (each phone/tablet sold)
    COUNT(*) FILTER (WHERE s.department = ANY(ARRAY['Android - XP','IPHONE - XP','TABLET - XP'])) AS box_count,
    -- bill payments = recharge transactions (Boost RTR / Xfinity Prepaid Refill), per trans
    COUNT(DISTINCT s.trans_id) FILTER (
        WHERE s.product_desc ILIKE '%Boost RTR%'
           OR s.product_desc ILIKE '%Xfinity Prepaid Refill%')                              AS billpay_count
  FROM s
  GROUP BY s.store_code, s.rep_name, s.trans_date;
$$;

GRANT EXECUTE ON FUNCTION commcalc.daily_sales_actuals(UUID, TEXT) TO anon, authenticated;
NOTIFY pgrst, 'reload schema';
SELECT 'Migration 013 complete — conversion (boxes ÷ bill-payments) ready' as status;
