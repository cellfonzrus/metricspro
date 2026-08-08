-- 420_storeops_google_reviews_lookback.sql — mod-people band 400-499.
--
-- Google Reviews Phase 1.5 (owner directive 2026-08-06, "surface reviews wherever an employee
-- appears"): the new per-EMPLOYEE store-set lookup (GET /storeops/google-reviews/employee/{id} +
-- GET /storeops/google-reviews/employee-summary) needs to know how far BACK to look for a shift
-- when deciding which store(s)' ratings belong to an employee (home_store is always included
-- regardless). RULE TWO (SAP-configurable) — this is a tenant-tunable window, not a hard-coded
-- constant, same idiom as every other threshold column added to this table (target_default, mig 411).
--
-- Additive + idempotent (`ADD COLUMN IF NOT EXISTS`), safe to re-run.
--
-- WHAT BREAKS UNTIL THIS RUNS: nothing existing. `google_reviews.get_config()` already selects '*'
-- and degrades to the code default (30 days) via `clamp_lookback_days()` when the column is absent,
-- so GET /storeops/google-reviews/config, GET /storeops/google-reviews/employee/{id} and
-- GET /storeops/google-reviews/employee-summary all keep working with the default 30-day lookback.
-- The ONLY thing that doesn't work pre-migration: PUT /storeops/google-reviews/config with a
-- lookback_days override in the body 400s (upsert against a column that doesn't exist yet) — the
-- Reviews Settings page's new "Lookback window" field will show that error until this runs.
ALTER TABLE storeops.google_review_config
  ADD COLUMN IF NOT EXISTS lookback_days INT NOT NULL DEFAULT 30;

-- Nothing to re-harden here — this table already has NO anon/authenticated grant (service_role
-- only, hardened in migration 411 N1/N2) and a new column inherits that same table-level posture.

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 420 complete — storeops.google_review_config.lookback_days' AS status;
