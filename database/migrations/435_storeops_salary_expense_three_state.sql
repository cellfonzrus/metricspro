-- 435_storeops_salary_expense_three_state.sql — salary -> store expenses: hours provenance +
-- per-org config (mod-people, band 400-499).
--
-- OWNER DIRECTIVE 2026-09-08 (verbatim): "then we need to pull the exact salaries paid as per the
-- schedule and update the same in the expenses as those are not getting updated for a lot of stores,
-- if we have the actual hours then the salary is based on actual hours if not then the salary is
-- based on scheduled hours for that month to go in the gross profit for that month".
--
-- WHY THIS MIGRATION EXISTS. `commcalc.store_expenses` cannot represent "unknown": the system-line
-- receiver (`_system_line_expand`) drops zero-amount cells, so a store-month with NO measured hours
-- and NO schedule is indistinguishable there from a store that genuinely spent $0 on wages. That is
-- precisely the state that must NEVER render as $0.00 — a store showing no salary because nobody
-- clocked in is a DATA DEFECT (no time clock, no schedule uploaded, an approval never filed), not a
-- number. So the three-state truth is persisted HERE, on the audit ledger, alongside the amount.
--
-- PURELY ADDITIVE + IDEMPOTENT. New nullable columns on an existing table and one new config table.
-- It changes NO existing value, moves NO money, and books nothing: the engine
-- (backend/app/modules/storeops/salary_expense.py) already degrades to the house defaults and to a
-- skipped ledger persist when this has not run, exactly as it does for a pre-405 database.
--
-- MONEY NOTE (surfaced for approval, per house rule): applying this migration by itself moves no
-- money. What DOES change the books is the next `POST /storeops/payroll-expenses/run/{period}`,
-- which pushes the 'payroll_gross' system line into `commcalc.store_expenses` and therefore into
-- that month's gross profit. Run the period read-only first (`GET /storeops/payroll-expenses/{period}`)
-- and read `gross_withheld` / `gross_unbound` before running the write.

-- ── storeops.payroll_gross_ledger — hours provenance columns ────────────────────────────────────
-- The audit trail for exactly what the run decided per (org, period, store), INCLUDING the
-- store-months it deliberately withheld (booked = false), which store_expenses cannot carry.
--   measured_hours   hours that came from a MEASUREMENT: a closed timelog punch or a manual DM
--                    correction. A measurement of 0.0 still counts as measured — that is a fact
--                    about a day somebody looked at, not a gap.
--   scheduled_hours  hours that stood in for a NOT-MEASURED day (the owner's fallback). ONLY a
--                    not-measured day falls back; a measured zero never does.
--   hours_state      'measured' | 'mixed' | 'scheduled_fallback' | 'no_data'.
--   booked           false == WITHHELD: reported to the reader, never written to store_expenses.
--   raw_store_codes  every raw shifts/timelog store spelling that folded onto this canonical code,
--                    so a case/alias fold is auditable rather than invisible.
ALTER TABLE storeops.payroll_gross_ledger ADD COLUMN IF NOT EXISTS measured_hours  NUMERIC;
ALTER TABLE storeops.payroll_gross_ledger ADD COLUMN IF NOT EXISTS scheduled_hours NUMERIC;
ALTER TABLE storeops.payroll_gross_ledger ADD COLUMN IF NOT EXISTS hours_state     TEXT;
ALTER TABLE storeops.payroll_gross_ledger ADD COLUMN IF NOT EXISTS booked          BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE storeops.payroll_gross_ledger ADD COLUMN IF NOT EXISTS raw_store_codes TEXT;

CREATE INDEX IF NOT EXISTS ix_payroll_gross_ledger_org_period_state
  ON storeops.payroll_gross_ledger (org_id, period, hours_state);

-- ── storeops.salary_expense_config — RULE TWO (config, never code) ──────────────────────────────
-- Nothing tenant-specific lives in the source. One row per org; absent row == house defaults.
--   line_label               the expense_name the system line carries on the Expenses sheet / P&L.
--                            House default 'Gross Payroll' (the long-standing source_key='payroll_gross'
--                            label). A tenant whose sheet calls it something else sets it here.
--   expense_type             the Expenses sheet's type column for that line.
--   book_scheduled_fallback  TRUE (house default, the owner's rule) = a store-month whose hours are
--                            ENTIRELY scheduled fallback is still booked, and flagged. A tenant that
--                            would rather see nothing than an unmeasured estimate sets FALSE.
--   book_no_data_as_zero     FALSE, and there is no house default that flips it: booking a store-month
--                            with no payroll evidence as $0.00 would paper over a data defect. It
--                            exists as an explicit, visible FALSE rather than an unstated assumption.
CREATE TABLE IF NOT EXISTS storeops.salary_expense_config (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   UUID NOT NULL,
  enabled                  BOOLEAN NOT NULL DEFAULT TRUE,
  line_label               TEXT    NOT NULL DEFAULT 'Gross Payroll',
  expense_type             TEXT    NOT NULL DEFAULT 'Fixed',
  book_scheduled_fallback  BOOLEAN NOT NULL DEFAULT TRUE,
  book_no_data_as_zero     BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by               TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_salary_expense_config_org
  ON storeops.salary_expense_config (org_id);

ALTER TABLE storeops.salary_expense_config ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS open_all ON storeops.salary_expense_config;
CREATE POLICY open_all ON storeops.salary_expense_config FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
GRANT ALL ON storeops.salary_expense_config TO anon, authenticated, service_role;

-- Seed the HOUSE org only (org 00000000-0000-0000-0000-000000000001) plus every existing tenant, all
-- with the house defaults — so the row exists and is editable, and behaviour is byte-identical to the
-- no-row case on day one. Idempotent: ON CONFLICT DO NOTHING against the unique org index.
CREATE OR REPLACE FUNCTION storeops.seed_salary_expense_config(p_org uuid)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO storeops.salary_expense_config (org_id) VALUES (p_org)
  ON CONFLICT (org_id) DO NOTHING;
END $$;
GRANT EXECUTE ON FUNCTION storeops.seed_salary_expense_config(uuid) TO anon, authenticated, service_role;

DO $$
DECLARE t RECORD;
BEGIN
  PERFORM storeops.seed_salary_expense_config('00000000-0000-0000-0000-000000000001');
  IF to_regclass('storeops.tenants') IS NOT NULL THEN
    FOR t IN SELECT org_id FROM storeops.tenants LOOP
      PERFORM storeops.seed_salary_expense_config(t.org_id);
    END LOOP;
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 435 complete — payroll_gross_ledger provenance + storeops.salary_expense_config' AS status;

-- REVERT:
--   DROP INDEX IF EXISTS storeops.ix_payroll_gross_ledger_org_period_state;
--   ALTER TABLE storeops.payroll_gross_ledger DROP COLUMN IF EXISTS raw_store_codes;
--   ALTER TABLE storeops.payroll_gross_ledger DROP COLUMN IF EXISTS booked;
--   ALTER TABLE storeops.payroll_gross_ledger DROP COLUMN IF EXISTS hours_state;
--   ALTER TABLE storeops.payroll_gross_ledger DROP COLUMN IF EXISTS scheduled_hours;
--   ALTER TABLE storeops.payroll_gross_ledger DROP COLUMN IF EXISTS measured_hours;
--   DROP FUNCTION IF EXISTS storeops.seed_salary_expense_config(uuid);
--   DROP TABLE IF EXISTS storeops.salary_expense_config;
--   -- Reverting drops only provenance/config; no wage, payout or store_expenses row is touched by
--   -- this migration in either direction. The engine falls back to the house defaults and reports
--   -- the ledger persist as skipped, exactly as it does before this migration is applied.
