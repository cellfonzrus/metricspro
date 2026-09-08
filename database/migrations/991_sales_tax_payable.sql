-- 991_sales_tax_payable.sql — SALES TAX ON THE BALANCE SHEET, and the built-in POS's missing
-- tax column.
--
-- Owner directive, 2026-09-08, verbatim: "now the sales tax in p&l".
--
-- ── WHAT WAS WRONG (measured live, house org, org-scoped reads 2026-09-07/08) ──────────────────
-- Retail sales tax reached NO P&L and NO balance-sheet line at all:
--   * account/coa.py `_sales_union_rows` projects
--     "trans_id,department,category,product_desc,ext_price,gp,voided,store" — `tax` is not in the
--     select, so `build_inputs` could not see a cent of it;
--   * PL_SPEC (24 lines), BS_SPEC and balance_sheet.EXTRA_BS_SPEC carried no tax line. The only
--     "tax" in account/ is PAYROLL tax (liabilities_due.py).
-- Cumulative uncaptured: $41,239.46 — June $13,527.70, July $14,031.80, August $13,679.96 (net of
-- returns; May and earlier carry no Tax column). August's tax alone is 2.02x that month's reported
-- net income of $6,796.81.
--
-- REVENUE IS NOT WRONG AND IS NOT RESTATED. Re-running the P&L's own classifiers over all 24,890
-- August raw_sales rows reproduces device_rev $78,735.24 and service_income $17,088.00 TO THE CENT
-- against the live snapshot (accessory_rev within $103.68); the tax-INCLUSIVE alternative is
-- $6,552 / $7,123 away. Revenue is already pre-tax. The gap is a MISSING LIABILITY — the state's
-- money, held by the tenant — so nothing on the P&L moves.
--
-- WHY IT MATTERS EVEN THOUGH THE SHEET "BALANCES" TODAY: `cash` is manual and reads 0.00,
-- `account_config.cash_on_hand_basis` is NULL ('off'), and commcalc.journal_entries has 0 rows for
-- this org — so the cash side of these dollars is not on the sheet either and the omission is
-- currently invisible rather than misplaced. The moment mig 938's store cash-on-hand is switched
-- on, the tax dollars enter ASSETS with no matching liability and fall into `imbalance` /
-- retained earnings (account/engine.py _assemble), and account/valuation.py's asset-based floor is
-- overstated by the same amount.
--
-- ── WHAT THIS MIGRATION ADDS (no new table, no new ledger, no new derivation) ──────────────────
-- 1. TWO per-org columns on commcalc.account_config (the mig-611/933/938/954 finance-config table),
--    read by account/balance_sheet.load_bs_config and consumed by account/statement_engine:
--      sales_tax_basis          'off' | 'collected'.  DEFAULT 'off' = every org's books stay
--                               BYTE-IDENTICAL until it opts in (the mig-938/954 pattern).
--                               'collected' books sales tax COLLECTED AND NOT YET REMITTED as of
--                               the statement date, NET OF RETURNS, to the new balance-sheet
--                               liability line `sales_tax_payable` ("Sales tax payable (collected,
--                               not remitted)", auto_opt, store grain).
--      sales_tax_accrual_start  the date the collected-not-remitted balance starts accruing from.
--                               NULL = NOT DECLARED ⇒ statement_engine discovers the org's
--                               EARLIEST sale line carrying tax instead of guessing a fiscal
--                               policy; the meta always reports which source was used
--                               (accrual_start_source 'config' | 'earliest_taxed_sale'). A tenant
--                               that has been remitting since before this system holds its data
--                               sets this so the books do not claim a balance it already paid.
--    NOTHING is seeded. Turning the line on for an org is a deliberate, money-touching act and is
--    left to the owner (the statement below is provided, commented out, for when that is decided).
--
-- 2. `tax` on the two built-in-POS stream tables (mig 727) and in the promotion function's column
--    lists. pos/commcalc_feed.py built its promoted row with `ext_price` correctly tax-EXCLUSIVE
--    but never emitted a `tax` column at all, though pos.sales.tax_total / pos.sale_items.tax_value
--    exist and hold the money. Any tenant on the built-in POS therefore reported $0.00 sales tax
--    and would book a $0.00 liability. The external (b2bsoft) pipeline already has the column
--    (mig 105 added `tax` to raw_sales and daily_sales_feed) and is not touched.
--
-- REMITTANCE RELIEVES THE LINE THROUGH THE EXISTING LEDGER — no new mechanism. account/engine.py
-- `_assemble` already folds commcalc.journal_entries rows onto a spec line by LABEL, so a
-- remittance is a manual balance_sheet / liability entry labelled exactly
--   "Sales tax payable (collected, not remitted)"
-- carrying a NEGATIVE amount, with its cash side on the `cash` line. The booking function
-- deliberately does NOT net journal entries as well; doing both would relieve the liability twice.
--
-- ⚠ MONEY-TOUCHING, and gated behind the default. Applying THIS migration alone moves NOTHING:
--   `sales_tax_basis` defaults to 'off' for every org, `auto_opt` means the line does not even
--   render, and the two new columns are additive. Only the (commented-out) opt-in at the bottom
--   moves money, and only on the balance sheet: liabilities rise by up to $41,239.46 on the house
--   org, `imbalance` moves by the same amount, and account/valuation.py's asset-based floor falls
--   by it. REVENUE, GROSS PROFIT AND NET INCOME DO NOT MOVE — the booking writes only to a
--   balance-sheet line key (proven DB-free in backend/harness_balance_sheet_truths.py §J).
--
-- Proof: backend/harness_tax_collected.py (the shared aggregator, the store-key canonicalization
-- and the gross-vs-net contract) and backend/harness_balance_sheet_truths.py (the pure booking,
-- the 'off' byte-identity, and the P&L-untouched guarantee).
--
-- REVERT:
--   ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS sales_tax_basis;
--   ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS sales_tax_accrual_start;
--   ALTER TABLE commcalc.pos_builtin_daily_sales DROP COLUMN IF EXISTS tax;
--   ALTER TABLE commcalc.pos_builtin_sales       DROP COLUMN IF EXISTS tax;
--   -- and re-apply the mig-727 body of commcalc.pos_promote_period (its column lists without tax).
--   -- Dropping the columns only removes the CONFIG and the POS stream field; no ledger row is
--   -- deleted by this migration and none is deleted by reverting it.

-- ── 1. per-org sales-tax config ────────────────────────────────────────────────────────────────
ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS sales_tax_basis TEXT NOT NULL DEFAULT 'off';

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS sales_tax_accrual_start DATE;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'account_config_sales_tax_basis_ck') THEN
    ALTER TABLE commcalc.account_config
      ADD CONSTRAINT account_config_sales_tax_basis_ck
      CHECK (sales_tax_basis IN ('off', 'collected'));
  END IF;
END $$;

COMMENT ON COLUMN commcalc.account_config.sales_tax_basis IS
  'Sales-tax balance-sheet basis (mig 991). ''off'' (default) books nothing — byte-identical books. '
  '''collected'' books sales tax collected and NOT YET REMITTED, net of returns, to the '
  '`sales_tax_payable` liability line as of the statement date. Remittance relieves the line via a '
  'negative commcalc.journal_entries row labelled "Sales tax payable (collected, not remitted)".';

COMMENT ON COLUMN commcalc.account_config.sales_tax_accrual_start IS
  'Date the collected-not-remitted balance starts accruing from (mig 991). NULL = not declared: the '
  'statement engine discovers the org''s earliest sale line carrying tax instead of guessing a '
  'fiscal policy, and reports which source it used. Set this for a tenant that was already '
  'remitting before this system held its data, so the books do not claim a balance already paid.';

-- ── 2. the built-in POS stream carries its sales tax (mig 727 tables + promotion) ──────────────
ALTER TABLE commcalc.pos_builtin_daily_sales ADD COLUMN IF NOT EXISTS tax NUMERIC;
ALTER TABLE commcalc.pos_builtin_sales       ADD COLUMN IF NOT EXISTS tax NUMERIC;

COMMENT ON COLUMN commcalc.pos_builtin_daily_sales.tax IS
  'Retail sales tax for this line (pos.sale_items.tax_value). mig 991 — previously dropped by '
  'pos/commcalc_feed.py, so a built-in-POS tenant reported $0.00 sales tax. `ext_price` stays '
  'tax-EXCLUSIVE; this is carried beside it, matching commcalc.raw_sales.tax (mig 105).';
COMMENT ON COLUMN commcalc.pos_builtin_sales.tax IS
  'Retail sales tax for this line (pos.sale_items.tax_value). mig 991 — see the daily twin.';

-- The promotion function's column lists must carry `tax` or the ledger still lands without it.
-- This is the mig-727 body with `tax` added in both branches and NOTHING else changed: the
-- empty-abort guard, the source='pos_builtin'-scoped DELETE and the foreign-row refusal all stand
-- exactly as they were (owner constraint 2026-08-08 — "can't delete anything").
CREATE OR REPLACE FUNCTION commcalc.pos_promote_period(p_org UUID, p_period TEXT, p_mode TEXT)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = commcalc, public
AS $$
DECLARE
  v_count INT;
  v_foreign INT;
BEGIN
  IF p_mode = 'daily' THEN
    SELECT COUNT(*) INTO v_count FROM commcalc.pos_builtin_daily_sales
     WHERE org_id = p_org AND period = p_period;
    IF v_count = 0 THEN
      RAISE EXCEPTION 'built-in stream has no rows for % — aborting so the ledger period is not wiped', p_period;
    END IF;
    SELECT COUNT(*) INTO v_foreign FROM commcalc.daily_sales_feed
     WHERE org_id = p_org AND period = p_period AND source IS DISTINCT FROM 'pos_builtin';
    IF v_foreign > 0 THEN
      RAISE EXCEPTION 'daily_sales_feed already holds % row(s) for % this module did not write '
                      '(source IS NULL — external feed, historical import or manual upload). '
                      'Promotion refuses: deleting them would lose data, inserting beside them '
                      'would double-count. Resolve the overlap before promoting.', v_foreign, p_period;
    END IF;
    DELETE FROM commcalc.daily_sales_feed
     WHERE org_id = p_org AND period = p_period AND source = 'pos_builtin';
    INSERT INTO commcalc.daily_sales_feed
      (org_id, period, period_month, period_year, store, salesperson, user_login, contract_type,
       department, category, product_desc, product_id, gp, ext_price, tax, trans_id, trans_date,
       mdn, serial_1, register, tender_type, voided, trans_type, customer, email, customer_no,
       source)
    SELECT org_id, period, period_month, period_year, store, salesperson, user_login, contract_type,
           department, category, product_desc, product_id, gp, ext_price, tax, trans_id, trans_date,
           mdn, serial_1, register, tender_type, voided, trans_type, customer, email, customer_no,
           'pos_builtin'
    FROM commcalc.pos_builtin_daily_sales
    WHERE org_id = p_org AND period = p_period;
  ELSE
    SELECT COUNT(*) INTO v_count FROM commcalc.pos_builtin_sales
     WHERE org_id = p_org AND period = p_period;
    IF v_count = 0 THEN
      RAISE EXCEPTION 'built-in stream has no rows for % — aborting so the ledger period is not wiped', p_period;
    END IF;
    SELECT COUNT(*) INTO v_foreign FROM commcalc.raw_sales
     WHERE org_id = p_org AND period = p_period AND source IS DISTINCT FROM 'pos_builtin';
    IF v_foreign > 0 THEN
      RAISE EXCEPTION 'raw_sales already holds % row(s) for % this module did not write '
                      '(source IS NULL — external feed, historical import or manual upload). '
                      'Promotion refuses: deleting them would lose data, inserting beside them '
                      'would double-count. Resolve the overlap before promoting.', v_foreign, p_period;
    END IF;
    DELETE FROM commcalc.raw_sales
     WHERE org_id = p_org AND period = p_period AND source = 'pos_builtin';
    INSERT INTO commcalc.raw_sales
      (org_id, period, period_month, period_year, store, salesperson, user_login, department,
       category, product_desc, product_id, gp, ext_price, tax, trans_id, trans_date, contract_type,
       mdn, serial_1, register, tender_type, voided, trans_type, sku, source)
    SELECT org_id, period, period_month, period_year, store, salesperson, user_login, department,
           category, product_desc, product_id, gp, ext_price, tax, trans_id, trans_date, contract_type,
           mdn, serial_1, register, tender_type, voided, trans_type, sku, 'pos_builtin'
    FROM commcalc.pos_builtin_sales
    WHERE org_id = p_org AND period = p_period;
  END IF;
  RETURN v_count;
END;
$$;
REVOKE ALL ON FUNCTION commcalc.pos_promote_period(UUID, TEXT, TEXT) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION commcalc.pos_promote_period(UUID, TEXT, TEXT) TO service_role;

-- ── 3. THE OPT-IN — deliberately NOT applied ───────────────────────────────────────────────────
-- This is the money-touching half and is left for the owner to run knowingly, per org, after
-- recomputing the open periods. Uncommenting the first statement adds up to $41,239.46 of
-- liabilities to the house org's balance sheet (and changes `imbalance` and the valuation floor by
-- the same amount). It does NOT change revenue, gross profit or net income.
--
--   UPDATE commcalc.account_config SET sales_tax_basis = 'collected'
--    WHERE org_id = '00000000-0000-0000-0000-000000000001';
--
-- Optionally pin the accrual start instead of letting the engine discover the earliest taxed sale
-- (the house org's data starts carrying a Tax column in June 2026, so discovery yields 2026-06-01
-- and the two agree):
--
--   UPDATE commcalc.account_config SET sales_tax_accrual_start = DATE '2026-06-01'
--    WHERE org_id = '00000000-0000-0000-0000-000000000001';
