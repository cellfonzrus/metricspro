-- 052_portal_reports.sql — which reports are surfaced in the employee portal, and to which roles.
-- Run in the Supabase SQL editor. Idempotent.
--
-- Backs the new Reports hub (/reports): admins toggle "show in portal" per report and pick which
-- roles can see it. The /portal kiosk + /employee dashboard read this to show each employee the
-- reports their role is cleared for (links open the real report page, auto-scoped by Phase 5).
-- roles = [] means "any role that already has the report's module" (clearance from Roles & Access).

CREATE TABLE IF NOT EXISTS storeops.portal_reports (
  id         BIGSERIAL PRIMARY KEY,
  org_id     UUID NOT NULL,
  href       TEXT NOT NULL,                 -- the report page path, e.g. '/commcalc/kpi'
  label      TEXT,
  category   TEXT,
  enabled    BOOLEAN DEFAULT true,          -- shown in the employee portal at all
  roles      TEXT[] DEFAULT '{}',           -- allowed role names; [] = all roles with the module
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (org_id, href)
);

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['storeops.portal_reports'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;
GRANT USAGE, SELECT ON SEQUENCE storeops.portal_reports_id_seq TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'portal_reports ready' AS status;
