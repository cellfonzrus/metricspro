-- 718_core_fix_pipeline.sql — AUTO-FIX PIPELINE, PHASE 1 registry + token/$ rate table.
-- Band 700–799 (mod-platform-core). Additive + idempotent (safe to re-run).
--
-- OWNER APPROVED 2026-07-30 (in-chat), design note docs/designs/auto-fix-pipeline.md §2b/§2e.
-- ORIGIN: the 2026-07-30 live incident (ref 3bf51b4d) sat unread in core.failure_log until the owner hit
-- it live. This registry is the state store for the loop
--     failure_log → triage → module agent builds PARKED → Gate 1 → owner says "push" IN CHAT → merge
-- and it exists so that loop is auditable and its token spend is visible.
--
-- HARD RULE OF PHASE 1 (encoded here AND in the app): nothing in this pipeline can deploy anything.
--   * status can NEVER reach 'pushed' without having passed 'approved' — enforced by a CHECK-style
--     trigger below (belt) and by fix_status_change() in app code (braces). Both, on purpose: the DB
--     rule holds even if a future caller bypasses the router.
--   * 'approved' is only ever written by a super-admin request (app gate). The agent service secret
--     (FIX_PIPELINE_SECRET) is scoped to feed-read + registry-write and can never set approved/pushed.
--
-- WHAT THIS ADDS
--   (1) core.fix_requests — ONE row per distinct problem, keyed by SIGNATURE (normalized path template +
--       exception type), so 50 occurrences of one bug = 1 request with occurrence_count = 50. Carries the
--       lifecycle, the parked branch/commit/worktree, the proof summary, per-stage token counts, the
--       computed cost, and an APPEND-ONLY audit array (who/what/when for every status change).
--   (2) core.token_rates — the admin-editable $/MTok rate table (model, in, out, effective_date). Rates
--       are DATA, never code: the app computes cost_usd from THIS table only, and shows no $ at all when
--       no rate row matches (never a hard-coded fallback rate).
--   (3) core.seed_token_rates(p_org) — idempotent seed of the published Anthropic rates, called from the
--       entitlement sync path (mig-076 pattern) so a fresh install self-heals without a manual step.
--
-- RELATIONSHIP TO storeops.support_fix_request (mig 716) — DELIBERATELY SEPARATE, not a duplicate:
--   support_fix_request = the HUMAN support pipeline (an admin CLUBS similar failures from /failures into a
--     ticket; a super-admin approves; a human picks it up). Keyed by kind, no code identity, no cost.
--   core.fix_requests  = the AGENT pipeline (machine triage, signature dedupe, branch/commit/worktree,
--     per-stage token spend, push gate). Different lifecycle, different actors, different key.
--   They can converge later (a support request could spawn a fix_request); Phase 1 keeps them independent
--   so the human path is untouched and cannot be destabilised by pipeline changes.
--
-- MULTI-TENANT (RULE ONE): both tables carry org_id uuid NOT NULL + an index. A fix_request's org_id is the
--   OWNING org — the house/platform org for the platform triage loop, or the tenant that filed it — and
--   `affected_orgs` lists every tenant the signature was observed in with counts (a code bug spans
--   tenants; the same semantics mig 716 documents for support_fix_request). token_rates rows are
--   house-owned platform config; a tenant row overrides house for that tenant (resolution is in-app).
--
-- DEGRADES GRACEFULLY: until this runs, every pipeline endpoint returns an honest empty payload + a
--   "run migration 718" hint (every read/write is try/except-guarded), the board renders an empty state,
--   and NOTHING else in the app changes. /failures, /admin/support/* and the existing support pipeline are
--   untouched by this migration.
--
-- NOT MONEY-TOUCHING: no rate, plan, tier, payout, commission or P&L row is read or written. `cost_usd`
--   here is INTERNAL AI spend reporting for the owner, not a payable and not part of any P&L feed.
--
-- RLS POSTURE (AGENT_CONTRACT §5, post-2026-07-28 lockdown): RLS ENABLED, ZERO policies, ZERO anon /
--   authenticated grants. All access is through the backend service role.

-- ══ (1) core.fix_requests — the registry ══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS core.fix_requests (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL,                 -- OWNING org (platform/house for the triage loop)
  signature        TEXT NOT NULL,                 -- normalized path template + '|' + exc_type (dedupe key)
  first_ref        TEXT,                          -- the failure_log ref that first surfaced it
  occurrence_count INTEGER NOT NULL DEFAULT 1,
  sample_path      TEXT,                          -- a real example path (un-normalized) for the human
  exc_type         TEXT,
  failure_ids      JSONB NOT NULL DEFAULT '[]'::jsonb,  -- core.failure_log ids folded into this request
  affected_orgs    JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{"org_id":…,"count":N}] — tenants it spans
  title            TEXT,
  status           TEXT NOT NULL DEFAULT 'reported',
                   -- reported → triaged → building → gate1_parked → approved → pushed
                   -- | rejected | not_code   (see core.fix_requests_guard below)
  classification   TEXT,
                   -- code_bug | config | data | transient | duplicate | money_touching
  module_agent     TEXT,                          -- which module agent owns the fix (mod-commission …)
  branch           TEXT,                          -- the PARKED branch (never pushed in Phase 1)
  commit_sha       TEXT,                          -- the parked commit
  worktree         TEXT,                          -- where it was built
  triage_summary   TEXT,
  proofs_summary   TEXT,
  model            TEXT,                          -- the model the agent ran on (→ core.token_rates.model)
  tokens_triage    BIGINT NOT NULL DEFAULT 0,
  tokens_build     BIGINT NOT NULL DEFAULT 0,
  tokens_review    BIGINT NOT NULL DEFAULT 0,
  cost_usd         NUMERIC,                       -- computed IN-APP from core.token_rates; NULL = no rate
  cost_basis       JSONB,                         -- {model, blended_usd_per_mtok, output_share, …} — shows
                                                  -- its work so the number on the board is auditable
  approved_by      TEXT,                          -- the super-admin who recorded the owner's chat approval
  approved_at      TIMESTAMPTZ,
  pushed_commit    TEXT,
  pushed_at        TIMESTAMPTZ,
  audit            JSONB NOT NULL DEFAULT '[]'::jsonb,
                                                  -- APPEND-ONLY [{at, actor, actor_kind, from, to, note}]
  created_by       TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, signature)                      -- THE dedupe guarantee: one bug = one row
);
CREATE INDEX IF NOT EXISTS fix_requests_org_idx        ON core.fix_requests(org_id);
CREATE INDEX IF NOT EXISTS fix_requests_org_status     ON core.fix_requests(org_id, status);
CREATE INDEX IF NOT EXISTS fix_requests_org_created    ON core.fix_requests(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS fix_requests_org_class      ON core.fix_requests(org_id, classification);

COMMENT ON TABLE core.fix_requests IS
  'Auto-Fix Pipeline registry (Phase 1, design docs/designs/auto-fix-pipeline.md). One row per distinct problem keyed by signature (normalized path + exception type) with occurrence_count, the parked branch/commit, per-stage token counts, computed cost_usd and an append-only audit trail. NOTHING here deploys anything: status can never reach ''pushed'' without passing ''approved'', and approval is a super-admin action recording the owner''s in-chat Gate 2.';
COMMENT ON COLUMN core.fix_requests.signature IS
  'Dedupe key: normalized request-path template + ''|'' + exception type. NOT the per-occurrence failure ref — 50 occurrences of one bug fold into ONE row (occurrence_count).';
COMMENT ON COLUMN core.fix_requests.cost_usd IS
  'Computed in-app from core.token_rates against (tokens_triage+tokens_build+tokens_review) using a BLENDED input/output rate — agent completion metadata is a per-agent TOTAL, not an in/out split. NULL when no rate row matches the model. See cost_basis for the exact rate used.';

-- ── Push gate (belt; the app is the braces) ───────────────────────────────────────────────────────
-- A row may only ENTER 'pushed' from 'approved' (and must carry approved_by/approved_at). Written as a
-- trigger rather than a CHECK because the rule is about the TRANSITION, not the row. Idempotent: the
-- function is CREATE OR REPLACE and the trigger is dropped-then-created.
CREATE OR REPLACE FUNCTION core.fix_requests_guard()
RETURNS TRIGGER LANGUAGE plpgsql AS $fn$
BEGIN
  IF NEW.status = 'pushed' THEN
    IF TG_OP = 'INSERT' THEN
      RAISE EXCEPTION 'fix_requests: a row cannot be CREATED already pushed (must pass approved first)';
    END IF;
    IF COALESCE(OLD.status, '') NOT IN ('approved', 'pushed') THEN
      RAISE EXCEPTION 'fix_requests: status pushed requires the previous status to be approved (was %)',
        COALESCE(OLD.status, 'null');
    END IF;
    IF NEW.approved_by IS NULL OR NEW.approved_at IS NULL THEN
      RAISE EXCEPTION 'fix_requests: status pushed requires approved_by + approved_at (the Gate-2 audit trail)';
    END IF;
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS fix_requests_guard_trg ON core.fix_requests;
CREATE TRIGGER fix_requests_guard_trg
  BEFORE INSERT OR UPDATE ON core.fix_requests
  FOR EACH ROW EXECUTE FUNCTION core.fix_requests_guard();

ALTER TABLE core.fix_requests ENABLE ROW LEVEL SECURITY;
-- RLS posture: backend service role only (AGENT_CONTRACT §5). No policies, no anon/authenticated grants.
GRANT ALL ON core.fix_requests TO service_role;

-- ══ (2) core.token_rates — admin-editable $/MTok rates (RULE TWO: rates are DATA) ═════════════════
CREATE TABLE IF NOT EXISTS core.token_rates (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL,                 -- house org = platform default; a tenant row overrides
  model            TEXT NOT NULL,                 -- e.g. 'claude-opus-5' (matches fix_requests.model)
  label            TEXT,                          -- human name shown on the board
  usd_per_mtok_in  NUMERIC NOT NULL,
  usd_per_mtok_out NUMERIC NOT NULL,
  effective_date   DATE NOT NULL DEFAULT CURRENT_DATE,   -- rate history: the newest row <= today wins
  output_share     NUMERIC NOT NULL DEFAULT 0.20,
                   -- The BLEND assumption. Agent completion metadata reports ONE total token count, not
                   -- an in/out split, so cost = total × (in·(1-share) + out·share). Owner-tunable per
                   -- model; stated on the board, never hidden. Set 0 to price everything as input.
  is_active        BOOLEAN NOT NULL DEFAULT true,
  notes            TEXT,
  updated_by       TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, model, effective_date)
);
CREATE INDEX IF NOT EXISTS token_rates_org_idx   ON core.token_rates(org_id);
CREATE INDEX IF NOT EXISTS token_rates_org_model ON core.token_rates(org_id, model, effective_date DESC);

COMMENT ON TABLE core.token_rates IS
  'Admin-editable AI token rates ($ per million tokens, per model, with effective_date history) used to compute core.fix_requests.cost_usd. Seeded from the published Anthropic pricing page at ship time and OWNER-CONFIRMED there; the app NEVER falls back to a hard-coded rate (no matching row = no $ shown).';
COMMENT ON COLUMN core.token_rates.output_share IS
  'Assumed output-token share of the total, used to blend in/out into one $/MTok because agent metadata gives a single total. Configurable per model; surfaced on the board as an explicit caveat.';

ALTER TABLE core.token_rates ENABLE ROW LEVEL SECURITY;
GRANT ALL ON core.token_rates TO service_role;

-- ══ (3) core.seed_token_rates(p_org) — idempotent rate seed (mig-076 seed-path pattern) ═══════════
-- Values below are the PUBLISHED Anthropic per-model API prices read from the official pricing page on
-- 2026-07-30 (platform.claude.com/docs/en/about-claude/pricing → "Model pricing").
--   ⚠️ OWNER MUST CONFIRM AT SHIP TIME. They are a SEED, not a source of truth: once this has run, the
--   table is the only place rates live, and the owner edits them at /admin/fix-requests. Published rates
--   change (Sonnet 5's introductory price is a real example — see the two dated rows) and any negotiated
--   or enterprise discount belongs in this table too.
-- ON CONFLICT DO NOTHING ⇒ re-running never clobbers an owner-edited rate.
CREATE OR REPLACE FUNCTION core.seed_token_rates(p_org uuid)
RETURNS integer LANGUAGE plpgsql AS $fn$
DECLARE
  n_before integer;
  n_after  integer;
BEGIN
  SELECT count(*) INTO n_before FROM core.token_rates WHERE org_id = p_org;

  INSERT INTO core.token_rates
    (org_id, model, label, usd_per_mtok_in, usd_per_mtok_out, effective_date, output_share, notes)
  VALUES
    (p_org, 'claude-opus-5',    'Claude Opus 5',    5,  25, DATE '2026-01-01', 0.20,
     'Published Anthropic API rate read 2026-07-30. Owner-confirm at ship.'),
    (p_org, 'claude-opus-4-8',  'Claude Opus 4.8',  5,  25, DATE '2026-01-01', 0.20,
     'Published Anthropic API rate read 2026-07-30. Owner-confirm at ship.'),
    (p_org, 'claude-sonnet-5',  'Claude Sonnet 5 (introductory)', 2, 10, DATE '2026-01-01', 0.20,
     'INTRODUCTORY pricing published through 2026-08-31. Read 2026-07-30. Owner-confirm at ship.'),
    (p_org, 'claude-sonnet-5',  'Claude Sonnet 5',  3,  15, DATE '2026-09-01', 0.20,
     'Standard pricing published as effective 2026-09-01 (introductory period ends 2026-08-31). Read 2026-07-30.'),
    (p_org, 'claude-sonnet-4-6','Claude Sonnet 4.6', 3, 15, DATE '2026-01-01', 0.20,
     'Published Anthropic API rate read 2026-07-30. Owner-confirm at ship.'),
    (p_org, 'claude-haiku-4-5', 'Claude Haiku 4.5', 1,   5, DATE '2026-01-01', 0.20,
     'Published Anthropic API rate read 2026-07-30. Owner-confirm at ship.'),
    (p_org, 'claude-fable-5',   'Claude Fable 5',  10,  50, DATE '2026-01-01', 0.20,
     'Published Anthropic API rate read 2026-07-30. Owner-confirm at ship.')
  ON CONFLICT (org_id, model, effective_date) DO NOTHING;

  SELECT count(*) INTO n_after FROM core.token_rates WHERE org_id = p_org;
  RETURN n_after - n_before;
END;
$fn$;

REVOKE ALL ON FUNCTION core.seed_token_rates(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.seed_token_rates(uuid) TO service_role;

-- Seed the HOUSE (platform) rates now so the board has $ the moment the code deploys.
SELECT core.seed_token_rates('00000000-0000-0000-0000-000000000001'::uuid);

NOTIFY pgrst, 'reload schema';
SELECT '718 complete — core.fix_requests (+push-gate trigger) + core.token_rates (7 seeded house rates) + core.seed_token_rates()' AS status;
