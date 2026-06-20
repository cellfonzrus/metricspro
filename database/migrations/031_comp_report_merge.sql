-- 031_comp_report_merge.sql — let the daily epay Comprehensive Comp (#100614) sweep MERGE into
-- commcalc.raw_comp_report instead of wipe+replace (user directive 2026-06-20). Carrier comp posts
-- in arrears and is pulled daily for the current month, so each pull must APPEND new payments and
-- OVERWRITE changed ones — never destroy prior data (an empty pull is left untouched in code).
--
-- (1) widen raw_comp_report to the full per-payment grain from the export
--     (Begin/End Date, Retailer Account, OwnerID, TerminalID, AccountID, Business Name, Brand,
--      SalesForce ID, Quantity, ExternalReferenceID, HasPaymentDetail, InternalBrand). The old
--     mapper kept only business_address / compensation_type / payment_amount.
-- (2) add a UNIQUE index on (org_id, period, external_reference_id) — the per-payment conflict key
--     the sweep upserts on. The column is new, so every existing row is NULL there; Postgres allows
--     many NULLs in a unique index, so creation can't fail on legacy rows and they never get merged
--     into (the mapper always emits a non-empty ref — real, or a deterministic content hash).
-- Run this in the Supabase SQL editor (Claude cannot run SQL). Idempotent — safe to re-run.

ALTER TABLE commcalc.raw_comp_report
  ADD COLUMN IF NOT EXISTS begin_date            TEXT,
  ADD COLUMN IF NOT EXISTS end_date              TEXT,
  ADD COLUMN IF NOT EXISTS retailer_account      TEXT,
  ADD COLUMN IF NOT EXISTS owner_id              TEXT,
  ADD COLUMN IF NOT EXISTS terminal_id           TEXT,
  ADD COLUMN IF NOT EXISTS account_id            TEXT,
  ADD COLUMN IF NOT EXISTS business_name         TEXT,
  ADD COLUMN IF NOT EXISTS brand                 TEXT,
  ADD COLUMN IF NOT EXISTS salesforce_id         TEXT,
  ADD COLUMN IF NOT EXISTS quantity              NUMERIC,
  ADD COLUMN IF NOT EXISTS external_reference_id TEXT,
  ADD COLUMN IF NOT EXISTS has_payment_detail    TEXT,
  ADD COLUMN IF NOT EXISTS internal_brand        TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS raw_comp_report_merge_idx
  ON commcalc.raw_comp_report (org_id, period, external_reference_id);

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 031 complete — raw_comp_report widened + unique (org_id,period,external_reference_id) for the merge sweep' AS status;
