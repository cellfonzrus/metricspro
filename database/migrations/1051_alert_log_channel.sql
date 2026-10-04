-- 1051_alert_log_channel.sql
-- A send record that does not say WHICH CHANNEL carried the message cannot tell a failed channel
-- from a channel nobody tried. storeops.alert_log held one row per (scope, ref_key), written as
-- soon as ANY channel delivered, so a digest whose email went out and whose WhatsApp failed was
-- recorded as done and the WhatsApp was never retried. Owner decision 2026-10-04: fix it properly.
--
-- Additive and idempotent. Applying it changes no behaviour on its own: the column is read and
-- written by backend/app/modules/storeops/alert_log.py, which ships in the same change.
-- Index §15.1. Money-touching: no.

ALTER TABLE storeops.alert_log
  ADD COLUMN IF NOT EXISTS channel text;

-- The backfill. Every row written before this migration recorded a delivery whose channel was not
-- stored, and the pre-1051 code recorded `addrs.get("email") or addrs.get("whatsapp")` — so a row
-- whose recipients hold an email address was carried by email. Measured read-only on 2026-10-04:
-- 709 rows, 706 with an email-shaped recipient, 3 with empty recipients.
--
-- Rows left NULL are the ones that recorded a deliberate SILENCE (no recipient configured). The
-- code treats a NULL channel as having carried nothing, which is what those rows mean.
UPDATE storeops.alert_log
   SET channel = 'email'
 WHERE channel IS NULL
   AND coalesce(recipients, '') LIKE '%@%';

-- A row is one (scope, finding, channel, recipient) fact. The lookup is always org + scope +
-- ref_key, so this index serves both the single-row question and the per-sweep bulk read.
CREATE INDEX IF NOT EXISTS ix_alert_log_org_scope_ref_channel
  ON storeops.alert_log (org_id, scope, ref_key, channel);

-- Only the two values the code knows how to speak, or NULL for "carried nothing".
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'alert_log_channel_known') THEN
    ALTER TABLE storeops.alert_log
      ADD CONSTRAINT alert_log_channel_known
      CHECK (channel IS NULL OR channel IN ('email', 'whatsapp'));
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';

-- REVERT:
--   ALTER TABLE storeops.alert_log DROP CONSTRAINT IF EXISTS alert_log_channel_known;
--   DROP INDEX IF EXISTS storeops.ix_alert_log_org_scope_ref_channel;
--   ALTER TABLE storeops.alert_log DROP COLUMN IF EXISTS channel;
--   (The backfill is dropped with the column. No other column is touched, so nothing else is lost.)
