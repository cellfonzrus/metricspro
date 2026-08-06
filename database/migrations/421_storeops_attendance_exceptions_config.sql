-- 421_storeops_attendance_exceptions_config.sql — mod-people band 400-499.
--
-- Owner directive 2026-08-06 (verbatim): "time clock should show who were scheduled and didn't
-- clock in and also if somebody else clocked in instead of the scheduled."
--
-- Backs the Attendance Exceptions report (GET /storeops/timeclock/attendance-exceptions,
-- backend/app/modules/storeops/attendance_exceptions.py) — joins storeops.shifts against
-- storeops.timelog to surface no_show / covered_by_other / unscheduled / late / left_early, with an
-- approved-time-off EXCUSED label. RULE TWO (SAP-configurable): every threshold below is a per-tenant
-- setting with a sane code default, never a hard-coded constant — editable via
-- GET/PUT /storeops/timeclock/attendance-config (manager/admin only) and the Time Clock page's
-- "Attendance Settings" panel.
--
--   storeops.tenants:
--     attendance_late_grace_min          integer  NOT NULL DEFAULT 10   -- minutes after shift start
--                                                                        -- before a clock-in is LATE
--     attendance_early_leave_grace_min   integer  NOT NULL DEFAULT 10   -- minutes before shift end a
--                                                                        -- clock-out counts LEFT_EARLY
--     attendance_noshow_grace_min        integer  NOT NULL DEFAULT 30   -- minutes after shift start
--                                                                        -- before an un-punched shift
--                                                                        -- becomes NO_SHOW ("don't
--                                                                        -- flag the future")
--     attendance_coverage_overlap_min    integer  NOT NULL DEFAULT 15   -- tolerance padding a punch
--                                                                        -- window must fall within to
--                                                                        -- count as "covering" a shift
--     attendance_timeoff_mode            text     NOT NULL DEFAULT 'label'  -- 'label' (show EXCUSED,
--                                                                        -- default) | 'suppress' (never
--                                                                        -- emit an excused row)
--
-- Additive + idempotent (ADD COLUMN IF NOT EXISTS; the CHECK constraint is added only if missing,
-- same guarded idiom as migration 409's timeoff_conflict_mode). No new table, no RLS/grant change
-- (storeops.tenants already carries its existing service-role-only posture).
--
-- WHAT BREAKS UNTIL THIS RUNS: nothing. attendance_exceptions.get_tenant_attendance_config() try/
-- excepts the column read and returns the SAME defaults documented above (DEFAULT_CONFIG in
-- attendance_exceptions.py) with `available=False` — the report classifies correctly on code defaults
-- immediately, unlike the lunch-deduction feature's deliberate hard off-switch (this is read-only
-- reporting, never a payroll dollar figure). Only the "Attendance Settings" panel's Save action 400s
-- ("is migration 421 applied?") until this runs; the report itself works either way.
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS attendance_late_grace_min        integer NOT NULL DEFAULT 10,
  ADD COLUMN IF NOT EXISTS attendance_early_leave_grace_min integer NOT NULL DEFAULT 10,
  ADD COLUMN IF NOT EXISTS attendance_noshow_grace_min      integer NOT NULL DEFAULT 30,
  ADD COLUMN IF NOT EXISTS attendance_coverage_overlap_min  integer NOT NULL DEFAULT 15,
  ADD COLUMN IF NOT EXISTS attendance_timeoff_mode          text    NOT NULL DEFAULT 'label';

-- Guard the two supported values (idempotent, same pattern as migration 409).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'tenants_attendance_timeoff_mode_chk'
  ) THEN
    ALTER TABLE storeops.tenants
      ADD CONSTRAINT tenants_attendance_timeoff_mode_chk CHECK (attendance_timeoff_mode IN ('label', 'suppress'));
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 421 complete — storeops.tenants attendance-exception thresholds (grace/overlap minutes + timeoff_mode, defaults 10/10/30/15/label)' AS status;
