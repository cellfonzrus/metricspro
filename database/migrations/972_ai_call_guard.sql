-- 972_ai_call_guard.sql — the SHARED guard for every outbound AI call: budget config + call audit
--
-- OWNER DIRECTIVE 2026-09-05 (sanjot@), on the control box's fix path: the AI must be
-- "protected from third party misuse of the ai api and only restricted to this module".
--
-- WHY THIS IS SHARED AND NOT CONTROL-BOX-SPECIFIC. The platform already makes outbound Anthropic
-- calls from at least five places — account/engine (P&L narrative), account/recon (missed-days note),
-- commcalc/agency (OCR), helpdesk /ai-assist, and remediation /propose (AI diagnosis) — and a sixth
-- and seventh are being added this week (this control box, and the sibling agent's insurance/lease
-- extraction). Every one of them independently re-solves "who may spend the key, how often, and who
-- finds out". That is the duplicate-derivation defect the CLAUDE.md build gate exists to stop, and
-- it is worse here than usual: five partial answers to "is this call authorized" is how an API key
-- gets burned. So the guard is ONE table pair with a `purpose` discriminator, and the DECISION is one
-- pure function (core/control_box.ai_guard_decision) that any caller can adopt.
--
-- WHAT THE GUARD ENFORCES (proven in backend/harness_control_box.py §D, not merely asserted here):
--   1. Fail-closed AUTHORIZATION first — a non-super-admin is refused before any other state is
--      consulted, so an unauthorized probe cannot learn the budget, the usage, or even whether a key
--      is configured (mig-434 fail-closed 403 posture).
--   2. PURPOSE restriction — a call must declare the purpose the key is scoped to; the control box's
--      key usage will not serve as a general-purpose AI endpoint.
--   3. NO PROMPT PASSTHROUGH — the only caller-supplied value is an identifier that must already
--      exist in a server-side registry. The prompt is assembled from server-side diagnostics.
--   4. RATE LIMIT then BUDGET — per-hour calls bound a runaway loop; per-day calls and tokens bound
--      the spend. Rate is checked BEFORE budget so a burst is throttled rather than spent.
--   5. EVERY ATTEMPT AUDITED, allowed or refused, org-scoped. Refusals are logged on purpose: a wall
--      of `not_super_admin` denials IS the signal that someone is probing the endpoint.
--
-- The key itself NEVER leaves the server: settings.ANTHROPIC_API_KEY is read in the backend process
-- only, and never reaches the browser, a response body, a log line or a client-visible error. Free
-- text is redacted (control_box.redact) before it is stored in `error`.
--
-- WHAT ALREADY EXISTED AND IS REUSED, NOT REBUILT (duplicate check, CLAUDE.md build gate):
--   · `core.token_rates` (mig 718) is the admin-editable $/MTok rate table, and mig 718's rule is
--     that cost is computed from THAT table only, with NO hard-coded fallback rate and no $ shown
--     when no rate row matches. So `ai_call_audit` deliberately stores TOKENS ONLY and has no
--     cost column: any dollar figure over these rows joins `core.token_rates` on model + effective
--     date, exactly as the fix pipeline does. A second rate source would drift from the first.
--   · `core.fix_requests` (mig 718) is a DIFFERENT thing and is left alone: it is the AGENT pipeline's
--     per-PROBLEM registry (signature dedupe, parked branch, push gate, per-stage spend). This is a
--     per-CALL meter and authorization trail. Different key, different lifecycle, different actor —
--     the same distinction mig 718 itself drew against storeops.support_fix_request. They can
--     converge later (a red control-box row could FILE a fix_request); nothing here presumes it.
--
-- SAFE: additive + idempotent. Re-runnable.
-- MONEY: touches NO payout, rate, plan or commission column. It bounds API SPEND, not payroll.
-- SECURITY: RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5).

BEGIN;

CREATE SCHEMA IF NOT EXISTS core;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. BUDGET CONFIG — per (org, purpose). RULE TWO: a tenant's AI ceiling is config, not a constant.
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- Resolution order in code: this org's row > the HOUSE org's row > DEFAULT_AI_CONFIG in
-- core/control_box.py. A tenant with no row inherits the house ceiling; setting `enabled=false`
-- turns AI off for that tenant while leaving the red/green board fully working — the board is
-- deterministic and never asks the model whether a light is red.
CREATE TABLE IF NOT EXISTS core.ai_budget_config (
  org_id             UUID NOT NULL,
  purpose            TEXT NOT NULL,                  -- e.g. 'control_box_triage'
  enabled            BOOLEAN NOT NULL DEFAULT true,
  max_calls_per_hour INT NOT NULL DEFAULT 10,        -- bounds a runaway loop
  daily_call_cap     INT NOT NULL DEFAULT 40,        -- bounds the spend
  daily_token_cap    INT NOT NULL DEFAULT 400000,
  max_input_chars    INT NOT NULL DEFAULT 12000,     -- the assembled bundle is truncated to this
  notes              TEXT,
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (org_id, purpose)
);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 2. CALL AUDIT — who, when, which subject, allowed or refused, and what it cost
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- This table is BOTH the audit trail and the meter: core/control_box.rollup_usage counts this org's
-- rows over the last hour / 24h to decide whether the next call is inside its rate limit and budget.
-- Only ALLOWED rows count against the caps — a refused call costs no tokens, and counting refusals
-- would let a spray of unauthorized attempts lock the owner out of their own triage.
CREATE TABLE IF NOT EXISTS core.ai_call_audit (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  purpose       TEXT NOT NULL,
  subject_key   TEXT,                     -- the registry identifier the call was about (never free text)
  actor_uid     TEXT,
  actor_email   TEXT,
  allowed       BOOLEAN NOT NULL DEFAULT false,
  deny_code     TEXT,                     -- not_super_admin / wrong_purpose / unknown_check /
                                          -- disabled / no_key / rate_limited / budget_exhausted
  model         TEXT,
  input_tokens  INT NOT NULL DEFAULT 0,
  output_tokens INT NOT NULL DEFAULT 0,
  error         TEXT,                     -- redacted before storage (control_box.redact)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- The index the meter reads on EVERY guarded call: this org + purpose, newest first.
CREATE INDEX IF NOT EXISTS ai_call_audit_org_purpose_at
  ON core.ai_call_audit(org_id, purpose, created_at DESC);
-- The index for "is somebody probing us": refusals across a window.
CREATE INDEX IF NOT EXISTS ai_call_audit_denials
  ON core.ai_call_audit(org_id, created_at DESC) WHERE allowed = false;

-- House-tenant ceiling for the control box's triage calls (CLAUDE.md house org).
INSERT INTO core.ai_budget_config (org_id, purpose, enabled, max_calls_per_hour, daily_call_cap,
                                   daily_token_cap, max_input_chars, notes)
VALUES ('00000000-0000-0000-0000-000000000001', 'control_box_triage', true, 10, 40, 400000, 12000,
        'Super-admin control-box triage commentary. The board is deterministic without it; this AI '
        'only explains a lamp that is ALREADY red.')
ON CONFLICT (org_id, purpose) DO NOTHING;

-- ── Security posture (AGENT_CONTRACT §5): RLS on, no policies, no anon/authenticated grants ────
ALTER TABLE core.ai_budget_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.ai_call_audit    ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  BEGIN REVOKE ALL ON core.ai_budget_config, core.ai_call_audit FROM anon, authenticated;
    EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN GRANT ALL ON core.ai_budget_config, core.ai_call_audit TO service_role;
    EXCEPTION WHEN OTHERS THEN NULL; END;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 972 — shared AI-call guard: per-(org,purpose) budget config + full call audit' AS status;

-- Who has been refused lately (the probe signal):
--   select created_at, actor_email, deny_code from core.ai_call_audit
--    where allowed = false order by created_at desc limit 50;
-- What today cost:
--   select purpose, count(*), sum(input_tokens + output_tokens) from core.ai_call_audit
--    where allowed and created_at > now() - interval '24 hours' group by 1;
--
-- REVERT:
--   DROP TABLE IF EXISTS core.ai_call_audit;
--   DROP TABLE IF EXISTS core.ai_budget_config;
