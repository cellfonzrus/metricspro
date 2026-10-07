-- 1061_labour_vocabulary_mode.sql
-- Owner report 2026-10-07 (Sanjot): "the finance module is doubling the salaries, it is appearing in
-- the store expenses and also in separate line as the wages/ hourly payroll".
--
-- THE PER-ORG OPT-OUT for the platform's own labour-row vocabulary.
--
-- The fix itself needs NO migration: `commcalc/labour_vocabulary.resolve` reads an absent column as
-- 'house' (the correct default), so the double-count stops on deploy for every org. This column adds
-- only the honest way to switch the mechanism OFF for one org:
--
--   'house' (DEFAULT, and what every existing row means) — where the org has named no payroll /
--           commission expense rows of its own, the platform's shipped labour rows apply, so a row
--           the platform itself auto-fills is not also re-derived onto its own P&L line.
--   'off'                                               — nothing is suppressed; reproduces the
--           pre-2026-10-07 behaviour for this org exactly.
--
-- An org that has named its OWN rows in `payroll_expense_names` /
-- `labour_commission_expense_names` is unaffected by this column: an explicit vocabulary wins
-- wholesale under every mode but 'off'.
--
-- RULE TWO: a per-tenant CONFIG ROW, never per-tenant code. No tenant, carrier, company or expense
-- name appears in this migration.
--
-- Additive and idempotent. Changes NO stored figure on its own: statements only move when the owner
-- recomputes a period (see the PR for which periods and by how much).
--
-- REVERT: ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS labour_vocabulary_mode;
--         (dropping it restores the 'house' default, which is the behaviour the code ships.)

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS labour_vocabulary_mode text NOT NULL DEFAULT 'house';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conname = 'account_config_labour_vocabulary_mode_chk'
  ) THEN
    ALTER TABLE commcalc.account_config
      ADD CONSTRAINT account_config_labour_vocabulary_mode_chk
      CHECK (labour_vocabulary_mode IN ('house', 'off'));
  END IF;
END $$;

COMMENT ON COLUMN commcalc.account_config.labour_vocabulary_mode IS
  'How the platform''s own labour-row vocabulary applies to this org (commcalc/labour_vocabulary.py). '
  '''house'' (default): where the org named no payroll/commission expense rows of its own, the '
  'platform''s shipped labour rows apply, so a row the platform auto-fills into the Expenses sheet is '
  'not counted again on the wages / rep_comm P&L lines. ''off'': nothing is suppressed (pre-2026-10-07 '
  'behaviour). An explicit per-org vocabulary always wins wholesale.';
