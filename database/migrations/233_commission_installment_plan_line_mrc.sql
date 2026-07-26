-- 233_commission_installment_plan_line_mrc.sql — a multi-month installment pays ONCE PER ACTIVATION,
-- on the ACTIVATION'S RATE-PLAN MRC. Additive + idempotent + safe to re-run.
--
-- WHY (owner money bug, 2026-07-25, reported on luxelink but universal):
--   commcalc.plan_installment_schedule triggers PER SALE LINE. A POS that stamps the transaction's
--   Contract Type (and therefore the resolved activation bucket) on EVERY line of the sale makes one
--   trigger match the DEVICE line, the RATE-PLAN line and the SIM line of the SAME activation — each
--   started its own installment chain, so the rep was paid once per matching LINE.
--   Repro: IMEI 357612117781238, "Total ALL ACCESS Plan $65", Port with IDV, sold July 2026 →
--   TWO month-1 rows, $28.75 on "MRC 575.00" (the DEVICE PRICE, scraped out of the handset line's
--   description by the bare-$ MRC prefill) AND $3.25 on MRC 65.00. Only the $3.25 is owed.
--
-- THE FIX IS IN THE ENGINE (sale_installment_engine.py), and it is ON BY DEFAULT — a schedule's trigger
-- configuration can no longer double-pay an activation. This migration only adds the two TENANT-EDITABLE
-- knobs the engine reads (RULE TWO: no hard-coded product/carrier wording in code):
--
--   installment_mrc_basis  'plan_line'    (DEFAULT) the %-of-MRC amount resolves from the activation's
--                                          RATE-PLAN line: product_mrc catalog first (user-confirmed),
--                                          then a structurally-monthly description ("$25/mo", "MRC $30"),
--                                          then a line matching plan_line_matcher. A line identifiable as
--                                          none of those can no longer donate its PRICE as an "MRC" — it
--                                          resolves to $0 and is reported in the preview's warnings.
--                          'trigger_line' the pre-fix per-line resolution (documented escape hatch for a
--                                          tenant whose rate-plan wording the matcher cannot express yet).
--   plan_line_matcher      NULL = the engine's seeded default (keyword-first: plan / unlimited / airtime /
--                                 access charge / monthly / mrc / per month / rate plan / talk & text).
--                          Same shape as activation_payment_matcher:
--                            {"departments":[...], "categories":[...], "product_keywords":[...]}
--                          Edited at /commcalc/plan-installments → "Which line carries the rate plan".
--
-- UNTIL THIS RUNS: the engine degrades to those exact code defaults (the column read is wrapped), so the
-- fix is live without it — the migration only makes the settings PERSISTABLE.
-- NOTHING here changes a stored number. The corrected amounts appear on the next POST /calculate.

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS installment_mrc_basis TEXT NOT NULL DEFAULT 'plan_line',
  ADD COLUMN IF NOT EXISTS plan_line_matcher     JSONB;

COMMENT ON COLUMN commcalc.commission_org_config.installment_mrc_basis IS
  'Multi-month %-of-MRC basis: plan_line (default; the activation''s rate-plan line) | trigger_line (pre-2026-07-25 per-line resolution).';
COMMENT ON COLUMN commcalc.commission_org_config.plan_line_matcher IS
  'Which sale line is the rate-plan line: {"departments":[],"categories":[],"product_keywords":[]}. NULL = engine default.';

-- Optional, per-tenant: only if a tenant needs wording the seeded keywords miss. Example (luxelink):
-- UPDATE commcalc.commission_org_config
--    SET plan_line_matcher = '{"departments":[],"categories":[],
--                              "product_keywords":["plan","unlimited","airtime","access charge","all access"]}'::jsonb
--  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 233 complete — commission_org_config.installment_mrc_basis + plan_line_matcher '
       '(multi-month installments pay once per activation, on the rate-plan MRC)' AS status;
