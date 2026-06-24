-- 040_shift_templates.sql
-- Recurring weekly shift templates: a per-employee canonical week (weekday → store + times). Save a
-- week as the template once, then APPLY it to any week with one click (dedup-safe). Durable, unlike
-- copy-week (which needs a concrete source week). Also a clean source for the targets weekly pattern.
CREATE TABLE IF NOT EXISTS storeops.shift_templates (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  employee_id     TEXT,
  employee_name   TEXT,
  store_code      TEXT,
  weekday         INT NOT NULL,             -- 0=Mon .. 6=Sun
  start_time      TEXT,
  end_time        TEXT,
  scheduled_hours NUMERIC DEFAULT 0,
  created_at      TIMESTAMPTZ DEFAULT now(),
  UNIQUE (org_id, employee_id, weekday, store_code)
);
CREATE INDEX IF NOT EXISTS shift_templates_emp ON storeops.shift_templates (org_id, employee_id);

ALTER TABLE storeops.shift_templates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS open_all ON storeops.shift_templates;
CREATE POLICY open_all ON storeops.shift_templates FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
GRANT ALL ON storeops.shift_templates TO anon, authenticated, service_role;
