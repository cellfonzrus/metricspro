-- 251_commission_ledger_ma_sync.sql — refresh the canonical Commission Ledger from the raw MA tables
-- that ALREADY flow (raw_ma_daily_tx / raw_ma_commission, migration 083), instead of waiting on a
-- hand-uploaded file per period. Owner directive 2026-07-30: "commission ledger has stale data and
-- should be updated from ma commission and ma tx".
--
-- ADDITIVE + IDEMPOTENT + MONEY-SAFE:
--   • it adds PROVENANCE to commcalc.commission_ledger so a file import and an MA sync can coexist and
--     each side's delete-then-insert touches only ITS OWN rows. Existing rows become origin='file'
--     (the column default), so every current number on /commcalc/commission-ledger is unchanged.
--   • it adds ONE config table (commcalc.ledger_sync_config) holding, per (org, ledger template):
--     which raw report feeds it, the per-line sanity CEILING, and an optional component override map.
--     No carrier/tenant name appears in code — a new MA report is a config row.
--   • it seeds NO classification rules. Which canonical bucket a label belongs to stays the owner's
--     decision, made on /commcalc/commission-category-map. A component whose label matches no rule is
--     booked as 'other' and SURFACED in the preview — the sync never guesses a payout category.
--
-- Until this runs: the ledger page and the file import behave exactly as today, and the "Refresh from MA
-- data" action reports itself NOT READY (it names this file) instead of writing anything.

-- ── 1) provenance on the canonical ledger ────────────────────────────────────────────────────────
-- origin        'file'    = came from POST /commission-ledger/import (a human uploaded the carrier file)
--               'ma_sync' = derived from the raw_ma_* tables by POST /commission-ledger/ma-sync
-- source_table  the raw table an ma_sync row came from (NULL for file rows)
-- source_row_id the raw row's id — line-level lineage back to raw_ma_daily_tx / raw_ma_commission
-- synced_at     when this row was (re)derived; drives the "last refreshed" honesty line on the page
ALTER TABLE commcalc.commission_ledger
  ADD COLUMN IF NOT EXISTS origin        TEXT NOT NULL DEFAULT 'file';
ALTER TABLE commcalc.commission_ledger
  ADD COLUMN IF NOT EXISTS source_table  TEXT;
ALTER TABLE commcalc.commission_ledger
  ADD COLUMN IF NOT EXISTS source_row_id UUID;
ALTER TABLE commcalc.commission_ledger
  ADD COLUMN IF NOT EXISTS synced_at     TIMESTAMPTZ;

-- belt AND braces: if the column pre-existed as NULLable (re-run against a partially-migrated table),
-- make every existing row explicitly a file import — that is what they all are.
UPDATE commcalc.commission_ledger SET origin = 'file' WHERE origin IS NULL;

CREATE INDEX IF NOT EXISTS commission_ledger_origin
  ON commcalc.commission_ledger (org_id, source_report, period, origin);

COMMENT ON COLUMN commcalc.commission_ledger.origin IS
  'Which ingest wrote this row: file (POST /commission-ledger/import) | ma_sync (POST /commission-ledger/ma-sync, derived from raw_ma_*). Each ingest deletes ONLY its own origin for (org, source_report, period), so the two never wipe each other. Both origins present for one period = a real overlap, surfaced on the page, never silently merged.';

-- ── 2) per-(org, ledger template) sync config ────────────────────────────────────────────────────
-- One row per (ledger template, raw report) pair. Absent rows => the built-in defaults in
-- commcalc/ledger_ma_sync.py DEFAULT_TEMPLATE_SOURCES (ma_daily_tx -> raw_ma_daily_tx,
-- ma_commission -> raw_ma_commission), so the feature works the moment the code deploys.
CREATE TABLE IF NOT EXISTS commcalc.ledger_sync_config (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  source_report  TEXT NOT NULL,                 -- the LEDGER template (commission_ledger.source_report)
  report_key     TEXT NOT NULL,                 -- the raw MA report feeding it (report_pull key)
  source_table   TEXT,                          -- override the raw table (NULL => report_pull default)
  kind           TEXT,                          -- 'row' | 'component' (NULL => built-in default)
  date_col       TEXT,                          -- the row's own date column (period fallback)
  enabled        BOOLEAN NOT NULL DEFAULT TRUE,
  amount_ceiling NUMERIC NOT NULL DEFAULT 25000,-- per-LINE |amount| sanity ceiling; over => excluded + counted
  component_map  JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {raw_col: {label?, payment_month?, enabled?}}
  field_hints    JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {ledger_field: raw_col} for CONTEXT fields only
                                                      -- (raw_amount / product_name can never be hinted)
  notes          TEXT,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (org_id, source_report, report_key)
);
CREATE INDEX IF NOT EXISTS ledger_sync_config_org
  ON commcalc.ledger_sync_config (org_id, source_report);

ALTER TABLE commcalc.ledger_sync_config ENABLE ROW LEVEL SECURITY;
-- No policies and no anon/authenticated grants: all access is through the backend service role.

COMMENT ON TABLE commcalc.ledger_sync_config IS
  'Per-(org, ledger template) refresh config for the canonical commission ledger: which raw MA report feeds the template, the per-line dollar sanity ceiling (a line over it is EXCLUDED and counted, never imported silently — the raw_ma_daily_tx.merchant_invoice class of bug), and an optional component override map for wide reports ({raw_col: {label, payment_month, enabled}}). Absent row => the code defaults. Classification is NOT here: which canonical bucket a label lands in stays in commcalc.commission_category_map.';

-- ── 3) seed the two MA sources for the house org (defaults only — no classification, no opinion) ──
INSERT INTO commcalc.ledger_sync_config
  (org_id, source_report, report_key, source_table, kind, date_col, amount_ceiling, field_hints, notes)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'ma_daily_tx', 'raw_ma_daily_tx',
   'row', 'tx_date', 25000, '{}'::jsonb,
   'One raw row = one ledger line. Every ledger field resolves from the tenant''s own ledger column-mapping headers, all at exact-match confidence. The signed amount comes from whichever column raw_amount maps to (default header "Retail Cost" -> retail_cost). merchant_invoice is NUMERIC but is an invoice NUMBER and is refused as an amount.'),
  ('00000000-0000-0000-0000-000000000001', 'ma_commission', 'ma_commission', 'raw_ma_commission',
   'component', 'tx_date', 25000,
   '{"order_number":"activation_order","store":"merchant_account_id","account_id":"ban","order_type":"activation_type"}'::jsonb,
   'One raw row carries many payout components (device/consumer margin, financing, rebate, wallet funding, fees margin, 1st-6th month spiffs) — the SAME 12 columns the residual/P&L surfaces treat as dealer payable. mrc_net_discount is deliberately excluded: it is the subscriber plan price, not a dealer payment. Each component is classified by its own report header, so an unmapped label shows as ''other'' rather than being guessed. field_hints only name CONTEXT columns this report spells differently; the amount and the product label can never be hinted.')
ON CONFLICT (org_id, source_report, report_key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 251 complete — commission_ledger provenance (origin/source_table/source_row_id/synced_at) + ledger_sync_config ('
       || (SELECT count(*)::text FROM commcalc.ledger_sync_config) || ' rows); existing ledger rows marked origin=file: '
       || (SELECT count(*)::text FROM commcalc.commission_ledger WHERE origin = 'file') AS status;
