-- 1007_onboarding_intake_state.sql — resumable state for the NEW tenant-onboarding flow
-- (design §4: `onboarding_run` + `onboarding_stage_state`; owner 2026-09-20).
--
-- OWNER: "I will not do anything manually … Whatever is being uploaded should be able to save is
-- most important — previously mostly all imports did not save the first time."
--
-- WHAT THESE TWO TABLES ARE. A RUN is one walk of the onboarding flow for one org ('initial' once,
-- 'monthly' thereafter). A STAGE STATE is one instance of one stage inside that run — for the
-- commission stage, one row per (carrier × statement type) — carrying which step the person is on,
-- its lamp (not_started | in_progress | needs_input | verified), the per-step payload they have
-- confirmed so far (the column map, the 3.4 answer, the label→bucket assignments, the period), the
-- numbers that were verified, and who verified them. The left rail on
-- frontend/src/app/(platform)/onboarding/intake/page.tsx is a PROJECTION of these rows, so leaving
-- and returning lands on the same step (design §0.2, §5.10).
--
-- DUPLICATE CHECK (build gate, CLAUDE.md). Searched docs/SYSTEM_DATA_FLOW_INDEX.md §16 (by TABLE),
-- §23n / §26 (the existing wizards) and mig 927. `commcalc.onboarding_state` (927) is the adaptive
-- setup NAVIGATOR: one row per (org, step_key), meta only, no instance dimension, no run, no verified
-- numbers, and by its own charter "never a config store". The intake needs state PER INSTANCE
-- (carrier × statement type, later POS source × company) inside a RUN, with verified_numbers that
-- Stage 4 re-derives against landed rows. Widening 927's unique key would change the meaning of every
-- existing row, so this is a sibling by design, registered in §30 of the index beside it.
--
-- WHAT IS DELIBERATELY NOT HERE. No `source_mapping` table: the confirmed column map is written to
-- commcalc.column_mapping through POST /commcalc/column-mapping (the ONE writer, §25.11), including
-- the amount column's sign convention (mig 1006/1008). No `label_bucket` table: the label→bucket
-- rules are commcalc.commission_category_map rows under the tenant's own source_report key. The
-- reversal flag and the zero/difference attestation live in `payload` / `verified_numbers` here,
-- because they are facts about THIS verification, not classification rules. One mapping row per
-- source; the verify step recomputes from landed rows (design §0.5).
--
-- MONEY NOTE. This migration moves no money: it creates two empty state tables that nothing in
-- payout, P&L or the ledger reads. Written and NOT applied — the endpoints degrade honestly while it
-- is pending (the payload says `state_ready: false` and names this file; nothing 500s).
--
-- Additive + idempotent. RLS on + GRANT to service_role (mig 927 posture).
-- REVERT: DROP TABLE IF EXISTS commcalc.onboarding_stage_state;
--         DROP TABLE IF EXISTS commcalc.onboarding_run;
--         NOTIFY pgrst, 'reload schema';

CREATE TABLE IF NOT EXISTS commcalc.onboarding_run (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                UUID NOT NULL,
  run_kind              TEXT NOT NULL DEFAULT 'initial',      -- initial | monthly
  period                TEXT,                                 -- monthly runs: the month being taken in
  status                TEXT NOT NULL DEFAULT 'in_progress',  -- in_progress | signed_off | abandoned
  current_step          TEXT,                                 -- e.g. '3.4' — where the person last was
  started_by            TEXT,
  started_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  signed_off_by         TEXT,
  signed_off_on_behalf  BOOLEAN NOT NULL DEFAULT false,
  signed_off_at         TIMESTAMPTZ,
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS onboarding_run_org_status ON commcalc.onboarding_run (org_id, status);

CREATE TABLE IF NOT EXISTS commcalc.onboarding_stage_state (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id            UUID NOT NULL REFERENCES commcalc.onboarding_run(id) ON DELETE CASCADE,
  org_id            UUID NOT NULL,                              -- denormalised so every read is org-scoped
  stage             TEXT NOT NULL,                              -- '1'..'5'
  step              TEXT,                                       -- '3.1'..'3.9' — the step the person is on
  instance_key      TEXT NOT NULL DEFAULT '',                   -- 'commission:<carrier_id>:<statement slug>'
  status            TEXT NOT NULL DEFAULT 'not_started',        -- not_started | in_progress | needs_input | verified
  payload           JSONB NOT NULL DEFAULT '{}',                -- per-step confirmed inputs (map, sign answer, buckets…)
  verified_numbers  JSONB,                                      -- our totals beside the file's, as re-read after commit
  verified_by       TEXT,
  verified_at       TIMESTAMPTZ,
  blocking_reason   TEXT,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- One row per (run, stage, instance): the rail is a projection of these rows.
CREATE UNIQUE INDEX IF NOT EXISTS onboarding_stage_state_uq
  ON commcalc.onboarding_stage_state (run_id, stage, instance_key);
CREATE INDEX IF NOT EXISTS onboarding_stage_state_org ON commcalc.onboarding_stage_state (org_id, stage);

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'onboarding_stage_state_status_ck') THEN
    ALTER TABLE commcalc.onboarding_stage_state
      ADD CONSTRAINT onboarding_stage_state_status_ck
      CHECK (status IN ('not_started', 'in_progress', 'needs_input', 'verified'));
  END IF;
END $$;

ALTER TABLE commcalc.onboarding_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.onboarding_stage_state ENABLE ROW LEVEL SECURITY;
GRANT ALL ON commcalc.onboarding_run TO service_role;
GRANT ALL ON commcalc.onboarding_stage_state TO service_role;

COMMENT ON TABLE commcalc.onboarding_run IS
  'One walk of the tenant onboarding flow per org (initial, then monthly). State only: no config, no money.';
COMMENT ON TABLE commcalc.onboarding_stage_state IS
  'One instance of one onboarding stage inside a run (commission stage: one per carrier x statement type). payload = confirmed per-step inputs; verified_numbers = our totals beside the file''s as RE-READ from the landed rows after commit. The onboarding rail is a projection of these rows.';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1007 complete — commcalc.onboarding_run + onboarding_stage_state (resumable intake state; books nothing)' AS status;
