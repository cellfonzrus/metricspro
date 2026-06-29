-- 061_rep_commissions_plan_comm.sql — visibility columns for the new engines wired into the live calc.
--
-- The live commission calc now layers two NEW configurable engines on top of the standard (Boost) calc,
-- ADDITIVELY and Boost-safe (with no payout_schedule + no commission_plan they're no-ops, so Boost is
-- byte-identical):
--   • residual_installment_comm — added by migration 057 already (multi-month payout installments).
--   • plan_comm / plan_name     — added here: when a rep is covered by a commission PLAN, the plan total
--                                 replaces their spiff subtotal; plan_comm records it, plan_name shows which.
--
-- OPTIONAL: the wiring probes for these columns and only writes them if present, so the calc works (folding
-- the plan into total_payout) even before this runs. Run it to surface the breakdown. Additive + idempotent.

ALTER TABLE commcalc.rep_commissions ADD COLUMN IF NOT EXISTS plan_comm NUMERIC DEFAULT 0;
ALTER TABLE commcalc.rep_commissions ADD COLUMN IF NOT EXISTS plan_name TEXT;

NOTIFY pgrst, 'reload schema';
