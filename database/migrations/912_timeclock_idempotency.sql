-- 912_timeclock_idempotency.sql — Clock-in/out reliability under load (idempotency + single-open).
--
-- WHY: under saturation the kiosk/mobile retried punches with no idempotency key, so a slow response
-- could open a SECOND concurrent punch (double clock-in) and a clock-out retry after a successful close
-- showed a confusing 404. This migration adds the dedupe primitives the handlers now rely on:
--   1) client_request_id — a client-generated UUID, STABLE across retries of the SAME punch, so the
--      backend can recognise a replay and return the existing row instead of creating a duplicate.
--   2) a unique idempotency index on (org_id, employee_id, client_request_id).
--   3) a unique ONE-OPEN index (org_id, employee_id) WHERE clock_out IS NULL — at most one open punch
--      per employee, enforced by Postgres (not just the app guard), which is what makes a concurrent
--      double clock-in collapse to a single row.
--
-- ADDITIVE + IDEMPOTENT (re-running is safe). Every statement is a single line so the tenant SQL runner
-- can execute them one at a time. Do NOT change hours math or closing-gate rules — this is dedupe only.
--
-- ORDER MATTERS: the ONE-OPEN unique index (step 4) will FAIL to build on any tenant that already has
-- more than one open row for an employee. Step 3 is a GUARDED dedupe UPDATE that CLOSES those duplicate
-- open rows FIRST (keeping the EARLIEST open row per (org, employee); the later duplicates are closed at
-- their own clock_in, i.e. zero-length, so NO phantom hours are added and hours stays NULL → excluded
-- from every payroll reader). Run the steps in order.

-- 1) client_request_id column.
ALTER TABLE storeops.timelog ADD COLUMN IF NOT EXISTS client_request_id TEXT;

-- 2) Idempotency key: at most one row per (org_id, employee_id, client_request_id) when the id is set.
CREATE UNIQUE INDEX IF NOT EXISTS timelog_client_req_idx ON storeops.timelog (org_id, employee_id, client_request_id) WHERE client_request_id IS NOT NULL;

-- 3) GUARDED DEDUPE — MUST run before step 4. Close every duplicate OPEN row (any open row that has an
--    EARLIER open sibling for the same org+employee), stamping clock_out = its own clock_in (zero-length,
--    hours left NULL so it never reaches payroll) and appending an audit note. Keeps exactly the earliest
--    open punch per (org, employee) open.
UPDATE storeops.timelog t SET clock_out = t.clock_in, notes = NULLIF(TRIM(BOTH ' |' FROM COALESCE(t.notes,'') || ' | auto-closed duplicate open punch (idempotency migration 912)'),'') WHERE t.clock_out IS NULL AND EXISTS (SELECT 1 FROM storeops.timelog o WHERE o.org_id = t.org_id AND o.employee_id = t.employee_id AND o.clock_out IS NULL AND (o.clock_in < t.clock_in OR (o.clock_in = t.clock_in AND o.id < t.id)));

-- 4) ONE-OPEN enforcement: at most one OPEN punch per (org_id, employee_id). Build AFTER step 3.
CREATE UNIQUE INDEX IF NOT EXISTS timelog_one_open_idx ON storeops.timelog (org_id, employee_id) WHERE clock_out IS NULL;
