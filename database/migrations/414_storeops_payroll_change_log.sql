-- 414_storeops_payroll_change_log.sql
-- Payroll module rework (owner directive 2026-07-27, mod-people band 400-499).
--
-- Deliverable 4: an append-only audit trail for every MANUAL change that alters a rep's payroll
-- hours — shift edits (PATCH /storeops/shifts/{id}), manager clock-in overrides
-- (POST /storeops/timeclock/override), manual hours adjustments (POST/DELETE
-- /storeops/manual-hours), and the force-clockout sweep (POST /storeops/timeclock/force-clockout/run,
-- both the manager "run now" button and the pg_cron auto-sweep) — so a District Manager's hand
-- corrections are visible on a dedicated "Payroll Change Log" page instead of silently vanishing
-- into the raw shifts/timelog tables.
--
-- Additive-only (ONE new table, nothing existing touched) + idempotent (CREATE TABLE IF NOT EXISTS).
-- What breaks until this runs: nothing — every write path's logging call is wrapped in try/except
-- (see storeops/router.py `_log_payroll_change`) and degrades to "no log row written" on a missing
-- table; the underlying shift/punch/manual-hours write itself is COMPLETELY unaffected either way
-- (the log call happens after the real write succeeds, and never raises on its own failure).
-- What it enables once run: the Payroll Change Log page + the "edited" marker/tooltip on the
-- Payroll Report's Actual Hrs drill-down actually have data to show.
CREATE TABLE IF NOT EXISTS storeops.payroll_change_log (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL,
  employee_id       text,
  employee_name     text,
  store_code        text,
  work_date         date,
  field             text NOT NULL,          -- e.g. 'scheduled_hours', 'actual_hours', 'clock_in', 'manual_hours'
  before_value      text,
  after_value       text,
  entry_point       text NOT NULL,          -- 'shift_edit' | 'timeclock_override' | 'manual_hours_add' |
                                             -- 'manual_hours_delete' | 'force_clockout_manual' | 'force_clockout_cron'
  source_table      text,                   -- 'shifts' | 'timelog' | 'manual_hours'
  source_id         text,
  changed_by_email  text,
  changed_by_role   text,
  reason            text,
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_payroll_change_log_org_date
  ON storeops.payroll_change_log (org_id, work_date);
CREATE INDEX IF NOT EXISTS ix_payroll_change_log_org_emp
  ON storeops.payroll_change_log (org_id, employee_id);
CREATE INDEX IF NOT EXISTS ix_payroll_change_log_org_created
  ON storeops.payroll_change_log (org_id, created_at DESC);

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 414 complete — storeops.payroll_change_log (append-only manual-hours-edit audit trail)' AS status;
