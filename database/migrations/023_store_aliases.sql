-- 023_store_aliases.sql — store-name aliases (the rep name_map/rep_aliases equivalent, for STORES).
--
-- The B2B daily-sales file sometimes spells a store differently than commcalc.store_mapping, so the
-- exact-match join in daily_sales_actuals misses and that store's sales land under an unknown code →
-- Daily Targets shows 0 achieved for it. Real example: sales file = "3 Palisade Ave Yonkers" but
-- store_mapping = "3 Palisade Ave" → store_code B-3PL, so B-3PL read 0 despite $25k of sales.
--
-- store_mapping is UNIQUE(org_id, store_code) so a second address row can't be added there, and
-- editing the mapping address would BREAK the asset market join (asset spells it "3 Palisade Ave").
-- An additive alias table consulted by daily_sales_actuals is the safe, reusable fix.
--
-- Run in the Supabase SQL editor (Claude cannot run SQL). Idempotent.

CREATE TABLE IF NOT EXISTS commcalc.store_aliases (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  alias       TEXT NOT NULL,            -- raw store string as it appears in raw_sales.store
  store_code  TEXT NOT NULL,            -- canonical commcalc.store_mapping.store_code
  note        TEXT,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS store_aliases_org_alias_uq
  ON commcalc.store_aliases (org_id, LOWER(TRIM(alias)));
CREATE INDEX IF NOT EXISTS store_aliases_org_idx ON commcalc.store_aliases (org_id);

ALTER TABLE commcalc.store_aliases ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                 WHERE schemaname='commcalc' AND tablename='store_aliases'
                   AND policyname='store_aliases_read') THEN
    CREATE POLICY store_aliases_read ON commcalc.store_aliases
      FOR SELECT TO anon, authenticated USING (true);
  END IF;
END $$;
GRANT SELECT ON commcalc.store_aliases TO anon, authenticated;
GRANT ALL    ON commcalc.store_aliases TO service_role;

-- Re-create daily_sales_actuals (the 013_conversion body) with the alias join folded in.
-- Signature is unchanged, so CREATE OR REPLACE is safe.
CREATE OR REPLACE FUNCTION commcalc.daily_sales_actuals(
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
      COALESCE(sm.store_code, sa.store_code, rs.store)                  AS store_code,
      rs.store                                                          AS store,
      rs.user_login                                                     AS login,
      COALESCE(NULLIF(nm.storeops_name, ''), rs.salesperson)            AS rep_name
    FROM commcalc.raw_sales rs
    LEFT JOIN commcalc.store_mapping sm
      ON sm.org_id = rs.org_id
     AND LOWER(TRIM(sm.store_address)) = LOWER(TRIM(rs.store))
    LEFT JOIN commcalc.store_aliases sa
      ON sa.org_id = rs.org_id
     AND LOWER(TRIM(sa.alias)) = LOWER(TRIM(rs.store))
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
GRANT EXECUTE ON FUNCTION commcalc.daily_sales_actuals(UUID, TEXT) TO anon, authenticated;

-- Seed the known alias (sales-file spelling → canonical store_code).
INSERT INTO commcalc.store_aliases (org_id, alias, store_code, note)
SELECT '00000000-0000-0000-0000-000000000001', '3 Palisade Ave Yonkers', 'B-3PL',
       'B2B sales file adds the Yonkers suffix; store_mapping/asset use "3 Palisade Ave".'
WHERE NOT EXISTS (
  SELECT 1 FROM commcalc.store_aliases
  WHERE org_id = '00000000-0000-0000-0000-000000000001'
    AND LOWER(TRIM(alias)) = LOWER(TRIM('3 Palisade Ave Yonkers'))
);

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 023 complete — store_aliases ready; Daily Targets actuals resolve aliased store names' AS status;
