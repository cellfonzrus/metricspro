-- 243_commission_calc_warnings.sql — surface "this sale paid NOTHING" as a first-class calc warning.
--
-- WHY (owner 2026-07-27, the "edge" reclassification): a Commission-Plan rule that stops matching a sale
-- line does not hand that line to another rule — the plan engine has NO exclusivity, and the multi-month
-- installment engine is a SEPARATE, additive component with its OWN trigger matcher. So re-keying a rule
-- (e.g. "edge" from a product-description keyword to the TW-financing tender) can silently take an
-- activation from $25 to $0 with nothing on any screen saying so.
--
-- calc_status already carries `save_errors` for a FAILED calc. This adds the missing half: a SUCCESSFUL
-- calc that nevertheless left real activations unpaid by every configured source.
--
-- ADDITIVE + IDEMPOTENT. Nothing breaks until it runs: `_run_calculation` probes for the column and skips
-- the write when absent (the warnings are still computed and returned by the read-only endpoint).
-- MONEY-FREE: this migration adds a diagnostic column only. It changes no rate, tier, rule or payout.

ALTER TABLE commcalc.calc_status
  ADD COLUMN IF NOT EXISTS calc_warnings JSONB;

COMMENT ON COLUMN commcalc.calc_status.calc_warnings IS
  'Diagnostic warnings from the LAST successful calculation for this org+period: activations that no '
  'commission-plan rule and no installment-schedule trigger paid, plus the sale-installment engine''s own '
  'warnings. Written by commcalc._run_calculation. Never affects a payout.';
