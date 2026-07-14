-- ════════════════════════════════════════════════════════════════════
-- 607_finance_statement_staleness_indexes.sql — finance (BACKLOG finance-7)
-- Statement auto-recompute (/account/run-due) + the P&L / Balance Sheet staleness banner.
--
-- NO SCHEMA CHANGE IS REQUIRED for the feature: commcalc.account_statements.computed_at already
-- exists (mig 021), and the source tables already carry the ingest timestamps the banner reads
-- (created_at / swept_at / updated_at). This migration is PURELY ADDITIVE, OPTIONAL PERFORMANCE:
-- it indexes the exact `WHERE org_id [+ period] ORDER BY <ts> DESC LIMIT 1` probe that
-- account/autocompute.py.newest_ingest_at runs per source table on every P&L/BS load + sweep tick.
--
-- The feature DEGRADES GRACEFULLY without it (Python still computes staleness, just with a heavier
-- scan on the big point-in-time tables). Every index is guarded so a column a tenant's schema
-- lacks is skipped, not an error; IF NOT EXISTS makes re-running safe. Run in the Supabase SQL
-- editor (Claude cannot run SQL).
-- ════════════════════════════════════════════════════════════════════

-- Point-in-time sources (filtered by org_id only, ordered by their ingest ts) — the real hotspot:
-- asset_ledger is ~43k rows and is scanned on every Balance Sheet staleness check.
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_asset_ledger_org_updated ON commcalc.asset_ledger (org_id, updated_at);     EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_asset_ledger_org_created ON commcalc.asset_ledger (org_id, created_at);     EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_invval_org_updated       ON commcalc.inventory_value (org_id, updated_at);  EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_borrow_org_updated       ON commcalc.store_borrowings (org_id, updated_at); EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

-- Period-scoped sources (filtered by org_id + period, ordered by ingest ts). The (org_id, period)
-- filter is already served by mig 030 indexes; these add the ts tail so LIMIT 1 avoids a re-sort.
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_raw_sales_org_created   ON commcalc.raw_sales (org_id, period, created_at);         EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_raw_mi_org_created      ON commcalc.raw_mi (org_id, period, created_at);            EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_comp_org_created        ON commcalc.raw_comp_report (org_id, period, created_at);   EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_repcomm_org_created     ON commcalc.rep_commissions (org_id, period, created_at);   EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_stexp_org_created       ON commcalc.store_expenses (org_id, period, created_at);    EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_je_org_created          ON commcalc.journal_entries (org_id, period, created_at);   EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_vipinv_org_created      ON commcalc.vip_invoices (org_id, period, created_at);      EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_vippaygo_org_swept      ON commcalc.vip_paygo_payments (org_id, period, swept_at);  EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_vipcm_org_swept         ON commcalc.vip_credit_memos (org_id, period, swept_at);    EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_chargeback_org_created  ON commcalc.chargeback_items (org_id, period, created_at);  EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

ANALYZE;
NOTIFY pgrst, 'reload schema';
SELECT 'Migration 607 complete — finance statement-staleness indexes (additive/idempotent/optional)' AS status;
