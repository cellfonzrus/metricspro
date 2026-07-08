-- 107_bank_deposit.sql
-- Bank deposit receipts for the ePay cash employees deposit in the bank — reconciled against the ePay
-- (bill-payment) cash they collected (declared on the closing + actual from sales). One row per
-- deposit (a store/day may have several). Idempotent / additive.

create table if not exists commcalc.bank_deposit (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null,
  close_date     date not null,
  period         text,
  store_code     text,
  store_address  text,
  employee_name  text,
  amount         numeric,          -- amount deposited in the bank (from the slip)
  receipt_path   text,             -- signed/stored path to the uploaded receipt image
  handed_to      text,             -- optional: who deposited / handed it
  note           text,
  created_at     timestamptz default now()
);
create index if not exists bank_deposit_lookup on commcalc.bank_deposit (org_id, close_date, store_code);
create index if not exists bank_deposit_period on commcalc.bank_deposit (org_id, period);
