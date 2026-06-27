-- 054_helpdesk_category_notify.sql — per-category new-ticket recipients.
--
-- A new ticket's email alert can now route by CATEGORY (e.g. "IT / Systems" → the IT lead,
-- "HR / Payroll" → HR). If a category has no recipients set, the alert falls back to the global
-- list in storeops.ticket_settings.notify_emails. Idempotent.

ALTER TABLE storeops.ticket_categories ADD COLUMN IF NOT EXISTS notify_emails TEXT[];

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 054 complete — ticket_categories.notify_emails added' AS status;
