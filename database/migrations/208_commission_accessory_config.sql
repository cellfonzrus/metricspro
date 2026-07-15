-- 208_commission_accessory_config.sql — PER-ORG accessory classification config.
--
-- WHY: the accessory classifier (_accessory_config) read commcalc.flag_rules, which is a hard SINGLETON
-- (id SMALLINT PK DEFAULT 1 + CONSTRAINT flag_rules_singleton CHECK (id=1); see mig 041). The whole table
-- can hold AT MOST ONE row (the house/Boost org's), so _accessory_config(<non-house org>) found no row and
-- fell back to the house default department 'Ondigo'. A tenant whose POS uses different accessory labels
-- (Luxelink/Total b2bsoft: System Category 'Accessory' → Category 'HandsetBranded' / 'Accessories') never
-- matched → Sales-Report Accessory$ = $0. Worse, put_accessory_config upserted {id:1, org_id:<caller>} on
-- CONFLICT(id) → a non-house save OVERWROTE the house row's org_id (a multi-tenant corruption).
--
-- FIX: a real per-org table keyed on org_id. _accessory_config resolves from here first, so each tenant
-- carries its own accessory departments/categories/product-keywords/ACIMA-tenders.
--
-- BOOST-SAFE (byte-identical): backfilled from flag_rules so the house org's CURRENT accessory config is
-- preserved EXACTLY (empty arrays → the code still falls back to ['Ondigo'], unchanged). No sales/GP/pay
-- number moves for the house org. Additive + idempotent + degrades gracefully (the resolver falls back to
-- flag_rules until this runs).

CREATE TABLE IF NOT EXISTS commcalc.accessory_config (
  org_id            UUID PRIMARY KEY,
  departments       TEXT[] NOT NULL DEFAULT '{}',   -- POS Department values that are accessory sales
  categories        TEXT[] NOT NULL DEFAULT '{}',   -- POS Category / System-Category values that are accessory
  product_keywords  TEXT[] NOT NULL DEFAULT '{}',   -- product-desc substrings (POS feeds w/ no dept/category)
  acima_tenders     TEXT[] NOT NULL DEFAULT '{}',   -- Tender Type substrings that mark an ACIMA lease
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE commcalc.accessory_config ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='accessory_config' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.accessory_config FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
  END IF;
END $$;
GRANT ALL ON commcalc.accessory_config TO anon, authenticated, service_role;

-- Preserve the house org's EXACT current accessory config (byte-identical Boost). If flag_rules lacks the
-- accessory_* columns (pre-092/093/094), coalesce to empty → resolver still yields the ['Ondigo'] default.
DO $$
BEGIN
  BEGIN
    INSERT INTO commcalc.accessory_config (org_id, departments, categories, product_keywords, acima_tenders)
    SELECT org_id,
           COALESCE(accessory_departments, '{}'),
           COALESCE(accessory_categories, '{}'),
           COALESCE(accessory_product_keywords, '{}'),
           COALESCE(acima_tenders, '{}')
    FROM commcalc.flag_rules
    ON CONFLICT (org_id) DO NOTHING;
  EXCEPTION WHEN undefined_column OR undefined_table THEN
    -- flag_rules missing the accessory columns → nothing to migrate; the house stays on the ['Ondigo'] default.
    NULL;
  END;
END $$;

-- Seed Luxelink (854f6d7b) so its b2bsoft accessory categories are recognized. Editable in the Sales
-- Report → Accessory settings UI afterward. ON CONFLICT DO NOTHING so a hand-edit is never clobbered.
INSERT INTO commcalc.accessory_config (org_id, categories)
VALUES ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', ARRAY['HandsetBranded','Accessories','Accessory'])
ON CONFLICT (org_id) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 208 complete — commcalc.accessory_config installed (per-org; house backfilled byte-identical, luxelink seeded)' AS status;
