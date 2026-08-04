-- 720_core_training_tours.sql — TRAINING CENTER: guided walk-through tours as DATA.
-- Band 700–799 (mod-platform-core). Additive + idempotent (safe to re-run).
--
-- OWNER DIRECTIVE 2026-08-04 (in chat, verbatim): "need to create simulation training videos for all
-- modules to walk the users through".
--
-- WHAT THIS IS (Phase 1). An in-app guided TOUR: the app spotlights a real control on a real page,
-- explains it in one short card, and steps the user forward — a click-through simulation of the flow,
-- on their own tenant's data, instead of a video they cannot interact with. Phase 2 (scaffold only, no
-- recording infrastructure in this package) reuses the SAME rows as a recording SCRIPT: every step
-- already carries `narration` (what a voice-over says) and `action_hint` (what the recorder does), so a
-- Playwright storyboard can be generated per tour without re-authoring any content.
--
-- RULE TWO (SAP-configurable): tours are DATA, never code. A tenant can edit the shipped wording, add
-- its own tours for its own process, or unpublish one — all from /admin/training, no deploy.
--
-- MULTI-TENANT (RULE ONE): org_id uuid NOT NULL + an index on BOTH tables (contract §2 — the contract
-- says NOT NULL "no exceptions", so the platform-default rows are NOT stored as NULL). The PLATFORM
-- DEFAULT rows are owned by the HOUSE org and every tenant reads them — exactly the pattern already in
-- production for core.support_doc (mig 715), core.failure_kind_doc (mig 716) and core.token_rates
-- (mig 718). Resolution, implemented in app/modules/core/training.py:
--     read  = rows WHERE org_id IN (HOUSE, <tenant>)  → a TENANT row with the same slug WINS
--     write = a tenant admin may only write org_id = its own tenant; only a super-admin may write the
--             HOUSE (platform-default) rows.
-- So a tenant customises a shipped tour by saving its own row under the same slug; the platform row is
-- never mutated and keeps flowing to every other tenant.
--
-- SEEDING IS DONE IN CODE, NOT HERE — deliberately. The platform default tours ship as
-- app/data/training_tours_seed.json and are loaded into the HOUSE org by
-- app/modules/core/training_seed.py on the house org's sync_tenant() pass (SEED_VERSION 8), with
-- NEVER-CLOBBER semantics (a row a human has edited is skipped). This is the mig-715/support_seed
-- precedent and it means the shipped wording can be corrected in a normal deploy instead of a new
-- migration — and re-running this file can never overwrite an edited tour.
--
-- DEGRADES GRACEFULLY: until this runs, every read/write is try/except-guarded → GET returns an empty
-- tour list, the Training Center shows an honest "not set up yet" empty state, the "Walk me through"
-- affordance in the help panel simply does not render, and NOTHING else in the app changes. No page
-- anywhere depends on these tables.
--
-- RLS POSTURE (AGENT_CONTRACT §5): RLS ENABLED, ZERO policies, ZERO anon/authenticated grants. All
-- access is through the backend service role.
--
-- NOT MONEY-TOUCHING: no rate, plan, tier, payout, commission or P&L row is read or written. The tour
-- engine is read-only over the pages it walks — it highlights and explains, it never clicks for the user.

-- ── (1) core.training_tour — one row per guided walk-through ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS core.training_tour (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,                  -- HOUSE = platform default (all tenants); tenant = override/own
  slug          TEXT NOT NULL,                  -- stable id used by ?tour=<slug> and by the Phase-2 script export
  title         TEXT NOT NULL,                  -- "Close out your day"
  module        TEXT,                           -- nav module key: closing | commissions | storeops | asset | account …
  description   TEXT,                           -- one plain-English line shown in the Training Center
  audience      TEXT NOT NULL DEFAULT 'all',    -- all | rep | manager | admin  (grouping/filter only, NOT a gate)
  start_href    TEXT,                           -- the page the tour opens on
  est_minutes   NUMERIC,                        -- rough "how long will this take me"
  sort_order    INTEGER NOT NULL DEFAULT 100,
  is_published  BOOLEAN NOT NULL DEFAULT true,
  is_seed       BOOLEAN NOT NULL DEFAULT false, -- true = shipped default (re-seedable); false = human-authored
  updated_by    TEXT,                           -- NULL/'seed' = never hand-edited → the seeder may refresh it
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, slug)
);
CREATE INDEX IF NOT EXISTS training_tour_org_idx      ON core.training_tour(org_id);
CREATE INDEX IF NOT EXISTS training_tour_org_module   ON core.training_tour(org_id, module);
CREATE INDEX IF NOT EXISTS training_tour_org_pub_sort ON core.training_tour(org_id, is_published, sort_order);

ALTER TABLE core.training_tour ENABLE ROW LEVEL SECURITY;
GRANT ALL ON core.training_tour TO service_role;

-- ── (2) core.training_tour_step — the ordered steps of one tour ────────────────────────────────────
-- `target` is the RESILIENT anchor syntax the frontend engine understands (src/lib/tours.ts):
--     tour:<id>   → [data-tour-id="<id>"]   (the stable anchor; only pages platform-core owns carry these
--                                            today — other modules add theirs in their next wave)
--     text:<str>  → the tightest VISIBLE element whose text contains <str> (case-insensitive)
--     css:<sel>   → a raw CSS selector
--     NULL/''     → no anchor: the step renders as a centered card (intro/outro, or a deliberately
--                   anchor-free step on a page whose markup is expected to move)
-- Any target that fails to resolve DEGRADES to the centered card — a moved button can never break a tour.
CREATE TABLE IF NOT EXISTS core.training_tour_step (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,                 -- same org as its tour (denormalised for direct org-scoped reads)
  tour_id        UUID NOT NULL REFERENCES core.training_tour(id) ON DELETE CASCADE,
  step_order     INTEGER NOT NULL,              -- 1-based
  page_href      TEXT,                          -- page this step happens on; a change navigates the user there
  target         TEXT,                          -- anchor (syntax above); NULL = centered card
  target_fragile BOOLEAN NOT NULL DEFAULT false,-- true = text/CSS anchor on a page another agent owns; it wants
                                                --        a data-tour-id (the asks are filed in the handoff)
  placement      TEXT NOT NULL DEFAULT 'auto',  -- auto | top | bottom | left | right
  title          TEXT NOT NULL,                 -- the card heading the user reads
  body           TEXT NOT NULL,                 -- the card text the user reads (plain English, no jargon)
  narration      TEXT,                          -- PHASE 2: the voice-over line for the recorded video
  action_hint    TEXT,                          -- PHASE 2: what the recorder DOES here (click/type/scroll/wait)
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tour_id, step_order)
);
CREATE INDEX IF NOT EXISTS training_step_org_idx  ON core.training_tour_step(org_id);
CREATE INDEX IF NOT EXISTS training_step_tour_idx ON core.training_tour_step(tour_id, step_order);

ALTER TABLE core.training_tour_step ENABLE ROW LEVEL SECURITY;
GRANT ALL ON core.training_tour_step TO service_role;

COMMENT ON TABLE core.training_tour IS
  'Guided in-app walk-throughs ("simulation training"), owner directive 2026-08-04. HOUSE org rows are the PLATFORM DEFAULTS every tenant sees; a tenant row with the same slug overrides it for that tenant only. Seeded from app/data/training_tours_seed.json on the house sync pass (never clobbers an edited row). Also the source of the Phase-2 video recording scripts.';
COMMENT ON COLUMN core.training_tour_step.target IS
  'Resilient anchor: "tour:<data-tour-id>" | "text:<visible text>" | "css:<selector>" | NULL for a centered card. An anchor that does not resolve falls back to the centered card, so a moved control degrades the step instead of breaking the tour.';
COMMENT ON COLUMN core.training_tour_step.narration IS
  'PHASE 2 (video): the spoken line for this step. Written now so recording scripts need no re-authoring.';

NOTIFY pgrst, 'reload schema';
SELECT '720 complete — core.training_tour + core.training_tour_step (RLS on, zero policies, service_role only). Tours seed from app/data/training_tours_seed.json on the HOUSE org sync pass (SEED_VERSION 8).' AS status;
