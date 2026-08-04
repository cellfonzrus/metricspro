-- 419_storeops_salary_advance_ledger.sql — mod-people band 400-499.
--
-- Envelope Expense/Payout package (owner directive 2026-08-04, cross-module spec:
-- docs/specs/envelope-expense-payout.md). WHY: salary paid IN CASH from the daily-closing envelope is
-- an ADVANCE against the employee's real clock-in-derived earnings — it NEVER changes what payroll
-- counts (payroll_gross / GET /storeops/payroll stay exactly as they are today, computed from
-- shifts/timelog, never from this table). This table is ONLY the audit trail of cash actually handed
-- out; GET /storeops/salary-owed reads it to show owed-vs-paid-vs-balance, and
-- POST /storeops/salary-advance/record writes to it, then recomputes the SEPARATE 'Additional Payroll'
-- P&L line (source_key='additional_payroll') for any excess of cumulative cash paid over cumulative
-- earned — see backend/app/modules/storeops/salary_owed.py's module docstring for the full math.
--
-- storeops.salary_advance_ledger: one row per cash payment recorded against an employee. Never
-- updated in place (a correction is a new row, e.g. a negative amount, same convention as other
-- append-only ledgers in this schema — payroll_change_log, google_review snapshots).
--
-- Additive + idempotent (`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`), safe to re-run.
-- WHAT BREAKS UNTIL THIS RUNS: nothing existing — GET /storeops/payroll, /payroll-by-store and every
-- other payroll surface never reads this table. GET /storeops/salary-owed and
-- POST /storeops/salary-advance/record both degrade gracefully (empty cash_paid_total / a clear error
-- on write) until this migration is applied, per AGENT_CONTRACT §5.
--
-- RLS: enabled, ZERO policies, anon/authenticated NOT granted — the backend's service-role client is
-- the only reader/writer (current house posture per the 2026-07-28 anon-key lockdown; see migration
-- 414's own header for the identical rationale on storeops.payroll_change_log, another
-- money-adjacent audit ledger).

CREATE TABLE IF NOT EXISTS storeops.salary_advance_ledger (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  employee_id    TEXT NOT NULL,          -- storeops.employees.employee_id (canonical business id)
  amount         NUMERIC NOT NULL,       -- cash handed to the employee; a correction is a new (possibly
                                          -- negative) row, never an UPDATE of a prior one
  paid_date      DATE NOT NULL,          -- the calendar day the cash was actually paid out
  method         TEXT NOT NULL DEFAULT 'envelope_cash',
  store_code     TEXT,                   -- the envelope/store the cash was paid from
  withdrawal_ref TEXT,                   -- optional link to commcalc.envelope_withdrawal (retail-ops
                                          -- mig 507) — a free-form id, no FK (cross-schema, cross-agent)
  recorded_by    TEXT,                   -- who recorded the payment (email/employee_id)
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_salary_advance_ledger_org
  ON storeops.salary_advance_ledger (org_id);
CREATE INDEX IF NOT EXISTS ix_salary_advance_ledger_org_employee
  ON storeops.salary_advance_ledger (org_id, employee_id);
CREATE INDEX IF NOT EXISTS ix_salary_advance_ledger_org_paid_date
  ON storeops.salary_advance_ledger (org_id, paid_date);

ALTER TABLE storeops.salary_advance_ledger ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON storeops.salary_advance_ledger FROM anon, authenticated;
GRANT ALL ON storeops.salary_advance_ledger TO service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 419 complete — storeops.salary_advance_ledger (cash salary advances against clock-in-derived earnings)' AS status;
