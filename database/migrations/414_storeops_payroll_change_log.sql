-- 414_storeops_payroll_change_log.sql
-- Payroll module rework (owner directive 2026-07-27, mod-people band 400-499).
--
-- Deliverable 4: an append-only audit trail for MANUAL changes that alter a rep's payroll hours.
-- Gate-1 review (2026-07-27) corrected this comment's original "every write path" overclaim — the
-- ACTUAL hooked set, as of this migration's Gate-1-folded revision, is:
--   PATCH /storeops/shifts/{id} (field diff)      DELETE /storeops/shifts/{id} (before-state)
--   PATCH /storeops/shift-swaps/{id} approval (_apply_swap's employee_id reassignment)
--   POST /storeops/timeclock/override             POST/DELETE /storeops/manual-hours
--   POST /storeops/timeclock/force-clockout/run (manager "run now") + the pg_cron auto-sweep
--   POST /storeops/timeclock/clock-out's stale-punch auto-stamp branch (mirrors the force-clockout
--     sweep's own "stamp at scheduled end" logic, so it's logged the same way)
-- Filed as follow-ups, NOT hooked yet (see docs/handoffs/people.md OPEN): shift CREATE
-- (POST /shifts — the original schedule entry, not a correction), the bulk shift-template apply
-- (/shift-templates/save-week), and the employee-merge shift reassignment (/employees/merge).
--
-- Additive-only (ONE new table, nothing existing touched) + idempotent (CREATE TABLE IF NOT EXISTS
-- — confirmed safe to re-run/double-apply in Gate-1 review). What breaks until this runs: nothing —
-- every write path's logging call is wrapped in try/except (see storeops/router.py
-- `_log_payroll_change`) and degrades to "no log row written" on a missing table; the underlying
-- shift/punch/manual-hours write itself is COMPLETELY unaffected either way (the log call happens
-- after the real write succeeds, and never raises on its own failure).
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
  entry_point       text NOT NULL,          -- 'shift_edit' | 'shift_swap' | 'timeclock_override' |
                                             -- 'manual_hours_add' | 'manual_hours_delete' |
                                             -- 'force_clockout_manual' | 'force_clockout_cron' |
                                             -- 'clock_out_stale_auto'
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
