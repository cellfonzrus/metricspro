-- 031_comp_report_columns.sql — widen commcalc.raw_comp_report to the full per-payment grain from
-- the Comprehensive Comp (#100614) export so the daily sweep can store it VERBATIM and the Residual
-- Trend report can track each payment across months.
--
-- COMP CADENCE (user 2026-06-20): each daily pull is the carrier's cumulative month-to-date snapshot.
-- The sweep REPLACES the open month (delete current period + insert) — a canceled account that drops
-- out of the report correctly disappears (a merge/upsert would keep it stale and mask the residual
-- dip). Closed months are a different `period`, so they are never re-pulled and stay frozen. An empty
-- in-arrears pull is left untouched in code, so a not-yet-posted month is never wiped.
--
-- (Supersedes the earlier '031 merge' design: comp is REPLACE, not upsert — so there is NO unique
-- index; the report is stored exactly as the carrier sends it, including any repeated reference id.)
-- Run in the Supabase SQL editor (Claude cannot run SQL). Idempotent — safe to re-run.

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

-- If an earlier run created the UNIQUE merge index, drop it — REPLACE stores the report verbatim,
-- including any repeated external_reference_id, so a unique constraint would reject valid rows.
DROP INDEX IF EXISTS commcalc.raw_comp_report_merge_idx;

-- Non-unique lookup index for the Residual Trend report (per period + payment identity) and joins.
CREATE INDEX IF NOT EXISTS raw_comp_report_period_ref_idx
  ON commcalc.raw_comp_report (org_id, period, external_reference_id);
-- Per-account lookups for the month-over-month residual trend / dip detection.
CREATE INDEX IF NOT EXISTS raw_comp_report_acct_idx
  ON commcalc.raw_comp_report (org_id, account_id);

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 031 complete — raw_comp_report widened (replace/verbatim, no unique index)' AS status;
