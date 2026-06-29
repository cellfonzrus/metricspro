-- 063_connector_account_id.sql — extra login identifiers on a connector (e.g. Total Wireless retailers).
--
-- WHY: some carrier/retailer portals (e.g. Total Wireless) log in with an ACCOUNT ID (retailer/dealer
-- number) in addition to a username + password, plus 2FA. connector_instances already tracks the 2FA
-- method/status; this adds the non-secret login identifiers so the connector can carry the full login.
-- (The password stays in the per-vendor *_sweep_config / secure store, never in this registry.)
--
-- Additive + idempotent.

ALTER TABLE commcalc.connector_instances ADD COLUMN IF NOT EXISTS account_id      TEXT;
ALTER TABLE commcalc.connector_instances ADD COLUMN IF NOT EXISTS login_username  TEXT;

NOTIFY pgrst, 'reload schema';
