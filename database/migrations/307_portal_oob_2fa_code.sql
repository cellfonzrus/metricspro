-- 307_portal_oob_2fa_code.sql
-- mod-commission · band 200–299 spill → 307 (306 taken). Additive + idempotent + safe to re-run.
--
-- WHAT IT IS FOR (owner directive 2026-08-30: autonomous logins into third-party portals "so the reports
-- can be pulled without human intervention").
--
-- THE GAP IT CLOSES. The portal drivers are INTERACTIVE by design: `begin_login()` drives the browser as
-- far as the 2FA code-entry screen and then stops, because a HUMAN reads the code out of their email and
-- calls `complete_2fa(code)`. vidapay_sweep.py says so in its own comments — "a human clicks these; a
-- headless login that only DETECTS the 2FA screen never receives a code". So a SCHEDULED pull could never
-- finish on its own, even once Cloudflare is cleared and the DOM selectors are calibrated: it would reach
-- the code prompt and stall. The audit named this the one missing primitive for unattended runs.
--
-- These columns are that primitive's CONFIG. The reader itself (app/modules/commcalc/oob_code.py) takes
-- its MAILBOX and CREDENTIAL from the tenant's existing commcalc.email_sweep_config row — the same inbox
-- the daily attachment sweep already uses — so enabling unattended 2FA introduces NO new secret to store
-- or rotate. Only the per-login matching rules live here, because different portals mail codes
-- differently (sender, subject wording, code format).
--
-- CONFIG, NEVER CODE: no portal, carrier or tenant name appears in a branch. A portal with an unusual
-- code format ('AB-45678', a 4-digit PIN) is a `oob_code_regex` / `oob_code_length` row edit, not a
-- code change.
--
-- 🔐 SECURITY NOTES, because this automates a second factor:
--   • oob_enabled defaults FALSE. Running this migration changes no behaviour at all; a login keeps
--     stopping for a human until an operator deliberately turns it on.
--   • oob_max_age_seconds is a SECURITY CONTROL, not tuning. The reader refuses any code older than this
--     window even when it is the only match, so a stale message can never be replayed into a login.
--     Default 300s (5 min) matches typical portal code lifetimes; raising it widens that replay window.
--   • The sender/subject filters narrow which messages may supply a code. They are a FILTER, not proof of
--     origin — mail headers are forgeable — which is exactly why the freshness window is enforced too.
--   • Automating a second factor weakens the "second" in two-factor: anything able to read the tenant's
--     mailbox can now complete a portal login unattended. That is a deliberate, operator-made trade for
--     unattended pulls, which is why it is per-login and off by default.
--   • The code itself is never logged, never stored, and never returned in an error string.
--
-- 💰 NOT a money setting. Nothing here touches a payout; it only governs how a report pull authenticates.

ALTER TABLE commcalc.data_source
  ADD COLUMN IF NOT EXISTS oob_enabled          boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS oob_from_contains    text,
  ADD COLUMN IF NOT EXISTS oob_subject_contains text,
  ADD COLUMN IF NOT EXISTS oob_code_regex       text,
  ADD COLUMN IF NOT EXISTS oob_code_length      integer,
  ADD COLUMN IF NOT EXISTS oob_max_age_seconds  integer NOT NULL DEFAULT 300;

-- Idempotent: drop + re-add so a re-run is safe.
ALTER TABLE commcalc.data_source
  DROP CONSTRAINT IF EXISTS data_source_oob_max_age_check;
ALTER TABLE commcalc.data_source
  ADD CONSTRAINT data_source_oob_max_age_check
  CHECK (oob_max_age_seconds BETWEEN 30 AND 3600);   -- a code older than an hour is never legitimate

ALTER TABLE commcalc.data_source
  DROP CONSTRAINT IF EXISTS data_source_oob_code_length_check;
ALTER TABLE commcalc.data_source
  ADD CONSTRAINT data_source_oob_code_length_check
  CHECK (oob_code_length IS NULL OR oob_code_length BETWEEN 3 AND 10);

COMMENT ON COLUMN commcalc.data_source.oob_enabled IS
  'Read this portal login''s 2FA code from the tenant mailbox so a scheduled pull completes without a '
  'human. OFF by default. Automating the second factor is an operator trade-off: anything that can read '
  'the mailbox can then complete this login unattended.';
COMMENT ON COLUMN commcalc.data_source.oob_max_age_seconds IS
  'Security control, not tuning: a code older than this is refused outright and never replayed. Default '
  '300s. Raising it widens the replay window.';
COMMENT ON COLUMN commcalc.data_source.oob_code_regex IS
  'Optional explicit pattern for portals with an unusual code format (group 1 if present, else the whole '
  'match). Unset = scan for a standalone 4-8 digit run adjacent to a code word.';

-- EXAMPLE (not executed) — turn it on for one login once the sender/subject are known:
--
-- UPDATE commcalc.data_source
--    SET oob_enabled = true,
--        oob_from_contains = 'vidapaycrm.com',
--        oob_subject_contains = 'verification code',
--        oob_max_age_seconds = 300
--  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
--    AND processor = 'vidapay';

NOTIFY pgrst, 'reload schema';
