-- 1120_corrections_effective_from.sql
--
-- §STOREOPS-07 (PR #679). Adds the per-org DATE that decides when a hand-typed hours correction
-- (storeops.manual_hours) starts counting toward pay, and names that date for the two tenants the
-- owner has ruled on: NY LOGISTICS (2026-11-09) and Luxelink Wireless LLC (2026-11-06).
--
-- WHY A DATE AND NOT A SWITCH. All four tenants already declare `count_manual_hours = true`. The
-- code REQUIRES this date for a fold: an org whose value is NULL folds nothing and the drill-down
-- says which date is missing. So adding the column changes no figure anywhere -- every org starts
-- NULL -- and each tenant is turned on by naming its own date, one row at a time. The permissive
-- reading (NULL = correct every day) would have restated 616 corrections across already-paid
-- months, double-counting three house corrections already rebuilt by hand as payroll_approval
-- adjustments in October (E029 +18.49, E043 -10.97, E282 -273.56).
--
-- OWNER DECISIONS 2026-11-12, Sanjot, in the order he made them:
--   · *"for ny logistics pick this mondays date"* -> NY LOGISTICS from 2026-11-09, the Monday of
--     the week in which he asked.
--   · Asked which date Luxelink should take, given that three corrections had just been typed there
--     for 6, 7 and 8 November, he chose **2026-11-06** over the same Monday -- the earlier date is
--     the only one that reaches the rows a human had actually entered.
-- Cellfonz R Us and Vzone stay NULL until he names their dates.
--
-- MEASURED BEFORE WRITING (production, read-only, 2026-11-12):
--   · NY LOGISTICS (ba084e25-adcf-4d4d-9cc5-d3728775513f) holds ZERO storeops.manual_hours rows,
--     so this restates nothing for it today. It arms the mechanism: a correction typed for any day
--     on or after 2026-11-09 will reach the figure.
--   · Its shift rows stop at 2026-10-25 while its time clock is current, so its hours come off the
--     clock -- which is why there was no shift row to edit in the first place.
--   · Luxelink Wireless LLC (854f6d7b-6590-4e4d-88ab-646f560d4f4c) holds 568 storeops.manual_hours
--     rows in all, and the date is what decides which of them count. Counted by REST with
--     Prefer: count=exact: 568 total, THREE with work_date >= 2026-11-06, ZERO with work_date
--     >= 2026-11-09, and 565 dated earlier than 2026-11-06. So 2026-11-06 folds exactly three rows
--     and leaves the other 565 -- months of already-paid history -- exactly as they resolve today,
--     while 2026-11-09 would fold none at all.
--   · Those three rows are the ones a human typed on 2026-11-12 at 17:15-17:16 UTC, all on employee
--     E254, reason "add": work_date 2026-11-06 +9.00h, 2026-11-07 +9.00h, 2026-11-08 +7.00h. The
--     whole effect of this line is therefore +25.00h on one employee across one week.
--
-- Idempotent and additive. No grant clause: this creates no relation (CORE-13 applies to
-- `create table` / `create view`), and storeops.payroll_hours_basis_config is already granted.
--
-- REVERT:
--   update storeops.payroll_hours_basis_config
--      set corrections_effective_from = null
--    where org_id in ('ba084e25-adcf-4d4d-9cc5-d3728775513f',
--                     '854f6d7b-6590-4e4d-88ab-646f560d4f4c');
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

-- Each tenant named separately, guarded on its own org_id, so re-running cannot touch a date the
-- owner has not named and the two dates can never be confused for one house default.

-- NY LOGISTICS -- arms the mechanism; it holds no corrections today, so this restates nothing.
update storeops.payroll_hours_basis_config
   set corrections_effective_from = date '2026-11-09',
       updated_at                 = now()
 where org_id = 'ba084e25-adcf-4d4d-9cc5-d3728775513f';

-- Luxelink Wireless LLC -- folds the three corrections typed 2026-11-12 for 6/7/8 Nov (+25.00h on
-- E254) and leaves its other 565, all dated earlier, untouched. This one DOES move a figure, which
-- is why it was put to the owner before being written.
update storeops.payroll_hours_basis_config
   set corrections_effective_from = date '2026-11-06',
       updated_at                 = now()
 where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';

-- Read both back in the same run, so you see what you set rather than trusting it.
select org_id, count_manual_hours, corrections_effective_from
  from storeops.payroll_hours_basis_config
 order by org_id;
