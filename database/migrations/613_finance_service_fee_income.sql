-- 613_finance_service_fee_income.sql — book SERVICE-FEE income (bill-payment fees etc.) on the P&L.
--
-- WHY (owner 2026-08-09): "epay service charge of $4 is an income to the store and should be added as
-- a line item in p&l". Today it is on NO line at all. coa.build_inputs classifies each sale line as
-- ACCESSORY or DEVICE and silently drops everything else, and the ePay Service Charge is neither — it
-- sits in department 'Bill Payments' alongside the RTR refills. Measured on the house org: 4,665 lines
-- in July 2026 = $18,492, ~$18k EVERY month, absent from revenue.
--
-- WHY A PRODUCT LIST AND NOT A DEPARTMENT MAP: the fee shares its DEPARTMENT with the bill payments
-- themselves, which are pass-through, not income (July: $60k+ of RTR on the same department). Mapping
-- the department would book the customers' refill money as store revenue. The fee is identifiable only
-- at the PRODUCT level, so the config is a picked list of observed product descriptions — RULE THREE
-- (pick-don't-type), the same shape as accessory_config.billpay_products.
--
-- WHAT: one additive column on the existing per-org finance config table (mig 611). Nothing is seeded
-- here: an EMPTY list books nothing and every tenant's P&L is byte-identical until a product is
-- explicitly picked in Accounts → settings. coa degrades to [] if this column is absent.

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS service_fee_products TEXT[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN commcalc.account_config.service_fee_products IS
  'Sale-line product_desc values that are FEE INCOME to the store (e.g. "ePay Service Charge"), booked '
  'to the P&L revenue line service_income at full ext_price with no COGS. Matched case-insensitively, '
  'EXACT (never containment — a substring rule would sweep in the bill payment the fee rides on). '
  'Empty = nothing booked = byte-identical. Owner directive 2026-08-09.';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 613 complete — account_config.service_fee_products installed (empty = no P&L change)' AS status;
