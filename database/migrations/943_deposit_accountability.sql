-- 943_deposit_accountability.sql — deposit-accountability management confirmation (owner
-- directive 2026-09-02)
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- Owner, verbatim: "cash deposit capture should be shown as a separate line item under cash
-- deposit recon, every cash deposit should be accompanied by the bank deposit slip, if the cash
-- has been handed over to the management then a check box should be there for all the dates of
-- which the cash has been handed over to the management - then the management should be able to
-- confirm that the cash has been received by them in the system as a check box and making the
-- color green for the days the cash has been accounted for whether deposit or handed over, it
-- should be a similar workflow as did for the approval".
--
-- The handed-over CHECKBOX state already exists (mig 089 disposition = 'handed_to_mgmt' on
-- commcalc.cash_pickup; mirrored on commcalc.billpay_pickup by mig 942). What does NOT exist is
-- the SECOND half of the approval-style handshake: management confirming IN THE SYSTEM that the
-- handed-over cash actually reached them. This migration adds that state — the same
-- pending → approved shape the payroll approvals board records (status + actor + timestamp:
-- dm_status/dm_by/dm_at precedent, mig 431/§14): a boolean plus WHO confirmed and WHEN, on each
-- pickup row (the disposition grain — one row per org × close_date × store × employee, both the
-- cash and the bill-pay sibling table so the accountability board covers every envelope kind).
--
-- A day goes GREEN (GET /closing/deposit-accountability) only when every picked-up envelope of
-- that store-day is accounted for: deposited WITH the bank deposit slip on file, or handed to
-- management AND mgmt_confirmed here. Confirmation is a MANAGEMENT action — the endpoint is
-- gated by closing/billpay_pickup.can_see_cash_recon (market manager and above, the mig-434
-- pay-visibility posture, fail-closed; per-org override storeops.tenants.cash_recon_visible_roles,
-- mig 942). DMs keep recording pickups/dispositions exactly as today.
--
-- MONEY: nothing here moves a booked number — pure workflow state (all rows default to
-- unconfirmed, which is exactly today's implicit state). Additive + idempotent. Run in the
-- Supabase SQL editor.

ALTER TABLE commcalc.cash_pickup
  ADD COLUMN IF NOT EXISTS mgmt_confirmed    BOOLEAN DEFAULT false,
  ADD COLUMN IF NOT EXISTS mgmt_confirmed_by TEXT,
  ADD COLUMN IF NOT EXISTS mgmt_confirmed_at TIMESTAMPTZ;

ALTER TABLE commcalc.billpay_pickup
  ADD COLUMN IF NOT EXISTS mgmt_confirmed    BOOLEAN DEFAULT false,
  ADD COLUMN IF NOT EXISTS mgmt_confirmed_by TEXT,
  ADD COLUMN IF NOT EXISTS mgmt_confirmed_at TIMESTAMPTZ;

COMMENT ON COLUMN commcalc.cash_pickup.mgmt_confirmed IS
  'Management confirmed IN THE SYSTEM that handed-over cash was received (owner 2026-09-02 — the '
  'approval-style second checkbox). Meaningful only for disposition=handed_to_mgmt rows; set via '
  'POST /closing/deposit-mgmt-confirm, gated market-manager-and-above (can_see_cash_recon).';
COMMENT ON COLUMN commcalc.billpay_pickup.mgmt_confirmed IS
  'Management confirmed IN THE SYSTEM that handed-over bill-pay cash was received (owner '
  '2026-09-02). Meaningful only for disposition=handed_to_mgmt rows; set via '
  'POST /closing/deposit-mgmt-confirm, gated market-manager-and-above (can_see_cash_recon).';

-- RLS: columns on existing tables — the tables'' open_all policies (mig 034 / mig 942) already
-- cover them; no policy change.

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 943 complete — mgmt_confirmed(+by/at) on cash_pickup & billpay_pickup' AS status;

-- REVERT:
--   ALTER TABLE commcalc.cash_pickup    DROP COLUMN IF EXISTS mgmt_confirmed,
--     DROP COLUMN IF EXISTS mgmt_confirmed_by, DROP COLUMN IF EXISTS mgmt_confirmed_at;
--   ALTER TABLE commcalc.billpay_pickup DROP COLUMN IF EXISTS mgmt_confirmed,
--     DROP COLUMN IF EXISTS mgmt_confirmed_by, DROP COLUMN IF EXISTS mgmt_confirmed_at;
--   (the accountability board reads these columns defensively — pre-943 schema degrades to
--    "handed rows all unconfirmed", i.e. handed days simply never turn green; the confirm
--    endpoint returns its schema-missing error instead of writing.)
