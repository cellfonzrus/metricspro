-- 715_tech_support.sql — In-house, Zendesk-class TECH-SUPPORT platform + per-page help-doc registry.
-- Band 700–799 (mod-platform-core). Additive + idempotent (safe to re-run).
--
-- OWNER DIRECTIVE 2026-07-22: build a cross-tenant tech-support console on top of the existing Helpdesk
-- module, plus a per-page help-documentation registry with TWO views (full support playbook + a trimmed
-- user-facing "?" panel). The HOUSE org's support staff handle escalated tickets from EVERY tenant in one
-- console.
--
-- SCHEMA CHOICE (deliberate deviation from the task's literal `helpdesk.*` naming — documented):
--   • The help-doc registry is `core.support_doc` (core schema, per task — same exposed schema as
--     core.failure_log / core.module_catalog).
--   • The support-case tables live in the `storeops` schema, NOT a new `helpdesk` schema. WHY: the live
--     Helpdesk module already stores ALL its tables (tickets, ticket_comments, ticket_events, …) in
--     `storeops`, and only public/commcalc/storeops/core/notify are exposed to PostgREST. A brand-new
--     `helpdesk` schema is NOT exposed → the supabase-py client (`.schema("helpdesk")`) would 404 until an
--     operator changes the Supabase "Exposed schemas" dashboard config, silently breaking the whole
--     console. Keeping the case tables in `storeops` puts them in the SAME exposed schema as the
--     `storeops.tickets` they escalate (so `ticket_id` is a same-schema reference) and needs ZERO new
--     exposure. The `support_` name prefix keeps them clearly the support-platform tables.
--
-- MULTI-TENANT (RULE ONE): every new table has org_id uuid NOT NULL + an index on it. A support_case's
-- org_id = the TENANT the ticket came from; a support_doc's org_id = HOUSE (global/product doc) OR a tenant
-- (per-tenant override); SLA policy + canned responses are HOUSE-org config (support is house-run).
--
-- CONFIG-AS-DATA (RULE TWO): SLA hours + canned-response text live in ROWS, never in code. The defaults
-- below are SEED rows the code reads — the backend never hard-codes an hour value.
--
-- DEGRADES GRACEFULLY: until this runs, every read/write of these tables in the helpdesk/core routers is
-- try/except-guarded → the console shows an honest empty state, escalate no-ops, and the "?" panel says
-- "help is coming soon". No unrelated page breaks.

-- ── core.support_doc — the per-page help registry ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS core.support_doc (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL,                    -- HOUSE = global/product doc; a tenant org = override
  page_key          TEXT NOT NULL,                    -- route href, e.g. '/storeops/payroll' (longest-prefix matched)
  title             TEXT,
  module            TEXT,                             -- nav module key (for coverage grouping)
  user_md           TEXT,                             -- trimmed user-facing markdown (the "?" panel)
  support_md        TEXT,                             -- full support playbook (console right-rail)
  common_issues     JSONB,                            -- [{symptom, diagnosis, fix, escalate_when}]
  permissions_needed TEXT,
  related_settings  JSONB,
  is_published      BOOLEAN NOT NULL DEFAULT true,
  updated_by        TEXT,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, page_key)
);
CREATE INDEX IF NOT EXISTS support_doc_org_idx  ON core.support_doc(org_id);
CREATE INDEX IF NOT EXISTS support_doc_page_idx ON core.support_doc(page_key);

ALTER TABLE core.support_doc ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY open_all ON core.support_doc FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN OTHERS THEN NULL; END $$;
GRANT ALL ON core.support_doc TO anon, authenticated, service_role;

-- ── storeops.support_case — escalation target (one per escalated ticket) ────────────────────────
CREATE TABLE IF NOT EXISTS storeops.support_case (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,                       -- the TENANT the ticket came from
  ticket_id      UUID,                                -- loose ref to storeops.tickets.id (same schema)
  page_key       TEXT,                                -- page the escalation was raised from (context)
  status         TEXT NOT NULL DEFAULT 'new',         -- new | in_progress | waiting_user | resolved | closed
  priority       TEXT NOT NULL DEFAULT 'normal',      -- low | normal | high | urgent
  assignee_email TEXT,
  sla_due_at     TIMESTAMPTZ,                          -- computed from support_sla_policy at escalation
  resolution     TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, ticket_id)                          -- idempotent: one case per ticket
);
CREATE INDEX IF NOT EXISTS support_case_org_idx      ON storeops.support_case(org_id);
CREATE INDEX IF NOT EXISTS support_case_status_idx   ON storeops.support_case(status);
CREATE INDEX IF NOT EXISTS support_case_assignee_idx ON storeops.support_case(assignee_email);

ALTER TABLE storeops.support_case ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY open_all ON storeops.support_case FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN OTHERS THEN NULL; END $$;
GRANT ALL ON storeops.support_case TO anon, authenticated, service_role;

-- ── storeops.support_case_event — timeline (replies · internal notes · status · assign) ─────────
CREATE TABLE IF NOT EXISTS storeops.support_case_event (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,                      -- mirrors the case's org (the tenant)
  case_id         UUID NOT NULL,
  kind            TEXT NOT NULL,                      -- reply | internal_note | status | assign
  body            TEXT,
  author_email    TEXT,
  visible_to_user BOOLEAN NOT NULL DEFAULT false,     -- true → also fanned into the tenant ticket thread
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS support_case_event_case_idx ON storeops.support_case_event(org_id, case_id);

ALTER TABLE storeops.support_case_event ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY open_all ON storeops.support_case_event FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN OTHERS THEN NULL; END $$;
GRANT ALL ON storeops.support_case_event TO anon, authenticated, service_role;

-- ── storeops.support_canned_response — HOUSE-org config (RULE TWO: content is data) ─────────────
CREATE TABLE IF NOT EXISTS storeops.support_canned_response (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL,
  title      TEXT NOT NULL,
  body       TEXT NOT NULL,
  category   TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS support_canned_org_idx ON storeops.support_canned_response(org_id);

ALTER TABLE storeops.support_canned_response ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY open_all ON storeops.support_canned_response FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN OTHERS THEN NULL; END $$;
GRANT ALL ON storeops.support_canned_response TO anon, authenticated, service_role;

-- ── storeops.support_sla_policy — HOUSE-org config: priority → response/resolve hours ───────────
CREATE TABLE IF NOT EXISTS storeops.support_sla_policy (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  priority       TEXT NOT NULL,                       -- low | normal | high | urgent
  response_hours INT,
  resolve_hours  INT,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, priority)
);
CREATE INDEX IF NOT EXISTS support_sla_org_idx ON storeops.support_sla_policy(org_id);

ALTER TABLE storeops.support_sla_policy ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY open_all ON storeops.support_sla_policy FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN OTHERS THEN NULL; END $$;
GRANT ALL ON storeops.support_sla_policy TO anon, authenticated, service_role;

-- Seed the HOUSE SLA defaults (VALUES live here in rows; the code never hard-codes hours). Idempotent.
INSERT INTO storeops.support_sla_policy (org_id, priority, response_hours, resolve_hours) VALUES
  ('00000000-0000-0000-0000-000000000001', 'urgent', 4,  24),
  ('00000000-0000-0000-0000-000000000001', 'high',   8,  48),
  ('00000000-0000-0000-0000-000000000001', 'normal', 24, 96),
  ('00000000-0000-0000-0000-000000000001', 'low',    48, 168)
ON CONFLICT (org_id, priority) DO NOTHING;

-- ── `support` module: register it in the canonical registry (mig 700) so it's a first-class module ──
-- (entitlement propagation to every tenant happens via the entitlement engine + SEED_VERSION bump).
INSERT INTO core.module_catalog (key, label, sort_order) VALUES
  ('support', 'Tech Support', 115)
ON CONFLICT (key) DO NOTHING;

-- ── Seed the HOUSE `support_agent` role (modules: support + helpdesk + notify, scope all). Idempotent;
--    ON CONFLICT keeps any already-edited permissions. HOUSE-only by design — support agents are house
--    staff (the console is cross-tenant + house-gated). Existing house roles are NOT auto-updated
--    (seeding is forward-only) — the owner grants `support` to any existing house role at /admin/roles. ──
INSERT INTO storeops.roles (org_id, name, display_name, permissions) VALUES
  ('00000000-0000-0000-0000-000000000001', 'support_agent', 'Tech Support Agent',
   '{"modules":{"support":true,"helpdesk":true,"notify":true},"scope":"all","home":"/admin/support"}')
ON CONFLICT (org_id, name) DO NOTHING;

-- ── Seed 2–3 EXEMPLAR help docs (HOUSE = global) so the panel + console demo end-to-end. The full
--    six domain content packs arrive later via POST /api/v1/core/support-docs/import. Idempotent. ──
INSERT INTO core.support_doc (org_id, page_key, title, module, user_md, support_md, common_issues, permissions_needed, related_settings)
VALUES
  ('00000000-0000-0000-0000-000000000001', '/storeops/payroll', 'Payroll', 'storeops',
   E'**Payroll** shows each employee''s hours × pay rate for the selected pay period.\n\n- Pick the pay period at the top.\n- Numbers come from approved time-clock punches and the pay rate on the employee record.\n- If a total looks off, check the employee''s hours on **Time Clock** and their rate on **Employees**.',
   E'Payroll = shifts (approved punches) × the employee''s pay rate for the pay period. It reads storeops time-clock data; it does NOT move money.\n\nDIAGNOSE A WRONG/EMPTY TOTAL:\n1. Confirm the pay period (a future period is empty by design).\n2. Missing person → they have no approved punches OR no pay rate on their Employees record.\n3. $0 across the board for a non-house tenant is often correct isolation (no data for that tenant yet), not a bug.\n4. Tax variant: /storeops/payroll-tax layers the tax withholding config on top.',
   '[{"symptom":"An employee is missing from payroll","diagnosis":"No approved time-clock punches in the period, or no pay rate on the Employees record.","fix":"Approve their punches on Time Clock and set the rate on Employees, then reload.","escalate_when":"Punches and rate both exist but the row is still missing after reload."},
     {"symptom":"Whole payroll is $0","diagnosis":"Wrong pay period, or (non-house tenant) no data ingested yet.","fix":"Select the correct closed pay period; confirm the tenant has punches for it.","escalate_when":"A period with known punches still totals $0."}]',
   'StoreOps module + company-wide (all) or market scope',
   '["Pay Period & Work-Week (/admin/tenant-settings)","Employees pay rates (/storeops/employees)"]'),

  ('00000000-0000-0000-0000-000000000001', '/commcalc', 'Commissions Dashboard', 'commissions',
   E'**Commissions** shows what reps earned for the selected month.\n\n- Upload the monthly **Sales Transaction Details** (78-column) file under Data Imports, then run the calculation.\n- Empty or $0? Usually the wrong sales file (missing Contract Type) or a period with no data.\n- Total/VidaPay carriers see commissions on the **Total Processor** page, not here.',
   E'Boost commissions come from POS sales × the commission plan. $0 is almost always MISSING INPUT, not a real zero.\n\nDIAGNOSE $0 / EMPTY:\n1. Wrong file — the 25-col legacy daily export has no Contract Type → $0. Re-upload the full 78-col "Sales Transaction Details".\n2. Dates stored as Excel serials (parser does str(val)[:10]) → re-export with real date cells.\n3. Non-Boost tenant → pay comes ONLY from Commission Plans; a missing plan assignment = $0 (carrier_mode gate), NOT a bug — assign a plan.\n4. Period with no upload → check Upload History badges.\n5. Recompute 502s but COMPLETES — do not re-fire; poll the period.',
   'Commissions module + report access (company-wide or explicit report grant)',
   '["Commission Plans & Payout Schedules","Carrier Selection","Sales Classification settings"]'),

  ('00000000-0000-0000-0000-000000000001', '/closing', 'Daily Closing', 'closing',
   E'**Daily Closing** reconciles each store''s cash and card at end of day.\n\n- Reps submit a closing sheet; a manager verifies it.\n- Cash short is blocked, cash over is flagged; credit over is blocked, credit under is flagged.\n- The tender (cash vs card) split comes from the POS X-Report, not the sales feed.',
   E'Closing reconciles submitted tender vs the POS X-Report vs expected sales.\n\nDIAGNOSE "recon flags everything":\n1. The X-Report is the tender source. If a store/day has no X-Report ingested, every tender line looks unmatched — confirm the X-Report import for that store+day.\n2. Cash short = block, over = flag; credit over = block, under = flag (thresholds are configurable in Tender/Cash Setup).\n3. A missing daily closing can raise an ops chargeback against the effective closer (retail-ops) — that is by design.',
   'Daily Closing module + market/store scope for verification',
   '["Cash Setup (/closing/cash-config)","Tender Setup (/closing/tender-config)","Auto-Import (/closing/imports)"]')
ON CONFLICT (org_id, page_key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT '715 complete — core.support_doc + storeops.support_case/_event/_canned_response/_sla_policy + support module + support_agent role + SLA defaults + 3 exemplar docs' AS status;
