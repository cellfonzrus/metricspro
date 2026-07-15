-- 207_commission_report_pull_map.sql — configurable VidaPay/T-CETRA report-pull mapping (RULE TWO)
--
-- WHY: the automated report pull (vidapay_sweep.run_vidapay_sweep) must never hard-code which report
-- lands in which table or how each source header maps to a destination column. That is DATA. This adds
-- commcalc.report_pull_map — one row per report_key, per org, with a house/default row every tenant
-- inherits unless it has an override — editable from /commcalc/report-mappings. It also adds the two
-- additive target tables for the un-screenshotted reports (SIM Assignment, PR Activation) with a
-- raw_row JSONB so nothing is lost before their columns are pinned, and a clean view over the existing
-- raw_ma_fulfillment (the Marketplace Handset Fulfillment Orders report's table) named
-- raw_ma_marketplace_orders so mod-asset can build the purchases/landing view on a stable name.
--
-- SAFE: additive + idempotent. Nothing existing changes; the seed is ON CONFLICT DO NOTHING and mirrors
-- report_pull.DEFAULT_REPORT_SPECS (the same defaults the engine falls back to if this seed is absent).

-- ── 1. the config table ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.report_pull_map (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  report_key    TEXT NOT NULL,          -- ma_commission | ma_daily_tx | ma_marketplace_orders | ma_sim_assignment | ma_pr_activation
  display_name  TEXT,                   -- the label to pick in the portal's Report <select>
  target_table  TEXT,                   -- commcalc.<table> the mapped rows ingest into
  column_map    JSONB NOT NULL DEFAULT '{}'::jsonb,   -- source-header -> dest column (string) or {col,type:text|num|date}
  param_spec    JSONB NOT NULL DEFAULT '{}'::jsonb,   -- how to drive the page: fields, date format, iterate_months, caps
  export_pref   TEXT DEFAULT 'csv',     -- 'csv' | 'excel'
  enabled       BOOLEAN NOT NULL DEFAULT true,
  sort_order    INT DEFAULT 0,
  processor     TEXT DEFAULT 'vidapay', -- processor family this report belongs to
  updated_at    TIMESTAMPTZ DEFAULT NOW(),
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, report_key)
);
CREATE INDEX IF NOT EXISTS report_pull_map_org ON commcalc.report_pull_map (org_id);

COMMENT ON TABLE commcalc.report_pull_map IS
  'Config-driven report->table->column mapping for the automated portal report pull. One row per report_key per org; org row overrides the house/default row. Editable at /commcalc/report-mappings. Mirrors report_pull.DEFAULT_REPORT_SPECS.';

-- ── 2. per-source back-range knob (config, not hard-coded) ───────────────────────────────────────
ALTER TABLE commcalc.data_source
  ADD COLUMN IF NOT EXISTS months_back INT DEFAULT 2;   -- how many months back a pull covers (each report caps it)

-- ── 3. additive target tables for the two un-calibrated reports (raw_row preserves unknown columns) ─
CREATE TABLE IF NOT EXISTS commcalc.raw_ma_sim_assignment (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  carrier_id   UUID,
  source_id    UUID,
  period       TEXT, period_month INT, period_year INT,
  report_date  DATE,
  natural_key  TEXT,
  raw_row      JSONB,
  created_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS raw_ma_sim_assignment_org_date   ON commcalc.raw_ma_sim_assignment (org_id, report_date);
CREATE INDEX IF NOT EXISTS raw_ma_sim_assignment_org_period ON commcalc.raw_ma_sim_assignment (org_id, period);

CREATE TABLE IF NOT EXISTS commcalc.raw_ma_pr_activation (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  carrier_id   UUID,
  source_id    UUID,
  period       TEXT, period_month INT, period_year INT,
  report_date  DATE,
  natural_key  TEXT,
  raw_row      JSONB,
  created_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS raw_ma_pr_activation_org_date   ON commcalc.raw_ma_pr_activation (org_id, report_date);
CREATE INDEX IF NOT EXISTS raw_ma_pr_activation_org_period ON commcalc.raw_ma_pr_activation (org_id, period);

-- ── 4. clean interface for mod-asset over the existing fulfillment table ──────────────────────────
-- The "MA - Marketplace Handset Fulfillment Orders" report already has a home: raw_ma_fulfillment
-- (mig 083, identical columns). Rather than fork a parallel table, expose it under the name the owner
-- asked for so mod-asset builds its purchases/landing view on a stable, purpose-named view.
CREATE OR REPLACE VIEW commcalc.raw_ma_marketplace_orders AS
  SELECT id, org_id, carrier_id, source_id,
         date_ordered, date_filled, order_number, order_status, order_type,
         tspid, business_name, business_address, city, state, zip,
         product_name, number_ordered, price, tracking_number, date_shipped, created_at
    FROM commcalc.raw_ma_fulfillment;

COMMENT ON VIEW commcalc.raw_ma_marketplace_orders IS
  'Clean, purpose-named read interface over raw_ma_fulfillment for mod-asset''s marketplace purchases/landing view. Query org-scoped (WHERE org_id = ...).';

-- ── 5. seed the house/default rows (mirrors report_pull.DEFAULT_REPORT_SPECS; idempotent) ─────────
INSERT INTO commcalc.report_pull_map
  (org_id, report_key, display_name, target_table, column_map, param_spec, export_pref, enabled, sort_order, processor)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'ma_commission', 'MA - Commission Details', 'raw_ma_commission', '{"Date": {"col": "tx_date", "type": "date"}, "Time": "tx_time", "Carrier Name": "carrier_name", "Activation Order": "activation_order", "MerchantAccountId": "merchant_account_id", "IMEI": "imei", "SIM": "sim", "SKU": "sku", "Activation Type": "activation_type", "Activation Type 2": "activation_type2", "Sub Type": "sub_type", "Device Margin": {"col": "device_margin", "type": "num"}, "Consumer Margin": {"col": "consumer_margin", "type": "num"}, "Consumer Financing": {"col": "consumer_financing", "type": "num"}, "Rebate": {"col": "rebate", "type": "num"}, "Perfect Sale": "perfect_sale", "Wallet Funding Amount": {"col": "wallet_funding", "type": "num"}, "MRC Net Discount": {"col": "mrc_net_discount", "type": "num"}, "Fees": {"col": "fees", "type": "num"}, "Fees Margin": {"col": "fees_margin", "type": "num"}, "1st Month Spiff": {"col": "spiff_m1", "type": "num"}, "2nd Month Spiff": {"col": "spiff_m2", "type": "num"}, "3rd Month Spiff": {"col": "spiff_m3", "type": "num"}, "4th Month Spiff": {"col": "spiff_m4", "type": "num"}, "5th Month Spiff": {"col": "spiff_m5", "type": "num"}, "6th Month Spiff": {"col": "spiff_m6", "type": "num"}, "Port Status": "port_status", "ID Verification": "id_verification", "Is Financed": "is_financed", "User Id": "user_id", "User Name": "user_name", "BAN": "ban", "BIN": "bin", "POS Invoice": "pos_invoice", "Line Status": "line_status", "Status Change Date": "status_change_date", "Suspension Reason": "suspension_reason", "Consumer Value": {"col": "consumer_value", "type": "num"}, "Platform": "platform", "Platform Transaction Id": "platform_tx_id", "External Reference Id": "external_ref"}'::jsonb, '{"has_period": true, "date_col": "tx_date", "period_from": "tx_date", "iterate_months": true, "interval_months": 1, "max_months_back": 12, "submit_timeout_s": 300, "fields": [{"name": "Account_ID", "kind": "static", "source": "account_id"}, {"name": "StartDate", "kind": "date", "role": "start", "format": "%m/%d/%Y %H:%M"}, {"name": "EndDate", "kind": "date", "role": "end", "format": "%m/%d/%Y %H:%M"}, {"name": "MonthIntervalLimit", "kind": "select", "literal": "1 Month"}, {"name": "SessionId", "kind": "static", "source": "session_id"}]}'::jsonb, 'csv', true, 10, 'vidapay'),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'MA Daily Tx SubMA', 'raw_ma_daily_tx', '{"Date of Transaction": {"col": "tx_date", "type": "date"}, "Date Due": {"col": "due_date", "type": "date"}, "Account ID": "account_id", "Account Name": "account_name", "Direct MA ID": "direct_ma_id", "Direct MA Name": "direct_ma_name", "Top MA ID": "top_ma_id", "Top MA Name": "top_ma_name", "Order Number": "order_number", "User": "user_name", "Order Type": "order_type", "Product Name": "product_name", "Retail Cost": {"col": "retail_cost", "type": "num"}, "Merchant Discount": {"col": "merchant_discount", "type": "num"}, "Merchant Invoice": {"col": "merchant_invoice", "type": "num"}}'::jsonb, '{"has_period": true, "date_col": "tx_date", "period_from": "tx_date", "iterate_months": true, "interval_months": 1, "max_months_back": 12, "submit_timeout_s": 300, "fields": [{"name": "Session ID", "kind": "static", "source": "session_id"}, {"name": "Master Agent ID", "kind": "static", "source": "blank", "optional": true}, {"name": "Start Date", "kind": "date", "role": "start", "format": "%m/%d/%Y"}, {"name": "End Date", "kind": "date", "role": "end", "format": "%m/%d/%Y"}]}'::jsonb, 'csv', true, 20, 'vidapay'),
  ('00000000-0000-0000-0000-000000000001', 'ma_marketplace_orders', 'MA - Marketplace Handset Fulfillment Orders', 'raw_ma_fulfillment', '{"Date Ordered": {"col": "date_ordered", "type": "date"}, "Date Filled": {"col": "date_filled", "type": "date"}, "Date Shipped": {"col": "date_shipped", "type": "date"}, "Order Number": "order_number", "Order Status": "order_status", "Order Type": "order_type", "TSPID": "tspid", "Business Name": "business_name", "Business Address": "business_address", "City": "city", "State": "state", "Zip": "zip", "Product Name": "product_name", "Number Ordered": {"col": "number_ordered", "type": "num"}, "Price": {"col": "price", "type": "num"}, "Tracking Number": "tracking_number"}'::jsonb, '{"has_period": false, "date_col": "date_ordered", "period_from": "date_ordered", "iterate_months": true, "interval_months": 1, "max_months_back": 12, "submit_timeout_s": 300, "fields": [{"name": "Start Date Ordered", "kind": "date", "role": "start", "format": "%m/%d/%Y"}, {"name": "End Date Ordered", "kind": "date", "role": "end", "format": "%m/%d/%Y"}, {"name": "Order Number", "kind": "static", "source": "blank", "optional": true}, {"name": "Session ID", "kind": "static", "source": "session_id"}]}'::jsonb, 'csv', true, 30, 'vidapay'),
  ('00000000-0000-0000-0000-000000000001', 'ma_sim_assignment', 'Activation SIM Assignment Report', 'raw_ma_sim_assignment', '{}'::jsonb, '{"has_period": true, "date_col": "report_date", "period_from": "report_date", "generic": true, "iterate_months": true, "interval_months": 1, "max_months_back": 6, "submit_timeout_s": 300, "calibration": true, "fields": [{"name": "Start Date", "kind": "date", "role": "start", "format": "%m/%d/%Y", "optional": true}, {"name": "End Date", "kind": "date", "role": "end", "format": "%m/%d/%Y", "optional": true}, {"name": "Session ID", "kind": "static", "source": "session_id", "optional": true}]}'::jsonb, 'csv', true, 40, 'vidapay'),
  ('00000000-0000-0000-0000-000000000001', 'ma_pr_activation', 'PR Activation Details', 'raw_ma_pr_activation', '{}'::jsonb, '{"has_period": true, "date_col": "report_date", "period_from": "report_date", "generic": true, "iterate_months": true, "interval_months": 1, "max_months_back": 6, "submit_timeout_s": 300, "calibration": true, "fields": [{"name": "Start Date", "kind": "date", "role": "start", "format": "%m/%d/%Y", "optional": true}, {"name": "End Date", "kind": "date", "role": "end", "format": "%m/%d/%Y", "optional": true}, {"name": "Session ID", "kind": "static", "source": "session_id", "optional": true}]}'::jsonb, 'csv', true, 50, 'vidapay')
ON CONFLICT (org_id, report_key) DO NOTHING;
