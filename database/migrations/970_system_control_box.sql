-- 970_system_control_box.sql — super-admin CONTROL BOX: check registry, run history, daily-check state
--
-- OWNER DIRECTIVE 2026-09-05 (sanjot@): "a separate agent is needed to work on the super admin side
-- control box to monitor the functions of all aspects of the platform, showing red light or green
-- light of the system and a daily check required to make sure the system is working, the control box
-- will have a link to those module and a way to fix that problem connected with Claude code".
--
-- DUPLICATE CHECK (CLAUDE.md build gate, owner 2026-09-02). Searched docs/SYSTEM_DATA_FLOW_INDEX.md
-- for health / attention / monitor / status before writing a line. The platform ALREADY owns the
-- condition of its subsystems in three places, and this migration adds NO fourth opinion:
--   · core.import_health PROVIDERS (~40 registered across 12 modules) — "what needs a human"
--   · commcalc.portal_session_health           — durable merchant-portal session state (§12a)
--   · core.import_health.feed_health           — feed freshness / overdue / never (mig 717)
-- The control box COMPOSES those. It stores no subsystem verdict of its own; the three tables here
-- hold (1) per-tenant OVERRIDES of how a check is judged, (2) the HISTORY of the daily run, and
-- (3) the daily run's own schedule state. Nothing here re-derives a subsystem's health.
--
-- SCHEMA CHOICE — core.system_*, not a new schema. PostgREST serves only the project's exposed
-- schemas (public / commcalc / storeops / core / notify) and that list is not reachable from here;
-- a `.schema("control")` call would 404. Same reasoning migrations 053, 715 and 800 recorded.
--
-- SAFE: additive + idempotent (create ... if not exists / on conflict do nothing). Re-runnable.
-- MONEY: touches NO payout, rate, plan, commission, or paid/earned column. This is instrumentation.
-- SECURITY: RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5). The backend
--           service role bypasses RLS; the frontend anon key is auth-only and never reaches these.
--           Every read the API makes is org-scoped, and the endpoints are super-admin gated.

BEGIN;

CREATE SCHEMA IF NOT EXISTS core;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. THE CHECK REGISTRY — per-tenant OVERRIDES over the code-derived default registry
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- RULE TWO (config, never code): the board must never become a giant if-chain of subsystem names.
-- The EFFECTIVE registry the runner evaluates is:
--
--     default specs derived in code  ⟵ one per LIVE attention provider (core.import_health.PROVIDERS)
--                                       plus the platform probes (portal sessions, scheduler
--                                       heartbeats, deploy identity)
--     overlaid by  core.system_check rows for the HOUSE org   (platform-wide defaults)
--     overlaid by  core.system_check rows for THIS org        (that tenant's tolerance)
--
-- So a module that registers a new attention provider gets a lamp with NO change here and NO row,
-- and a tenant that wants a different threshold, a different link, or a check switched off writes a
-- ROW — never code. A row may also DECLARE a brand-new check (kind 'unmonitored' or a generic probe
-- kind) that no code knows about yet, which is how a coverage gap gets on the board honestly.
CREATE TABLE IF NOT EXISTS core.system_check (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  key             TEXT NOT NULL,                 -- stable identifier, [a-z0-9_.-]
  subsystem       TEXT,                          -- grouping on the board (ingest, finance, payroll…)
  label           TEXT,
  -- One of the GENERIC evidence shapes in control_box.CHECK_KINDS. A kind describes a shape of
  -- evidence — never a tenant, carrier or module. An unrecognised kind evaluates to `unknown`
  -- (a lamp you can see), never to green.
  kind            TEXT,
  config          JSONB NOT NULL DEFAULT '{}'::jsonb,   -- thresholds / cadence / notes per kind
  deep_link       TEXT,                          -- "a link to those module" (owner, 2026-09-05)
  deep_link_label TEXT,
  index_ref       TEXT,                          -- the SYSTEM_DATA_FLOW_INDEX anchor for the fixer
  code_refs       JSONB NOT NULL DEFAULT '[]'::jsonb,   -- files the fix bundle points a human at
  owner_agent     TEXT,                          -- CLAUDE.md routing (commission-agent, finance-agent…)
  enabled         BOOLEAN NOT NULL DEFAULT true, -- false ⇒ the row reads `unmonitored`, never green
  sort_order      INT NOT NULL DEFAULT 100,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (org_id, key)
);
CREATE INDEX IF NOT EXISTS system_check_org ON core.system_check(org_id, enabled);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 2. RUN HISTORY — the evidence that the daily check actually happened, and what it said
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- WHY A HISTORY AND NOT JUST A CURRENT STATE. Two reasons, both learned here:
--   · The owner asked for "a daily check required to make sure the system is working". A check
--     nobody can prove ran is not a check; this table IS the proof, and the board reads its own
--     freshness back out of it (control_box.selfcheck_row) so a stopped watchman shows up RED
--     rather than leaving stale green lamps on screen (the mig-950 lesson).
--   · Escalation needs a previous run to compare against, so the board pages on what got WORSE
--     instead of re-sending the same red every day — the notify-once discipline
--     portal_session_health.should_notify already established.
CREATE TABLE IF NOT EXISTS core.system_check_run (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  run_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  trigger      TEXT NOT NULL DEFAULT 'cron' CHECK (trigger IN ('cron','manual','boot')),
  lamp         TEXT,                             -- rolled-up headline lamp for the whole board
  headline     TEXT,
  counts       JSONB NOT NULL DEFAULT '{}'::jsonb,   -- per-lamp tally
  coverage     JSONB NOT NULL DEFAULT '{}'::jsonb,   -- registered / monitored / unmonitored + keys
  results      JSONB NOT NULL DEFAULT '[]'::jsonb,   -- the per-check rows, already redacted
  duration_ms  INT,
  notified     BOOLEAN NOT NULL DEFAULT false,
  error        TEXT
);
CREATE INDEX IF NOT EXISTS system_check_run_org_at ON core.system_check_run(org_id, run_at DESC);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 3. DAILY-CHECK STATE — one row per tenant; what the cron tick reads to decide "is this org due?"
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- The pg_cron job (mig 971) ticks hourly and this row decides whether a tenant's DAILY check
-- actually runs — the same "one global job, per-org next_run_at gates the work" shape as the
-- google-reviews sweep (mig 950) and the data-sources sweep (mig 956). A quiet tick is one
-- indexed read.
CREATE TABLE IF NOT EXISTS core.system_check_state (
  org_id              UUID PRIMARY KEY,
  enabled             BOOLEAN NOT NULL DEFAULT true,
  cadence_hours       NUMERIC NOT NULL DEFAULT 24,   -- "daily" is the default, not a hard-coded law
  grace_hours         NUMERIC NOT NULL DEFAULT 6,    -- late ⇒ amber; past grace ⇒ red
  last_run_at         TIMESTAMPTZ,
  next_run_at         TIMESTAMPTZ,
  last_lamp           TEXT,
  last_notified_lamp  TEXT,                          -- notify-once: only a WORSE lamp pages again
  last_notified_at    TIMESTAMPTZ,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Seeds: the HOUSE org's daily check, and the HONEST coverage gaps ──────────────────────────
-- House tenant 00000000-0000-0000-0000-000000000001 (CLAUDE.md).
INSERT INTO core.system_check_state (org_id, enabled, cadence_hours, grace_hours)
VALUES ('00000000-0000-0000-0000-000000000001', true, 24, 6)
ON CONFLICT (org_id) DO NOTHING;

-- These rows exist so the board ADMITS what it does not watch. "A control box that shows green for a
-- subsystem it does not actually check is worse than one that says 'not monitored'" — so each of
-- these renders as an explicit grey `unmonitored` lamp with a note, and each is counted in the
-- board's coverage fraction. Replace a row's `kind` with a real probe kind to start monitoring it;
-- that is a config change, not a deploy.
INSERT INTO core.system_check (org_id, key, subsystem, label, kind, config, deep_link,
                               deep_link_label, index_ref, sort_order)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'db_backup_restore', 'platform',
   'Database backup / restore drill', 'unmonitored',
   '{"note":"Nothing in this repo observes Supabase backup status or has ever tested a restore. Declared so the gap is visible; it cannot be probed from the application."}'::jsonb,
   NULL, NULL, '§20 Super-admin control box', 910),
  ('00000000-0000-0000-0000-000000000001', 'frontend_uptime', 'platform',
   'Frontend availability (Vercel)', 'unmonitored',
   '{"note":"The board runs inside the backend and can only report on itself. Whether the Next.js frontend is serving is not observed here."}'::jsonb,
   NULL, NULL, '§20 Super-admin control box', 920),
  ('00000000-0000-0000-0000-000000000001', 'outbound_delivery', 'notify',
   'Outbound email / WhatsApp actually delivered', 'unmonitored',
   '{"note":"notify records send ATTEMPTS; provider-side delivery/bounce webhooks are not ingested, so a silently bouncing address would not light this board."}'::jsonb,
   '/notify', 'Open notifications', '§20 Super-admin control box', 930)
ON CONFLICT (org_id, key) DO NOTHING;

-- ── Security posture (AGENT_CONTRACT §5): RLS on, no policies, no anon/authenticated grants ────
ALTER TABLE core.system_check       ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.system_check_run   ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.system_check_state ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  BEGIN REVOKE ALL ON core.system_check, core.system_check_run, core.system_check_state
         FROM anon, authenticated; EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN GRANT ALL ON core.system_check, core.system_check_run, core.system_check_state
        TO service_role; EXCEPTION WHEN OTHERS THEN NULL; END;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 970 — super-admin control box: check registry, run history, daily-check state' AS status;

-- REVERT:
--   DROP TABLE IF EXISTS core.system_check_run;
--   DROP TABLE IF EXISTS core.system_check_state;
--   DROP TABLE IF EXISTS core.system_check;
