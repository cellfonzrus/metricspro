-- 053_helpdesk.sql — Helpdesk / ticketing module (multi-tenant ready).
--
-- Per MetricsPro_Helpdesk_Build_Spec_v2: a per-tenant ticket system (employees raise tickets,
-- managers/admins resolve them), configurable like SAP — categories / priorities / statuses /
-- teams / custom fields all live in per-org config tables (config-as-data, not code).
--
-- Everything lives in the `storeops` schema because that schema is already exposed to PostgREST
-- (same reason migrations 015 + 050 put roles/org there — `core`/`helpdesk` schemas are NOT
-- exposed, so a dedicated schema would silently fail without a Supabase "Exposed schemas" change).
-- Every table carries org_id and the backend filters every query by it (single-tenant today with
-- org_id defaulting to the house org, but the schema is tenant-ready). RLS is open_all to match
-- the rest of storeops.* (the backend service role is the real guard).

-- ── Module entitlement: which orgs have the helpdesk on ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.tenant_modules (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  module_key  TEXT NOT NULL,                 -- 'helpdesk', 'commcalc', …
  is_enabled  BOOLEAN NOT NULL DEFAULT false,
  config      JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, module_key)
);

-- ── Per-tenant config (the SAP-like layer) ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.ticket_categories (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL, name TEXT NOT NULL, description TEXT,
  sort_order INT NOT NULL DEFAULT 0, is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS storeops.ticket_priorities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL, key TEXT NOT NULL, label TEXT NOT NULL, color TEXT,
  sort_order INT NOT NULL DEFAULT 0, is_active BOOLEAN NOT NULL DEFAULT true,
  UNIQUE (org_id, key)
);
CREATE TABLE IF NOT EXISTS storeops.ticket_statuses (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL, key TEXT NOT NULL, label TEXT NOT NULL,
  stage TEXT NOT NULL CHECK (stage IN ('open','pending','done')),
  color TEXT, sort_order INT NOT NULL DEFAULT 0, is_active BOOLEAN NOT NULL DEFAULT true,
  UNIQUE (org_id, key)
);
CREATE TABLE IF NOT EXISTS storeops.ticket_teams (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL, name TEXT NOT NULL, is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS storeops.ticket_team_members (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  team_id UUID NOT NULL REFERENCES storeops.ticket_teams(id) ON DELETE CASCADE,
  member TEXT NOT NULL,                       -- employee_id / app_user id / email (loose, like other modules)
  UNIQUE (org_id, team_id, member)
);
CREATE TABLE IF NOT EXISTS storeops.ticket_custom_fields (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL, field_key TEXT NOT NULL, label TEXT NOT NULL,
  field_type TEXT NOT NULL CHECK (field_type IN
    ('text','textarea','number','date','select','multiselect','checkbox')),
  options JSONB, is_required BOOLEAN NOT NULL DEFAULT false,
  sort_order INT NOT NULL DEFAULT 0, is_active BOOLEAN NOT NULL DEFAULT true,
  UNIQUE (org_id, field_key)
);
CREATE TABLE IF NOT EXISTS storeops.ticket_settings (
  org_id UUID PRIMARY KEY,
  default_assignee TEXT, notify_emails TEXT[], brand_logo_url TEXT, brand_color TEXT,
  business_hours JSONB, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Core ticket tables ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.tickets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL, ticket_number BIGINT,
  subject TEXT NOT NULL, description TEXT NOT NULL,
  status_id   UUID REFERENCES storeops.ticket_statuses(id),
  priority_id UUID REFERENCES storeops.ticket_priorities(id),
  category_id UUID REFERENCES storeops.ticket_categories(id),
  team_id     UUID REFERENCES storeops.ticket_teams(id),
  -- requester identity is loose so ANY employee/user can raise a ticket (login not required)
  requester_id TEXT, requester_name TEXT, requester_email TEXT,
  store_code TEXT, assignee TEXT,
  custom_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ, closed_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS storeops.ticket_comments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  ticket_id UUID NOT NULL REFERENCES storeops.tickets(id) ON DELETE CASCADE,
  author TEXT, author_name TEXT, body TEXT NOT NULL,
  is_internal BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS storeops.ticket_attachments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  ticket_id UUID NOT NULL REFERENCES storeops.tickets(id) ON DELETE CASCADE,
  comment_id UUID REFERENCES storeops.ticket_comments(id) ON DELETE CASCADE,
  uploader TEXT, file_name TEXT NOT NULL, storage_path TEXT NOT NULL,
  file_size BIGINT, mime_type TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS storeops.ticket_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  ticket_id UUID NOT NULL REFERENCES storeops.tickets(id) ON DELETE CASCADE,
  actor TEXT, event_type TEXT NOT NULL, detail JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS storeops.ticket_counters (
  org_id UUID PRIMARY KEY, last_value BIGINT NOT NULL DEFAULT 1000
);

-- ── Indexes ──────────────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_tickets_org       ON storeops.tickets(org_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status    ON storeops.tickets(org_id, status_id);
CREATE INDEX IF NOT EXISTS idx_tickets_assignee  ON storeops.tickets(org_id, assignee);
CREATE INDEX IF NOT EXISTS idx_tickets_requester ON storeops.tickets(org_id, requester_email);
CREATE INDEX IF NOT EXISTS idx_tickets_custom    ON storeops.tickets USING gin (custom_fields);
CREATE INDEX IF NOT EXISTS idx_tk_comments       ON storeops.ticket_comments(ticket_id);
CREATE INDEX IF NOT EXISTS idx_tk_attach         ON storeops.ticket_attachments(ticket_id);
CREATE INDEX IF NOT EXISTS idx_tk_events         ON storeops.ticket_events(ticket_id);
CREATE INDEX IF NOT EXISTS idx_tk_cat            ON storeops.ticket_categories(org_id);
CREATE INDEX IF NOT EXISTS idx_tk_pri            ON storeops.ticket_priorities(org_id);
CREATE INDEX IF NOT EXISTS idx_tk_status         ON storeops.ticket_statuses(org_id);

-- ── Per-tenant sequential ticket number (TKT-1001, 1002 … per org) ───────────────────────────────
CREATE OR REPLACE FUNCTION storeops.assign_ticket_number()
RETURNS TRIGGER AS $$
DECLARE next_val BIGINT;
BEGIN
  INSERT INTO storeops.ticket_counters(org_id, last_value) VALUES (NEW.org_id, 1001)
  ON CONFLICT (org_id) DO UPDATE SET last_value = storeops.ticket_counters.last_value + 1
  RETURNING last_value INTO next_val;
  NEW.ticket_number := next_val;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_assign_ticket_number ON storeops.tickets;
CREATE TRIGGER trg_assign_ticket_number
  BEFORE INSERT ON storeops.tickets FOR EACH ROW EXECUTE FUNCTION storeops.assign_ticket_number();

-- ── Enable the module for the house org so it works immediately (defaults are lazy-seeded by the
--    backend on first /helpdesk/config/bootstrap call) ────────────────────────────────────────────
INSERT INTO storeops.tenant_modules (org_id, module_key, is_enabled)
  VALUES ('00000000-0000-0000-0000-000000000001', 'helpdesk', true)
  ON CONFLICT (org_id, module_key) DO UPDATE SET is_enabled = true;

-- ── RLS open_all (matches the rest of storeops.*; backend service role is the real guard) ─────────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'storeops.tenant_modules','storeops.ticket_categories','storeops.ticket_priorities',
    'storeops.ticket_statuses','storeops.ticket_teams','storeops.ticket_team_members',
    'storeops.ticket_custom_fields','storeops.ticket_settings','storeops.tickets',
    'storeops.ticket_comments','storeops.ticket_attachments','storeops.ticket_events',
    'storeops.ticket_counters'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 053 complete — helpdesk tables + per-org ticket numbering ready (storeops schema)' AS status;
