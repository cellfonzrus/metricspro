-- 256_commission_installment_category_flat_payout.sql   (mod-commission, band 200-299)
--
-- FLAT (ONE-TIME) PAYOUT BY DEVICE CATEGORY — the config home for the owner directive of 2026-08-01:
--   "fwa is paid on flat rate should not be in monthly payments - fix but dont hard code"
--
-- WHAT IT ADDS: two nullable JSONB columns. Nothing else. No new table, no seed, NO DOLLAR AMOUNT.
--
--   commcalc.commission_org_config.installment_category_payout   per-tenant  (the normal place)
--   commcalc.plan_installment_schedule.category_payout           per-schedule override (inherits when NULL)
--
-- Shape (canonical):
--   {"home_internet": {"mode":"flat_once","amount":25.00,"pay_month":1},
--    "tablet":        {"mode":"installments","amount":null,"pay_month":1}}
--   `mode` is 'installments' (today's M1..MN chain) or 'flat_once' (one payment, then nothing).
--   `amount` is the tenant's own dollar figure. A bare number is also accepted
--   ({"home_internet": 25}) and read as flat at that amount.
--
-- ── THIS MIGRATION MOVES $0, AND CANNOT MOVE MONEY BY ITSELF ─────────────────────────────────────
-- Both columns land NULL. NULL means "inherit", and the code default is EVERY category on
-- 'installments' — i.e. exactly the behaviour shipped today. A payout only changes after a human
-- opens Plan Installments -> "Flat payout by category", switches a category to one-time and TYPES A
-- DOLLAR AMOUNT, and then a Run Calculation is fired. Until all three happen, nothing anywhere moves.
--
-- ── AND A HALF-CONFIGURED SWITCH CANNOT ZERO ANYBODY ─────────────────────────────────────────────
-- mode='flat_once' with amount NULL is deliberately NOT active: the engine keeps paying the monthly
-- installments exactly as before and raises a `flat_amount_unconfigured` warning naming the category
-- and the dollars still being paid monthly. The engine never invents an amount and never substitutes
-- a $0. (backend/app/modules/commcalc/installment_category_payout.py, resolve_flat().)
--
-- ── NOTHING IS HARD-CODED (contract RULE TWO) ────────────────────────────────────────────────────
-- There is no 'luxelink', 'FWA' or 'home_internet' literal in the engine's decision. The category of
-- an activation comes from the tenant's OWN commcalc.installment_category_rule rows (mig 245) plus
-- the built-in fallback tail; this config only answers "how does THIS tenant pay THAT category". A
-- different tenant can put `phone` on flat and leave `home_internet` on installments.
--
-- ── UNTIL THIS RUNS ──────────────────────────────────────────────────────────────────────────────
-- installment_category_payout.load_org_payout() swallows the missing column and returns the code
-- defaults, so the engine is byte-identical and every page still renders. The admin card reports
-- `ready:false` and names this file; its Save returns a clear 400 naming this file, never a 500.
--
-- ADDITIVE + IDEMPOTENT. No new table, so no RLS/GRANT clause is required — both target tables
-- already carry their own posture. No GRANT, no CREATE POLICY, no anon/authenticated (contract §5).

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS installment_category_payout JSONB;

ALTER TABLE commcalc.plan_installment_schedule
  ADD COLUMN IF NOT EXISTS category_payout JSONB;

COMMENT ON COLUMN commcalc.commission_org_config.installment_category_payout IS
  'Per-tenant FLAT (one-time) payout by device category for the sale-triggered installment chain '
  '(mig 256). NULL = inherit the code default = every category on monthly installments (today''s '
  'behaviour). Shape: {"<category_key>":{"mode":"installments|flat_once","amount":<number|null>,'
  '"pay_month":<1..12>}}. category_key comes from installment_category.CATEGORY_KEYS (phone, tablet, '
  'home_internet, sim, accessory, unknown). mode=flat_once with amount NULL is NOT active: the chain '
  'keeps paying monthly and the engine warns — it never guesses an amount and never pays $0.';

COMMENT ON COLUMN commcalc.plan_installment_schedule.category_payout IS
  'Per-schedule override of commission_org_config.installment_category_payout (mig 256). NULL/empty = '
  'inherit the org row, then the code default (all categories on monthly installments).';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 256 complete — installment_category_payout (org) + category_payout (schedule). '
       'Both NULL: no category is on flat, no amount exists, no payout changed. A payout moves only '
       'after a human sets a category to one-time AND types an amount AND runs a calculation.' AS status;
