-- 255_commission_cost_integrity_config.sql  (mod-commission, band 200-299)
--
-- PAY-INPUT DATA QUALITY — per-tenant thresholds for the DISPLAY-ONLY cost/rate integrity guard.
--
-- WHY: commcalc.raw_sales has NO cost column. A line's cost is IMPLIED: cost = ext_price - gp. When the
-- POS catalog carries cost == retail on an item (the known "* BYOD" accessory class) the GP lands at $0
-- and every %-of-GP payout on that line is $0 — correctly, silently, and indistinguishably from a bug.
-- When the cost is stored NEGATIVE the GP is larger than the price and the payout inflates. Separately,
-- commcalc.commission_rule.pct is a FRACTION (0.10 = 10%) that the save path stores verbatim, so a rate
-- typed as a whole percent pays 100x. The commission drill-down and the Accessory Cost Audit now SAY SO.
--
-- THIS CHANGES NO PAYOUT. The guard is presentation only: it flags lines and rates on read surfaces and
-- never feeds the calculator, commission_engine, sale_installment_engine or POST /calculate.
--
-- UNTIL THIS RUNS: pay_data_quality.load_cost_config() degrades to the code defaults
--   {enabled:true, min_ext_price:0.01, tolerance:0.005, rate_max:1.0,
--    flags:{cost_equals_price:true, cost_negative:true, cost_zero:true, gp_negative:true}}
-- so both surfaces already work with this migration UNAPPLIED. Running it only makes the thresholds
-- tenant-editable (RULE TWO).
--
-- ADDITIVE + IDEMPOTENT. No GRANT, no CREATE POLICY, no anon/authenticated (contract §5).

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS cost_integrity_config JSONB;

COMMENT ON COLUMN commcalc.commission_org_config.cost_integrity_config IS
  'DISPLAY-ONLY pay-input data-quality thresholds. NULL = engine defaults. Shape: {"enabled":true,'
  '"min_ext_price":0.01,"tolerance":0.005,"rate_max":1.0,"flags":{"cost_equals_price":true,'
  '"cost_negative":true,"cost_zero":true,"gp_negative":true}}. Never read by any payout calculation.';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 255 complete — commission_org_config.cost_integrity_config (display-only pay-input '
       'data-quality thresholds; no payout number is read from or written by this column)' AS status;
