-- 247_commission_calc_notices.sql — what a Run Calculation deliberately did NOT pay, in one place the
-- owner actually reads. Additive + idempotent + safe to re-run.
--
-- WHY (owner protocol 2026-07-27): "Calculate warnings for anything newly non-qualifying — the owner
-- reads warnings after recalc." commcalc.calc_status already carries `save_errors`, but the dashboard
-- only shows it when the calc FAILED, so a successful calculation that silently paid nothing for 40
-- tablet activations had no channel at all. `calc_notices` is that channel: an array of
--   {type, severity, message, chains, amount, by_rep}
-- written by _run_calculation right after the status stamp, and rendered on /commcalc as an amber
-- "what this calculation did not pay" panel.
--
-- Types today: category_excluded (a device category is unticked) · category_unknown (could not classify)
--            · mrc_unresolved (no rate-plan line → $0 rather than a % of a device price)
--            · duplicate_device_month (one device paid twice in a month — usually a duplicate schedule).
--
-- WRITTEN IN ITS OWN UPSERT (never folded into the status stamp): a column that does not exist yet fails
-- the whole statement, and folding it in would take the calc's completion status down with it — the
-- lesson from migs 241/242. So until this migration runs, calculations behave exactly as before and the
-- notices simply do not persist (they are still visible live at
-- /commcalc/plan-installments → Preview and → Check impact).

ALTER TABLE commcalc.calc_status
  ADD COLUMN IF NOT EXISTS calc_notices JSONB;

COMMENT ON COLUMN commcalc.calc_status.calc_notices IS
  'Operator notices from the last Run Calculation: what was deliberately NOT paid (excluded device categories, unclassifiable activations, unresolved MRCs, duplicate device-months), with dollars + per-rep amounts.';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 247 complete — commcalc.calc_status.calc_notices (post-calculation "what did not pay" panel)' AS status;
