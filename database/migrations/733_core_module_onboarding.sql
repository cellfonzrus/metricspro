-- 733_core_module_onboarding.sql  ·  platform-core band (700-799)
-- ---------------------------------------------------------------------------------------------
-- CONFIG-DRIVEN MODULE ONBOARDING (owner directive 2026-08-09: "as soon as the user clicks on the
-- POS button they should be tasked with completing the pending onboarding tasks ... more will keep
-- evolving").
--
-- Two tables, deliberately GENERIC (module_key), not pos-specific — the owner's "more will keep
-- evolving" is a statement about the registry growing, and a pos_onboarding_task table would have to
-- be rebuilt the first time another module needs the same treatment.
--
--   core.module_onboarding_task   the REGISTRY: which tasks a module needs, in what order, what each
--                                 one depends on, and a DECLARATIVE completion predicate. Per-tenant
--                                 (org_id) so a tenant can add/retire/re-order its own steps without
--                                 a deploy (RULE TWO — SAP-configurable, never hard-coded steps).
--   core.module_onboarding_state  the HUMAN overlay: skipped / acknowledged / notes. Completion of a
--                                 task with a real predicate is ALWAYS re-derived live from the data,
--                                 never trusted from a stored flag — a stored "done" that disagrees
--                                 with an empty table is exactly how an onboarding wizard lies.
--
-- DEGRADES GRACEFULLY: app/modules/core/onboarding.py ships an in-code DEFAULT_TASKS registry and
-- falls back to it whenever these tables are absent or empty for the tenant (same pattern as
-- entitlements.load_module_catalog / core.module_catalog). If this migration is never run the wizard
-- still works end to end; it just isn't editable per tenant.
--
-- CONTRACT COMPLIANCE: org_id uuid NOT NULL + index on both tables; RLS enabled with ZERO policies
-- and ZERO grants to anon/authenticated (all access is via the backend service role).
-- Additive + idempotent — safe to re-run.
-- ---------------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.module_onboarding_task (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid        NOT NULL,
    module_key    text        NOT NULL,              -- 'pos', and whatever comes next
    task_key      text        NOT NULL,              -- stable key; the state row + code refer to this
    title         text        NOT NULL,              -- shown as the wizard step heading
    why           text,                              -- "why it matters", in rep/DM English
    step_group    text,                              -- coarse grouping for the progress rail
    sort_order    integer     NOT NULL DEFAULT 100,
    depends_on    text[]      NOT NULL DEFAULT '{}', -- task_keys that must be complete first
    predicate     jsonb       NOT NULL DEFAULT '{"type":"manual"}'::jsonb,
    is_required   boolean     NOT NULL DEFAULT true, -- required = blocks the POS entry gate
    skippable     boolean     NOT NULL DEFAULT false,
    template_key  text,                              -- downloadable template, NULL = none
    import_source text,                              -- import-from-existing key, NULL = none
    href          text,                              -- where the "do it now" button goes
    is_active     boolean     NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS module_onboarding_task_uniq
    ON core.module_onboarding_task (org_id, module_key, task_key);
CREATE INDEX IF NOT EXISTS module_onboarding_task_org
    ON core.module_onboarding_task (org_id, module_key, sort_order);

CREATE TABLE IF NOT EXISTS core.module_onboarding_state (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid        NOT NULL,
    module_key    text        NOT NULL,
    task_key      text        NOT NULL,
    status        text        NOT NULL DEFAULT 'pending',  -- pending | skipped | acknowledged
    notes         text,
    actor         text,                                     -- employee_id / email that acted
    acted_at      timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS module_onboarding_state_uniq
    ON core.module_onboarding_state (org_id, module_key, task_key);
CREATE INDEX IF NOT EXISTS module_onboarding_state_org
    ON core.module_onboarding_state (org_id, module_key);

ALTER TABLE core.module_onboarding_task  ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.module_onboarding_state ENABLE ROW LEVEL SECURITY;

-- Service role only. NEVER grant anon/authenticated (AGENT_CONTRACT §5) — the anon key ships in the
-- browser bundle and is public.
GRANT ALL ON core.module_onboarding_task  TO service_role;
GRANT ALL ON core.module_onboarding_state TO service_role;
REVOKE ALL ON core.module_onboarding_task  FROM anon, authenticated;
REVOKE ALL ON core.module_onboarding_state FROM anon, authenticated;

COMMENT ON TABLE core.module_onboarding_task IS
  'Config-driven onboarding step registry per (org, module). Seeded from the in-code DEFAULT_TASKS '
  'registry in app/modules/core/onboarding.py on first read; editable per tenant thereafter.';
COMMENT ON COLUMN core.module_onboarding_task.predicate IS
  'Declarative completion check evaluated LIVE against the tenant''s own data. Shapes: '
  '{"type":"count","schema":..,"table":..,"min":N,"where":{..}} | {"type":"any","of":[..]} | '
  '{"type":"manual"}. schema/table are validated against a whitelist in onboarding.py — a config '
  'row can never be used to probe an arbitrary table.';
COMMENT ON TABLE core.module_onboarding_state IS
  'Human overlay on the registry: skipped / acknowledged / notes. Never the source of truth for a '
  'task that has a real predicate — completion is always re-derived from live data.';
