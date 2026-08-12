-- 800_crm_pipeline.sql — CRM: sales pipeline, follow-up engine, agencies, Customer 360
--
-- OWNER DIRECTIVE 2026-08-12 (sanjot@): "a salesforce kind top of the line sales pipeline and follow
-- up system with reminders for employees to log in their leads, dispose them, assign them to other
-- teammates and outside agencies" + "once you enter a phone number it should give you total access
-- about that customer if they bought the phone from us, gated by permission who views".
--
-- BAND: 800–849 is hereby RESERVED FOR CRM. AGENT_CONTRACT §1 bands stop at 799 (platform-core) and
-- CRM belongs to no existing module agent. Highest number applied anywhere before this: 746.
--
-- SCHEMA CHOICE — core.crm_*, NOT a `crm` schema. A dedicated schema would not work: PostgREST only
-- serves schemas on the project's "Exposed schemas" dashboard list, which is not reachable from here
-- (the Management API returns 403 for /postgrest and there is no pgrst.db_schemas role setting to
-- patch), so every `.schema("crm")` call would 404. This is the identical reasoning migrations 053
-- (helpdesk) and 715 (tech support) recorded when they kept their tables in an already-exposed
-- schema. `core` is exposed, already holds platform-wide tables, and the crm_ prefix keeps it legible.
--
-- SAFE: additive + idempotent (create ... if not exists / on conflict do nothing). Re-runnable.
-- MONEY: touches NO payout number, rate, plan, or paid/earned column. Leads are not money.
-- SECURITY: RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5). The backend
--           service role bypasses RLS; the frontend anon key is auth-only and never reaches these.

BEGIN;

CREATE SCHEMA IF NOT EXISTS core;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. CONFIGURATION (RULE TWO — nothing about a tenant's sales process is hard-coded)
-- ══════════════════════════════════════════════════════════════════════════════════════════════

-- Per-tenant switches. One row per org.
CREATE TABLE IF NOT EXISTS core.crm_config (
  org_id                       UUID PRIMARY KEY,
  default_pipeline_id          UUID,
  timezone                     TEXT NOT NULL DEFAULT 'America/New_York',
  business_hours               JSONB NOT NULL DEFAULT '{"start":"09:00","end":"20:00","days":[1,2,3,4,5,6,0]}'::jsonb,
  stale_lead_hours             INT  NOT NULL DEFAULT 48,    -- no activity for this long = stale
  escalate_after_hours         INT  NOT NULL DEFAULT 24,    -- stale + this long = escalate to the DM
  miss_grace_hours             INT  NOT NULL DEFAULT 4,     -- past due + grace = task flips to `missed`
  require_disposition_on_close INT  NOT NULL DEFAULT 1,     -- 1 = a closing stage demands an outcome
  duplicate_match              TEXT NOT NULL DEFAULT 'phone'
                                 CHECK (duplicate_match IN ('phone','email','both','none')),
  reminder_channels            JSONB NOT NULL DEFAULT '["in_app","email"]'::jsonb,
  auto_convert_on_won          BOOLEAN NOT NULL DEFAULT true,
  max_open_leads_per_rep       INT,                          -- NULL = no cap
  daily_logging_reminder_hour  INT NOT NULL DEFAULT 18,      -- nudge reps who logged nothing today
  intake_key                   TEXT,                         -- Web-to-Lead shared secret (NULL = off)
  lookup_requires_grant        BOOLEAN NOT NULL DEFAULT true, -- Customer 360 default-closed
  updated_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- A sales process. A tenant can run several (Retail, B2B, FWA...).
CREATE TABLE IF NOT EXISTS core.crm_pipeline (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  key         TEXT NOT NULL,
  name        TEXT NOT NULL,
  description TEXT,
  is_default  BOOLEAN NOT NULL DEFAULT false,
  is_active   BOOLEAN NOT NULL DEFAULT true,
  sort_order  INT NOT NULL DEFAULT 100,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (org_id, key),
  UNIQUE (org_id, id)          -- composite target so children can FK tenant-scoped (cf. mig 728)
);
CREATE INDEX IF NOT EXISTS crm_pipeline_org ON core.crm_pipeline(org_id, is_active);

-- Ordered stages inside a pipeline. probability drives the weighted forecast.
CREATE TABLE IF NOT EXISTS core.crm_stage (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               UUID NOT NULL,
  pipeline_id          UUID NOT NULL,
  key                  TEXT NOT NULL,
  name                 TEXT NOT NULL,
  sort_order           INT  NOT NULL DEFAULT 100,
  probability          NUMERIC(5,2) NOT NULL DEFAULT 0,   -- 0..100
  is_won               BOOLEAN NOT NULL DEFAULT false,
  is_lost              BOOLEAN NOT NULL DEFAULT false,
  sla_hours            INT,                                -- max time a lead may sit in this stage
  requires_disposition BOOLEAN NOT NULL DEFAULT false,
  is_active            BOOLEAN NOT NULL DEFAULT true,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (org_id, pipeline_id, key),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, pipeline_id) REFERENCES core.crm_pipeline(org_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS crm_stage_org_pipe ON core.crm_stage(org_id, pipeline_id, sort_order);

-- Where the lead came from.
CREATE TABLE IF NOT EXISTS core.crm_source (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL,
  key        TEXT NOT NULL,
  name       TEXT NOT NULL,
  category   TEXT NOT NULL DEFAULT 'other'
               CHECK (category IN ('walk_in','referral','digital','agency','outbound','other')),
  is_active  BOOLEAN NOT NULL DEFAULT true,
  sort_order INT NOT NULL DEFAULT 100,
  UNIQUE (org_id, key),
  UNIQUE (org_id, id)
);
CREATE INDEX IF NOT EXISTS crm_source_org ON core.crm_source(org_id, is_active);

-- What the lead wants.
CREATE TABLE IF NOT EXISTS core.crm_interest (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL,
  key        TEXT NOT NULL,
  name       TEXT NOT NULL,
  category   TEXT,
  is_active  BOOLEAN NOT NULL DEFAULT true,
  sort_order INT NOT NULL DEFAULT 100,
  UNIQUE (org_id, key),
  UNIQUE (org_id, id)
);
CREATE INDEX IF NOT EXISTS crm_interest_org ON core.crm_interest(org_id, is_active);

-- The outcome of a touch. This is "dispose them" — the owner's word for it.
CREATE TABLE IF NOT EXISTS core.crm_disposition (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                  UUID NOT NULL,
  key                     TEXT NOT NULL,
  name                    TEXT NOT NULL,
  outcome                 TEXT NOT NULL DEFAULT 'connected'
                            CHECK (outcome IN ('connected','no_contact','won','lost','nurture')),
  requires_followup       BOOLEAN NOT NULL DEFAULT false,
  default_followup_hours  INT,
  requires_reason         BOOLEAN NOT NULL DEFAULT false,
  closes_lead             BOOLEAN NOT NULL DEFAULT false,
  sets_stage_id           UUID,      -- optional auto-advance (validated app-side against the pipeline)
  is_active               BOOLEAN NOT NULL DEFAULT true,
  sort_order              INT NOT NULL DEFAULT 100,
  UNIQUE (org_id, key),
  UNIQUE (org_id, id)
);
CREATE INDEX IF NOT EXISTS crm_disposition_org ON core.crm_disposition(org_id, is_active);

-- Why lost / why disqualified — pick-don't-type (RULE THREE), never a free-text reason.
CREATE TABLE IF NOT EXISTS core.crm_reason_code (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  disposition_id UUID,                      -- NULL = applies to every disposition
  key            TEXT NOT NULL,
  name           TEXT NOT NULL,
  is_active      BOOLEAN NOT NULL DEFAULT true,
  sort_order     INT NOT NULL DEFAULT 100,
  UNIQUE (org_id, key),
  UNIQUE (org_id, id)
);
CREATE INDEX IF NOT EXISTS crm_reason_org ON core.crm_reason_code(org_id, is_active);

-- Shared work pools (Salesforce Queues) + round-robin membership.
CREATE TABLE IF NOT EXISTS core.crm_queue (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL,
  key        TEXT NOT NULL,
  name       TEXT NOT NULL,
  rr_cursor  INT NOT NULL DEFAULT 0,        -- round-robin position, persisted so it survives restarts
  is_active  BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (org_id, key),
  UNIQUE (org_id, id)
);
CREATE TABLE IF NOT EXISTS core.crm_queue_member (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  queue_id    UUID NOT NULL,
  employee_id TEXT NOT NULL,                -- storeops.employees.employee_id (business id, text)
  sort_order  INT NOT NULL DEFAULT 100,
  is_active   BOOLEAN NOT NULL DEFAULT true,
  UNIQUE (org_id, queue_id, employee_id),
  FOREIGN KEY (org_id, queue_id) REFERENCES core.crm_queue(org_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS crm_queue_member_org ON core.crm_queue_member(org_id, queue_id);

-- Outside agencies — the "assign to outside agencies" half of the directive.
CREATE TABLE IF NOT EXISTS core.crm_agency (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  name            TEXT NOT NULL,
  type            TEXT NOT NULL DEFAULT 'referral'
                    CHECK (type IN ('referral','outsourced_sales','distributor','marketing','other')),
  contact_name    TEXT,
  email           TEXT,
  phone           TEXT,
  commission_note TEXT,                      -- deliberately a NOTE: agency pay is not wired to payouts
  portal_enabled  BOOLEAN NOT NULL DEFAULT false,   -- phase 2 (agency login)
  is_active       BOOLEAN NOT NULL DEFAULT true,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (org_id, name),
  UNIQUE (org_id, id)
);
CREATE INDEX IF NOT EXISTS crm_agency_org ON core.crm_agency(org_id, is_active);

CREATE TABLE IF NOT EXISTS core.crm_agency_contact (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL,
  agency_id  UUID NOT NULL,
  name       TEXT NOT NULL,
  email      TEXT,
  phone      TEXT,
  is_primary BOOLEAN NOT NULL DEFAULT false,
  FOREIGN KEY (org_id, agency_id) REFERENCES core.crm_agency(org_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS crm_agency_contact_org ON core.crm_agency_contact(org_id, agency_id);

-- First matching rule wins, in `priority` order.
CREATE TABLE IF NOT EXISTS core.crm_assignment_rule (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,
  name               TEXT NOT NULL,
  priority           INT  NOT NULL DEFAULT 100,
  match              JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {source_key,interest_key,market,store_code,min_value,priority}
  strategy           TEXT NOT NULL DEFAULT 'store_owner'
                       CHECK (strategy IN ('specific_user','store_owner','round_robin','queue','agency')),
  target_employee_id TEXT,
  target_queue_id    UUID,
  target_agency_id   UUID,
  is_active          BOOLEAN NOT NULL DEFAULT true,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS crm_rule_org ON core.crm_assignment_rule(org_id, is_active, priority);

-- Follow-up sequences.
CREATE TABLE IF NOT EXISTS core.crm_cadence (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  name        TEXT NOT NULL,
  pipeline_id UUID,                          -- NULL = every pipeline
  stage_id    UUID,                          -- required when trigger = on_stage_enter
  trigger     TEXT NOT NULL DEFAULT 'on_create'
                CHECK (trigger IN ('on_create','on_stage_enter','no_activity')),
  idle_hours  INT,                           -- for trigger = no_activity
  is_active   BOOLEAN NOT NULL DEFAULT true,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (org_id, id)
);
CREATE INDEX IF NOT EXISTS crm_cadence_org ON core.crm_cadence(org_id, is_active);

CREATE TABLE IF NOT EXISTS core.crm_cadence_step (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  cadence_id   UUID NOT NULL,
  step_no      INT  NOT NULL,
  offset_hours INT  NOT NULL DEFAULT 0,      -- from the trigger moment
  channel      TEXT NOT NULL DEFAULT 'task'
                 CHECK (channel IN ('task','email','whatsapp','in_app')),
  task_type    TEXT NOT NULL DEFAULT 'call'
                 CHECK (task_type IN ('call','text','email','visit','other')),
  title        TEXT NOT NULL DEFAULT 'Follow up',
  body         TEXT,
  assign_to    TEXT NOT NULL DEFAULT 'owner'
                 CHECK (assign_to IN ('owner','manager','queue','agency')),
  is_active    BOOLEAN NOT NULL DEFAULT true,
  UNIQUE (org_id, cadence_id, step_no),
  FOREIGN KEY (org_id, cadence_id) REFERENCES core.crm_cadence(org_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS crm_cadence_step_org ON core.crm_cadence_step(org_id, cadence_id, step_no);

-- Rule-based lead scoring (the AI hook can replace the evaluator later; the storage stays).
CREATE TABLE IF NOT EXISTS core.crm_score_rule (
  id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id    UUID NOT NULL,
  name      TEXT NOT NULL,
  field     TEXT NOT NULL,                   -- lead field, e.g. source_key / value_estimate / has_email
  op        TEXT NOT NULL DEFAULT 'eq'
              CHECK (op IN ('eq','ne','gt','gte','lt','lte','in','exists','contains')),
  value     TEXT,
  points    INT NOT NULL DEFAULT 0,
  is_active BOOLEAN NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS crm_score_rule_org ON core.crm_score_rule(org_id, is_active);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 2. OPERATIONAL
-- ══════════════════════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS core.crm_lead (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                UUID NOT NULL,
  lead_no               BIGINT GENERATED BY DEFAULT AS IDENTITY,
  -- identity
  first_name            TEXT,
  last_name             TEXT,
  company_name          TEXT,
  phone                 TEXT,
  -- The 10-digit national number: the ONE key that joins a lead to raw_sales.mdn,
  -- pos.customers.phone_primary, pos.activations.cell_number and asset_ledger.phone_number.
  -- ⚠️ MUST stay identical to app/modules/crm/pipeline_core.normalize_phone(): a lead is STORED
  -- with this key and LOOKED UP with that one. Keeping the FIRST ten digits (not the last) is
  -- deliberate — an extension is written at the end, so "5165550134 x22" must key as 5165550134,
  -- not 6555013422, which would match nothing and read as "we have never seen this customer".
  phone_norm            TEXT GENERATED ALWAYS AS (
                          CASE
                            WHEN LENGTH(REGEXP_REPLACE(COALESCE(phone,''), '[^0-9]', '', 'g')) < 7
                              THEN ''
                            WHEN LENGTH(REGEXP_REPLACE(COALESCE(phone,''), '[^0-9]', '', 'g')) > 10
                                 AND LEFT(REGEXP_REPLACE(COALESCE(phone,''), '[^0-9]', '', 'g'), 1) = '1'
                              THEN SUBSTR(REGEXP_REPLACE(COALESCE(phone,''), '[^0-9]', '', 'g'), 2, 10)
                            WHEN LENGTH(REGEXP_REPLACE(COALESCE(phone,''), '[^0-9]', '', 'g')) > 10
                              THEN LEFT(REGEXP_REPLACE(COALESCE(phone,''), '[^0-9]', '', 'g'), 10)
                            ELSE REGEXP_REPLACE(COALESCE(phone,''), '[^0-9]', '', 'g')
                          END) STORED,
  email                 TEXT,
  email_norm            TEXT GENERATED ALWAYS AS (LOWER(BTRIM(COALESCE(email,'')))) STORED,
  address_1             TEXT,
  city                  TEXT,
  state                 TEXT,
  zip                   TEXT,
  -- routing / scoping
  store_code            TEXT,
  market                TEXT,
  -- classification
  source_id             UUID,
  interest_id           UUID,
  campaign              TEXT,
  notes                 TEXT,
  -- pipeline
  pipeline_id           UUID,
  stage_id              UUID,
  stage_entered_at      TIMESTAMPTZ,
  status                TEXT NOT NULL DEFAULT 'open'
                          CHECK (status IN ('open','won','lost','disqualified')),
  disposition_id        UUID,
  reason_code_id        UUID,
  lost_note             TEXT,
  -- ownership
  owner_employee_id     TEXT,
  queue_id              UUID,
  agency_id             UUID,
  agency_assigned_at    TIMESTAMPTZ,
  agency_accepted_at    TIMESTAMPTZ,
  -- value
  value_estimate        NUMERIC(12,2) NOT NULL DEFAULT 0,
  lines_estimate        INT NOT NULL DEFAULT 0,
  expected_close_date   DATE,
  score                 INT NOT NULL DEFAULT 0,
  priority              TEXT NOT NULL DEFAULT 'warm' CHECK (priority IN ('hot','warm','cold')),
  -- clock
  created_by            TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  first_contacted_at    TIMESTAMPTZ,
  last_activity_at      TIMESTAMPTZ,
  next_action_at        TIMESTAMPTZ,
  closed_at             TIMESTAMPTZ,
  -- conversion / linkage back to the customer master (pos.customers)
  matched_customer_id   UUID,
  converted_customer_id UUID,
  converted_sale_id     UUID,
  converted_at          TIMESTAMPTZ,
  -- consent hygiene
  do_not_call           BOOLEAN NOT NULL DEFAULT false,
  sms_opt_in            BOOLEAN NOT NULL DEFAULT false,
  UNIQUE (org_id, lead_no),
  UNIQUE (org_id, id)
);
CREATE INDEX IF NOT EXISTS crm_lead_org_status  ON core.crm_lead(org_id, status);
CREATE INDEX IF NOT EXISTS crm_lead_org_owner   ON core.crm_lead(org_id, owner_employee_id, status);
CREATE INDEX IF NOT EXISTS crm_lead_org_phone   ON core.crm_lead(org_id, phone_norm);
CREATE INDEX IF NOT EXISTS crm_lead_org_email   ON core.crm_lead(org_id, email_norm);
CREATE INDEX IF NOT EXISTS crm_lead_org_stage   ON core.crm_lead(org_id, stage_id);
CREATE INDEX IF NOT EXISTS crm_lead_org_store   ON core.crm_lead(org_id, store_code);
CREATE INDEX IF NOT EXISTS crm_lead_org_agency  ON core.crm_lead(org_id, agency_id);
CREATE INDEX IF NOT EXISTS crm_lead_org_next    ON core.crm_lead(org_id, next_action_at);
CREATE INDEX IF NOT EXISTS crm_lead_org_lastact ON core.crm_lead(org_id, last_activity_at);

-- Append-only timeline. Per [[recalc-additive-never-erase-review]] nothing here is ever updated or
-- deleted by the app: a human's note about a customer is not something a recompute gets to erase.
CREATE TABLE IF NOT EXISTS core.crm_activity (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL,
  lead_id           UUID NOT NULL,
  kind              TEXT NOT NULL DEFAULT 'note'
                      CHECK (kind IN ('note','call','sms','email','whatsapp','visit','stage_change',
                                      'assignment','disposition','task','conversion','system')),
  body              TEXT,
  meta              JSONB NOT NULL DEFAULT '{}'::jsonb,
  direction         TEXT CHECK (direction IN ('in','out')),
  actor_employee_id TEXT,
  actor_app_user_id UUID,
  actor_agency_id   UUID,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  FOREIGN KEY (org_id, lead_id) REFERENCES core.crm_lead(org_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS crm_activity_org_lead ON core.crm_activity(org_id, lead_id, created_at DESC);
CREATE INDEX IF NOT EXISTS crm_activity_org_time ON core.crm_activity(org_id, created_at DESC);

CREATE TABLE IF NOT EXISTS core.crm_task (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 UUID NOT NULL,
  lead_id                UUID NOT NULL,
  title                  TEXT NOT NULL DEFAULT 'Follow up',
  body                   TEXT,
  type                   TEXT NOT NULL DEFAULT 'call'
                           CHECK (type IN ('call','text','email','visit','other')),
  due_at                 TIMESTAMPTZ NOT NULL,
  remind_at              TIMESTAMPTZ,
  assigned_employee_id   TEXT,
  assigned_agency_id     UUID,
  queue_id               UUID,
  status                 TEXT NOT NULL DEFAULT 'open'
                           CHECK (status IN ('open','done','snoozed','cancelled','missed')),
  priority               TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high')),
  cadence_id             UUID,
  cadence_step_no        INT,
  reminder_sent_at       TIMESTAMPTZ,
  reminder_count         INT NOT NULL DEFAULT 0,
  escalated_at           TIMESTAMPTZ,
  snooze_until           TIMESTAMPTZ,
  completed_at           TIMESTAMPTZ,
  completed_by           TEXT,
  outcome_disposition_id UUID,
  created_by             TEXT,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  FOREIGN KEY (org_id, lead_id) REFERENCES core.crm_lead(org_id, id) ON DELETE CASCADE
);
-- One task per cadence step per lead — this is what makes materialization idempotent, so a sweep
-- that runs twice (or a retry after a timeout) cannot double-book the rep.
CREATE UNIQUE INDEX IF NOT EXISTS crm_task_cadence_uniq
  ON core.crm_task(org_id, lead_id, cadence_id, cadence_step_no)
  WHERE cadence_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS crm_task_org_due    ON core.crm_task(org_id, status, due_at);
CREATE INDEX IF NOT EXISTS crm_task_org_asgn   ON core.crm_task(org_id, assigned_employee_id, status, due_at);
CREATE INDEX IF NOT EXISTS crm_task_org_lead   ON core.crm_task(org_id, lead_id);
CREATE INDEX IF NOT EXISTS crm_task_org_remind ON core.crm_task(org_id, status, remind_at);

CREATE TABLE IF NOT EXISTS core.crm_assignment (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL,
  lead_id          UUID NOT NULL,
  from_employee_id TEXT,
  to_employee_id   TEXT,
  from_queue_id    UUID,
  to_queue_id      UUID,
  from_agency_id   UUID,
  to_agency_id     UUID,
  by_employee_id   TEXT,
  by_app_user_id   UUID,
  reason           TEXT,
  rule_id          UUID,                      -- set when an assignment_rule made the choice
  accepted_at      TIMESTAMPTZ,
  declined_at      TIMESTAMPTZ,
  declined_reason  TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  FOREIGN KEY (org_id, lead_id) REFERENCES core.crm_lead(org_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS crm_assignment_org_lead ON core.crm_assignment(org_id, lead_id, created_at DESC);

-- One row per reminder actually dispatched. The sweep consults this before sending, which is what
-- keeps it idempotent — a task is never reminded twice for the same due window.
CREATE TABLE IF NOT EXISTS core.crm_reminder_log (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  task_id     UUID,
  lead_id     UUID,
  kind        TEXT NOT NULL DEFAULT 'task_due'
                CHECK (kind IN ('task_due','task_missed','lead_stale','escalation','logging_duty')),
  channel     TEXT NOT NULL DEFAULT 'in_app',
  target      TEXT,
  window_key  TEXT,                            -- e.g. '<task_id>:<due_at>' — the dedupe key
  status      TEXT NOT NULL DEFAULT 'sent',
  error       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS crm_reminder_window_uniq
  ON core.crm_reminder_log(org_id, kind, channel, window_key) WHERE window_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS crm_reminder_org_time ON core.crm_reminder_log(org_id, created_at DESC);

-- Customer-360 access trail. Reading a customer's purchase history is a PII/commercial event; it
-- leaves a record, including the DENIED attempts.
CREATE TABLE IF NOT EXISTS core.crm_lookup_audit (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL,
  actor_app_user_id   UUID,
  actor_employee_id   TEXT,
  phone_masked        TEXT,                    -- last 4 only — never the full number
  matched_customer_id UUID,
  matched_lead_id     UUID,
  allowed             BOOLEAN NOT NULL DEFAULT true,
  sections            JSONB NOT NULL DEFAULT '{}'::jsonb,   -- returned vs withheld
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS crm_lookup_audit_org ON core.crm_lookup_audit(org_id, created_at DESC);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 3. DEFAULT CONTENT — carrier-neutral, tenant-safe, idempotent
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- Mirrors commcalc.seed_tenant_defaults(): ON CONFLICT DO NOTHING everywhere, so it NEVER clobbers a
-- pipeline/stage/disposition an admin has edited. Called by the migration for every existing tenant
-- AND lazily by the backend on first CRM access, so a tenant created later self-provisions.

CREATE OR REPLACE FUNCTION core.seed_crm_defaults(p_org UUID)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = core, pg_catalog
AS $fn$
DECLARE
  v_pipeline UUID;
  v_new      UUID;
  v_contact  UUID;
  v_cadence  UUID;
BEGIN
  INSERT INTO core.crm_config (org_id) VALUES (p_org) ON CONFLICT (org_id) DO NOTHING;

  INSERT INTO core.crm_pipeline (org_id, key, name, description, is_default, sort_order)
  VALUES (p_org, 'retail', 'Retail Sales', 'Walk-in and call-in customers', true, 10)
  ON CONFLICT (org_id, key) DO NOTHING;
  SELECT id INTO v_pipeline FROM core.crm_pipeline WHERE org_id = p_org AND key = 'retail';

  INSERT INTO core.crm_stage (org_id, pipeline_id, key, name, sort_order, probability,
                              is_won, is_lost, sla_hours, requires_disposition)
  VALUES
    (p_org, v_pipeline, 'new',        'New',            10,  10, false, false, 4,  false),
    (p_org, v_pipeline, 'contacted',  'Contacted',      20,  25, false, false, 24, false),
    (p_org, v_pipeline, 'qualified',  'Qualified',      30,  50, false, false, 48, false),
    (p_org, v_pipeline, 'appointment','Appointment Set',40,  70, false, false, 48, false),
    (p_org, v_pipeline, 'won',        'Sold',           50, 100, true,  false, NULL, true),
    (p_org, v_pipeline, 'lost',       'Lost',           60,   0, false, true,  NULL, true)
  ON CONFLICT (org_id, pipeline_id, key) DO NOTHING;

  SELECT id INTO v_new FROM core.crm_stage
    WHERE org_id = p_org AND pipeline_id = v_pipeline AND key = 'new';
  SELECT id INTO v_contact FROM core.crm_stage
    WHERE org_id = p_org AND pipeline_id = v_pipeline AND key = 'contacted';

  UPDATE core.crm_config SET default_pipeline_id = v_pipeline
    WHERE org_id = p_org AND default_pipeline_id IS NULL;

  INSERT INTO core.crm_source (org_id, key, name, category, sort_order) VALUES
    (p_org, 'walk_in',    'Walk-in',            'walk_in',  10),
    (p_org, 'phone_in',   'Inbound call',       'walk_in',  20),
    (p_org, 'referral',   'Customer referral',  'referral', 30),
    (p_org, 'employee',   'Employee referral',  'referral', 40),
    (p_org, 'website',    'Website / form',     'digital',  50),
    (p_org, 'social',     'Social media',       'digital',  60),
    (p_org, 'agency',     'Outside agency',     'agency',   70),
    (p_org, 'outbound',   'Outbound call list', 'outbound', 80),
    (p_org, 'event',      'Event / community',  'other',    90)
  ON CONFLICT (org_id, key) DO NOTHING;

  INSERT INTO core.crm_interest (org_id, key, name, category, sort_order) VALUES
    (p_org, 'new_line',   'New line',              'wireless',  10),
    (p_org, 'upgrade',    'Device upgrade',        'wireless',  20),
    (p_org, 'add_line',   'Add a line',            'wireless',  30),
    (p_org, 'port_in',    'Switch carrier (port)', 'wireless',  40),
    (p_org, 'fwa',        'Home internet (FWA)',   'broadband', 50),
    (p_org, 'accessory',  'Accessories',           'accessory', 60),
    (p_org, 'business',   'Business / multi-line', 'b2b',       70),
    (p_org, 'insurance',  'Protection / insurance','service',   80)
  ON CONFLICT (org_id, key) DO NOTHING;

  INSERT INTO core.crm_disposition (org_id, key, name, outcome, requires_followup,
                                    default_followup_hours, requires_reason, closes_lead,
                                    sets_stage_id, sort_order) VALUES
    (p_org, 'spoke_interested', 'Spoke — interested',      'connected',  true,  24,  false, false, v_contact, 10),
    (p_org, 'spoke_callback',   'Spoke — call back later', 'connected',  true,  48,  false, false, v_contact, 20),
    (p_org, 'appointment',      'Appointment set',         'connected',  true,  24,  false, false, NULL,      30),
    (p_org, 'no_answer',        'No answer',               'no_contact', true,  4,   false, false, NULL,      40),
    (p_org, 'voicemail',        'Left voicemail',          'no_contact', true,  24,  false, false, NULL,      50),
    (p_org, 'wrong_number',     'Wrong number',            'lost',       false, NULL, true,  true,  NULL,     60),
    (p_org, 'not_interested',   'Not interested',          'lost',       false, NULL, true,  true,  NULL,     70),
    (p_org, 'nurture',          'Not now — follow up later','nurture',   true,  720, false, false, NULL,      80),
    (p_org, 'sold',             'Sold',                    'won',        false, NULL, false, true,  NULL,     90)
  ON CONFLICT (org_id, key) DO NOTHING;

  INSERT INTO core.crm_reason_code (org_id, key, name, sort_order) VALUES
    (p_org, 'price',        'Price / too expensive',        10),
    (p_org, 'coverage',     'Coverage concern',             20),
    (p_org, 'competitor',   'Went to a competitor',         30),
    (p_org, 'credit',       'Credit / approval',            40),
    (p_org, 'no_stock',     'We did not have the device',   50),
    (p_org, 'timing',       'Bad timing',                   60),
    (p_org, 'unreachable',  'Could not reach after tries',  70),
    (p_org, 'duplicate',    'Duplicate lead',               80),
    (p_org, 'other',        'Other',                        99)
  ON CONFLICT (org_id, key) DO NOTHING;

  -- Default cadence: chase a brand-new lead 3 times in the first 3 days.
  INSERT INTO core.crm_cadence (org_id, name, pipeline_id, trigger)
  SELECT p_org, 'New lead — 3 touches', v_pipeline, 'on_create'
  WHERE NOT EXISTS (SELECT 1 FROM core.crm_cadence
                    WHERE org_id = p_org AND name = 'New lead — 3 touches');
  SELECT id INTO v_cadence FROM core.crm_cadence
    WHERE org_id = p_org AND name = 'New lead — 3 touches' LIMIT 1;

  IF v_cadence IS NOT NULL THEN
    INSERT INTO core.crm_cadence_step (org_id, cadence_id, step_no, offset_hours, channel,
                                       task_type, title, body, assign_to) VALUES
      (p_org, v_cadence, 1, 1,  'task', 'call', 'Call the new lead',
       'First call within the hour — this is the touch that converts.', 'owner'),
      (p_org, v_cadence, 2, 24, 'task', 'text', 'Text the lead',
       'No answer yesterday? Send a short text with your name and the store.', 'owner'),
      (p_org, v_cadence, 3, 72, 'task', 'call', 'Last attempt',
       'Third and final attempt before you dispose the lead.', 'owner')
    ON CONFLICT (org_id, cadence_id, step_no) DO NOTHING;
  END IF;

  -- Scoring starters — a lead with contact details and real intent floats to the top.
  INSERT INTO core.crm_score_rule (org_id, name, field, op, value, points)
  SELECT p_org, r.name, r.field, r.op, r.value, r.points
  FROM (VALUES
    ('Has email',            'email',          'exists', NULL,       10),
    ('Walk-in',              'source_key',     'eq',     'walk_in',  20),
    ('Referral',             'source_key',     'in',     'referral,employee', 25),
    ('Wants to switch',      'interest_key',   'eq',     'port_in',  20),
    ('Business / multi-line','interest_key',   'eq',     'business', 25),
    ('Value over $500',      'value_estimate', 'gt',     '500',      15)
  ) AS r(name, field, op, value, points)
  WHERE NOT EXISTS (SELECT 1 FROM core.crm_score_rule s
                    WHERE s.org_id = p_org AND s.name = r.name);

  -- Default routing: the lead's own store works its own lead.
  INSERT INTO core.crm_assignment_rule (org_id, name, priority, match, strategy)
  SELECT p_org, 'Default — the lead''s store owns it', 900, '{}'::jsonb, 'store_owner'
  WHERE NOT EXISTS (SELECT 1 FROM core.crm_assignment_rule
                    WHERE org_id = p_org AND name = 'Default — the lead''s store owns it');
END;
$fn$;

-- Seed every EXISTING tenant. New tenants self-provision on first CRM access (backend calls this).
DO $$
DECLARE o RECORD;
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'storeops' AND table_name = 'tenants') THEN
    FOR o IN EXECUTE 'SELECT DISTINCT org_id FROM storeops.tenants WHERE org_id IS NOT NULL' LOOP
      PERFORM core.seed_crm_defaults(o.org_id);
    END LOOP;
  ELSE
    PERFORM core.seed_crm_defaults('00000000-0000-0000-0000-000000000001'::uuid);
  END IF;
END $$;

-- Register the module in the canonical registry (mig 700), mirroring MODULE_CATALOG in
-- app/modules/core/entitlements.py. The in-code dict is the fallback, so the app is identical
-- whether or not this ran; this is what makes the module appear in the tenant-entitlement and
-- billing pickers.
INSERT INTO core.module_catalog (key, label, sort_order) VALUES
  ('crm', 'CRM / Sales Pipeline', 120)
ON CONFLICT (key) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 4. SECURITY — RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5)
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- The backend's service role bypasses RLS and needs no policy; the frontend anon key is auth-only
-- and must never reach these tables. Migration 731's ddl_command_end auto-lock covers new objects
-- too, but this is explicit so the migration is correct on its own.
DO $$
DECLARE t RECORD;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables
           WHERE schemaname = 'core' AND tablename LIKE 'crm\_%' LOOP
    EXECUTE format('ALTER TABLE core.%I ENABLE ROW LEVEL SECURITY', t.tablename);
    EXECUTE format('REVOKE ALL ON core.%I FROM anon, authenticated', t.tablename);
    EXECUTE format('GRANT ALL ON core.%I TO service_role', t.tablename);
  END LOOP;
END $$;
REVOKE ALL ON FUNCTION core.seed_crm_defaults(UUID) FROM anon, authenticated, PUBLIC;
GRANT EXECUTE ON FUNCTION core.seed_crm_defaults(UUID) TO service_role;

NOTIFY pgrst, 'reload schema';

COMMIT;
