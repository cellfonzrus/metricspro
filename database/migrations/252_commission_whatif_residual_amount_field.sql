-- 252_commission_whatif_residual_amount_field.sql
-- Corrects the What-If residual $ column (mig 209 seed + column DEFAULT) and adds the MA-commission
-- sign key. Band 200-299 (mod-commission). ADDITIVE + IDEMPOTENT + a NO-OP if the owner already ran the
-- equivalent UPDATE by hand (finance handoff 2026-07-30, §③ statement #6).
--
-- WHY (root cause, 2026-07-30): commcalc.raw_ma_daily_tx has three NUMERIC columns and one of them is
-- not money at all — `merchant_invoice` is the Merchant Invoice NUMBER (the MA column catalogue in
-- ma_upload.FIELD_LABELS gives it role "key"; mig 083 declared it NUMERIC and mig 207 maps the file's
-- "Merchant Invoice" header into it). mig 209 seeded `residual_amount_field = 'merchant_invoice'`, so the
-- What-If page summed a few hundred INVOICE NUMBERS as dollars and reported -$492,946,277,716 of May-2026
-- "residual". `retail_cost` is the correct column: it is the SAME signed column the canonical Commission
-- Ledger books its MA payout lines from (column_mapping.py maps the "Retail Cost" header onto the
-- ledger's raw_amount, negative = payout), which is why the ledger's figures were believable while the
-- residual view's were not.
--
-- NOTHING WAS EVER PAID FROM THIS. The What-If page is read-only: no payout, commission, journal or
-- ledger row derives from the figure. No re-ingest is needed either — the raw tables faithfully store the
-- file's Merchant Invoice column; only the READ picked the wrong one.
--
-- SAFE / RE-RUNNABLE:
--   * the UPDATE is filtered on `residual_amount_field = 'merchant_invoice'`, so a second run (or a run
--     after the owner's manual statement) matches ZERO rows and appends no duplicate note;
--   * the column add is `IF NOT EXISTS`;
--   * everything is wrapped in a table-exists guard, so it cannot fail on an install where mig 209 has
--     not run yet (a fresh install runs 209 first, then this);
--   * NO grants, NO policies, NO anon/authenticated exposure (contract §5).
--
-- CODE AGREEMENT: whatif.py's code defaults are 'retail_cost' for BOTH modes and `ma_commission_sign`
-- defaults to 'negate' in code, so the module behaves correctly even before this migration runs (it
-- degrades to the code defaults) and identically after.

DO $mig252$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'commcalc' AND table_name = 'whatif_source_config'
  ) THEN
    RAISE NOTICE 'mig 252: commcalc.whatif_source_config absent (mig 209 not run) — nothing to do';
    RETURN;
  END IF;

  -- 1) the column DEFAULT for any NEW config row (mig 209 set it to the identifier column)
  EXECUTE 'ALTER TABLE commcalc.whatif_source_config
             ALTER COLUMN residual_amount_field SET DEFAULT ''retail_cost''';

  -- 2) MA-commission sign, config-driven like residual_sign. On the MA Commission Details export
  --    NEGATIVE = paid to the dealer, so the default normalizes to income ('negate') — the same
  --    convention /ma-commission/summary, account.residual_subs._aggregate_ma and coa.build_inputs
  --    already use. A tenant whose export arrives positive sets 'as_is' on its own carrier row.
  EXECUTE 'ALTER TABLE commcalc.whatif_source_config
             ADD COLUMN IF NOT EXISTS ma_commission_sign TEXT NOT NULL DEFAULT ''negate''';

  -- 3) fix the seeded rows (and any org override still pointing at the identifier column).
  --    NO-OP when the owner already ran the manual statement.
  EXECUTE $upd$
    UPDATE commcalc.whatif_source_config
       SET residual_amount_field = 'retail_cost',
           updated_at = NOW(),
           notes = COALESCE(notes, '') ||
                   ' [mig 252 / 2026-07-31: residual $ column corrected merchant_invoice -> retail_cost. '
                   'merchant_invoice is the Merchant Invoice NUMBER (ma_upload role "key"); summing it '
                   'reported -$492,946,277,716 of May-2026 residual on the What-If page. retail_cost is '
                   'the same signed column the canonical Commission Ledger books from.]'
     WHERE residual_amount_field = 'merchant_invoice'
  $upd$;

  -- 4) documentation on the columns themselves (inside the guard: the table may not exist yet)
  EXECUTE $c1$
    COMMENT ON COLUMN commcalc.whatif_source_config.residual_amount_field IS
      'Which raw_ma_daily_tx MONEY column carries the residual $. Use retail_cost (signed; the column the Commission Ledger books from) or merchant_discount (airtime margin). NEVER merchant_invoice — that is the Merchant Invoice NUMBER, an identifier stored NUMERIC; summing it reported -$492,946,277,716 of "residual" (2026-07-30). whatif.py flags an identifier column loudly on the page instead of pretending it is money.'
  $c1$;
  EXECUTE $c2$
    COMMENT ON COLUMN commcalc.whatif_source_config.ma_commission_sign IS
      'Sign normalization for raw_ma_commission money columns (M1-M6 spiffs, rebate) on the carrier-income + BYOD-residual views: negate | as_is | abs. Default negate — the MA Commission Details export is NEGATIVE when the amount is paid to the dealer.'
  $c2$;
END
$mig252$;
