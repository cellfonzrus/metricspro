-- 927_onboarding_state.sql — per-tenant onboarding wizard STATE (owner 2026-08-26)
--
-- WHY: the platform needs a guided, ADAPTIVE setup wizard (owner: "too many loose ends; if I don't know how
-- to use these menus, nobody else can"). The wizard is a NAVIGATOR + STATE LEDGER, never a config store:
-- every real answer (carrier, store, plan…) writes through the EXISTING config endpoint its settings page
-- already uses; this table stores ONLY wizard meta — which step, its status, and the routing ANSWERS
-- (carrier / company / POS / processor) that TAILOR which later steps and menus appear. Completion of a step
-- is DERIVED from the same readiness probes the pages use, never stored here. This is the single-source rule
-- the codebase already fought for in migs 208 / 923.
--
-- Additive + idempotent; single-line-safe. RLS on + GRANT to service_role (mig 208/916/923 posture).
-- REVERT: drop table if exists commcalc.onboarding_state; notify pgrst, 'reload schema';

create table if not exists commcalc.onboarding_state (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null,
  step_key     text not null,                       -- matches the wizard step catalog keys
  status       text not null default 'not_started', -- not_started | in_progress | skipped | reviewed
  answers      jsonb not null default '{}',         -- wizard-only routing answers / free-text — NEVER a copy
                                                     -- of carrier/store/plan config (that lives in its own table)
  reviewed_by  text,
  reviewed_at  timestamptz,
  updated_at   timestamptz default now()
);

-- One row per (org, step) — same upsert posture as accessory_config / metric_source_of_truth.
create unique index if not exists onboarding_state_uq on commcalc.onboarding_state (org_id, step_key);

alter table commcalc.onboarding_state enable row level security;
grant all on commcalc.onboarding_state to service_role;

notify pgrst, 'reload schema';
select 'Migration 927 complete — commcalc.onboarding_state (adaptive wizard state; meta only)' as status;
