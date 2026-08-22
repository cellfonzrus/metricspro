-- 911 · Vision — what the camera setup wizard remembers while you work
--
-- OWNER DIRECTIVE 2026-08-22 (sanjot@), verbatim: "This is very complex set up for any tenant to
-- follow , need t make it very easy for them to onboard their camera, if i was to do it agin i
-- cannot - so we need a detailed wizard to set up the cameras with every minute details possible
-- with links and storing the information as we go along so the user does not have to go back and
-- forth like we did earlier".
--
-- ══ WHY THERE ARE COLUMNS HERE AT ALL ═══════════════════════════════════════════════════════════
-- Connecting Nest cameras takes about half an hour spread across three Google consoles. The operator
-- is copying long strings between browser tabs the whole time, and until now the app remembered only
-- the two values it needed at the very END of that process. Everything else — which Cloud project
-- they picked, what they named the topic, which service account the push subscription uses — existed
-- only in the operator's short-term memory and in tabs they were about to close.
--
-- That is what "back and forth" means in the directive, and it is not a UI problem. The setup was
-- attempted once before this migration, by the owner and an assistant with the API reference open,
-- and it took a day. Most of that day was re-deriving a value that had been on screen an hour
-- earlier. So: every value the wizard shows the operator is now stored the moment they type it, and
-- every value it asks Google for is DERIVED from those rather than typed twice.
--
-- ══ WHAT IS DELIBERATELY NOT HERE ═══════════════════════════════════════════════════════════════
-- NO SECRET. The OAuth client secret is not in this migration and the wizard never stores one in a
-- new place — it goes where it already went, encrypted, in vision_credential.client_secret_enc. An
-- earlier round of this work leaked an agent secret into a chat window, and the lesson taken from it
-- was that a credential should exist in exactly one place with exactly one way in. These columns
-- hold only things that are already public: project identifiers, a topic name, a service-account
-- address. None of them grants access to anything on its own.
--
-- ══ WHY NOT core.module_onboarding_state ════════════════════════════════════════════════════════
-- That table (mig 733) holds the HUMAN overlay — skipped, acknowledged, notes — and the wizard uses
-- it for exactly that, unchanged. It has no place for typed values, and widening it would have made
-- a shared cross-module table carry one module's fields. These sit next to the credential they
-- describe instead.
--
-- Completion is NOT stored. It is re-derived on every read from what the tenant actually has — a
-- credential that works, cameras that exist, events that arrived. A stored "step 9 done" flag that
-- disagrees with a Pub/Sub subscription nobody ever created is precisely how a setup wizard tells an
-- operator they are finished while nothing works, which is the failure this whole feature exists to
-- prevent. Only the acknowledge-only steps ("I published the consent screen") are stored, in 733's
-- state table, because there is genuinely nothing to observe from here.
--
-- SECURITY: no new table, so no new RLS surface. vision_credential is already RLS-on with zero
--           anon/authenticated grants (mig 900) and these columns inherit that.
-- SAFE: additive + idempotent. Re-runnable. No data is written or moved.

BEGIN;

-- ── The Google CLOUD project (NOT the Device Access one) ─────────────────────────────────────────
-- vision_credential.project_id already holds the DEVICE ACCESS project id — a UUID. This is the
-- other thing called "project id": the Cloud project, a short lowercase name like
-- 'metrics-pro-506103'. Both are needed, they come from different consoles, and confusing them is
-- the single most common way this setup fails. They are separate columns with names that cannot be
-- misread, and onboarding.check_value() rejects each one by name if pasted into the other's box.
ALTER TABLE core.vision_credential
  ADD COLUMN IF NOT EXISTS cloud_project_id TEXT;

-- The project NUMBER (all digits) — the third value in this setup that people paste into the wrong
-- box, because it appears beside the other two and is the prefix of the OAuth client id.
ALTER TABLE core.vision_credential
  ADD COLUMN IF NOT EXISTS cloud_project_number TEXT;

-- ── Pub/Sub ─────────────────────────────────────────────────────────────────────────────────────
-- The SHORT topic id, as typed into the Cloud console. The long form Device Access demands
-- ('projects/<cloud project>/topics/<id>') is derived from this and cloud_project_id at read time by
-- onboarding.full_topic(), never stored: storing both is how the two drift apart, and pasting the
-- short id where the long one belongs is trap 5 of the setup.
ALTER TABLE core.vision_credential
  ADD COLUMN IF NOT EXISTS pubsub_topic TEXT;

-- The service account the operator picked when they ticked "Enable authentication" on the push
-- subscription. Not a secret — an address. Stored so the wizard can show an administrator exactly
-- what to put in VISION_PUBSUB_SA_EMAIL instead of asking them to remember it, and so a later
-- mismatch between the two can be pointed at rather than guessed.
ALTER TABLE core.vision_credential
  ADD COLUMN IF NOT EXISTS pubsub_sa_email TEXT;

-- ── Trap 2, the seven-day token ─────────────────────────────────────────────────────────────────
-- Google expires a refresh token issued by a consent screen still in "Testing" mode after seven
-- days. Everything works, and then the following week every camera goes dark, looking exactly like a
-- fault in us. There is no API that reports a project's publishing status, so we cannot check
-- whether the operator did that step — but we CAN notice a connection quietly approaching seven days
-- old and say so at day five, while one click still prevents it.
--
-- Set on every successful authorize and on every token refresh, so a published app's connection
-- keeps resetting its own age and sails past day seven with the warning never shown again.
ALTER TABLE core.vision_credential
  ADD COLUMN IF NOT EXISTS token_issued_at TIMESTAMPTZ;

COMMENT ON COLUMN core.vision_credential.cloud_project_id IS
  'GOOGLE CLOUD project id (e.g. metrics-pro-506103). NOT project_id, which is the Device Access '
  'project (a UUID) from a different console. Both are needed; confusing them is the most common '
  'setup failure, so they are separate columns with names that cannot be misread.';
COMMENT ON COLUMN core.vision_credential.pubsub_topic IS
  'The SHORT Pub/Sub topic id. The long "projects/<p>/topics/<t>" form that the Device Access '
  'console demands is derived at read time, never stored — storing both is how they drift apart.';
COMMENT ON COLUMN core.vision_credential.pubsub_sa_email IS
  'Service account on the push subscription. An address, not a credential. Stored so the wizard can '
  'show an admin the exact value for VISION_PUBSUB_SA_EMAIL rather than asking them to recall it.';
COMMENT ON COLUMN core.vision_credential.token_issued_at IS
  'When the current authorization was granted or last refreshed. Exists to catch the seven-day '
  'expiry of a Testing-mode consent screen BEFORE it takes the cameras down, since Google exposes '
  'no way to read a project''s publishing status.';

COMMIT;
