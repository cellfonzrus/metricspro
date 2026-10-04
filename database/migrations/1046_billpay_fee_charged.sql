-- 1046_billpay_fee_charged.sql — DOES THIS TENANT CHARGE A BILL-PAYMENT FEE? A DECLARED fact.
--
-- OWNER ASK 2026-10-03, verbatim: "if lucelink has no fee then we should build a user defined line
-- if tehy take fee for bill payments or not and if they dont it should calculate accordingly so
-- there is not balmnket error on a different tenant … all user defnined platform wide no hardcoding".
--
-- THE CLASS OF DEFECT THIS CLOSES (not the instance that surfaced it). Migration 1045 made the fee
-- VOCABULARY per-org, which answers "what is this tenant's fee line called". It cannot answer "does
-- this tenant have one at all", and that is the question the comparison actually depends on. With
-- the vocabulary alone, a store-day carrying no fee leg is three different facts wearing one face:
-- the tenant charges no fee (the basis is right); the tenant charges one and the feed did not ring it
-- (the basis is understated and every store-day reads as under-declared); or nobody has said which.
-- The code was guessing, and a guess made from one tenant's data becomes another tenant's blanket
-- accusation. So the question is ASKED, once, per org.
--
-- WHAT IT CHANGES, AND WHAT IT DELIBERATELY DOES NOT. No arithmetic moves: the POS basis is still
-- the bill lines' cash leg plus whatever fee leg actually rang, added in ONE place
-- (commcalc/metric_recon.pos_billpay_cash). What this value changes is whether that basis may be
-- COMPARED against a rep's declaration, and whose problem it is when it may not:
--   'no'       a store-day with bill-pay activity and no fee line is EXPECTED. Compared normally.
--              The tenant stops being judged by another tenant's fee wording.
--   'yes'      the same store-day is a FEED DEFECT — the basis is understated, so it is NOT compared
--              and NOT charged to the rep; it is counted and reported as a data-feed gap.
--   'unknown'  (the default) nobody has answered, so the store-day is not compared either, and the
--              digest says so and names the remedy. An unanswered question is not answered by code.
-- A fee line that rings while the policy says 'no' is still real cash in the drawer, so the
-- arithmetic stands and the CONFIG ROW is what gets reported.
--
-- MEASURED 2026-10-03, read-only, September 2026, through the system's own helpers:
--   • The house org rings its fee on 736 of 772 store-days, $14,184 in the month. Removing the leg
--     collapses agreement from 348 store-days to 20 — the fee is real and load-bearing there.
--   • The other live tenant rings it on 0 of 371 store-days, and recomputing the month with the fee
--     leg removed leaves the classification IDENTICAL. Declaring its policy therefore moves not one
--     cent of its $57,905 September gap. That gap has a different, real cause which this migration
--     does not claim to fix and does not hide: 217 of its 373 September closings declare bill-pay
--     cash of $0.00 against real POS bill-pay activity.
--
-- WHY A CONFIG ROW AND NOT CODE (RULE TWO, and the no-patchwork directive). A tenant name in a
-- branch would fix the tenant that surfaced this and leave every future one exactly as wrong. The
-- value is per-org with a house default, resolved in ONE place
-- (commcalc/metric_recon.resolve_fee_policy / billpay_fee_state / billpay_basis_comparable) which
-- every caller dereferences; harness_billpay_fee_basis.py section J FAILS THE BUILD if a caller
-- spells a policy value or re-derives which states are comparable.
--
-- 'unknown' DEFAULT = NOTHING SILENTLY CHANGES FOR AN UNCONFIGURED TENANT except that a store-day
-- nobody can judge stops being reported as a rep's error. The reader has its own defensive read (the
-- mig-313 / mig-944 / mig-1045 posture), so a pre-1046 schema, a missing row or any failure resolves
-- to 'unknown' and the report works before this migration is applied.
--
-- Additive, idempotent, no backfill, no data movement, NO MONEY MOVED and no money column written.
-- REVERT: ALTER TABLE commcalc.accessory_config DROP COLUMN IF EXISTS billpay_fee_charged;
--         (Dropping it resolves every org to 'unknown', which is the shipped default.)

ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS billpay_fee_charged TEXT NOT NULL DEFAULT 'unknown';

-- Pick-don't-type at the database boundary too: a typo in the SQL Editor must not become a third
-- behaviour nobody coded for. The resolver already treats an unrecognised value as 'unknown', so
-- this CHECK is belt-and-braces rather than the only guard.
DO $fee$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'accessory_config_billpay_fee_charged_chk') THEN
    ALTER TABLE commcalc.accessory_config
      ADD CONSTRAINT accessory_config_billpay_fee_charged_chk
      CHECK (billpay_fee_charged IN ('yes', 'no', 'unknown'));
  END IF;
END
$fee$;

COMMENT ON COLUMN commcalc.accessory_config.billpay_fee_charged IS
  'Does this org charge customers a BILL-PAYMENT SERVICE FEE? ''yes'' / ''no'' / ''unknown'' '
  '(the default — nobody has answered). Declared, never inferred from the data: a store-day with '
  'bill-pay activity and no fee line means the basis is right under ''no'', is a FEED DEFECT under '
  '''yes'', and is unassessable under ''unknown''. Resolved in ONE home, '
  'commcalc/metric_recon.resolve_fee_policy / billpay_fee_state / billpay_basis_comparable; every '
  'caller dereferences it and no caller spells a policy value. Set from Sales Report → '
  'Classification settings (PUT /api/v1/commcalc/accessory-config). Changes no arithmetic: it '
  'changes whether the POS basis may be compared against a declaration, and whose problem it is '
  'when it may not be.';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 1046 complete — commcalc.accessory_config.billpay_fee_charged installed '
       '(per-org; default ''unknown'', so no tenant is blanket-judged by another tenant''s fee '
       'vocabulary and no unanswered question is answered by code).' AS status;
