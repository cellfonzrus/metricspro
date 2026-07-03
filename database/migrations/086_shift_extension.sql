-- 086_shift_extension.sql — shift-extension requests (manager → District Manager approval workflow)
--
-- WHY: at the scheduled end of a shift an employee is FORCE clocked-out (a pg_cron job closes the
-- open timelog punch, stamping the clock-out at the scheduled end so paid hours match the schedule).
-- The ONLY way to keep working past that is an extension approved AHEAD OF TIME: a manager files a
-- request, the District Manager approves it in-app (a workflow, not an email approval — the DM's tick
-- IS the approval, recorded with who + when), and the forced-clockout job then honors the extended
-- end for that employee/day. An email notification is sent to the DM as an FYI (not the approval path).
--
-- SAFE: additive + idempotent.

CREATE TABLE IF NOT EXISTS storeops.shift_extension (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  employee_id       TEXT,                 -- the employee whose shift is extended (business id)
  employee_name     TEXT,
  store_code        TEXT,
  shift_id          BIGINT,               -- optional link to storeops.shifts.id
  shift_date        DATE,
  original_end      TEXT,                 -- "HH:MM" (business-local) the shift was scheduled to end
  requested_end     TEXT NOT NULL,        -- "HH:MM" the manager wants it extended to
  reason            TEXT,
  status            TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | denied | expired
  requested_by      TEXT,                 -- the manager (email) who filed it
  requested_by_name TEXT,
  requested_at      TIMESTAMPTZ DEFAULT NOW(),
  dm_employee_id    TEXT,                 -- the resolved District Manager who should approve
  dm_email          TEXT,
  decided_by        TEXT,                 -- who approved/denied (email)
  decided_by_name   TEXT,
  decided_at        TIMESTAMPTZ,
  decision_note     TEXT,
  created_at        TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS shift_extension_org_status ON storeops.shift_extension (org_id, status);
CREATE INDEX IF NOT EXISTS shift_extension_emp_date   ON storeops.shift_extension (org_id, employee_id, shift_date);

ALTER TABLE storeops.shift_extension ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS open_all ON storeops.shift_extension;
CREATE POLICY open_all ON storeops.shift_extension FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
GRANT ALL ON storeops.shift_extension TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 086 complete — storeops.shift_extension' AS status;
