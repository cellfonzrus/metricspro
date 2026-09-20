-- 1008_sign_convention_netted.sql — the THIRD named sign convention: negative-earned, chargebacks netted.
--
-- WHY. The onboarding intake (design §3.4) asks one plain question — "In this file, is money you
-- EARNED positive or negative?" — and BOTH answers book a reversal NEGATIVE into the bucket it
-- reverses (design §3.6: the same money coming back is never abs()-ed into earnings and never a
-- sixth bucket). Mig 1006's vocabulary has no name for "earned is negative AND a positive is a
-- chargeback that nets off": `payout_negative` deliberately treats the opposite sign as a CHARGE (a
-- different money stream — the master-agent feeds it describes), which is right for those feeds and
-- wrong for a statement written negative-earned with positive deactivation clawbacks: that file
-- would either inflate (abs) or leave its chargebacks outside the buckets and never tie out.
--
-- So one more NAMED behaviour, not a free knob: `payout_negative_netted` = payout_sign −1 with
-- reversal handling 'signed'. Declared, like the other two, on the AMOUNT column's mapping row
-- (commcalc.column_mapping.sign_convention) per (org, report, carrier) — RULE TWO holds; no carrier,
-- tenant or product is named here or in the code that reads it (commission_ledger.CONVENTIONS).
--
-- ADDITIVE, IDEMPOTENT, A NO-OP ON EVERY EXISTING ROW. Only the CHECK constraint's allowed set grows;
-- NULL still means the long-standing negative-is-payout reading. Nothing is backfilled and nothing
-- is recomputed: a convention is declared by a tenant's own 3.4 answer and takes effect on THAT
-- report's next import only. Written and NOT applied: until it runs, a commit that answers "earned
-- is negative" is REFUSED with a message naming this file (the DB rejects the value), never written
-- as something else.
--
-- REVERT: ALTER TABLE commcalc.column_mapping DROP CONSTRAINT IF EXISTS column_mapping_sign_convention_ck;
--         ALTER TABLE commcalc.column_mapping ADD CONSTRAINT column_mapping_sign_convention_ck
--           CHECK (sign_convention IS NULL OR sign_convention IN ('payout_negative', 'payout_positive'));
--         (Only after clearing any row declared 'payout_negative_netted'; revert together with the
--          code that reads it, else those rows silently fall back to the charge reading.)

ALTER TABLE commcalc.column_mapping
  ADD COLUMN IF NOT EXISTS sign_convention TEXT;   -- no-op after 1006; keeps this file single-line-safe

ALTER TABLE commcalc.column_mapping DROP CONSTRAINT IF EXISTS column_mapping_sign_convention_ck;
ALTER TABLE commcalc.column_mapping
  ADD CONSTRAINT column_mapping_sign_convention_ck
  CHECK (sign_convention IS NULL OR sign_convention IN ('payout_negative', 'payout_positive', 'payout_negative_netted'));

COMMENT ON COLUMN commcalc.column_mapping.sign_convention IS
  'For an AMOUNT column only (transform = number): which sign of this column is money EARNED by us. payout_negative = a negative is earned and a positive is a charge to us (the long-standing default, and what NULL means). payout_positive = a positive is earned and a negative is a chargeback that nets off in the same bucket. payout_negative_netted = a negative is earned and a positive is a chargeback that nets off (the onboarding intake''s "earned is negative" answer, mig 1008). Read by commission_ledger.convention_from_mapping(); nothing is backfilled.';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1008 complete — column_mapping.sign_convention admits payout_negative_netted ('
       || (SELECT count(*) FROM commcalc.column_mapping WHERE sign_convention = 'payout_negative_netted')
       || ' row(s) declared; nothing backfilled)' AS status;
