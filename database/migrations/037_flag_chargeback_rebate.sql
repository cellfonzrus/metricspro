-- 037_flag_chargeback_rebate.sql
-- Rep-report upgrade: a CHARGEBACK flag should show the REBATE LOST for that phone (the reimbursement
-- clawed back), not the raw bill-payment amount, plus the device + dates. days_active / phone_model /
-- customer_plan already exist on commcalc.flags; add the rest. Populated on the next commission recalc.
ALTER TABLE commcalc.flags ADD COLUMN IF NOT EXISTS rebate_lost      NUMERIC;
ALTER TABLE commcalc.flags ADD COLUMN IF NOT EXISTS transaction_date TEXT;
ALTER TABLE commcalc.flags ADD COLUMN IF NOT EXISTS activation_date  TEXT;
