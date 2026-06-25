-- 045_timeclock.sql — StoreOps Time Clock + Face Recognition + Payroll tax (Part B / Theme 7).
--
-- Four tables, all keyed by employee_id (TEXT) = storeops.employees.employee_id (same key shifts use):
--   timelog          — one row per clock-in; clock-out updates the SAME row (never a new row).
--   face_descriptors — one 128-float face-api.js embedding per employee (enroll on first clock-in).
--   payroll_settings — per-employee W-4 + state election for the tax engine.
--   manual_hours     — admin adjustments (missed punches, training); can be negative.
--
-- Idempotent (CREATE ... IF NOT EXISTS). Re-running is safe.

CREATE TABLE IF NOT EXISTS storeops.timelog (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  employee_id    TEXT NOT NULL,                 -- → storeops.employees.employee_id
  employee_name  TEXT,                          -- denormalized snapshot at clock-in
  store_code     TEXT,
  clock_in       TIMESTAMPTZ NOT NULL DEFAULT now(),
  clock_out      TIMESTAMPTZ,                   -- null until clock-out
  hours          NUMERIC(6,2),                  -- computed on clock-out
  work_date      DATE NOT NULL,                 -- date(clock_in) for easy filtering
  device         TEXT,                          -- mobile | desktop
  selfie_path    TEXT,                          -- Supabase Storage path (signed on read)
  gps_lat        NUMERIC(10,8),
  gps_lng        NUMERIC(11,8),
  gps_accuracy_m INT,
  face_match_pct INT,                           -- 0-100, audit only (never gates entry)
  notes          TEXT,                          -- admin manual-adjustment note
  created_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS timelog_emp_date_idx ON storeops.timelog (org_id, employee_id, work_date);
CREATE INDEX IF NOT EXISTS timelog_open_idx     ON storeops.timelog (org_id, employee_id) WHERE clock_out IS NULL;

CREATE TABLE IF NOT EXISTS storeops.face_descriptors (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  employee_id    TEXT NOT NULL,
  descriptor     JSONB NOT NULL,                -- 128 floats (face-api.js FaceRecognitionNet)
  register_count INT DEFAULT 1,
  registered_at  TIMESTAMPTZ DEFAULT now(),
  updated_at     TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS face_descriptors_emp_uq ON storeops.face_descriptors (org_id, employee_id);

CREATE TABLE IF NOT EXISTS storeops.payroll_settings (
  org_id            UUID NOT NULL,
  employee_id       TEXT NOT NULL,
  filing_status     TEXT DEFAULT 'Single',       -- Single | Married | HOH
  allowances        INT DEFAULT 0,               -- W-4 line 5
  state             TEXT DEFAULT 'NY',           -- NY | NJ | PA | DE
  extra_withholding NUMERIC(8,2) DEFAULT 0,      -- additional $ per pay period
  skipped           BOOLEAN DEFAULT false,       -- flat-rate instead of W-4 table
  updated_at        TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (org_id, employee_id)
);

CREATE TABLE IF NOT EXISTS storeops.manual_hours (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  employee_id TEXT NOT NULL,
  work_date   DATE NOT NULL,
  hours       NUMERIC(6,2) NOT NULL,             -- can be negative for deductions
  reason      TEXT NOT NULL,                     -- required — audit trail
  added_by    TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS manual_hours_emp_idx ON storeops.manual_hours (org_id, employee_id, work_date);

-- RLS + grants (mirror migration 003; service_role added explicitly for the backend service key).
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['storeops.timelog','storeops.face_descriptors','storeops.payroll_settings','storeops.manual_hours'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS anon_read ON %s', t);
    EXECUTE format('DROP POLICY IF EXISTS auth_write ON %s', t);
    EXECUTE format('CREATE POLICY anon_read ON %s FOR SELECT TO anon USING (true)', t);
    EXECUTE format('CREATE POLICY auth_write ON %s FOR ALL TO authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT SELECT ON %s TO anon', t);
    EXECUTE format('GRANT ALL ON %s TO authenticated, service_role', t);
  END LOOP;
END $$;
