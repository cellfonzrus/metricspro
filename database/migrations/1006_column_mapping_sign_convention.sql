-- 1006_column_mapping_sign_convention.sql — WHICH SIGN IS MONEY EARNED becomes part of the MAPPING.
--
-- OWNER DIRECTIVE 2026-09-20: "the inverted sign should not be hard coded, it should be a part of
-- mapping — we should not be making new rules for <a carrier>, we should be able to test this as a
-- new carrier to be able to work with the system we built."
--
-- WHY. Every commission statement states the same money with its own sign. One master-agent feed
-- writes what it owes the dealer as a NEGATIVE (a credit) and the dealer's own purchases as
-- positives; another writes what it owes as a POSITIVE and a deactivation clawback as a negative.
-- The canonical ledger assumed the first, in code — `is_payout = amount < 0` — so a statement
-- written the other way up booked its EARNINGS as charges and its CHARGEBACKS as the payout, i.e.
-- exactly backwards, with a confident total on screen.
--
-- The existing `sign_rule` on commcalc.commission_category_map is NOT that switch and must not be
-- used as one: it decides whether a RULE may match a line pointing the "wrong" way, and a matched
-- line still booked abs(amount) — so a −$1,500 deactivation would book as +$1,500 EARNED. Direction
-- must be known BEFORE the magnitude is taken, which is what this column makes possible.
--
-- WHY HERE AND NOT A NEW TABLE. It is a fact about ONE COLUMN OF ONE FILE — the same thing
-- commcalc.column_mapping already records for every other column (which header feeds it, how to
-- coerce it), on the same key: (org_id, report_key, COALESCE(carrier_id,…), target_field). A new
-- carrier declares it once in the mapping wizard, beside the header it picked for the amount, and
-- every downstream number follows. No template constant, no carrier branch, no seeded rule — RULE
-- TWO holds, and this migration names no carrier, tenant or product.
--
-- ADDITIVE, IDEMPOTENT, AND A NO-OP ON EVERY EXISTING ROW. The column is nullable with no default.
-- NULL / '' means "not declared", which the code reads as the long-standing negative-is-payout
-- convention — byte-identical to today for ma_daily_tx, ma_commission, boost and every other feed
-- (proven DB-free by backend/harness_commission_ledger_sign.py). NOTHING is backfilled: declaring a
-- convention for an existing feed would MOVE MONEY and is a decision, not a migration.
--
-- MONEY NOTE. This migration moves no money on its own. It adds the ability to declare a convention;
-- a tenant declaring one on their own new report changes only that report's ledger, and only from
-- its next import/refresh — the stored rows of other feeds are not touched, read or recomputed.
--
-- REVERT: ALTER TABLE commcalc.column_mapping DROP COLUMN IF EXISTS sign_convention;
--         (Dropping it returns every mapping to the negative-is-payout reading. Any tenant that had
--          declared 'payout_positive' would silently invert again, so revert only together with the
--          code that reads it.)

ALTER TABLE commcalc.column_mapping
  ADD COLUMN IF NOT EXISTS sign_convention TEXT;

-- Only the two declared conventions, or NULL for "not declared". A CHECK (not an enum) so the
-- vocabulary can grow without a type migration; the code treats anything it does not recognise as
-- the default rather than inventing a third direction.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'column_mapping_sign_convention_ck') THEN
    ALTER TABLE commcalc.column_mapping
      ADD CONSTRAINT column_mapping_sign_convention_ck
      CHECK (sign_convention IS NULL OR sign_convention IN ('payout_negative', 'payout_positive'));
  END IF;
END $$;

COMMENT ON COLUMN commcalc.column_mapping.sign_convention IS
  'For an AMOUNT column only (transform = number): which sign of this column is money EARNED by us. payout_negative = a negative amount is money earned and a positive is a charge to us (the long-standing default, and what NULL means). payout_positive = a positive amount is money earned and a negative is a chargeback, which nets off against the same canonical bucket rather than being abs()-ed into earnings. Declared in the mapping wizard beside the header and the transform, per (org, report, carrier); read by commission_ledger.convention_from_mapping(). NULL on every pre-1006 row, and nothing is backfilled — declaring a convention is a money decision, not a migration.';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1006 complete — column_mapping.sign_convention ('
       || (SELECT count(*) FROM commcalc.column_mapping WHERE sign_convention IS NOT NULL)
       || ' column(s) declared; NULL = negative-is-payout, unchanged)' AS status;
