-- 253_commission_whatif_income_source_ledger.sql
-- Point the MA-fed CARRIER-INCOME source at the canonical Commission Ledger (owner-authorised 2026-07-31).
--
-- WHY. The What-If "Company Payout / Carrier Income" tab took its COMMISSION and SPIFF headings from
-- `commcalc.raw_ma_commission` — Σ spiff_m1..m6 and Σ rebate. That is 2 of the 12 payout components that
-- report carries, and nothing at all from MA Daily Tx. `commcalc.commission_ledger` (mig 071, provenance
-- mig 251) is the canonical record of what a carrier/master-agent actually paid: every line classified
-- ONCE, by the tenant's own `commission_category_map`, into five canonical buckets, whatever file or raw
-- table it arrived on. mod-finance escalated the swap (finance handoff §④.3); the commission handoff filed
-- it as OPEN because it MOVES DISPLAYED DOLLARS.
--
-- WHAT MOVES. Only the Commission / Spiff / Equipment-rebate headings on that tab (and the derived
-- "TOTAL carrier income" / "Net to company" tiles). RESIDUAL and airtime margin keep reading
-- `raw_ma_daily_tx` exactly as before — the ledger books daily-tx payout lines too, so reading its
-- residual buckets there would double-count. Boost/ePay tenants are untouched: their income_source stays
-- 'boost_comp_mi_atu' (Comprehensive Comp + MI/ATU) and this file does not match those rows.
-- NOTHING anyone is PAID changes: this is a read-side analysis surface. rep_commissions, the calc, plans,
-- tiers and payout schedules are not referenced.
--
-- CODE WITHOUT THIS SQL: whatif.py's plan-mode code default is already 'ma_ledger', so a tenant with NO
-- whatif_source_config row is on the ledger the moment the code deploys. Mig 209 seeded a HOUSE plan-mode
-- row with income_source='ma', and every tenant inherits it, so in practice production stays on the LEGACY
-- source until this file runs (or an admin picks "Commission Ledger" in What-If → ⚙️ Sources, which needs
-- no deploy and no SQL). Either route reaches the same place; both are safe to do in any order.
--
-- THIS SQL WITHOUT THE CODE: harmless. Pre-deploy, whatif.py does not recognise 'ma_ledger' and falls
-- through to the Boost branch of the income dispatch for MA carriers — so run this AFTER the deploy that
-- ships the code, or simply run it whenever and reload the page once the deploy is green.
--
-- REVERT: set income_source back to 'ma' on the row (What-If → ⚙️ Sources dropdown, or the UPDATE at the
-- bottom of this file, commented out). No data is rewritten by this migration — it changes one setting.
--
-- ADDITIVE + IDEMPOTENT: filtered on income_source = 'ma', so a second run matches zero rows. Guarded on
-- the table existing (mig 209) so it can never fail a fresh install. No GRANT, no CREATE POLICY, no
-- anon/authenticated. Touches exactly one column of one config table.

DO $$
DECLARE
  n_updated INT := 0;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                 WHERE table_schema = 'commcalc' AND table_name = 'whatif_source_config') THEN
    RAISE NOTICE '253: commcalc.whatif_source_config absent (mig 209 not applied) — nothing to do; '
                 'whatif.py code default already resolves plan mode to ma_ledger.';
    RETURN;
  END IF;

  -- Only rows still on the LEGACY thin source. A tenant that already chose 'ma_ledger' (or is on
  -- 'boost_comp_mi_atu') is left exactly as it is — an explicit choice is never overridden (RULE TWO).
  UPDATE commcalc.whatif_source_config
     SET income_source = 'ma_ledger',
         notes = COALESCE(NULLIF(notes, ''), '') ||
                 CASE WHEN COALESCE(notes, '') LIKE '%[253]%' THEN ''
                      ELSE ' [253] 2026-07-31: carrier-income COMMISSION/SPIFF switched from '
                           'raw_ma_commission (spiff_m1..m6 + rebate) to the canonical Commission Ledger '
                           '(origin-agnostic). Residual + airtime margin still read raw_ma_daily_tx. '
                           'Revert by setting income_source back to ''ma'' here or in What-If -> Sources.'
                 END,
         updated_at = NOW()
   WHERE income_source = 'ma';
  GET DIAGNOSTICS n_updated = ROW_COUNT;
  RAISE NOTICE '253: % whatif_source_config row(s) moved from income_source=''ma'' to ''ma_ledger''.',
               n_updated;
END $$;

COMMENT ON COLUMN commcalc.whatif_source_config.income_source IS
  'Carrier-income (Company Payout) source. boost_comp_mi_atu = Comprehensive Comp + MI/ATU (Boost/ePay). ma_ledger = canonical commcalc.commission_ledger for Commission/Spiff/Equipment-rebate, origin-agnostic (RECOMMENDED, code default for plan mode). ma = legacy raw_ma_commission spiff_m1..m6 + rebate only. Residual + airtime margin always come from raw_ma_daily_tx regardless.';

-- Status read-back — paste the output back to the operator to confirm the shape.
SELECT org_id, carrier_id, carrier_mode, income_source, residual_source, residual_amount_field,
       ma_commission_sign, is_active, updated_at
  FROM commcalc.whatif_source_config
 ORDER BY org_id, carrier_mode, carrier_id;

-- ── REVERT (do not run unless rolling back) ───────────────────────────────────────────────────────
-- UPDATE commcalc.whatif_source_config SET income_source = 'ma', updated_at = NOW()
--  WHERE income_source = 'ma_ledger';
