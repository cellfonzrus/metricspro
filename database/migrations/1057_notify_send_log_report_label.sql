-- MIGRATION 1057: NOTIFY — a send record NAMES THE REPORT IT CARRIED
-- Band 1000+ (mod-platform-core). Additive + idempotent (safe to re-run). No data is moved, no money.
--
-- WHY (owner 2026-10-05): *"in the notify app, the employees are sending themselves reports or
-- notification, in the notify history it should show which report was exported"*. The history row did
-- not say which report it carried. `/notify/send-file` — the universal export-bar path every page's
-- "Send" button uses — wrote the literal `(client-export)` for EVERY report and threw away the title
-- the browser had already sent, and the registered paths wrote a machine key the history rendered raw.
--
-- THE FIX IS THE MECHANISM, not the one path: `backend/app/modules/notify/send_identity.py` is now the
-- ONE home for "which report does this send record name", dereferenced by every writer (/send,
-- /send-to-designated, /send-file, the no-login download row) and by the reader (GET /notify/send-log).
-- This migration only gives that answer a column to live in.
--
-- DEGRADES GRACEFULLY BOTH WAYS, so it is safe to run late or never:
--   • un-run → `_insert_log` strips `report_label` and retries, and `send_identity.display_label`
--     resolves the name at READ time, so the Notify history already shows report names today;
--   • run → the name is stored as sent, so a report later renamed or unregistered still reads back as
--     the name it actually went out under.
--
-- REVERT:
--   ALTER TABLE notify.send_log      DROP COLUMN IF EXISTS report_label;
--   ALTER TABLE notify.send_artifact DROP COLUMN IF EXISTS report_label;

ALTER TABLE notify.send_log      ADD COLUMN IF NOT EXISTS report_label TEXT;  -- human name of the report sent
ALTER TABLE notify.send_artifact ADD COLUMN IF NOT EXISTS report_label TEXT;  -- same, for the download row

COMMENT ON COLUMN notify.send_log.report_label IS
  'Human name of the report this send carried, as resolved by notify/send_identity.log_identity at send '
  'time (registry label for a registered report_key, else the caller-supplied title). The Notify history '
  'displays this; report_key stays the machine key. NULL on rows written before mig 1057 — the read '
  'resolves those via send_identity.display_label.';
COMMENT ON COLUMN notify.send_artifact.report_label IS
  'Human name of the report this stored file is, so the no-login download row written by GET /notify/dl '
  'names the report too. Same one home: notify/send_identity.';
