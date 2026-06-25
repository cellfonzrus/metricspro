-- 042_column_mapping.sql
-- Generic, config-driven column mapping — the "any-carrier" ingestion spine (SaaS framework A2).
--
-- Until now every report's spreadsheet columns were mapped to DB fields by a HARD-CODED Python
-- function (map_*_row). A new carrier whose export uses different headers could not be ingested
-- without a code change. commcalc.column_mapping makes that mapping DATA: one row per
-- (report_key, carrier, target_field) saying which SOURCE spreadsheet header feeds it and how to
-- transform the value.
--
--   report_key    : the logical report (comp_report, mi_report, sales, payment_detail, or any new
--                   report_definitions.report_key)
--   carrier_id    : NULL = applies to every carrier (the org default); a value = carrier-specific
--                   override (takes precedence over the NULL/global rule for the same target_field)
--   target_field  : the canonical DB column in the report's target_table
--   source_header : the exact spreadsheet column header to read (case-insensitive match at ingest)
--   transform     : how to coerce the cell — text | number | int | date10 | mdn | upper | lower | bool
--
-- ADDITIVE + SAFE: the legacy hard-coded upload branches are untouched and stay the proven path for
-- the seeded Boost reports. The generic importer (POST /commcalc/upload-mapped) is the opt-in path a
-- NEW connector's reports use. Defaults can be seeded from the existing layouts via
-- POST /commcalc/column-mapping/seed so they show up editable in the UI.
--
-- Idempotent (CREATE ... IF NOT EXISTS). Re-running is safe.

CREATE TABLE IF NOT EXISTS commcalc.column_mapping (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  report_key    TEXT NOT NULL,                 -- comp_report | mi_report | sales | <report_definitions.report_key>
  carrier_id    UUID,                          -- NULL = global/default; else carrier-specific override
  target_field  TEXT NOT NULL,                 -- canonical column in the report's target_table
  source_header TEXT NOT NULL,                 -- spreadsheet header to read (case-insensitive)
  transform     TEXT NOT NULL DEFAULT 'text',  -- text|number|int|date10|mdn|upper|lower|bool
  is_active     BOOLEAN NOT NULL DEFAULT true,
  priority      INT NOT NULL DEFAULT 100,
  created_at    TIMESTAMPTZ DEFAULT now(),
  updated_at    TIMESTAMPTZ DEFAULT now()
);

-- One mapping per (report, carrier-scope, target field). COALESCE so the NULL (global) scope is a
-- single distinct slot rather than allowing unlimited NULL-carrier duplicates of the same field.
CREATE UNIQUE INDEX IF NOT EXISTS column_mapping_uq
  ON commcalc.column_mapping (org_id, report_key, COALESCE(carrier_id, '00000000-0000-0000-0000-000000000000'::uuid), target_field);
CREATE INDEX IF NOT EXISTS column_mapping_lookup
  ON commcalc.column_mapping (org_id, report_key, carrier_id) WHERE is_active;

-- RLS + grants (service_role doesn't get privileges on new commcalc tables automatically).
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['commcalc.column_mapping'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;
