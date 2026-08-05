-- 277_commission_leg_ma_margin_not_m1.sql
-- CORRECTION to migration 274: the VidaPay/master-agent activation-order MARGIN columns are NOT the
-- 1st-month COMMISSION leg.
--
-- OWNER-REPORTED 2026-08-05: the Gross Profit report's "1st Month" commission read ~$124k while the
-- VidaPay portal states M1 ~$28k for the same period — a 4.4x overstatement. The gap is exactly the
-- margin block: migration 274 seeded
--     ma_m1_fields = {rebate, device_margin, consumer_margin, consumer_financing, wallet_funding,
--                     fees_margin}
-- which forces every one of those columns into the M1 bucket ON TOP OF spiff_m1.
--
-- WHY THAT SEED WAS WRONG (three things in the product already said so):
--   1. OWNER, verbatim 2026-08-04 (recorded in commcalc/ma_overview.py): "commission is only the
--      current months commission paid out on the activations which would be M1, these are not margins
--      but paid commission based on MRC." The /commcalc/ma-overview-recon tile "Commissions Paid (M1)"
--      therefore reads `spiff_m1` ALONE, and was live-verified at $17,140.91 for luxelink Feb-Jul 2026.
--   2. The canonical Commission Ledger's map of the SAME twelve raw_ma_commission columns
--      (commcalc/ledger_ma_sync.py DEFAULT_COMPONENTS) carries `payment_month: NULL` on all six margins
--      and `payment_month: 1` on spiff_m1 only.
--   3. VidaPay's own "Overview of Accounts" report states Rebates Paid and Fees Margin Paid as their
--      OWN tiles, separate from Commissions Paid — so putting them in M1 double-counts them against
--      the very figure the owner cross-checks against.
--
-- 💰 MOVES NO PAYOUT. Reporting attribution only. Nothing here feeds _run_calculation, rep_commissions,
-- the plan/installment engines, the P&L or any rate/tier/rule. No rep's pay changes by one cent.
-- The GROSS PROFIT REPORT'S COMMISSION COLUMN TOTAL DOES NOT MOVE EITHER: the six margin columns stay
-- in that total, they just stop being counted as the 1st-month leg and sit in the honest `unsplit`
-- bucket beside it (m1 + trailing + unsplit == the column total, before and after — the sum identity
-- migration 274 established is preserved by construction).
--
-- EFFECT ON THE NUMBERS THE OWNER READS (Total/VidaPay tenants only; ePay/Boost orgs are unaffected
-- because the ePay path splits on the payment-type label, not on these columns):
--     1st Month  ->  Sigma(spiff_m1)                              (matches the portal's Commissions Paid)
--     M2-M12     ->  Sigma(spiff_m2..spiff_m6)                    (unchanged)
--     Unsplit    ->  + Sigma(the six margin columns)              (was silently inside 1st Month)
--     Commission ->  unchanged
--
-- An org that genuinely wants the margins counted as 1st-month money puts them back in `ma_m1_fields`
-- (per org and carrier, POST /commcalc/commission-leg-config) — the behaviour is still available, it
-- is simply no longer the default.
--
-- SAFE: additive + idempotent + re-runnable. No new table, no new column, no backfill of any data
-- table, no GRANT, no policy. Degrades to a no-op if migration 274 has not been run.

DO $$
BEGIN
  IF to_regclass('commcalc.commission_leg_config') IS NULL THEN
    RAISE NOTICE '277: commcalc.commission_leg_config does not exist (migration 274 not run) — nothing to do; the code default is already corrected.';
    RETURN;
  END IF;

  -- 1. New column default for any row created from here on.
  EXECUTE $ddl$ALTER TABLE commcalc.commission_leg_config
               ALTER COLUMN ma_m1_fields SET DEFAULT '{}'::text[]$ddl$;

  -- 2. Clear the six-margin set from every row that still carries EXACTLY the migration-274 seed.
  --    Order-insensitive and exact-set: a row an admin has deliberately customised (any other set) is
  --    left alone. The admin UI never exposed this field, so an exact match IS the bad seed.
  --    Re-running this finds zero rows to change.
  UPDATE commcalc.commission_leg_config
     SET ma_m1_fields = '{}'::text[],
         notes = COALESCE(NULLIF(notes, ''), '') ||
                 ' | 2026-08-05 (mig 277): activation-order margins removed from the 1st-month leg — ' ||
                 'only spiff_m1 is the MA M1 commission leg (owner 2026-08-04: "these are not margins ' ||
                 'but paid commission based on MRC"). The margins remain in the commission TOTAL, in ' ||
                 'the unsplit bucket.',
         updated_at = NOW()
   WHERE ma_m1_fields IS NOT NULL
     AND (SELECT array_agg(x ORDER BY x) FROM unnest(ma_m1_fields) AS x)
         = ARRAY['consumer_financing','consumer_margin','device_margin','fees_margin','rebate','wallet_funding']::text[];

  RAISE NOTICE '277: ma_m1_fields default cleared; % seeded row(s) corrected.',
               (SELECT count(*) FROM commcalc.commission_leg_config WHERE ma_m1_fields = '{}'::text[]);
END $$;

COMMENT ON COLUMN commcalc.commission_leg_config.ma_m1_fields IS
  'EXTRA raw_ma_commission columns to force into the 1st-month leg, ON TOP OF spiff_m1. EMPTY by default (mig 277): on the VidaPay/MA export only spiff_m1 is the M1 commission leg — the activation-order margin columns (rebate, device_margin, consumer_margin, consumer_financing, wallet_funding, fees_margin) are not commission and the portal states them separately, so they stay in the commission total but in the unsplit bucket. Listing a column here is a deliberate per-org override.';
