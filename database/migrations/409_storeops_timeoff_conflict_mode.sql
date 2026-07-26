-- 409_storeops_timeoff_conflict_mode.sql
-- Owner directive 2026-07-26: Boost managers could not reschedule an employee with approved time
-- off — POST /shifts hard-blocked (409) with no override. Owner wants scheduling ALLOWED over
-- approved time off (with a warning, not a hard block) as the default across every tenant, while
-- keeping an opt-in hard-block for any tenant that wants the old behavior back.
--
-- timeoff_conflict_mode:
--   warn   : creating a shift on a day with approved time off SUCCEEDS, response carries a
--            `timeoff_warning` string (DEFAULT — matches the owner directive for ALL tenants).
--   block  : original hard-block behavior — POST /shifts 409s (opt-in, per-tenant).
-- Read/written by GET/PUT /storeops/timeoff-conflict-mode and consumed by POST /storeops/shifts.
-- Missing column/row/migration-not-yet-run all degrade to 'warn' (see router.py) — never a 500.
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS timeoff_conflict_mode text NOT NULL DEFAULT 'warn';

-- Guard the two supported values (idempotent).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'tenants_timeoff_conflict_mode_chk'
  ) THEN
    ALTER TABLE storeops.tenants
      ADD CONSTRAINT tenants_timeoff_conflict_mode_chk CHECK (timeoff_conflict_mode IN ('warn', 'block'));
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 409 complete — storeops.tenants.timeoff_conflict_mode (warn|block, default warn)' AS status;
