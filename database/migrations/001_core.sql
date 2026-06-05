-- MIGRATION 001: CORE SCHEMA
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS commcalc;
CREATE SCHEMA IF NOT EXISTS storeops;

CREATE TABLE IF NOT EXISTS core.organizations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  plan TEXT DEFAULT 'starter',
  enabled_modules TEXT[] DEFAULT ARRAY['commcalc','storeops'],
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS core.users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES core.organizations(id),
  auth_id UUID UNIQUE,
  email TEXT NOT NULL,
  full_name TEXT,
  role TEXT DEFAULT 'viewer',
  market TEXT,
  store_codes TEXT[],
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE core.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.users ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users_own_org" ON core.users FOR ALL TO authenticated
  USING (org_id IN (SELECT org_id FROM core.users WHERE auth_id = auth.uid()));
CREATE POLICY "orgs_read" ON core.organizations FOR SELECT TO authenticated
  USING (id IN (SELECT org_id FROM core.users WHERE auth_id = auth.uid()));

INSERT INTO core.organizations (id,name,slug,plan) VALUES
  ('00000000-0000-0000-0000-000000000001','Cellular Services','cellular-services','growth')
ON CONFLICT (slug) DO NOTHING;

SELECT 'Migration 001 complete' as status;
