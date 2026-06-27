-- 055_tenants.sql — SaaS logins Phase 1: tenant registry + super-admin.
--
-- A "tenant" is an org_id + metadata. Super-admins (internal) create tenants and provision each
-- tenant's first admin login; that admin then manages their own company's users (Roles & Access).
-- This is ADDITIVE — the single-tenant app is unaffected. org_id resolution from the session
-- (so each tenant sees only its own data) is a later phase. Idempotent.

CREATE TABLE IF NOT EXISTS storeops.tenants (
  org_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT NOT NULL,
  slug       TEXT,
  is_active  BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- super-admin flag on the logged-in identity (internal operators who can create/manage tenants)
ALTER TABLE storeops.app_users ADD COLUMN IF NOT EXISTS super_admin BOOLEAN DEFAULT false;

-- bootstrap: register the house org as a tenant + make its existing admins super-admins
INSERT INTO storeops.tenants (org_id, name, slug)
  VALUES ('00000000-0000-0000-0000-000000000001', 'Cellfonz R Us', 'cellfonzrus')
  ON CONFLICT (org_id) DO NOTHING;
UPDATE storeops.app_users SET super_admin = true
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND role = 'admin';

-- RLS open_all (backend service role is the real guard, like the rest of storeops.*)
ALTER TABLE storeops.tenants ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS open_all ON storeops.tenants;
CREATE POLICY open_all ON storeops.tenants FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
GRANT ALL ON storeops.tenants TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 055 complete — storeops.tenants + app_users.super_admin' AS status;
