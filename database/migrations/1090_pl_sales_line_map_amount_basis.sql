-- 1090_pl_sales_line_map_amount_basis.sql
--
-- OWNER RULING 2026-10-20, verbatim: "commission and anythig like margin which comes in considered
-- as income other than the money reimbursed towards the phone cost as subsidy".
--
-- WHAT THIS ADDS. One column: WHICH FIGURE a rule on commcalc.pl_sales_line_map books. The map
-- (mig 1022, index §37.4) routes a POS sale line no P&L classifier claims to a revenue line of the
-- chart, and it could only ever book `ext_price` — the GROSS the customer paid. For a whole family
-- of lines that is the wrong number:
--   · a wallet / airtime refill rings the CUSTOMER's money as ext_price and pays the store its
--     MARGIN in gp. Booking the gross would book pass-through money as income — the opposite of
--     what the ruling says;
--   · a plan activation rings ext_price ~0 (the customer pays the carrier) and the store's whole
--     earning is gp, so there was nothing bookable at all.
-- A rule now DECLARES its basis: 'ext' (a sale, or a fee the store charges, at full price) or
-- 'gp' (the store's margin on otherwise pass-through money).
--
-- NOTHING MOVES ON APPLY. The column defaults to 'ext', which is what every existing rule books
-- today, and today every org has ZERO rows in this table (measured live 2026-10-20), so no P&L
-- figure on any tenant changes when this is applied. Dollars move only when the owner saves a rule.
-- The code works BEFORE it is applied: the reader's select is column-tolerant and a missing column
-- resolves to 'ext' (account/sales_line_map.resolve_basis), and PUT /account/pl-sales-map refuses a
-- non-default basis naming this migration rather than silently saving the gross.
--
-- RULE TWO: no department, product, carrier or tenant name appears here. The basis is a value on
-- the ORG's own rule row.
--
-- Idempotent and additive. Index §37.11.
--
-- REVERT: ALTER TABLE commcalc.pl_sales_line_map DROP COLUMN IF EXISTS amount_basis;
--         (dropping it returns every rule to the gross basis — the pre-ruling behaviour.)

ALTER TABLE commcalc.pl_sales_line_map
    ADD COLUMN IF NOT EXISTS amount_basis TEXT NOT NULL DEFAULT 'ext';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'pl_sales_line_map_amount_basis_chk'
          AND conrelid = 'commcalc.pl_sales_line_map'::regclass
    ) THEN
        ALTER TABLE commcalc.pl_sales_line_map
            ADD CONSTRAINT pl_sales_line_map_amount_basis_chk
            CHECK (amount_basis IN ('ext', 'gp'));
    END IF;
END $$;

COMMENT ON COLUMN commcalc.pl_sales_line_map.amount_basis IS
    'Which figure this rule books: ext = Sigma ext_price (the gross the customer paid); gp = Sigma gp '
    '(the store MARGIN on otherwise pass-through money). Owner ruling 2026-10-20, index 37.11. '
    'Resolved by account/sales_line_map.resolve_basis; house default ext.';
