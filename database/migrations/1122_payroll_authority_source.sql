-- 1122_payroll_authority_source.sql — WHICH SOURCE IS A TENANT'S SALARY AUTHORITY: the Payroll
-- Report the payroll run publishes ('measured', the house default), or the salary rows typed into
-- the Expenses sheet ('entered', ruling K2's behaviour verbatim). Band 1000+ (platform).
-- Additive + idempotent (safe to re-run).
--
-- MONEY-TOUCHING — it decides which figure the P&L's `wages` line and the Gross Profit report's
-- expense column carry. It is surfaced for owner approval before it is applied, and the code ships
-- with its own house default, so NOTHING here has to run for the owner's ruling to take effect.
-- What this column buys is the ability for one tenant to opt BACK to the typed sheet.
--
-- OWNER, 2026-11-16, verbatim: "the typer paytol was only for reference take it out, only use that
-- if the store is not running payroll" — and, on the decision card that asked which source should
-- outrank which, "Per-tenant switch, Payroll on by default".
--
-- WHAT IT REPLACES. Ruling K2 (2026-08-10) made a hand-entered payroll row outrank the platform's
-- own measurement, to stop a tenant that typed its salaries into the Expenses sheet getting the
-- typed rows AND a shifts x rate estimate on top. That was right while the measurement WAS an
-- estimate. Since index SS-STOREOPS-10 it is not: the books read the Payroll Report's own producer,
-- the figure the payroll run publishes.
--
-- WHAT IT MOVES, measured live before the change (October 2026, LuxeLink): the typed sheet carried
-- $77,668.69 LESS than the hours and punches measure, across 12 stores, and the P&L printed
-- $57,721.13 of wages against a payroll that ran $121,632.67.
--
-- NEVER A SILENT DELETION: a typed row is superseded only at a store the Payroll Report actually
-- measured. A store that is NOT running payroll keeps whatever was typed for it — which is exactly
-- the case the owner kept the typed figure for.
--
-- REVERT: alter table commcalc.account_config drop column if exists payroll_authority_source;
--         (the code then resolves every tenant to the house default, 'measured'.)

alter table if exists commcalc.account_config
  add column if not exists payroll_authority_source text;

comment on column commcalc.account_config.payroll_authority_source is
  'Which source is this tenant''s salary authority: ''measured'' (the Payroll Report, house '
  'default) or ''entered'' (the salary rows typed into the Expenses sheet). NULL = house default. '
  'Resolved by commcalc/labour_coverage.resolve_payroll_authority — the one home; no caller '
  'compares this string itself.';

-- A value this platform does not recognise must never reach a statement. The code resolves an
-- unknown value to the house default as well, so this constraint is a second line, not the only
-- one: it stops the typo being STORED rather than hoping every reader survives it.
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'account_config_payroll_authority_ck') then
    alter table commcalc.account_config
      add constraint account_config_payroll_authority_ck
      check (payroll_authority_source is null
             or payroll_authority_source in ('measured', 'entered'));
  end if;
end $$;

-- The backend reaches every relation through PostgREST. `account_config` already exists, so this
-- is a re-grant, not a new one, and it is here so that a re-run of this file leaves the table
-- reachable whatever else has happened to it.
grant select, insert, update, delete on commcalc.account_config to service_role;
