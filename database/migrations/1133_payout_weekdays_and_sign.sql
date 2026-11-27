-- 1133_payout_weekdays_and_sign.sql
--
-- §PAYABLES-02 — TWO THINGS A PAYOUT ARRANGEMENT COULD NOT SAY, both found the first time a real
-- tenant tried to declare one (owner, 2026-11-27).
--
-- OWNER, verbatim: "Boost pays every Tuesday and Friday" and "Vida pay settles every day with the
-- money owed to them and the the money owed to us".
--
-- ① A WEEKLY RHYTHM CAN LAND ON MORE THAN ONE DAY. `payout_weekday` holds ONE weekday, so "every
--    Tuesday and Friday" could not be declared at all — and it could not be faked with a second
--    declaration row either, because `rsm_org_source` allows one row per (org, source_table,
--    amount_field) precisely so a re-declaration cannot double a tenant's expected money. Declaring
--    only Tuesday would have placed Friday's receipts on the following Tuesday: a cash position
--    wrong by up to three days, every week, with nothing on screen admitting it. So the declaration
--    holds a SET. One declared day is the one-element case, not a different cadence, and
--    payables/billing_period.weekdays_of + next_weekday_in_on_or_after are the one home for both
--    readings (the single-day function now delegates to the set one, so the two cannot drift).
--
-- ② WHICH SIGN MEANS MONEY EARNED IS THE FEED'S CONVENTION, NOT A CONSTANT. Measured against
--    production 2026-11-27: commcalc.raw_ma_commission writes money owed TO the dealer as a
--    NEGATIVE across all twelve of its declared payout components. The expected-receipts read summed
--    the declared column raw, so declaring that feed would have reported roughly MINUS $45,000 a
--    week of incoming money — a tenant's income shown as a debt, on the one report whose purpose is
--    to say what is arriving. Flipping the sign inside the report would have been a second copy of a
--    convention that already has a home, and flipping it for that one feed by name would have been
--    the per-tenant code RULE TWO forbids. So the TENANT declares which convention its column
--    follows, in the vocabulary commcalc/commission_ledger.py already owns and the onboarding intake
--    already asks for ("in this file, is money you earned positive or negative?").
--
--    NULL is read as `payout_positive` — the column at FACE VALUE — not as the ledger's own
--    `payout_negative` default. That default is right where it lives, because it describes the
--    master-agent statements it was written for; here the declaration describes an arbitrary column
--    of an arbitrary feed, and taking the column at its word is the only reading that asserts
--    nothing about a carrier's export. Every declaration made before this migration keeps the exact
--    figure it produced, which is what makes the change provable.
--
-- WHY NOT THE SAME TWO COLUMNS ON commcalc.payable_source_map (the OUTBOUND twin). Deliberately
-- excused rather than forgotten. Outbound, `weekly` does not ask "which day does this dollar land
-- on" but "which seven days does this billing day cover" (billing_period.week_window), and several
-- billing days a week makes that window variable-length — a different question needing its own
-- design, not a column. No tenant declares the outbound weekly cadence today (the house org answers
-- off its feed's own stamped column and the other declares per_due_date), so the column would be one
-- nothing honours, which is worse than none. The shared arithmetic is already in place for the day
-- it is declared.
--
-- MONEY: this migration MOVES NO MONEY and writes no amount. It adds two declaration columns to a
-- table that holds commercial arrangements, both NULL for every existing row. The only figure that
-- can change is on a report, and only once a tenant declares something new.
--
-- ADDITIVE AND IDEMPOTENT. Safe to run more than once.
--
-- REVERT: ALTER TABLE commcalc.receipt_source_map
--           DROP CONSTRAINT IF EXISTS receipt_source_map_weekdays_ck,
--           DROP CONSTRAINT IF EXISTS receipt_source_map_sign_ck,
--           DROP COLUMN IF EXISTS payout_weekdays,
--           DROP COLUMN IF EXISTS amount_sign_convention;

ALTER TABLE commcalc.receipt_source_map
  ADD COLUMN IF NOT EXISTS payout_weekdays       integer[],
  ADD COLUMN IF NOT EXISTS amount_sign_convention text;

DO $$
BEGIN
  -- Every element a real weekday, 0 = Monday, matching date.weekday() so the stored number needs no
  -- translation. An EMPTY array is refused too: it reads as "a set was declared" while naming no
  -- day, which is indistinguishable on screen from a complete declaration that places nothing.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'receipt_source_map_weekdays_ck') THEN
    ALTER TABLE commcalc.receipt_source_map
      ADD CONSTRAINT receipt_source_map_weekdays_ck
      CHECK (payout_weekdays IS NULL
             OR (array_length(payout_weekdays, 1) >= 1
                 AND payout_weekdays <@ ARRAY[0, 1, 2, 3, 4, 5, 6]));
  END IF;
  -- The sign vocabulary, mirrored from commission_ledger.SIGN_CONVENTIONS. A value that home has
  -- never ruled on falls back to face value in the reader; refusing it at the column keeps the two
  -- from disagreeing about what is storable.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'receipt_source_map_sign_ck') THEN
    ALTER TABLE commcalc.receipt_source_map
      ADD CONSTRAINT receipt_source_map_sign_ck
      CHECK (amount_sign_convention IS NULL OR amount_sign_convention IN
             ('payout_negative', 'payout_positive', 'payout_negative_netted'));
  END IF;
END $$;

COMMENT ON COLUMN commcalc.receipt_source_map.payout_weekdays IS
  'Cadence weekly: EVERY weekday this carrier pays on, 0 = Monday. A set, because a carrier may pay '
  'more than once a week (the first declared here pays Tuesday and Friday). Read through '
  'payables/billing_period.weekdays_of; payout_weekday still answers for a row saved before this '
  'column existed, so one declared day needs no migration (§PAYABLES-02).';
COMMENT ON COLUMN commcalc.receipt_source_map.amount_sign_convention IS
  'Which sign of amount_field means money EARNED, in commission_ledger.SIGN_CONVENTIONS vocabulary: '
  'payout_negative / payout_positive / payout_negative_netted. NULL = face value (payout_positive), '
  'so a feed that writes earnings as a negative must say so — raw_ma_commission does (§PAYABLES-02).';

grant select, insert, update, delete on commcalc.receipt_source_map to service_role;
