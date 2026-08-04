-- 721_core_release_note.sql — "WHAT'S NEW": new-features + improvements log for admin staff.
-- Band 700–799 (mod-platform-core). Additive + idempotent (safe to re-run).
--
-- OWNER DIRECTIVE 2026-08-04 (in chat, verbatim): "like we have the warnings for the admin who logs in,
-- there should be 2 more areas new features and improvements and keep them logged somewhere only for
-- admin staff."
--
-- WHAT THIS IS. The admin-attention popup (mig 717) already tells an administrator what is BROKEN on
-- login. This adds the other two things an owner wants to see there — what is NEW and what got BETTER —
-- as first-class data instead of a chat message that scrolls away. Same audience, same surface, same
-- gate; a rep never sees any of it. It is also a permanent, filterable, exportable log at
-- /admin/whats-new, so "when did that change?" has an answer six months later.
--
-- MULTI-TENANT (RULE ONE): org_id uuid NOT NULL + an index (contract §2 — NOT NULL, no exceptions, so a
-- platform-wide entry is a HOUSE-org row rather than a NULL row; same pattern as core.support_doc 715,
-- core.failure_kind_doc 716, core.token_rates 718 and core.training_tour 720).
--     read  = rows WHERE org_id IN (HOUSE, <tenant>)  → every tenant's admins see the platform-wide
--             entries PLUS anything their own organisation logged for itself.
--     write = a tenant admin may only write its own org; only a super-admin writes the platform-wide
--             (HOUSE) entries that every tenant will read. The org comes from the caller's verified
--             membership, never from the request body.
--
-- WHY A `status` COLUMN. Some entries announce work that is genuinely still landing. Publishing those as
-- "released" would be a lie an admin then chases; hiding them loses the roadmap value. `status` =
-- 'shipped' | 'in_progress' lets the UI badge them honestly ("Coming shortly") in the same list.
--
-- SEEN/UNSEEN. v1 keeps the per-user "last time I looked" watermark in localStorage (per browser), same
-- as the walk-through completion ticks. UPGRADE PATH when it needs to be reportable across devices: add
-- core.release_note_seen (org_id, user_id, last_seen_at) + a two-route read/write and swap the two
-- helpers in src/lib/whats-new.ts. Nothing else changes — no column here is affected.
--
-- SEEDING is done in CODE (app/data/release_notes_seed.json + app/modules/core/whats_new_seed.py) on the
-- HOUSE org's sync_tenant pass (SEED_VERSION 9), with NEVER-CLOBBER semantics — the mig-715/720
-- precedent. That is what lets a future ship process append an entry without writing a migration.
--
-- DEGRADES GRACEFULLY: until this runs, the feed returns an empty payload + a hint, the popup shows only
-- its existing Warnings tab (semantics untouched), /admin/whats-new shows an honest empty state, and
-- NOTHING else in the app is affected.
--
-- RLS POSTURE (AGENT_CONTRACT §5): RLS ENABLED, ZERO policies, ZERO anon/authenticated grants.
-- NOT MONEY-TOUCHING: no rate, plan, tier, payout, commission or P&L row is read or written.

CREATE TABLE IF NOT EXISTS core.release_note (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,                  -- HOUSE = platform-wide (all tenants); tenant = own entry
  slug         TEXT NOT NULL,                  -- stable key, so a re-seed refreshes instead of duplicating
  category     TEXT NOT NULL DEFAULT 'new_feature',   -- new_feature | improvement | fix
  module       TEXT,                           -- nav module key (closing, commissions, storeops, …)
  title        TEXT NOT NULL,                  -- one plain-English line
  body         TEXT,                           -- 1–3 plain sentences aimed at an ADMIN, not a developer
  status       TEXT NOT NULL DEFAULT 'shipped',       -- shipped | in_progress
  deep_link    TEXT,                           -- where to go and see it
  released_at  DATE NOT NULL DEFAULT CURRENT_DATE,
  is_published BOOLEAN NOT NULL DEFAULT true,
  is_seed      BOOLEAN NOT NULL DEFAULT false,
  created_by   TEXT,
  updated_by   TEXT,                           -- NULL/'seed' = never hand-edited → the seeder may refresh
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, slug)
);
CREATE INDEX IF NOT EXISTS release_note_org_idx      ON core.release_note(org_id);
CREATE INDEX IF NOT EXISTS release_note_org_released ON core.release_note(org_id, released_at DESC);
CREATE INDEX IF NOT EXISTS release_note_org_cat      ON core.release_note(org_id, category);

ALTER TABLE core.release_note ENABLE ROW LEVEL SECURITY;
GRANT ALL ON core.release_note TO service_role;

COMMENT ON TABLE core.release_note IS
  'What''s New / Improvements log shown to ADMIN STAFF beside the login warnings (owner directive 2026-08-04). HOUSE org rows are platform-wide and read by every tenant; a tenant row is that tenant''s own entry. Seeded from app/data/release_notes_seed.json on the house sync pass (never clobbers a hand-edited row). Category new_feature|improvement|fix; status shipped|in_progress so a not-yet-live item is badged honestly instead of announced as done.';

NOTIFY pgrst, 'reload schema';
SELECT '721 complete — core.release_note (RLS on, zero policies, service_role only). Entries seed from app/data/release_notes_seed.json on the HOUSE org sync pass (SEED_VERSION 9).' AS status;
