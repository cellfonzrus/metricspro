-- ════════════════════════════════════════════════════════════════════
-- 612_payables_drop_house_org_default.sql — finance / payables org-hygiene (audit 2026-07-23)
--
-- WHY (AGENT_CONTRACT §2, multi-tenant): migration 095_device_payables.sql declared FOUR payables
-- tables with `org_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001'` (the HOUSE org):
--   commcalc.payable_source_map      (095 line 17)
--   commcalc.device_payable_ledger   (095 line 51)
--   commcalc.device_model_alias      (095 line 89)
--   commcalc.priority_ack_log        (095 line 98)
-- A column DEFAULT of the house org silently lands ANY future unstamped insert in Boost's tenant —
-- a cross-tenant contamination waiting to happen. All current payables code stamps org_id EXPLICITLY
-- on every insert (engine.build_ledger / _build_one_carrier / sync_payable_flags / the router
-- rebuild + ack paths), so dropping the default changes NO current behavior; it only converts a
-- would-be silent mis-file into a loud NOT NULL error at the offending call site — which is what we
-- want. Removing the DEFAULT (not the NOT NULL) keeps the column required.
--
-- WHAT:
--   1. DROP the house-org DEFAULT on all four columns.
--   2. Ensure each table has an org_id index (three already do via composite org_id-leading indexes
--      from mig 095; device_model_alias only had it via its UNIQUE(org_id,raw_model) constraint, so
--      we add an explicit single-column index there — see the per-table notes below).
--
-- Additive + idempotent + SAFE TO RE-RUN: `ALTER COLUMN org_id DROP DEFAULT` is a no-op when the
-- default is already gone (Postgres does not error), and every CREATE INDEX is `IF NOT EXISTS`. Each
-- statement is guarded so a missing table/column is skipped, never raised. Run in the Supabase SQL
-- editor (Claude cannot run SQL).
-- ════════════════════════════════════════════════════════════════════

-- ── 1. Drop the house-org DEFAULT on all four payables tables ─────────────────────────────────────
DO $$ BEGIN ALTER TABLE commcalc.payable_source_map    ALTER COLUMN org_id DROP DEFAULT; EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE commcalc.device_payable_ledger ALTER COLUMN org_id DROP DEFAULT; EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE commcalc.device_model_alias    ALTER COLUMN org_id DROP DEFAULT; EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE commcalc.priority_ack_log      ALTER COLUMN org_id DROP DEFAULT; EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

-- ── 2. Ensure an org_id index on each of the four ─────────────────────────────────────────────────
-- Already present from mig 095 (org_id-leading composite indexes) — listed for the record, not re-created:
--   payable_source_map     → psm_org  (org_id, is_active)              [095:46]
--   device_payable_ledger  → dpl_store/dpl_due/dpl_status/dpl_imei ... (org_id, ...) [095:80-84]
--   priority_ack_log       → pal_org  (org_id, ack_date)              [095:105]
-- device_model_alias had org_id indexed ONLY via its UNIQUE(org_id, raw_model) constraint — add an
-- explicit single-column org_id index so its org-scoped reads (_load_model_alias / load_phone_map)
-- have a dedicated index and the org_id-index guarantee is literal for all four.
DO $$ BEGIN CREATE INDEX IF NOT EXISTS dma_org ON commcalc.device_model_alias (org_id); EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 612 complete — payables org-hygiene: house-org DEFAULT dropped on 4 tables; device_model_alias org_id index added (additive/idempotent/safe-to-re-run)' AS status;
