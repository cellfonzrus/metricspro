-- 405_storeops_payroll_gross_ledger.sql — Gross Payroll audit ledger (mod-people, band 400-499).
--
-- WHY (owner decision 2026-07-15): the P&L must show TWO SEPARATE payroll line items —
--   (1) Gross Payroll  = the EXACT gross $ paid to employees, and
--   (2) Payroll Expenses = the employer burden (tax + unemployment + workers comp; migration 404) —
-- and they must NOT double-count (gross wages vs. burden are distinct costs).
--
-- This migration adds the audit trail for the NEW 'payroll_gross' system line pushed by
-- POST /storeops/payroll-expenses/run/{period} (backend/app/modules/storeops/router.py) on the SAME
-- run that already pushes 'payroll_expenses', alongside the existing payroll_tax_ledger /
-- payroll_expense_ledger (migration 404). PURELY ADDITIVE: it reads no new source data (the wages
-- basis is the SAME hours * pay_rate figure the migration-404 engine already uses as its wage base —
-- see payroll_expenses.py's wages_by_store_from_hours) and writes no wage/payout number — it only
-- persists, for auditability, the per-store gross figure that is already computed today.
--
-- storeops.payroll_gross_ledger: one row per (org, period, store) per run — the audit trail for
-- exactly what was pushed to commcalc.store_expenses as source_key='payroll_gross', label='Gross
-- Payroll'. A re-run DELETEs the prior rows for that (org, period) first (see router), matching
-- payroll_tax_ledger / payroll_expense_ledger's idempotent-replace convention. The router degrades
-- gracefully if this migration hasn't run yet (the push to Store Expenses still fires; only the
-- ledger persist is skipped and reported back in the run response).
--
-- Contract for other modules (mod-finance): the 'payroll_gross' system line is DISTINCT from
-- 'payroll_expenses' and from PTO's 'pto_accrual' — all three coexist in commcalc.store_expenses as
-- separate source_key rows and must never be summed as if they were the same cost.

CREATE TABLE IF NOT EXISTS storeops.payroll_gross_ledger (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  period      TEXT NOT NULL,        -- "YYYY-MM"
  store       TEXT NOT NULL,
  wages       NUMERIC NOT NULL DEFAULT 0,   -- exact gross payroll pushed for this store this period
  headcount   INTEGER NOT NULL DEFAULT 0,   -- informational: distinct employees with >0 hours at this store
  run_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  run_by      TEXT
);
CREATE INDEX IF NOT EXISTS ix_payroll_gross_ledger_org_period ON storeops.payroll_gross_ledger (org_id, period);
CREATE INDEX IF NOT EXISTS ix_payroll_gross_ledger_org_period_store ON storeops.payroll_gross_ledger (org_id, period, store);

ALTER TABLE storeops.payroll_gross_ledger ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS open_all ON storeops.payroll_gross_ledger;
CREATE POLICY open_all ON storeops.payroll_gross_ledger FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
GRANT ALL ON storeops.payroll_gross_ledger TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 405 complete — storeops.payroll_gross_ledger' AS status;
