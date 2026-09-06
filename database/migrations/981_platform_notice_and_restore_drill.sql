-- 981_platform_notice_and_restore_drill.sql — two of the researched, missing operator controls
--
-- OWNER DIRECTIVE 2026-09-05 (sanjot@): "…what other industry wide super admin controls are missing
-- yet very import do a thorough research and add those also."
--
-- Of the researched gap list (see the PR comment and docs/SYSTEM_DATA_FLOW_INDEX.md §21), these two
-- are the ones that are BOTH high-value AND fully self-contained — they add a surface, they change
-- no authorization semantics, and they cannot cut anyone's access:
--
--   1. A PLATFORM STATUS / INCIDENT NOTICE broadcast to tenants. The platform had no way to tell its
--      tenants "we are doing maintenance at 02:00" or "carrier ingest is degraded". Every tenant
--      currently discovers an incident by noticing a number looks wrong.
--   2. A BACKUP / RESTORE-DRILL ATTESTATION. Index §20 declares `db_backup_restore` UNMONITORED —
--      an explicitly known, real gap in the owner's own control box: "Supabase backup/restore drills
--      … not observable from the backend today". It is not OBSERVABLE, but it IS ATTESTABLE, and once
--      attested, staleness of the attestation is an ordinary heartbeat. An untested backup is not a
--      backup, and the platform currently cannot say when one was last tested.
--
-- DUPLICATE CHECK (CLAUDE.md build gate). Searched the index for notice / banner / broadcast /
-- announcement / backup / restore / drill before writing a line:
--   · `storeops.whats_new` (the product-changelog surface behind /admin/whats-new) is a MARKETING
--     changelog for shipped features, per tenant, with no severity, no time window and no incident
--     semantics. A "the platform is degraded right now" banner is a different question with a
--     different lifetime, so it is a separate table — but the NAV and the read-path shape follow it.
--   · `core.system_check` (mig 970) already declares the backup gap as an `unmonitored` row. This
--     migration does NOT add a second opinion about backup health: it adds the EVIDENCE the existing
--     check can consume, and the check is retargeted by a ROW (bottom of this file), not by code.
--   · `core.notify_*` is per-tenant messaging to PEOPLE; this is a platform state read by the app
--     shell. No overlap.
--
-- ★ THE CONTROL BOX NEEDS NO CODE CHANGE TO USE THIS. ★ `control_box_api._heartbeat_evidence` reads
-- its source from CONFIG (`heartbeat_source` = {schema, table, column}) rather than from a branch per
-- subsystem — mig 970's own RULE TWO design. So pointing the existing `db_backup_restore` check at
-- core.restore_drill.verified_at is a single UPDATE of a `core.system_check` row. That UPDATE is at
-- the foot of this file and is COMMENTED OUT, because switching it on turns a grey `unmonitored`
-- lamp into an honest RED until the first drill is recorded, and that is the owner's call to make.
--
-- SAFE: additive + idempotent. Re-runnable. Creates two tables and changes no existing row.
-- MONEY: touches NO payout, rate, plan, commission, or paid/earned column.
-- SECURITY: RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5).

BEGIN;

CREATE SCHEMA IF NOT EXISTS core;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. PLATFORM STATUS NOTICE — operator → tenants
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- Audience is by ORG_ID, never by tenant name (RULE TWO). `org_ids` NULL/empty = every tenant;
-- otherwise exactly the listed tenants. `GET /core/platform-notice` resolves the caller's org from
-- their VERIFIED membership and filters server-side, and strips `org_ids` from the response — so a
-- tenant can never learn which OTHER tenants a notice was aimed at (§19.15 cross-tenant discipline;
-- the same "lamps and counts only, never another tenant's figures" rule the control box's platform
-- view follows).
--
-- The time window is what keeps this honest: a notice with an `ends_at` disappears by itself, so the
-- platform cannot end up with a stale "maintenance tonight" banner from three months ago.
CREATE TABLE IF NOT EXISTS core.platform_notice (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  severity           TEXT NOT NULL DEFAULT 'info',   -- info | maintenance | degraded | outage
  title              TEXT NOT NULL,
  body               TEXT,
  starts_at          TIMESTAMPTZ,                    -- NULL = live now
  ends_at            TIMESTAMPTZ,                    -- NULL = until withdrawn
  org_ids            UUID[],                         -- NULL/empty = every tenant
  is_active          BOOLEAN NOT NULL DEFAULT TRUE,  -- withdrawal is soft; the row and audit remain
  created_by_auth_id UUID,
  created_by_email   TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS platform_notice_live_idx
  ON core.platform_notice (is_active, starts_at, ends_at);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 2. BACKUP / RESTORE-DRILL ATTESTATION — evidence for §20's declared UNMONITORED lamp
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- `org_id` exists purely so the control box's generic heartbeat probe (which filters on org_id) can
-- read this table with no code change; the platform-wide drill is recorded against the house org,
-- the same convention §20's platform specs already use.
--
-- `verified_at` (not `performed_at`) is the heartbeat column: it is the moment the restore was
-- confirmed good. The API writes both, and `operator.drill_lamp` refuses to call anything GREEN that
-- is stale, `failed`, or missing — §20's honesty rule ("a control box that shows green for a
-- subsystem it does not actually check is worse than one that says 'not monitored'").
CREATE TABLE IF NOT EXISTS core.restore_drill (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,                  -- house org for the platform-wide drill
  outcome            TEXT NOT NULL,                  -- passed | failed | partial
  scope              TEXT NOT NULL,                  -- WHAT was restored, in words
  performed_at       TIMESTAMPTZ NOT NULL,
  verified_at        TIMESTAMPTZ NOT NULL,           -- ← the heartbeat column the control box reads
  notes              TEXT,
  recorded_by_auth_id UUID,
  recorded_by_email  TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS restore_drill_recent_idx ON core.restore_drill (org_id, verified_at DESC);

-- ── Security posture (AGENT_CONTRACT §5): RLS on, no policies, no anon/authenticated grants ────
ALTER TABLE core.platform_notice ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.restore_drill   ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  BEGIN REVOKE ALL ON core.platform_notice, core.restore_drill FROM anon, authenticated;
    EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN GRANT ALL ON core.platform_notice, core.restore_drill TO service_role;
    EXCEPTION WHEN OTHERS THEN NULL; END;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 981 — platform status notices + backup restore-drill attestation' AS status;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- ⚠ COMMENTED OUT — turns a grey lamp RED until the first drill is recorded. OWNER'S CALL. ⚠
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- Migration 970 seeded `db_backup_restore` as an honest `unmonitored` declaration. The statement
-- below retargets that SAME check (no new check, no second opinion) at the attestation table, using
-- the control box's config-driven heartbeat source — zero code change in control_box.py.
--
-- Cadence 90 days + 30 days grace: a quarterly restore drill is the ordinary standard. With no drill
-- recorded, `heartbeat_lamp` reads "never" ⇒ RED. That is CORRECT and is exactly why this is opt-in:
-- the board will honestly tell you the backups have never been tested, on the day you switch it on.
--
-- To switch it on: record one drill first (Console → Companies → Restore Drills, or
-- POST /api/v1/core/operator/restore-drill), confirm it reads green, then run this.
--
-- UPDATE core.system_check
--    SET kind = 'heartbeat',
--        config = jsonb_build_object(
--                   'heartbeat_source', jsonb_build_object('schema','core','table','restore_drill','column','verified_at'),
--                   'cadence_hours', 2160,     -- 90 days
--                   'grace_hours',   720)      -- + 30 days
--  WHERE key = 'db_backup_restore';   -- every org declaring the check (today: only the house row 970 seeded)
--
-- To go back to an honest grey lamp:
--   UPDATE core.system_check SET kind = 'unmonitored' WHERE key = 'db_backup_restore';

-- REVERT:
--   DROP TABLE IF EXISTS core.restore_drill;
--   DROP TABLE IF EXISTS core.platform_notice;
--   -- If the commented-out system_check UPDATE above was applied, also run:
--   --   UPDATE core.system_check SET kind = 'unmonitored' WHERE key = 'db_backup_restore';
