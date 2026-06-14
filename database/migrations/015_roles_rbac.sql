-- 015_roles_rbac.sql — Role assignment / RBAC for the employee rollout.
--
-- IMPORTANT: everything lives in the `storeops` schema because that schema is already exposed
-- to PostgREST (the backend's supabase-py client talks to PostgREST, which only serves exposed
-- schemas — `core` is NOT exposed, so core.* would silently fail). Uses the existing
-- storeops.roles table; adds storeops.app_users (the logged-in identity) + storeops.app_config
-- (the master enforce-login switch).
--
-- Employees authenticate via Supabase Auth (email+password); the backend creates the auth
-- accounts (service key) and upserts a storeops.app_users row per employee. The frontend never
-- reads these tables directly — it calls the token-verified GET /api/v1/core/me — so they stay
-- backend-only (RLS on, no anon/authenticated policy; the service role bypasses RLS).
--
-- permissions JSONB shape (editable in the Roles admin):
--   { "modules": { "commissions":bool, "targets":bool, "asset":bool, "vip":bool,
--                  "storeops":bool, "notify":bool, "admin":bool },
--     "scope": "all" | "market" | "store" | "self",
--     "home": "/commcalc"  }            -- landing page for this role

-- ── App users: the logged-in identity (links Supabase Auth -> role + scope) ───────────
CREATE TABLE IF NOT EXISTS storeops.app_users (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID,
  auth_id      UUID UNIQUE,                 -- Supabase Auth user id
  email        TEXT NOT NULL,
  full_name    TEXT,
  role         TEXT,                        -- storeops.roles.name
  market       TEXT,
  store_code   TEXT,
  store_codes  TEXT[],
  employee_id  TEXT,                        -- links storeops.employees.employee_id
  is_active    BOOLEAN DEFAULT true,
  must_reset_password BOOLEAN DEFAULT true,
  last_login   TIMESTAMPTZ,
  created_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS app_users_org_email ON storeops.app_users(org_id, email);
CREATE INDEX IF NOT EXISTS app_users_auth ON storeops.app_users(auth_id);

-- ── App config: the master "enforce login" switch (default OFF so deploying this does NOT
--    lock anyone out — flip it ON from the Roles admin once everyone is provisioned) ──────
CREATE TABLE IF NOT EXISTS storeops.app_config (
  id      INT PRIMARY KEY DEFAULT 1,
  org_id  UUID,
  rbac_enabled BOOLEAN DEFAULT false,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT app_config_singleton CHECK (id = 1)
);
INSERT INTO storeops.app_config (id, org_id, rbac_enabled)
  VALUES (1, '00000000-0000-0000-0000-000000000001', false)
  ON CONFLICT (id) DO NOTHING;

-- ── Seed the four roles (idempotent; ON CONFLICT keeps any already-edited permissions) ──
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

-- ── RLS: app_users + app_config are BACKEND-ONLY (no anon/authenticated policy → denied;
--    the service role used by the backend bypasses RLS). The frontend never reads them. ──────
ALTER TABLE storeops.app_users  ENABLE ROW LEVEL SECURITY;
ALTER TABLE storeops.app_config ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON storeops.app_users, storeops.app_config FROM anon, authenticated;
GRANT ALL ON storeops.app_users, storeops.app_config TO service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 015 complete — roles seeded; storeops.app_users + app_config ready (backend-only)' as status;
