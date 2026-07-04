-- 091_closing_mode.sql
-- Per-tenant "who owes the daily closing" policy (workstream F2).
--   per_rep      : every rep who WORKED the store that day owes their own closing (default,
--                  matches today's behavior — one envelope per rep).
--   one_closing  : only the store's assigned closer owes a closing; they tally the whole store's
--                  cash. The onus for closing + cash-tallying is on the closer.
-- Read/written by closing.get_cash_config / put_cash_config and consumed by /closing/summary,
-- which now dun for a MISSING closing based on who actually worked (clock-in ∪ B2B sales-by-rep),
-- not who was scheduled.
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS closing_mode text NOT NULL DEFAULT 'per_rep';

-- Guard the two supported values (idempotent).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'tenants_closing_mode_chk'
  ) THEN
    ALTER TABLE storeops.tenants
      ADD CONSTRAINT tenants_closing_mode_chk CHECK (closing_mode IN ('per_rep', 'one_closing'));
  END IF;
END $$;
