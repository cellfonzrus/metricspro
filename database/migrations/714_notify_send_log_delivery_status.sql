-- MIGRATION 714: NOTIFY — record WhatsApp/Meta delivery-status events onto notify.send_log
-- Band 700–799 (mod-platform-core). Additive + idempotent (safe to re-run).
--
-- WHY: OWNER INCIDENT 2026-07-18 — WhatsApp template sends carrying the no-login download URL are ACCEPTED
-- by Meta (a wamid is returned, send_log.status='sent') but SILENTLY DROPPED before delivery (Meta's
-- link-safety crawler fetched the tokenized railway.app URL out of the template body, then dropped the
-- message). Today send_log records only send-time acceptance ('sent'), so a silent drop is INVISIBLE. Meta
-- delivers real delivery STATUS events (sent|delivered|read|failed) on the same webhook subscription. These
-- three columns capture the latest such status so a drop becomes visible (status stays 'sent'/no 'delivered'
-- — or 'failed' with the Meta error) in the /notify Send History.
--
-- DEGRADES GRACEFULLY: until this runs, the remediation POST /whatsapp-webhook still returns 200 for every
-- well-formed payload — the status-event handler catches the missing-column error and drops to a no-op
-- (statuses are simply not recorded). Nothing else depends on these columns.

ALTER TABLE notify.send_log ADD COLUMN IF NOT EXISTS delivery_status     TEXT;         -- latest Meta status: sent|delivered|read|failed
ALTER TABLE notify.send_log ADD COLUMN IF NOT EXISTS delivery_error      TEXT;         -- flattened Meta errors[] when failed
ALTER TABLE notify.send_log ADD COLUMN IF NOT EXISTS delivery_updated_at TIMESTAMPTZ;  -- when the latest status event landed

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 714 complete — notify.send_log delivery_status/delivery_error/delivery_updated_at' AS status;
