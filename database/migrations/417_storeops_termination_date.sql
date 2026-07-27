-- 417_storeops_termination_date.sql — storeops.employees.termination_date (mod-people, band 400-499).
--
-- WHY: the salary pay-basis feature (migration 416) needs to CALENDAR-DAY-PRORATE a salaried
-- employee's pay on a mid-period hire or termination ("days employed ÷ days in period" — owner
-- directive 2026-07-27, Deliverable 5). migration 077 already added `hire_date`; there was no
-- symmetrical termination/end date anywhere on storeops.employees (`is_active=false` records THAT
-- someone left, never WHEN) — this column is the minimum needed to prorate the term side of that
-- rule. General-purpose (not salary-specific) so any future HR surface can use the same field.
--
-- ADDITIVE (ONE column on the existing storeops.employees table), idempotent
-- (`ADD COLUMN IF NOT EXISTS`). NULL = still employed (the common case; every existing row defaults
-- to NULL, so a period's proration end simply falls back to the period's own end date — no behavior
-- change for anyone until HR actually sets a termination_date).
--
-- EXPLICIT GRANTS: NONE — same table, same no-grant posture as migration 416 (see that file's header
-- for the full mig-414 no-grant-lesson rationale). Grants are table-level; storeops.employees is
-- already covered by migration 003's blanket schema grant.
--
-- What breaks until this runs: nothing on its own — payroll_salary.py reads termination_date via the
-- SAME widened-select-with-fallback pattern as pay_basis/pay_amount (migration 416), so a database
-- with 416 run but not 417 still gets hire-side proration correctly and simply treats every employee
-- as "not yet terminated" (falls back to the period's own end date) until this runs too.
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS termination_date DATE;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 417 complete — storeops.employees.termination_date (NULL default = still employed)' AS status;
