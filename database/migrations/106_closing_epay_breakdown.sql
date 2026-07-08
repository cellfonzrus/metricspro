-- 106_closing_epay_breakdown.sql
-- Daily-closing ePay (bill-payment) breakdown: how much of the CASH / CREDIT / ACIMA collected was
-- ePay bill payments. INFORMATIONAL only — a subset of those tenders, NOT added to the total and NOT
-- part of the cash/credit recon. Feeds the ePay bank-deposit reconciliation. Idempotent / additive.

alter table commcalc.daily_closing
  add column if not exists epay_on_cash   numeric,
  add column if not exists epay_on_credit numeric,
  add column if not exists epay_on_acima  numeric;
