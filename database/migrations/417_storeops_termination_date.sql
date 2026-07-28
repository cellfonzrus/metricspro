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
-- What breaks until this runs: EVERYTHING in the salary pay-basis feature, not just termination-side
-- proration (Gate-1 N1 correction, 2026-07-27 — an earlier version of this comment incorrectly
-- claimed 416-alone gives working hire-side proration). payroll_salary.PAY_FIELDS is ONE combined
-- select string — "pay_basis,pay_amount,hire_date,termination_date" — requested together by every
-- call site (`_employees_with_pay_fields`); PostgREST/Postgres reject the WHOLE select if ANY named
-- column is missing, and the try/except around it falls back to the caller's pre-existing column list
-- ALONE on that failure. So if 416 has run but 417 has NOT, the combined select still fails
-- (termination_date doesn't exist yet) — pay_basis/pay_amount/hire_date are ALSO unavailable, and the
-- entire salary feature (not just termination proration) is a no-op until BOTH 416 AND 417 have run.
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS termination_date DATE;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 417 complete — storeops.employees.termination_date (NULL default = still employed)' AS status;
