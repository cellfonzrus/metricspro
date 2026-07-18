-- 214_commission_billpay_products.sql — PER-ORG bill-payment product membership (RULE TWO).
--
-- WHY: the DISPLAY conversion metric (Daily-Targets store summary) is boxes ÷ bill-payments × 100. The
-- bill-payment membership inside the shared aggregation (_sales_cell_agg → the `_billpay` distinct-txn set,
-- router.py ~8997) was HARD-CODED to Boost product tokens ('boost rtr' / 'xfinity prepaid refill') matched
-- by lower-cased CONTAINMENT on the line's product_desc. For a Total-carrier tenant (e.g. luxelink) whose
-- b2bsoft POS labels its walk-in recharges differently, NO line ever matched → billpays = 0 → CONVERSION
-- read 0% regardless of the mig-213 contract-type map (Gate-1 follow-up f1 on luxelink-targets-actuals,
-- 2026-07-18).
--
-- FIX: a per-org, admin-editable list of the product/item values that count as a bill payment, picked from
-- the tenant's OBSERVED sales product descriptions (pick-don't-type over commcalc /sales-fields, RULE THREE)
-- so a tenant can set its own without a code change. When the list is NON-EMPTY, a line is a bill payment
-- iff its product_desc matches an entry case-insensitively. When the list is EMPTY/unset, the aggregation
-- falls back to the EXACT historical Boost tokens with the EXACT historical containment semantics.
--
-- SCOPE — DISPLAY ONLY, NO PAY CHANGE: this list is consumed exclusively by the shared DISPLAY aggregation's
-- `_billpay` conversion set (_sales_cell_agg → Daily-Targets conversion). The CONVERSION FORMULA itself
-- (boxes ÷ billpays) is unchanged — only which lines are counted as billpays. The Boost payout path
-- (calculator.py) and the plan-mode payout path (commission_engine.py) do NOT read this column. The Boost
-- accessory-flag classifier (flags.py), the installment bill_payment classifier (sale_installment_engine.py),
-- the closing cash recon (closing/router.py _EPAY_*), and the Executive-MTD bill_payment LINE metric
-- (exec_metric_config['bill_payment']) are all SEPARATE token sites and are intentionally NOT affected.
--
-- BOOST-SAFE / GRACEFUL DEGRADE: default '[]' (empty) → the aggregation uses the hard-coded Boost tokens →
-- the house/Boost conversion stays BYTE-IDENTICAL. The resolver reads this column in its OWN defensive query
-- and falls back to '[]' when the column/row is absent, so the feature works BEFORE this migration runs and a
-- missing column can never break the conversion display. Additive + idempotent + re-runnable. Reuses
-- commcalc.accessory_config (mig 208) rather than a parallel table.
--
-- NO SEED: the correct list depends on the tenant's LIVE product descriptions (not statically known), so
-- nothing is seeded — the owner picks them in Sales Report → Classification settings → "Bill-payment items"
-- (options come from the org's real observed values). An empty list = today's Boost-default behavior.

ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS billpay_products JSONB NOT NULL DEFAULT '[]'::jsonb;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 214 complete — commcalc.accessory_config.billpay_products installed (per-org; empty default = Boost-token behavior, byte-identical)' AS status;
