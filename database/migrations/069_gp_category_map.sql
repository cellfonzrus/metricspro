-- 069_gp_category_map.sql — per-tenant POS DEPARTMENT → GP category map (de-hardcode Gross Profit / P&L).
--
-- WHY: gp_report.py hard-codes the Boost taxonomy — DEVICE_DEPTS = {'Android - XP','IPHONE - XP',
-- 'TABLET - XP'} → phone sales (by ext_price), 'Ondigo' → accessory GP, a BLANK department → plan GP,
-- and everything else → other GP. A tenant whose POS uses different department names gets wrong/empty
-- Gross Profit and a broken P&L. This table lets each tenant map their own department labels to a GP
-- category, so GP computes correctly for any POS taxonomy.
--
-- ADDITIVE + IDEMPOTENT + BOOST-SAFE: the map is a set of OVERRIDES layered on the built-in defaults.
-- An EMPTY map (the house org, or any tenant before this runs) yields byte-IDENTICAL Gross Profit —
-- the engine falls back to the original hard-coded buckets. No data path changes until a tenant adds rows.
--
-- category ∈ device (sales counted at ext_price) | accessory | plan | other (each counted at GP/margin)
--            | exclude (dropped from GP entirely). department '' matches blank-department rows.

CREATE TABLE IF NOT EXISTS commcalc.gp_category_map (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  department  TEXT NOT NULL,                       -- raw POS department label ('' = blank-department rows)
  category    TEXT NOT NULL DEFAULT 'other',       -- device | accessory | plan | other | exclude
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, department)
);

ALTER TABLE commcalc.gp_category_map ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='gp_category_map' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.gp_category_map FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 069 complete — commcalc.gp_category_map installed (empty = built-in Boost GP defaults)' AS status;
