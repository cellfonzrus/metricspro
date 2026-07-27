-- 244_commission_portal_rate_limit_backoff.sql — mod-commission (band 200–299)
-- "VIDAPAY SAYS YOU HAVE TOO MANY REQUESTS AND HAVE BEEN TEMPORARILY BLOCKED" (owner report 2026-07-27).
--
-- WHY
--   Nothing in the portal-pull stack recognised a rate-limit / temporary-block response. A blocked
--   portal produced a GENERIC failure — "could not find the password field" at login, "report not
--   listed" / "the session has expired" at pull time — and NOTHING prevented the next attempt. Worse,
--   every one of those messages points a human at an action that makes the block deeper:
--
--     • "session expired"        → the operator re-runs 🔴 Live login (a fresh headless login, the
--                                  single most expensive request this module makes);
--     • "report not listed"      → the operator re-checks Report mapping and presses ▶ Pull now again
--                                  (5 reports × up to 3 month-windows of report GENERATION);
--     • the scheduled /run-due   → fires again on its own next tick, with no memory of the refusal.
--
--   This migration adds the state that lets the code say "we are in a cooldown, do not touch the
--   portal", plus the config the detection is driven from (RULE TWO: markers and the backoff ladder
--   are DATA, not constants in a decision path).
--
-- ADDITIVE + IDEMPOTENT. Safe to re-run. Nothing breaks before it runs and nothing breaks if it is
-- rolled back: portal_backoff.read_state() sees no blocked_until key and reports "not blocked", every
-- cooldown write is a self-contained best-effort UPDATE, and the marker/ladder loaders fall back to
-- the seeded defaults in code. The feature is INERT pre-migration, not broken.
--
-- NOT MONEY-TOUCHING. No rate, tier, plan rule, payout or calculation input is read or written here.
-- This file only decides WHEN a portal may be contacted.

-- ── (1) cooldown state on every portal login ─────────────────────────────────────────────────────
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
              WHERE table_schema = 'commcalc' AND table_name = 'data_source') THEN

    ALTER TABLE commcalc.data_source
      ADD COLUMN IF NOT EXISTS blocked_until        timestamptz,
      ADD COLUMN IF NOT EXISTS blocked_at           timestamptz,
      ADD COLUMN IF NOT EXISTS block_reason         text,
      ADD COLUMN IF NOT EXISTS consecutive_failures integer NOT NULL DEFAULT 0;

    COMMENT ON COLUMN commcalc.data_source.blocked_until IS
      'The portal has rate-limited / temporarily blocked us; do not contact it before this time. The '
      'scheduled /run-due SKIPS such a login entirely (the skip is NOT recorded as an attempt — '
      'last_attempt_at, last_status and consecutive_failures are untouched; only next_run_at moves '
      'past the cooldown), the automatic post-login pull is suppressed, and a HUMAN Live login / Pull '
      'now requires an explicit second confirm. Escalating: 30min → 2h → 8h cap by default, and never '
      'earlier than a Retry-After header the portal supplied.';
    COMMENT ON COLUMN commcalc.data_source.blocked_at IS
      'When the current cooldown was stamped (blocked_until minus the backoff step actually applied).';
    COMMENT ON COLUMN commcalc.data_source.block_reason IS
      'What was detected: the HTTP status (429 / 503+Retry-After) or the block-page marker phrase that '
      'matched. Shown verbatim on /commcalc/email-imports and in the admin attention popup.';
    COMMENT ON COLUMN commcalc.data_source.consecutive_failures IS
      'Consecutive non-delivering attempts (failed pull, failed login, detected block). Reset to 0 by '
      'the first pull that actually IMPORTS rows — that is the recovery signal. Drives both the '
      'escalating backoff step and the "this connector keeps failing" attention item.';
  END IF;
END $$;

-- Partial index: the scheduler and the attention provider both ask "which of this org's logins are in
-- cooldown right now?".
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'commcalc' AND table_name = 'data_source'
                AND column_name = 'blocked_until') THEN
    CREATE INDEX IF NOT EXISTS data_source_blocked_idx
      ON commcalc.data_source (org_id, blocked_until)
      WHERE blocked_until IS NOT NULL;
  END IF;
END $$;

-- ── (2) RULE TWO — the block-page vocabulary is CONFIG, not code ─────────────────────────────────
-- One row per phrase. org_id = the house org means "the platform default, inherited by every tenant";
-- a tenant that adds its OWN rows overrides the defaults wholesale (the same inheritance shape
-- report_pull_map uses). processor NULL = applies to every portal; set it to pin a phrase to one.
CREATE TABLE IF NOT EXISTS commcalc.portal_block_marker (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL,
  processor   text,
  marker      text NOT NULL,
  enabled     boolean NOT NULL DEFAULT true,
  notes       text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS portal_block_marker_org_idx ON commcalc.portal_block_marker (org_id);
CREATE UNIQUE INDEX IF NOT EXISTS portal_block_marker_uniq_idx
  ON commcalc.portal_block_marker (org_id, coalesce(processor, ''), lower(marker));

COMMENT ON TABLE commcalc.portal_block_marker IS
  'Phrases that identify a portal rate-limit / temporary-block page, matched case-insensitively as '
  'substrings of the visible page text. Seeded with the T-CETRA/VidaPay wording the owner reported on '
  '2026-07-27. Deliberately NARROW: a false positive parks a healthy login in a needless cooldown, so '
  'broad phrases are seeded DISABLED for an operator to switch on if their portal needs them. HTTP 429 '
  '(and 503 carrying Retry-After) is detected separately at the wire level and needs no row here.';

-- Seed the house defaults. ON CONFLICT DO NOTHING ⇒ a re-run never resurrects a marker an operator
-- disabled, and never overwrites their notes.
INSERT INTO commcalc.portal_block_marker (org_id, processor, marker, enabled, notes) VALUES
  ('00000000-0000-0000-0000-000000000001', NULL, 'too many requests',      true,
   'The exact T-CETRA/VidaPay wording in the owner''s 2026-07-27 report.'),
  ('00000000-0000-0000-0000-000000000001', NULL, 'temporarily blocked',    true,
   'The exact T-CETRA/VidaPay wording in the owner''s 2026-07-27 report.'),
  ('00000000-0000-0000-0000-000000000001', NULL, 'temporarily block',      true,
   'Covers "we have temporarily blocked your access".'),
  ('00000000-0000-0000-0000-000000000001', NULL, 'blocked temporarily',    true, NULL),
  ('00000000-0000-0000-0000-000000000001', NULL, 'rate limit',             true,
   'Substring — also matches "rate limited" / "rate limiting".'),
  ('00000000-0000-0000-0000-000000000001', NULL, 'too many attempts',      true, NULL),
  ('00000000-0000-0000-0000-000000000001', NULL, 'too many failed',        true, NULL),
  ('00000000-0000-0000-0000-000000000001', NULL, 'request throttled',      true, NULL),
  ('00000000-0000-0000-0000-000000000001', NULL, 'throttled',              true, NULL),
  ('00000000-0000-0000-0000-000000000001', NULL, 'exceeded the maximum number of requests', true, NULL),
  ('00000000-0000-0000-0000-000000000001', NULL, 'unusual number of requests', true, NULL),
  -- Seeded OFF: real phrases, but common enough on ordinary error pages to risk a false cooldown.
  -- Switch one on only if your portal's block page actually uses it.
  ('00000000-0000-0000-0000-000000000001', NULL, 'try again later',        false,
   'OFF by default — appears on many ordinary error pages. Enable only if your block page needs it.'),
  ('00000000-0000-0000-0000-000000000001', NULL, 'please wait a few minutes', false,
   'OFF by default — see above.'),
  ('00000000-0000-0000-0000-000000000001', NULL, 'access temporarily denied', false,
   'OFF by default — see above.')
ON CONFLICT DO NOTHING;

-- ── (3) RULE TWO — the backoff ladder + the alert threshold, per tenant ──────────────────────────
-- Extends the EXISTING commission posture table (mig 201/241) rather than inventing a parallel one.
-- Both are read best-effort with a documented default, so this section is optional.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
              WHERE table_schema = 'commcalc' AND table_name = 'commission_org_config') THEN
    ALTER TABLE commcalc.commission_org_config
      ADD COLUMN IF NOT EXISTS portal_backoff_minutes       text,
      ADD COLUMN IF NOT EXISTS portal_block_alert_failures  integer;
    COMMENT ON COLUMN commcalc.commission_org_config.portal_backoff_minutes IS
      'Escalating portal cooldown, CSV of minutes, e.g. "30,120,480" (the default when NULL). Indexed '
      'by the consecutive-failure count; the LAST entry is the cap. A portal-supplied Retry-After can '
      'only push a cooldown LATER, never earlier.';
    COMMENT ON COLUMN commcalc.commission_org_config.portal_block_alert_failures IS
      'Consecutive non-delivering attempts before the admin attention popup raises this connector '
      '(default 4 when NULL).';
  END IF;
END $$;

-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- OPERATOR — INCIDENT DIAGNOSTIC (read-only) + THE TEMPORARY MITIGATION
-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- Run these in the Supabase SQL editor to reconstruct what hammered the portal on 2026-07-27.
--
-- 1) Is the 30-minute portal-pull cron registered at all? (mig 241 §3 supplies the SQL to create it;
--    if this returns zero rows, the scheduler was NEVER the source of the traffic.)
--      SELECT jobid, jobname, schedule, active, command
--        FROM cron.job
--       WHERE jobname IN ('data-sources-run-due','connectors-run-due')
--          OR command ILIKE '%data-sources/sweep/run-due%';
--
-- 2) How often did we actually call out, and what came back? (net.http_post is ASYNC — the real status
--    lives in net._http_response, never in the caller.)
--      SELECT date_trunc('hour', created) AS hr, status_code, count(*)
--        FROM net._http_response
--       WHERE created > now() - interval '3 days'
--       GROUP BY 1,2 ORDER BY 1 DESC, 3 DESC;
--
--    …and the run-due calls specifically (403 = NOTIFY_RUN_SECRET mismatch, 200 = it ran):
--      SELECT r.created, r.status_code, left(r.content, 300)
--        FROM net._http_response r
--       WHERE r.created > now() - interval '3 days'
--       ORDER BY r.created DESC LIMIT 50;
--
-- 3) The portal-login timeline — when was each login last ATTEMPTED vs last DELIVERED, and what did it
--    say? (last_attempt_at is mig 241; blocked_until/consecutive_failures appear once THIS file runs.)
--      SELECT label, processor, enabled, frequency, auth_status,
--             last_attempt_at, last_run_at, last_pull_at, next_run_at,
--             left(last_status, 200) AS last_status
--        FROM commcalc.data_source
--       ORDER BY coalesce(last_attempt_at, last_run_at) DESC NULLS LAST;
--
-- 4) How many pulls actually landed rows (upload_trace evidence, mig 202)?
--      SELECT date_trunc('hour', created_at) AS hr, source, upload_type, count(*)
--        FROM commcalc.upload_trace
--       WHERE created_at > now() - interval '3 days' AND source LIKE 'portal-pull%'
--       GROUP BY 1,2,3 ORDER BY 1 DESC;
--
-- 5) TEMPORARY MITIGATION — stop the scheduled portal pulls until this backoff fix is deployed.
--    cron.unschedule() is overloaded: by NAME (text) or by jobid (bigint). The name form is correct
--    here and RAISES if the job does not exist, so guard it:
--      SELECT cron.unschedule('data-sources-run-due')
--       WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'data-sources-run-due');
--    Re-register it AFTER the fix deploys, using the exact SQL in migration 241 §3.
--
-- 6) After the fix is deployed, watch the cooldown working:
--      SELECT label, processor, blocked_until, consecutive_failures, left(block_reason,160)
--        FROM commcalc.data_source WHERE blocked_until IS NOT NULL ORDER BY blocked_until DESC;
--    …and lift one by hand ONLY if you know the portal released us (the UI has a button for this):
--      UPDATE commcalc.data_source
--         SET blocked_until = NULL, block_reason = NULL, consecutive_failures = 0
--       WHERE id = '<source-id>' AND org_id = '<org-id>';
