-- 1120_corrections_effective_from.sql
--
-- §STOREOPS-07 (PR #679). Adds the per-org DATE that decides when a hand-typed hours correction
-- (storeops.manual_hours) starts counting toward pay, and sets it for NY LOGISTICS.
--
-- WHY A DATE AND NOT A SWITCH. All four tenants already declare `count_manual_hours = true`. The
-- code REQUIRES this date for a fold: an org whose value is NULL folds nothing and the drill-down
-- says which date is missing. So adding the column changes no figure anywhere -- every org starts
-- NULL -- and each tenant is turned on by naming its own date, one row at a time. The permissive
-- reading (NULL = correct every day) would have restated 616 corrections across already-paid
-- months, double-counting three house corrections already rebuilt by hand as payroll_approval
-- adjustments in October (E029 +18.49, E043 -10.97, E282 -273.56).
--
-- OWNER DECISION 2026-11-12, Sanjot: *"for ny logistics pick this mondays date"* -> 2026-11-09,
-- the Monday of the week in which he asked. NY LOGISTICS only; the other three stay NULL until he
-- names their dates.
--
-- MEASURED BEFORE WRITING (production, read-only, 2026-11-12):
--   · NY LOGISTICS (ba084e25-adcf-4d4d-9cc5-d3728775513f) holds ZERO storeops.manual_hours rows,
--     so this restates nothing for it today. It arms the mechanism: a correction typed for any day
--     on or after 2026-11-09 will reach the figure.
--   · Its shift rows stop at 2026-10-25 while its time clock is current, so its hours come off the
--     clock -- which is why there was no shift row to edit in the first place.
--
-- Idempotent and additive. No grant clause: this creates no relation (CORE-13 applies to
-- `create table` / `create view`), and storeops.payroll_hours_basis_config is already granted.
--
-- REVERT:
--   update storeops.payroll_hours_basis_config
--      set corrections_effective_from = null
--    where org_id = 'ba084e25-adcf-4d4d-9cc5-d3728775513f';
--   alter table storeops.payroll_hours_basis_config drop column if exists corrections_effective_from;
--   -- Dropping the column returns every org to "folds nothing", which is the pre-1120 behaviour.

alter table if exists storeops.payroll_hours_basis_config
  add column if not exists corrections_effective_from date;

comment on column storeops.payroll_hours_basis_config.corrections_effective_from is
  'Per-org date on and after which a storeops.manual_hours correction is folded into paid hours. '
  'NULL = no fold at all (REQUIRED for a fold, never a licence): the figure resolves exactly as it '
  'does today and the drill-down names this column as the missing one. Its OWN column, deliberately '
  'not clocked_basis_effective_from, which means when the org''s clocked-only basis began. §14y / '
  '§STOREOPS-07.';

-- NY LOGISTICS only. Guarded on org_id so re-running cannot touch another tenant's date.
update storeops.payroll_hours_basis_config
   set corrections_effective_from = date '2026-11-09',
       updated_at                 = now()
 where org_id = 'ba084e25-adcf-4d4d-9cc5-d3728775513f';
