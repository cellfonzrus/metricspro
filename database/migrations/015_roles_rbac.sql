-- 015_roles_rbac.sql — Role assignment / RBAC for the employee rollout.
--
-- Builds on the existing scaffold (storeops.roles + core.users) rather than new tables:
--   - storeops.roles      : role definitions with an editable permissions JSONB
--   - core.users          : the logged-in identity (links Supabase Auth -> role + scope)
-- Employees authenticate via Supabase Auth (email+password); the backend creates the auth
-- accounts (service key) and upserts a core.users row per employee. The frontend reads its
-- OWN core.users row via the session (RLS auth_id = auth.uid()) to discover its role, then
-- reads storeops.roles for that role's permissions to gate the nav + pages.
--
-- permissions JSONB shape (editable in the Roles admin):
--   { "modules": { "commissions":bool, "targets":bool, "asset":bool, "vip":bool,
--                  "storeops":bool, "notify":bool, "admin":bool },
--     "scope": "all" | "market" | "store" | "self",
--     "home": "/commcalc"  }            -- landing page for this role

-- ── App config: the master "enforce login" switch (default OFF so deploying this does NOT
--    lock anyone out — flip it ON from the Roles admin once everyone is provisioned) ──────
CREATE TABLE IF NOT EXISTS core.app_config (
  id      INT PRIMARY KEY DEFAULT 1,
  org_id  UUID,
  rbac_enabled BOOLEAN DEFAULT false,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT app_config_singleton CHECK (id = 1)
);
INSERT INTO core.app_config (id, org_id, rbac_enabled)
  VALUES (1, '00000000-0000-0000-0000-000000000001', false)
  ON CONFLICT (id) DO NOTHING;

-- ── core.users: columns the rollout needs ───────────────────────────────────────────
ALTER TABLE core.users
  ADD COLUMN IF NOT EXISTS employee_id TEXT,            -- links storeops.employees.employee_id
  ADD COLUMN IF NOT EXISTS store_code  TEXT,            -- single home store (scope=store)
  ADD COLUMN IF NOT EXISTS must_reset_password BOOLEAN DEFAULT true,
  ADD COLUMN IF NOT EXISTS last_login  TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS core_users_auth ON core.users(auth_id);
CREATE INDEX IF NOT EXISTS core_users_org  ON core.users(org_id);

-- ── Seed the four roles (idempotent; ON CONFLICT keeps existing edited permissions) ──
-- Uniqueness for upsert: (org_id, name).
CREATE UNIQUE INDEX IF NOT EXISTS storeops_roles_org_name ON storeops.roles(org_id, name);

INSERT INTO storeops.roles (org_id, name, display_name, permissions) VALUES
 ('00000000-0000-0000-0000-000000000001', 'admin', 'Admin',
  '{"modules":{"commissions":true,"targets":true,"asset":true,"vip":true,"storeops":true,"notify":true,"admin":true},"scope":"all","home":"/commcalc"}'),
 ('00000000-0000-0000-0000-000000000001', 'market_manager', 'Market Manager',
  '{"modules":{"commissions":true,"targets":true,"asset":true,"vip":true,"storeops":true,"notify":true,"admin":false},"scope":"market","home":"/commcalc/targets"}'),
 ('00000000-0000-0000-0000-000000000001', 'store_manager', 'Store Manager',
  '{"modules":{"commissions":true,"targets":true,"asset":true,"vip":false,"storeops":true,"notify":false,"admin":false},"scope":"store","home":"/commcalc/targets"}'),
 ('00000000-0000-0000-0000-000000000001', 'sales_rep', 'Sales Rep',
  '{"modules":{"commissions":false,"targets":true,"asset":false,"vip":false,"storeops":false,"notify":false,"admin":false},"scope":"self","home":"/commcalc/targets/my"}')
ON CONFLICT (org_id, name) DO NOTHING;

-- ── RLS ──────────────────────────────────────────────────────────────────────────────
-- core.users: each logged-in user may read ONLY their own row (the frontend "who am I").
-- The backend admin (service_role) bypasses RLS, so the Roles admin still lists everyone.
ALTER TABLE core.users ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS user_self_read ON core.users;
CREATE POLICY user_self_read ON core.users FOR SELECT TO authenticated USING (auth_id = auth.uid());
DROP POLICY IF EXISTS user_self_update ON core.users;
CREATE POLICY user_self_update ON core.users FOR UPDATE TO authenticated USING (auth_id = auth.uid()) WITH CHECK (auth_id = auth.uid());

-- storeops.roles: role DEFINITIONS are not sensitive — any logged-in user may read them
-- (to resolve their own permissions). Writes go through the backend (service_role).
ALTER TABLE storeops.roles ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS roles_read ON storeops.roles;
CREATE POLICY roles_read ON storeops.roles FOR SELECT TO anon, authenticated USING (true);

GRANT USAGE ON SCHEMA core, storeops TO anon, authenticated;
GRANT SELECT, UPDATE ON core.users TO authenticated;
GRANT SELECT ON storeops.roles TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 015 complete — roles seeded, core.users ready for RBAC' as status;
