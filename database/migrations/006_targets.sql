-- MIGRATION 006: DAILY SALES TARGETS
-- Adds the per-store monthly targets config table, the daily-actuals aggregation
-- RPC that powers the targets engine, and a forward-prep user<->employee link column.
-- Safe to run multiple times.

-- ────────────────────────────────────────────────────────────
-- 1. Targets config (one row per store + period, fully editable)
--    Monthly targets are defined at the start of the month and reverse-
--    calculated to per-day / per-rep by the StoreOps schedule in the engine.
--    Units: activations/upgrades = transaction counts, accessories = $ GP.
--    byod_pct: % of activations expected to be BYOD (KPI-derived target);
--    NULL falls back to commcalc.payout_config.kpi_byod_target.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.targets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  store_code TEXT NOT NULL,
  period TEXT NOT NULL, period_month INT, period_year INT,
  activations_monthly NUMERIC DEFAULT 0,
  upgrades_monthly NUMERIC DEFAULT 0,
  accessories_monthly NUMERIC DEFAULT 0,
  byod_pct NUMERIC,
  notes TEXT,
  updated_by TEXT,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(org_id, store_code, period)
);
CREATE INDEX IF NOT EXISTS targets_period ON commcalc.targets(org_id, period);

-- ────────────────────────────────────────────────────────────
-- 2. Daily actuals aggregation RPC (heavy work stays in Postgres)
--    Returns, per (store_code, rep, day): premium / byod / upgrade act counts
--    (distinct trans_id) and accessory GP ($). Sales store-address is mapped to
--    store_code via store_mapping; rep name is resolved via name_map when present.
--    Mirrors the contract-type sets and accessory rule in calculator.py.
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION commcalc.daily_sales_actuals(
  p_org_id UUID,
  p_period TEXT
)
RETURNS TABLE (
  store_code   TEXT,
  store        TEXT,
  rep_name     TEXT,
  login        TEXT,
  trans_date   DATE,
  prem_count   BIGINT,
  byod_count   BIGINT,
  upg_count    BIGINT,
  acc_gp       NUMERIC
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
    COALESCE(SUM(s.gp) FILTER (WHERE s.department = 'Ondigo'), 0)                            AS acc_gp
  FROM s
  GROUP BY s.store_code, s.rep_name, s.trans_date;
$$;

-- ────────────────────────────────────────────────────────────
-- 3. Forward-prep: link a logged-in user to an employee (for the future
--    per-rep auth phase / subscriber model). Just an id; no FK to avoid
--    cross-schema coupling. Nothing reads it yet.
-- ────────────────────────────────────────────────────────────
ALTER TABLE core.users ADD COLUMN IF NOT EXISTS employee_id BIGINT;

-- ────────────────────────────────────────────────────────────
-- 4. RLS + grants (match sibling commcalc tables: blanket open_all)
-- ────────────────────────────────────────────────────────────
DO $$
BEGIN
  EXECUTE 'ALTER TABLE commcalc.targets ENABLE ROW LEVEL SECURITY';
  EXECUTE 'DROP POLICY IF EXISTS open_all ON commcalc.targets';
  EXECUTE 'CREATE POLICY open_all ON commcalc.targets FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)';
EXCEPTION WHEN OTHERS THEN RAISE NOTICE 'targets RLS skipped: %', SQLERRM;
END $$;

GRANT ALL ON commcalc.targets TO anon, authenticated;
GRANT EXECUTE ON FUNCTION commcalc.daily_sales_actuals(UUID, TEXT) TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 006 complete — daily sales targets ready' as status;
