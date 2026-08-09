-- 423_storeops_face_retention_triggers.sql — people band.
--
-- FIXES A DEFECT INTRODUCED THE SAME DAY, BEFORE IT COULD DESTROY ANYTHING SILENTLY.
--
-- Migration 422 constrained `storeops.face_retention_log.trigger` to exactly four values. The derived
-- last-day rule added hours later emits a FIFTH (`purpose_satisfied_derived`), and `_write_audit_log`
-- is deliberately best-effort — it catches its own exception so that a logging failure can never undo
-- a completed delete. Those two facts combine badly: a derived-trigger destruction would have deleted
-- the descriptor, had its audit INSERT rejected by this CHECK, printed a warning nobody reads, and
-- left NO evidence. The policy's §7 promise ("every destruction is recorded") would have been false
-- for precisely the destructions the derived rule exists to perform.
--
-- Two values are added:
--   * purpose_satisfied_derived — same 90-day rule as purpose_satisfied, but the last day of
--     employment was DERIVED from the timekeeping record rather than recorded by HR. Kept distinct so
--     an auditor can always tell which dates a human attested to and which the system inferred.
--   * admin_directed — an administrator destroying a descriptor EARLIER than the schedule requires,
--     as a deliberate act. Destroying early is never a violation (BIPA's "whichever occurs first"
--     makes earlier strictly safer), but it must not be recorded as though a scheduled trigger fired,
--     and it is not an `employee_request` unless the employee actually asked. Recording an owner
--     decision as the employee's request would be a false entry in the very log that exists to be
--     trusted years later.
--
-- Additive + idempotent + re-runnable. Widening a CHECK cannot invalidate an existing row.

ALTER TABLE storeops.face_retention_log
  DROP CONSTRAINT IF EXISTS face_retention_log_trigger_chk;

ALTER TABLE storeops.face_retention_log
  ADD CONSTRAINT face_retention_log_trigger_chk
  CHECK (trigger = ANY (ARRAY[
    'purpose_satisfied'::text,
    'purpose_satisfied_derived'::text,
    'statutory_backstop'::text,
    'employee_request'::text,
    'tenant_disabled_purge'::text,
    'admin_directed'::text
  ]));

SELECT 'Migration 423 complete — audit log accepts the derived and admin-directed triggers' AS status;
