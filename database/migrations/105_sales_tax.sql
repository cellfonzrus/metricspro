-- 105_sales_tax.sql
-- Capture retail SALES TAX per transaction line so it can be reconciled (tenders include tax; ext_price
-- is pre-tax merchandise) and reported in Finance. Populated by the sales parser from the export's
-- Tax column on the next ingest (same pattern as Ext Price / GP). Idempotent / additive — safe to re-run.

alter table commcalc.raw_sales        add column if not exists tax numeric;
alter table commcalc.daily_sales_feed add column if not exists tax numeric;
