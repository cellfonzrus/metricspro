-- 206_commission_expense_system_line.sql — mark a store-expense row as an AUTO ("system") line so an
--   automated producer (e.g. mod-people's payroll run inserting the per-store 'Paid Leave Accumulated'
--   PTO accrual) can coexist with hand-entered expenses (mod-commission, band 200-299).
--
-- WHY (owner request 2026-07-15): a new "Paid Leave Accumulated" expense is AUTO-COMPUTED by the payroll
-- run and INSERTED into the Store Expenses section. It must show in the expense matrix and roll into the
-- SAME GP/P&L totals as manual expenses, but be READ-ONLY in the UI and never overwritten/copied by the
-- manual expense paths (put_expenses / bulk-apply / apply-to-months). A single nullable `source_key` marks
-- the row's origin: NULL == manual (the existing behavior, unchanged); a non-null token (e.g. 'pto_accrual')
-- == an auto system line owned by that producer, replaced wholesale on each re-run keyed on (org,period,source_key).
--
-- Consumed by POST /commcalc/expenses/{period}/system-line (the RECEIVER) + the manual-expense guards in
-- put_expenses / bulk-apply / apply-to-months, and surfaced read-only in the Store Expenses page.
-- ADDITIVE + IDEMPOTENT + BACKWARD-COMPATIBLE: existing rows default to NULL (manual); every existing read
-- and write is unaffected when source_key is NULL. NON-money on the pay path — expenses feed GP/P&L, not
-- commission payouts.

ALTER TABLE commcalc.store_expenses
  ADD COLUMN IF NOT EXISTS source_key TEXT;               -- NULL = manual; non-null = auto system line

-- (org, period, source_key) is the replace-key for the receiver's delete-then-insert — index it.
CREATE INDEX IF NOT EXISTS store_expenses_source_key_idx
  ON commcalc.store_expenses (org_id, period, source_key);

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 206 complete — store_expenses.source_key (auto/system expense lines)' AS status;
