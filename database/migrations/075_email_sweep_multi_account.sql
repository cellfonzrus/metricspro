-- 075_email_sweep_multi_account.sql — one tenant can pull reports from MORE THAN ONE mailbox.
--
-- WHY: a tenant's reports arrive in different inboxes — e.g. the B2B/Metro daily sales feed at
-- b2breports@…, and Total Wireless at luxelink@… — and different tenants use entirely different
-- mailboxes. email_sweep_config was keyed on org_id (one mailbox per tenant), so a second inbox had
-- nowhere to live. Add an 'account' key so each org can hold N mailbox rows.
--
-- ADDITIVE + BACKWARD-COMPATIBLE: the existing single row defaults to account='default', so the current
-- b2breports@ sweep keeps running unchanged. Idempotent.

-- ── config: add the account dimension + a friendly label ─────────────────────────────────────────
ALTER TABLE commcalc.email_sweep_config ADD COLUMN IF NOT EXISTS account TEXT NOT NULL DEFAULT 'default';
ALTER TABLE commcalc.email_sweep_config ADD COLUMN IF NOT EXISTS label   TEXT;   -- e.g. 'Total Wireless'

-- repoint the PRIMARY KEY from (org_id) to (org_id, account). Drop whatever the current PK is named,
-- then add the composite. Safe to re-run: it drops the composite and re-adds it.
DO $$
DECLARE pk TEXT;
BEGIN
  SELECT conname INTO pk FROM pg_constraint
   WHERE conrelid = 'commcalc.email_sweep_config'::regclass AND contype = 'p';
  IF pk IS NOT NULL THEN
    EXECUTE format('ALTER TABLE commcalc.email_sweep_config DROP CONSTRAINT %I', pk);
  END IF;
  ALTER TABLE commcalc.email_sweep_config ADD PRIMARY KEY (org_id, account);
END $$;

-- ── processed-tracking: stamp the account so the same filename from two mailboxes can't dedupe-collide
ALTER TABLE commcalc.email_processed ADD COLUMN IF NOT EXISTS account TEXT NOT NULL DEFAULT 'default';
DROP INDEX IF EXISTS commcalc.email_processed_uq;
CREATE UNIQUE INDEX IF NOT EXISTS email_processed_uq
  ON commcalc.email_processed (org_id, account, message_id, filename);

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 075 complete — email sweep is now multi-mailbox' AS status;
