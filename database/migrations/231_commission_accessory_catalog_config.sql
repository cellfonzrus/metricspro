-- 231_commission_accessory_catalog_config.sql — per-org toggles for catalog-driven accessory classification
-- + BYOD-in-boxes (RULE TWO — config, never hard-coded). Extends commcalc.accessory_config (mig 208).
--
-- OWNER DIRECTIVE 2026-07-24 (luxelink, org 854f6d7b), deliverables 1 + 4:
--  (1) count_byod_in_boxes → `box_count_buckets`: which activation buckets ('byod'/'upgrade'/'premium')
--      add their DISTINCT-transaction count to the "total boxes sold" (box_count) metric. The owner's
--      "customer phone = BYOD" must count toward total boxes. Default '{}' = byte-identical (device-line
--      boxes only, the current behavior). A tenant sets '{byod}' to count BYOD activations as boxes.
--  (4) catalog-driven accessory classification:
--      `catalog_classify_enabled`     (bool, default FALSE) — master switch (Boost/house stays OFF → the
--                                      legacy dept/category/keyword classifier is byte-identical).
--      `catalog_accessory_categories` (text[], default '{}') — the SET of catalog Category values that
--                                      count as accessory. Empty + enabled → defaults to the catalog's own
--                                      'Accessories' in code (accessory_catalog.accessory_category_set).
--
-- MONEY NOTE: `box_count_buckets` feeds DISPLAY + Daily-Targets attainment (a plan that pays on box targets
-- is money-adjacent). `catalog_classify_enabled` widens accessory REVENUE/target attainment AND — via a
-- Commission Plan rule keyed on the synthetic `accessory` match_field — accessory PAY. BOTH default OFF so
-- NOTHING moves until the owner enables them AND runs a recalc. Additive + idempotent + degrade-gracefully:
-- the resolver reads each column in its OWN defensive query and falls back to the safe default, so the
-- feature works before this runs and a missing column never breaks classification.

ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS box_count_buckets           TEXT[]  NOT NULL DEFAULT '{}';
ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS catalog_classify_enabled    BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS catalog_accessory_categories TEXT[] NOT NULL DEFAULT '{}';

NOTIFY pgrst, 'reload schema';

-- ── OPTIONAL SEED (NOT auto-applied — money-adjacent). Uncomment + confirm luxelink's org_id to (a) count
--    BYOD/customer-phone activations toward total boxes and (b) turn on catalog-driven accessory
--    classification with the catalog's 'Accessories' category. Enable ONLY when ready to recalc.
-- INSERT INTO commcalc.accessory_config (org_id, box_count_buckets, catalog_classify_enabled, catalog_accessory_categories)
-- VALUES ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', ARRAY['byod'], true, ARRAY['Accessories'])
-- ON CONFLICT (org_id) DO UPDATE SET
--   box_count_buckets = EXCLUDED.box_count_buckets,
--   catalog_classify_enabled = EXCLUDED.catalog_classify_enabled,
--   catalog_accessory_categories = EXCLUDED.catalog_accessory_categories;

SELECT 'Migration 231 complete — accessory_config.box_count_buckets + catalog_classify_enabled + catalog_accessory_categories installed (defaults = byte-identical)' AS status;
