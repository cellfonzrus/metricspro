-- 416_storeops_pay_basis.sql — Salary pay-basis on storeops.employees (mod-people, band 400-499).
--
-- WHY (owner directive 2026-07-27, verbatim): "in hr module the payroll set upo is currently per
-- hour, need to have the option to set up flat weekly or monthly salary or annual salary for the
-- employees and then calculate what is payable per week or biweekly as set up for the company."
--
-- Two ADDITIVE columns on the ALREADY-EXISTING storeops.employees table (migration 003):
--   pay_basis  TEXT NOT NULL DEFAULT 'hourly'  — 'hourly' | 'weekly' | 'monthly' | 'annual'
--   pay_amount NUMERIC                          — the flat weekly/monthly/annual figure for a
--                                                  non-hourly pay_basis; NULL/unused for 'hourly'
--                                                  (employees.pay_rate is untouched and remains the
--                                                  ONLY figure hourly pay uses — see
--                                                  backend/app/modules/storeops/payroll_salary.py).
--
-- DEFAULT 'hourly' on every existing row means this migration changes NO existing employee's pay
-- computation the moment it runs — every current row keeps behaving exactly as before (hours ×
-- pay_rate). No CHECK constraint on pay_basis: the app layer (payroll_salary.resolve_pay_basis)
-- validates/clamps any unrecognized value back to 'hourly' defensively, matching the SAP-configurable
-- rule's "safe default, never crash on bad data" pattern used elsewhere (e.g. the face-match
-- threshold clamp) rather than a rigid DB constraint that would 500 on a stray value.
--
-- EXPLICIT GRANTS: NONE — matching this table's EXISTING posture (the mig-414 no-grant lesson:
-- storeops.employees already carries the blanket schema-level grant from migration 003 —
-- `GRANT ALL ON ALL TABLES IN SCHEMA storeops TO authenticated; GRANT SELECT ... TO anon` — which
-- covers every column on the table, including ones added later by ALTER TABLE. Grants are
-- table-level, not column-level, so no new GRANT statement is needed or correct here.
--
-- Additive + idempotent (`ADD COLUMN IF NOT EXISTS`, safe to re-run). What breaks until this runs:
-- nothing — every consumer (backend/app/modules/storeops/payroll_salary.py + its call sites in
-- router.py / hr/router.py) widens its `employees` SELECT to include pay_basis/pay_amount inside a
-- try/except that degrades to the pre-migration column list on failure, so the salary feature is a
-- silent no-op (byte-identical hourly behavior) until both this migration and 417 have run.
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS pay_basis  TEXT NOT NULL DEFAULT 'hourly';
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS pay_amount NUMERIC;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 416 complete — storeops.employees.pay_basis/pay_amount (hourly default, no-op until set)' AS status;
