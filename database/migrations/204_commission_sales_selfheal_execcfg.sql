-- 204_commission_sales_selfheal_execcfg.sql — org-level self-healing sales promotion helper +
--   the Executive MTD metric-definition config (mod-commission, band 200-299).
--
-- WHY (owner report 2026-07-15): on a non-house tenant (luxelink 854f6d7b…) the "Daily Sales" reports
-- show nothing even though the daily B2B feed IS ingesting (daily_sales_feed has thousands of July rows).
-- ROOT CAUSE: the feed→raw_sales promotion (_promote_feed_to_raw_sales) is only invoked as a SIDE-EFFECT
-- of the email sweep processing a NEW attachment (router.py ~9126). If no fresh attachment is fetched in a
-- run (deliveries paused, all already-deduped, or a fix deployed after the last delivery), raw_sales never
-- catches up to the feed — and ~10 display consumers that read raw_sales with NO feed fallback (gp_report,
-- sales_analyzer, top-sellers, discrepancy phantom, accessory-flags, fraud scan, plus the finance P&L and
-- the retail closing _b2b_day) all show empty. The fix is an org-AGNOSTIC, scheduled, self-healing promote
-- job that reconciles feed→raw_sales for every tenant's OPEN month regardless of fresh deliveries.
--
-- Two objects, both ADDITIVE + IDEMPOTENT (safe to re-run):
--   1. commcalc.sales_feed_orgs_for_period(text[])  — the distinct org_ids (+ feed row counts) that have
--      daily_sales_feed rows for a set of period spellings. Lets POST /commcalc/sales/promote-due iterate
--      every tenant in one sub-second query instead of scanning the feed. (The endpoint degrades to a
--      bounded distinct scan if this RPC is absent, so the feature works before this migration runs.)
--   2. commcalc.exec_metric_config  — per-tenant, admin-editable metric DEFINITIONS for the Executive MTD
--      summary (Total Phones / Bill Payment / Accessory sales / Activation fee / Total Protect + the
--      activation split). SAP-configurable rule: these are CONFIG rows (not a ninth hard-coded accessory
--      classifier — see the "accessory flow diverges across ~8 surfaces" note), seeded with the defaults
--      DERIVED from the real luxelink Sales-Transaction-Details sample, editable per tenant.
--
-- NON-money: (1) is an org-agnostic promotion HELPER (promotion merges + dedups by trans_id, guarded by
-- the existing retain guard, OPEN month only — the calculator reads the feed for the open month regardless,
-- so Boost pay is unchanged and a trans_id in both tables is counted once); (2) drives a DISPLAY report.
-- Neither changes any commission rate/tier/plan/calculator.

-- ── 1. sales_feed_orgs_for_period: distinct tenants with feed rows for a period (self-healing promote) ──
CREATE OR REPLACE FUNCTION commcalc.sales_feed_orgs_for_period(p_periods text[])
RETURNS TABLE(org_id uuid, feed_rows bigint)
LANGUAGE sql STABLE AS $$
  SELECT f.org_id, count(*)::bigint AS feed_rows
  FROM commcalc.daily_sales_feed f
  WHERE f.period = ANY(p_periods)
  GROUP BY f.org_id;
$$;
GRANT EXECUTE ON FUNCTION commcalc.sales_feed_orgs_for_period(text[]) TO anon, authenticated, service_role;

-- ── 2. exec_metric_config: per-tenant, UI-editable Executive-MTD metric definitions ──────────────────
CREATE TABLE IF NOT EXISTS commcalc.exec_metric_config (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL,
  bucket     TEXT NOT NULL,                 -- 'activation' | 'phones' | 'bill_payment' | 'accessory'
                                            -- | 'activation_fee' | 'protect'
  rules      JSONB NOT NULL DEFAULT '{}'::jsonb,  -- token lists matched case-insensitively over a sales
                                            -- line. Keys: category / department / product_desc_contains /
                                            -- exclude_category / exclude_department /
                                            -- exclude_product_desc_contains. For 'activation':
                                            -- {byod:[…],upgrade:[…],port:[…]} keyword-CONTAINS on contract_type.
  basis      TEXT NOT NULL DEFAULT 'count', -- 'count' (line count) | 'ext_price' (sum of ext_price)
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (org_id, bucket)
);
CREATE INDEX IF NOT EXISTS exec_metric_config_org ON commcalc.exec_metric_config (org_id);

DO $$
DECLARE t text := 'commcalc.exec_metric_config';
BEGIN
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
  EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
  EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
  EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
END $$;

-- Seed the canonical Executive-MTD definitions for one tenant. Idempotent (ON CONFLICT DO NOTHING) so it
-- never overwrites a tenant's edited definitions. Defaults derived from the real luxelink Total-Wireless
-- Sales-Transaction-Details export (System Category CellPhone/RTR Product/Accessory + the Category-column
-- variant KittedBranded/HandsetBranded/Other Carr. payments — the ingest stores whichever the file carries,
-- so both token sets are listed). Tokens are lowercase; the engine lowercases the sale line before matching.
CREATE OR REPLACE FUNCTION commcalc.seed_exec_metric_config(p_org uuid)
RETURNS void LANGUAGE plpgsql AS $fn$
BEGIN
  INSERT INTO commcalc.exec_metric_config (org_id, bucket, rules, basis) VALUES
    (p_org, 'activation',
     '{"byod":["byod"],"upgrade":["upgrade"],"port":["port"]}'::jsonb, 'count'),
    (p_org, 'phones',
     '{"category":["cellphone","kittedbranded"]}'::jsonb, 'count'),
    (p_org, 'bill_payment',
     '{"department":["rtr"],"category":["rtr product","other carr. payments"]}'::jsonb, 'count'),
    (p_org, 'accessory',
     '{"category":["accessory","handsetbranded","accessories"]}'::jsonb, 'ext_price'),
    (p_org, 'activation_fee',
     '{"product_desc_contains":["access charge"]}'::jsonb, 'ext_price'),
    (p_org, 'protect',
     '{"product_desc_contains":["protect"],"exclude_product_desc_contains":["screen protect"],"exclude_department":["rtr"],"exclude_category":["rtr product","other carr. payments"]}'::jsonb, 'count')
  ON CONFLICT (org_id, bucket) DO NOTHING;
END;
$fn$;
GRANT EXECUTE ON FUNCTION commcalc.seed_exec_metric_config(uuid) TO anon, authenticated, service_role;

-- Seed the house org + back-fill every existing tenant now (idempotent; skips any bucket already present).
DO $seed$
DECLARE t record;
BEGIN
  PERFORM commcalc.seed_exec_metric_config('00000000-0000-0000-0000-000000000001');
  BEGIN
    FOR t IN SELECT org_id FROM storeops.tenants LOOP
      PERFORM commcalc.seed_exec_metric_config(t.org_id);
    END LOOP;
  EXCEPTION WHEN undefined_table THEN
    NULL;  -- storeops.tenants absent in a bare env → house seed above still applied
  END;
END $seed$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 204 complete — sales self-heal org helper + exec_metric_config' AS status;
