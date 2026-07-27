-- 410_storeops_onboarding_stuck_days.sql
-- Settings + import-health audit (operator dispatch 2026-07-26, package "settings-audit").
--
-- The new "HR onboarding invites stuck >N days" admin-attention provider (backend/app/modules/hr/
-- attention.py) needs a tenant-tunable N (RULE TWO — a threshold a human would want to tune is a
-- config value with a sane default, never a hard-coded constant). Read/written by
-- GET/PUT /hr/onboarding/attention-config; a missing column/row/un-run migration all degrade to the
-- code default of 7 days (see hr/attention.py's onboarding_stuck_days()) — never a 500, and the
-- provider itself simply contributes nothing until this runs.
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS onboarding_stuck_days integer NOT NULL DEFAULT 7;

-- Guard a sane range (idempotent) — same "ADD CONSTRAINT IF NOT EXISTS via DO block" idiom as
-- migration 409's timeoff_conflict_mode check.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'tenants_onboarding_stuck_days_chk'
  ) THEN
    ALTER TABLE storeops.tenants
      ADD CONSTRAINT tenants_onboarding_stuck_days_chk CHECK (onboarding_stuck_days BETWEEN 1 AND 90);
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 410 complete — storeops.tenants.onboarding_stuck_days (default 7, clamped 1-90)' AS status;
