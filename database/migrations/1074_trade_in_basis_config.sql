-- 1074_trade_in_basis_config.sql
-- TRADE-IN BASIS — "which record says a trade-in happened" becomes a DECLARED per-org fact.
--
-- Owner ruling 2026-10-12 (decision card, option "Point of sale"): the trade-in spiff counts
-- trade-ins as recorded at the till, not as recorded on the carrier statement.
--
-- THE CLASS, not the instance (owner directive 2026-09-20). The engine counted payment-detail rows
-- whose type contained 'trade', so a month the carrier had not reimbursed yet paid $0 for trade-ins
-- the stores really did. A carrier's reimbursement record is not a record of what the store did.
-- The code home is backend/app/modules/commcalc/trade_in_basis.py; these columns are the fact it
-- reads. RULE TWO: no carrier, tenant or department name appears in code.
--
-- ADDITIVE AND IDEMPOTENT. Every column is nullable with no default, so applying this migration
-- changes NOTHING by itself: `trade_in_basis IS NULL` resolves to the code's house default through
-- the allowlist, and the code default is the owner's ruling (`pos`). A tenant that wants the old
-- carrier-statement basis declares it explicitly.
--
-- MONEY NOTICE. Applying this file moves no money and alters no stored payout. What moves rep pay is
-- the next Run Calculation after the code ships, measured read-only on 2026-10-12 against the house
-- org (POS transactions valid per the engine's own void/Return filter, at each month's own
-- trade_in_spiff rate):
--     Jun 2026   0 till /  0 statement   $0 -> $0        +$0
--     Jul 2026   1 till /  0 statement   $0 -> $5        +$5
--     Aug 2026  30 till /  0 statement   $0 -> $150    +$150
--     Sep 2026  65 till / 50 statement  $1,000 -> $1,300  +$300   (a month already paid)
--     Oct 2026  53 till /  1 statement   $5 -> $265    +$260    across 12 reps
-- Back-pay for Jun-Oct was offered to the owner on the same decision card and NOT chosen, so no
-- stored row is touched here and no recompute is triggered by this file.
--
-- REVERT: the columns are additive and inert while NULL. To revert the BEHAVIOUR without dropping
-- anything, declare the historic basis explicitly:
--     UPDATE commcalc.accessory_config SET trade_in_basis = 'carrier'
--      WHERE org_id = '00000000-0000-0000-0000-000000000001';
-- To revert the SCHEMA (only if nothing has declared a basis):
--     ALTER TABLE commcalc.accessory_config
--       DROP COLUMN IF EXISTS trade_in_basis,
--       DROP COLUMN IF EXISTS trade_in_departments,
--       DROP COLUMN IF EXISTS trade_in_categories,
--       DROP COLUMN IF EXISTS trade_in_keywords,
--       DROP COLUMN IF EXISTS trade_in_payment_types;

BEGIN;

-- ── The declaration lives beside the accessory / setup-fee / ACIMA declarations it is a sibling of.
-- accessory_config is already the per-org home for "which departments, categories and product words
-- mean X" (mig 208 departments/categories/product_keywords, mig 094 acima_tenders, mig 217
-- setup_fee_keywords, mig 218 box_departments). A new table would be a second home for the same
-- kind of fact — the duplicate defect the index rules forbid — so this EXTENDS that row.
ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS trade_in_basis         text,
  ADD COLUMN IF NOT EXISTS trade_in_departments   text[],
  ADD COLUMN IF NOT EXISTS trade_in_categories    text[],
  ADD COLUMN IF NOT EXISTS trade_in_keywords      text[],
  ADD COLUMN IF NOT EXISTS trade_in_payment_types text[];

-- The allowlist is enforced in code (trade_in_basis.resolve_basis, an allowlist so that an unruled
-- value reads as the house default rather than becoming a third behaviour). This constraint is the
-- same answer at the schema boundary, so a hand-written UPDATE cannot store a value the engine will
-- silently ignore. NULL stays legal and means "not declared".
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conname = 'accessory_config_trade_in_basis_chk'
       AND conrelid = 'commcalc.accessory_config'::regclass
  ) THEN
    ALTER TABLE commcalc.accessory_config
      ADD CONSTRAINT accessory_config_trade_in_basis_chk
      CHECK (trade_in_basis IS NULL OR trade_in_basis IN ('pos', 'carrier', 'higher'));
  END IF;
END $$;

COMMENT ON COLUMN commcalc.accessory_config.trade_in_basis IS
  'Which record pays the trade-in spiff: pos (the till) | carrier (the statement) | higher. NULL = '
  'the house default, which is pos (owner ruling 2026-10-12). One home: commcalc/trade_in_basis.py.';
COMMENT ON COLUMN commcalc.accessory_config.trade_in_departments IS
  'Sales-feed department values that mean "trade-in", matched case-INSENSITIVELY. NULL/empty = the '
  'code default. Deliberately case-folded: box_departments matching is case-sensitive and the index '
  'records that as a live defect; this column does not inherit it.';
COMMENT ON COLUMN commcalc.accessory_config.trade_in_categories IS
  'Sales-feed category values that mean "trade-in" (case-insensitive). NULL/empty = the code default.';
COMMENT ON COLUMN commcalc.accessory_config.trade_in_keywords IS
  'Product-description SUBSTRINGS that mean "trade-in" (case-insensitive). Empty by default: a '
  'substring test is the loosest of the three and stays off until a tenant asks for it.';
COMMENT ON COLUMN commcalc.accessory_config.trade_in_payment_types IS
  'Carrier payment_type SUBSTRINGS that mean "trade-in reimbursement", used by the carrier and '
  'higher bases. NULL/empty = the historic substring, preserved exactly.';

COMMIT;
