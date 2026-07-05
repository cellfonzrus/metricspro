-- 095_device_payables.sql
-- NEW MODULE: "Device Forecasting & Vendor Payables".
-- Carrier-agnostic, config-driven (SAP doctrine). Adds a per-carrier "payable source map" so a new
-- carrier's report is a CONFIG ROW, not code; a materialized per-IMEI payable ledger; a model alias
-- table (align free-text stocked model vs sold model); and two per-tenant clock-in-gate flags.
--
-- HARD CONSTRAINT honored here: this migration adds NO DDL for commcalc.asset_ledger or
-- commcalc.discrepancy_results (Supabase-created snapshot tables we only READ). Nothing about the
-- existing asset-lending / owed-weekly system is changed.
--
-- Additive + idempotent (CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / ON CONFLICT DO NOTHING).
-- Run in the Supabase SQL editor.

-- ── 1. Config spine: per-carrier payable source mapping (add-a-carrier-by-config) ──────────────────
CREATE TABLE IF NOT EXISTS commcalc.payable_source_map (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                    UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  carrier_id                UUID NOT NULL,                 -- commcalc.carrier.id
  distributor_id            UUID,                          -- commcalc.distributors.id (net-terms fallback)
  label                     TEXT,
  source_table              TEXT NOT NULL,                 -- 'asset_ledger' | 'raw_ma_commission' | <new>
  imei_field                TEXT NOT NULL,                 -- THE UNIVERSAL TALLY KEY: 'esn_imei' | 'imei'
  model_field               TEXT,                          -- 'device_model' | 'sku' | 'product_name'
  store_field               TEXT,                          -- 'store' | NULL (Total has none today)
  owed_field                TEXT,                          -- 'owed_to_vip' | NULL (unset = "no source")
  invoice_date_source       TEXT NOT NULL DEFAULT 'field', -- 'field' | 'vip_invoices'
  invoice_date_field        TEXT,                          -- when source='field' (e.g. 'tx_date','trigger_date')
  due_date_mode             TEXT NOT NULL DEFAULT 'net_terms', -- 'field' | 'net_terms'
  due_date_field            TEXT,                          -- when mode='field' (e.g. 'due_date')
  billing_friday_field      TEXT,                          -- 'billing_friday' (Boost: copied so DUE == /owed-weekly)
  sold_source               TEXT NOT NULL DEFAULT 'none',  -- 'asset_field' | 'sales_match' | 'none'
  sold_date_field           TEXT,                          -- when sold_source='asset_field' (e.g. 'date_sold')
  sold_match_table          TEXT,                          -- when sold_source='sales_match' (e.g. 'raw_sales')
  sold_match_imei_field     TEXT,                          -- IMEI col on sold_match_table (e.g. 'serial_1')
  reimbursement_source      TEXT NOT NULL DEFAULT 'none',  -- 'asset_ledger' | 'epay' | 'imei_match' | 'none'
  reimbursement_field       TEXT,                          -- e.g. 'reimbursement'
  reimbursement_date_field  TEXT,                          -- e.g. 'reimbursement_date'
  reimbursement_match_table TEXT,                          -- when source='imei_match' (pending for Total)
  reimbursement_match_imei_field   TEXT,
  reimbursement_match_amount_field TEXT,
  epay_crosscheck           BOOLEAN NOT NULL DEFAULT false,-- cross-check ePay reimbursement-by-IMEI
  is_active                 BOOLEAN NOT NULL DEFAULT true,
  created_at                TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, carrier_id)
);
CREATE INDEX IF NOT EXISTS psm_org ON commcalc.payable_source_map (org_id, is_active);

-- ── 2. Materialized per-IMEI payable ledger (rebuilt by POST /payables/rebuild) ────────────────────
CREATE TABLE IF NOT EXISTS commcalc.device_payable_ledger (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  carrier_id         UUID,
  imei               TEXT,               -- normalized (_norm_imei) — the tally key
  store              TEXT,
  device_model       TEXT,               -- canonical (aliased)
  owed               NUMERIC,            -- NULL when no owed source configured (Total)
  owed_source        TEXT,               -- 'asset_ledger' | 'unconfigured'
  invoice_date       DATE,
  invoice_source     TEXT,               -- 'vip_invoices' | 'field'
  due_date           DATE,
  due_source         TEXT,               -- 'report' | 'net_terms'
  billing_friday     DATE,               -- copied from asset_ledger for Boost (guarantees /owed-weekly match)
  bill_path          TEXT,               -- 'billed' | 'aging' (copied from asset_ledger)
  sold_flag          BOOLEAN DEFAULT false,
  sold_date          DATE,
  rebate_amount      NUMERIC DEFAULT 0,
  rebate_date        DATE,
  rebate_source      TEXT,               -- 'asset_ledger' | 'epay' | 'imei_match' | 'none'
  epay_rebate_amount NUMERIC,            -- ePay cross-check value
  rebate_mismatch    BOOLEAN DEFAULT false,
  net_offset         NUMERIC DEFAULT 0,  -- min(rebate_amount, owed) applied against owed
  net_owed           NUMERIC,            -- owed - net_offset
  window_start       DATE,
  window_end         DATE,               -- = due_date
  priority           BOOLEAN DEFAULT false, -- today in final pct% of window
  status             TEXT,               -- 'open' | 'offset' | 'discrepancy' | 'due'
  built_at           TIMESTAMPTZ DEFAULT NOW(),
  raw                JSONB
);
CREATE INDEX IF NOT EXISTS dpl_store  ON commcalc.device_payable_ledger (org_id, carrier_id, store);
CREATE INDEX IF NOT EXISTS dpl_due    ON commcalc.device_payable_ledger (org_id, due_date);
CREATE INDEX IF NOT EXISTS dpl_status ON commcalc.device_payable_ledger (org_id, status);
CREATE INDEX IF NOT EXISTS dpl_prio   ON commcalc.device_payable_ledger (org_id, store, priority) WHERE priority;
CREATE INDEX IF NOT EXISTS dpl_imei   ON commcalc.device_payable_ledger (org_id, imei);

-- ── 3. Model alias: align free-text stocked model (asset_ledger) vs sold model (item_mapping) ──────
CREATE TABLE IF NOT EXISTS commcalc.device_model_alias (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  raw_model       TEXT NOT NULL,           -- e.g. asset_ledger.device_model free text
  canonical_model TEXT NOT NULL,           -- should exist in commcalc.device_model (mig 043)
  UNIQUE (org_id, raw_model)
);

-- ── 4. Priority-acknowledgment log (records each "I will prioritize" clock-in ack) ─────────────────
CREATE TABLE IF NOT EXISTS commcalc.priority_ack_log (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  employee_id TEXT,
  store_code  TEXT,
  ack_date    DATE,
  imei_count  INT DEFAULT 0,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS pal_org ON commcalc.priority_ack_log (org_id, ack_date);

-- ── 5. Per-tenant clock-in gate flags (same table the closing gate already reads) ─────────────────
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS priority_ack_enabled BOOLEAN DEFAULT false,  -- clock-in ACK gate on/off
  ADD COLUMN IF NOT EXISTS priority_window_pct  INT     DEFAULT 25;     -- final % of pay window = priority

-- ── 6. Seed Boost + Total as CONFIG ROWS (no code) ────────────────────────────────────────────────
-- Boost: everything from the VIP asset_ledger + vip_invoices; DUE copies billing_friday/bill_path/owed.
INSERT INTO commcalc.payable_source_map
  (org_id, carrier_id, distributor_id, label, source_table, imei_field, model_field, store_field,
   owed_field, invoice_date_source, due_date_mode, due_date_field, billing_friday_field,
   sold_source, sold_date_field, reimbursement_source, reimbursement_field, reimbursement_date_field,
   epay_crosscheck)
SELECT c.org_id, c.id,
       (SELECT d.id FROM commcalc.distributors d WHERE d.org_id = c.org_id AND lower(d.name) = 'vip' LIMIT 1),
       'Boost / VIP', 'asset_ledger', 'esn_imei', 'device_model', 'store',
       'owed_to_vip', 'vip_invoices', 'field', 'due_date', 'billing_friday',
       'asset_field', 'date_sold', 'asset_ledger', 'reimbursement', 'reimbursement_date',
       true
FROM commcalc.carrier c
WHERE lower(c.name) LIKE '%boost%' OR lower(coalesce(c.code, '')) LIKE '%boost%'
ON CONFLICT (org_id, carrier_id) DO NOTHING;

-- Total: raw_ma_commission has the IMEI (the tally key) + tx_date; NO per-IMEI owed/reimbursement AMOUNT
-- source yet, so owed_field is NULL. sold-status tallies Total's IMEI against raw_sales; the reimbursement
-- IMEI-join is pre-wired (imei_match) with the match table left blank until a Total reimbursement report exists.
INSERT INTO commcalc.payable_source_map
  (org_id, carrier_id, distributor_id, label, source_table, imei_field, model_field,
   owed_field, invoice_date_source, invoice_date_field, due_date_mode,
   sold_source, sold_match_table, sold_match_imei_field, reimbursement_source, epay_crosscheck)
SELECT c.org_id, c.id,
       (SELECT d.id FROM commcalc.distributors d WHERE d.org_id = c.org_id AND lower(d.name) LIKE '%total%' LIMIT 1),
       'Total / MA', 'raw_ma_commission', 'imei', 'sku',
       NULL, 'field', 'tx_date', 'net_terms',
       'sales_match', 'raw_sales', 'serial_1', 'imei_match', false
FROM commcalc.carrier c
WHERE lower(c.name) LIKE '%total%' OR lower(coalesce(c.code, '')) LIKE '%total%'
ON CONFLICT (org_id, carrier_id) DO NOTHING;

-- ── 7. RLS open_all (new commcalc tables; service_role bypasses RLS but match the house pattern) ───
ALTER TABLE commcalc.payable_source_map    ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.device_payable_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.device_model_alias    ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.priority_ack_log      ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='payable_source_map' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.payable_source_map FOR ALL USING (true) WITH CHECK (true); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='device_payable_ledger' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.device_payable_ledger FOR ALL USING (true) WITH CHECK (true); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='device_model_alias' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.device_model_alias FOR ALL USING (true) WITH CHECK (true); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='priority_ack_log' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.priority_ack_log FOR ALL USING (true) WITH CHECK (true); END IF;
END $$;
