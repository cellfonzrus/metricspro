-- 212_commission_manual_report_mapping.sql — per-carrier MANUAL upload column mapping (SAP-style)
--
-- WHY: the MA reports (MA Commission → raw_ma_commission, MA Daily Tx → raw_ma_daily_tx, MA Handset
-- Ordering → raw_ma_fulfillment) are pulled by the flaky live portal AND, in PARALLEL, uploaded by
-- hand (owner directive 2026-07-17). The manual path is SAP-style: a user maps a sample file's columns
-- ONCE, then just uploads data against the saved mapping. That saved mapping must be persisted PER
-- (org, carrier, report) so different tenants/carriers never collide, and so it is DECOUPLED from the
-- scraper's report_pull_map (mig 207) — a hand-exported file may carry renamed/reordered headers, and a
-- manual re-map must never silently rewrite the scraper's DOM/column contract (or vice-versa).
--
-- This adds ONE small config table. It is an OVERRIDE store only: when a report has no manual override
-- row the manual upload falls back to report_pull.DEFAULT_REPORT_SPECS (the MA reports are pre-mapped),
-- so uploads still work with this migration UNRUN — the endpoints wrap every read in try/except and
-- degrade to the report_pull defaults. Nothing existing changes.
--
-- SAFE: additive + idempotent (safe to re-run). Multi-tenant: org_id NOT NULL + index. RLS open_all
-- (matches the rest of commcalc.*; tenant isolation is enforced in the API layer via org_id).

CREATE TABLE IF NOT EXISTS commcalc.manual_report_mapping (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  carrier_id     UUID NOT NULL,                 -- the carrier this mapping belongs to (the UI divides uploads per carrier)
  report_key     TEXT NOT NULL,                 -- ma_commission | ma_daily_tx | ma_marketplace_orders | …
  target_table   TEXT,                          -- denormalized dest table (from report_pull) for robustness
  column_map     JSONB NOT NULL DEFAULT '{}'::jsonb,   -- source-header -> {"col":<dest>,"type":text|num|date} (same shape as report_pull_map.column_map)
  sample_headers JSONB,                          -- the detected columns of the last-mapped sample file (for display / re-detect diffing)
  saved_by       TEXT,                           -- who saved it (audit)
  updated_at     TIMESTAMPTZ DEFAULT NOW(),
  created_at     TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, carrier_id, report_key)
);
CREATE INDEX IF NOT EXISTS manual_report_mapping_org ON commcalc.manual_report_mapping (org_id);

COMMENT ON TABLE commcalc.manual_report_mapping IS
  'Per-(org,carrier,report_key) MANUAL-upload column mapping. Override store only: absent row => the manual upload falls back to report_pull.DEFAULT_REPORT_SPECS. Decoupled from report_pull_map (the scraper contract) on purpose. Edited from /commcalc/ma-upload.';

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['commcalc.manual_report_mapping'] LOOP
    EXECUTE format('alter table %s enable row level security', t);
    EXECUTE format('drop policy if exists open_all on %s', t);
    EXECUTE format('create policy open_all on %s for all to anon, authenticated using (true) with check (true)', t);
    EXECUTE format('grant all on %s to anon, authenticated, service_role', t);
  END LOOP;
END $$;
