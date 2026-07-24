-- 230_commission_catalog_total_variant.sql — TOTAL/luxelink product-catalog variant + category overrides.
--
-- OWNER DIRECTIVE 2026-07-24 (luxelink, org 854f6d7b), deliverables 2 + 3:
--  (2) the product-catalog upload must accept the NEW luxelink/TOTAL variant (Product_Catalog_Update_TOTAL:
--      Department, Category, Product Desc, Primary Vendor, SKU, UPC, Cost, In Stock, Retail Price, Active,
--      Taxable, Tax Set, Created Date — NO 'Product ID'; UPC-keyed). The existing house format (Product-ID-
--      keyed) keeps working. Both land in commcalc.raw_catalog, PER-ORG (the table already has org_id NOT
--      NULL and the catalog upload deletes ONLY .eq('org_id', …) rows, so no cross-tenant wipe).
--  (3) categories are user-editable but file-as-default: an OVERRIDE layer on top of the loaded rows
--      (commcalc.catalog_category_override) — non-destructive, per-org.
--
-- ADDITIVE + IDEMPOTENT + DEGRADE-GRACEFULLY: new columns are nullable with no default; the upload probes
-- for them and strips them from the insert if this migration hasn't run yet (so the house Product-ID upload
-- keeps working before 230). No sales/GP/pay number moves from this migration (schema only). Re-runnable.

-- ── (2) TOTAL-variant columns on raw_catalog (house format leaves them NULL) ──────────────────────────
ALTER TABLE commcalc.raw_catalog ADD COLUMN IF NOT EXISTS upc            TEXT;
ALTER TABLE commcalc.raw_catalog ADD COLUMN IF NOT EXISTS department     TEXT;
ALTER TABLE commcalc.raw_catalog ADD COLUMN IF NOT EXISTS category       TEXT;
ALTER TABLE commcalc.raw_catalog ADD COLUMN IF NOT EXISTS retail_price   NUMERIC;
ALTER TABLE commcalc.raw_catalog ADD COLUMN IF NOT EXISTS primary_vendor TEXT;
ALTER TABLE commcalc.raw_catalog ADD COLUMN IF NOT EXISTS active         BOOLEAN;
ALTER TABLE commcalc.raw_catalog ADD COLUMN IF NOT EXISTS in_stock       NUMERIC;

-- Lookups for the classifier + the admin category editor (org-scoped scans). Partial/plain indexes are all
-- IF NOT EXISTS so re-runs are safe. (The classifier reads all rows for an org and matches in Python, so
-- these mainly help the per-org fetch + any future UPC/SKU join.)
CREATE INDEX IF NOT EXISTS raw_catalog_org_idx      ON commcalc.raw_catalog (org_id);
CREATE INDEX IF NOT EXISTS raw_catalog_org_upc_idx  ON commcalc.raw_catalog (org_id, upc);
CREATE INDEX IF NOT EXISTS raw_catalog_org_sku_idx  ON commcalc.raw_catalog (org_id, sku);
CREATE INDEX IF NOT EXISTS raw_catalog_org_cat_idx  ON commcalc.raw_catalog (org_id, category);

-- ── (3) per-org category OVERRIDE layer (file rows are the default; overrides win, non-destructive) ────
CREATE TABLE IF NOT EXISTS commcalc.catalog_category_override (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  match_type  TEXT NOT NULL CHECK (match_type IN ('upc','sku','product_id','product_desc')),
  match_value TEXT NOT NULL,          -- normalized (lowercased/trimmed; product_desc whitespace-collapsed)
  category    TEXT NOT NULL,          -- the override category (what this product should be classified as)
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, match_type, match_value)
);
CREATE INDEX IF NOT EXISTS catalog_cat_override_org_idx ON commcalc.catalog_category_override (org_id);

ALTER TABLE commcalc.catalog_category_override ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='catalog_category_override' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.catalog_category_override FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
  END IF;
END $$;
GRANT ALL ON commcalc.catalog_category_override TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 230 complete — raw_catalog TOTAL-variant columns + commcalc.catalog_category_override installed' AS status;
