-- ════════════════════════════════════════════════════════════════════
-- 030_perf_indexes.sql — latency pass #1: add indexes on hot filter columns.
-- PURELY ADDITIVE + NON-BREAKING. No application code changes.
-- Every index is created in its own DO block that swallows undefined_table/
-- undefined_column, so an index for a column that doesn't exist is silently
-- skipped instead of aborting the migration. Re-running is safe (IF NOT EXISTS).
-- Run in the Supabase SQL editor.
-- ════════════════════════════════════════════════════════════════════

-- Helper note: indexes match the real WHERE/JOIN patterns the app uses
-- (period filters, store/rep joins, dates, IMEI/serial joins).

DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_raw_sales_org_pmy   ON commcalc.raw_sales (org_id, period_month, period_year); EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_raw_sales_org_trans ON commcalc.raw_sales (org_id, trans_date);                EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_raw_sales_org_store ON commcalc.raw_sales (org_id, store);                     EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_raw_sales_org_sp    ON commcalc.raw_sales (org_id, salesperson);               EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_raw_mi_org_pmy       ON commcalc.raw_mi (org_id, period_month, period_year);    EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_paydetail_org_period ON commcalc.raw_payment_detail (org_id, period);          EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_paydetail_org_imei   ON commcalc.raw_payment_detail (org_id, imei);            EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_comp_org_period      ON commcalc.raw_comp_report (org_id, period);             EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_dlar_store_org_pmy   ON commcalc.raw_dlar_store (org_id, period_month, period_year); EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_dlar_store_org_addr  ON commcalc.raw_dlar_store (org_id, address);             EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_dlar_rep_org_pmy     ON commcalc.raw_dlar_rep (org_id, period_month, period_year);   EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_dlar_rep_org_store   ON commcalc.raw_dlar_rep (org_id, store);                 EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_repcomm_org_period   ON commcalc.rep_commissions (org_id, period);             EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_repcomm_sp           ON commcalc.rep_commissions (epay_salesperson);           EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_chargeback_org_per   ON commcalc.chargeback_items (org_id, period);            EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_chargeback_sp        ON commcalc.chargeback_items (epay_salesperson);          EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_flags_org_period     ON commcalc.flags (org_id, period);                       EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_acctstmt_lookup      ON commcalc.account_statements (org_id, period, statement_type, scope_key); EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

-- asset_ledger (43k rows; scanned heavily) — no org filter in code, so single-column indexes.
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_asset_store         ON commcalc.asset_ledger (store);                          EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_asset_category      ON commcalc.asset_ledger (category);                       EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_asset_reimb_date    ON commcalc.asset_ledger (reimbursement_date);             EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_asset_acq_date      ON commcalc.asset_ledger (acquired_date);                  EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_asset_billing_fri   ON commcalc.asset_ledger (billing_friday);                 EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_asset_date_sold     ON commcalc.asset_ledger (date_sold);                      EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_asset_market        ON commcalc.asset_ledger (market);                         EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_shifts_date         ON storeops.shifts (shift_date);                           EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_shifts_store        ON storeops.shifts (store_code);                           EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_shifts_emp          ON storeops.shifts (employee_name);                        EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_closing_org_date    ON commcalc.daily_closing (org_id, close_date);            EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_storemap_sfid       ON commcalc.store_mapping (org_id, salesforce_id);         EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_vipinv_org_period   ON commcalc.vip_invoices (org_id, period);                 EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_vipdev_serial       ON commcalc.vip_invoice_devices (serial);                  EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

-- Update planner stats so the new indexes are used immediately.
ANALYZE;
NOTIFY pgrst, 'reload schema';
SELECT 'Migration 030 complete — performance indexes added (safe/idempotent)' AS status;
