-- 855 — per-store NET PROFIT target (owner: "set the target for each store to 10K net profit")
-- A DEDICATED target, separate from stores.monthly_target (which is a sales/production target the
-- commission engine uses to allocate accessory goals — not to be overwritten). ADD COLUMN ... DEFAULT
-- backfills every EXISTING store to 10000, so this one statement sets the 10K target across the board;
-- each store can be overridden afterward in Admin. The P&L report compares actual net profit to it.
ALTER TABLE storeops.stores ADD COLUMN IF NOT EXISTS net_profit_target NUMERIC DEFAULT 10000;

-- Make sure any pre-existing rows that somehow hold NULL are set to the 10K default too.
UPDATE storeops.stores SET net_profit_target = 10000 WHERE net_profit_target IS NULL;
