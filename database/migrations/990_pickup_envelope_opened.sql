-- 990_pickup_envelope_opened.sql — "was the cash envelope opened?" (owner directive 2026-09-08)
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- Owner, verbatim: "it should have a check box asking if the cash envelope was opened", alongside
-- "if the declared cash pick by the dm is less then the sheet does not update the actual cash
-- picked up, it only shows the envelope amount".
--
-- The two are one defect. mig 949 gave the DM a place to record the ACTUAL cash taken out of the
-- envelope, but recording it was OPTIONAL and nothing ever asked for it — so a DM who opened an
-- envelope, counted it, and found it short could confirm the pickup having recorded nothing, and
-- the row then reads "not recorded" with only the declared envelope amount standing. An opened
-- envelope that records no count is exactly that hole.
--
-- This column is the DM's own statement about what they physically did:
--   FALSE / NULL — the envelope was collected SEALED. Nobody counted it, so the DECLARED envelope
--                  amount stands, and that is honest: absence of a count, not a count of zero. The
--                  envelope goes on to management's own count (mig 936 envelope_count) as always.
--   TRUE         — the DM opened and counted it. The count is then REQUIRED before the pickup can
--                  be confirmed (enforced in closing/router._confirm_pickup_impl via the pure
--                  pickup_actual.opened_without_count), and the short/over variance recorded
--                  against it is a counted fact rather than a blank.
--
-- WHY NOT INFER IT from "actual_picked_amount IS NOT NULL": that conflates two different states.
-- A blank count on a sealed envelope is CORRECT (nothing was opened); a blank count on an opened
-- one is a MISSING FACT. Without this column the two are indistinguishable, which is precisely why
-- the short cash went unrecorded. The flag is the DM's assertion; the amount is the evidence.
--
-- WHAT SHIPS HERE (all additive, both nullable, no default backfill of TRUE anywhere):
--   1. commcalc.cash_pickup.envelope_opened    — the flag on the general cash envelope.
--   2. commcalc.billpay_pickup.envelope_opened — the sibling mirror (mig 942: "the same process
--      same wiring" — the confirm machinery is ONE parameterized implementation, so the bill-pay
--      side gets the column for free and stays shape-compatible; a divergent shape here would
--      immediately break the shared writer).
--
-- MONEY: nothing here moves a booked number, for any org. This column never relieves cash and is
-- never summed. Whether the actual count (mig 949) relieves the general cash movement remains the
-- pre-existing, still-default-false cash_pickup_config.pickup_actual_relieves_cash knob — this
-- migration does not touch it. All existing rows read FALSE ("collected sealed"), which is exactly
-- how every pickup recorded before today behaved.
--
-- ADAPTIVE BY CONSTRUCTION: the code reads AND writes this column defensively (the mig-201
-- product_mrc retry-without-the-column precedent) — an un-migrated database simply never records
-- the flag, the confirm gate never fires, and the pickup screen behaves exactly as it does today.
-- Running this migration is what turns the checkbox into a requirement; not running it changes
-- nothing.
--
-- Additive + idempotent. RLS: columns on existing open_all tables (mig 034/942) — unchanged.
-- Run in the Supabase SQL editor.

ALTER TABLE commcalc.cash_pickup
  ADD COLUMN IF NOT EXISTS envelope_opened BOOLEAN;
COMMENT ON COLUMN commcalc.cash_pickup.envelope_opened IS
  'Did the DM OPEN this cash envelope at pickup (owner 2026-09-08)? TRUE = opened and counted — '
  'actual_picked_amount (mig 949) is then REQUIRED to confirm the pickup, so an opened envelope '
  'can never be recorded without its count. FALSE/NULL = collected sealed; the declared `amount` '
  'snapshot stands and the envelope goes on to management''s own count (mig 936 envelope_count). '
  'Deliberately NOT inferred from actual_picked_amount IS NOT NULL: a blank count on a sealed '
  'envelope is correct, a blank count on an opened one is a missing fact. Never relieves cash and '
  'is never summed. See index 23p.';

ALTER TABLE commcalc.billpay_pickup
  ADD COLUMN IF NOT EXISTS envelope_opened BOOLEAN;
COMMENT ON COLUMN commcalc.billpay_pickup.envelope_opened IS
  'Sibling mirror of cash_pickup.envelope_opened (mig 942 shared confirm machinery): did the DM '
  'open this bill-payment envelope at pickup? TRUE requires actual_picked_amount to confirm. '
  'FALSE/NULL = collected sealed. Never relieves cash and is never summed.';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 990 complete — envelope_opened on cash_pickup + billpay_pickup' AS status;

-- REVERT:
--   ALTER TABLE commcalc.cash_pickup    DROP COLUMN IF EXISTS envelope_opened;
--   ALTER TABLE commcalc.billpay_pickup DROP COLUMN IF EXISTS envelope_opened;
--   (Safe at any time. Every read is adaptive — pickup_actual.envelope_opened resolves a missing
--    column to False, so the confirm gate stops firing and both pickup screens fall back to the
--    optional-count behavior that shipped with mig 949. The confirm writer includes the key only
--    when the client sends it, and retries WITHOUT it if the column is gone, so pickups keep
--    recording either way. Nothing that was already recorded is lost or re-interpreted: a row that
--    carried envelope_opened = TRUE also carries its actual_picked_amount, which survives.)
