-- 1041_payroll_change_log_employee_id_backfill.sql — fill the employee number on change-log rows that
-- were written without it (index §19.42). Numbered, idempotent, additive. NOT APPLIED — the owner applies.
--
-- ⚠️ DATA BACKFILL — SURFACED FOR OWNER APPROVAL BEFORE APPLYING. No money column is read or written:
-- it sets `storeops.payroll_change_log.employee_id` (an identity) on rows where it is NULL, and only
-- where the row names an employees record of the SAME org that holds a business id. before_value /
-- after_value / field / who / when are never touched.
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────────
-- Owner report 2026-10-03 (Vzone): the change log's 2026-10-02T21:23:19 rows for Shweta (employees
-- id 237, entry_point 'pay_basis_change') show no employee number, while the record holds 'E237'.
-- Cause: `storeops/router.py::update_employee` logged its UPDATE echo and only THEN minted the business
-- id (`_ensure_employee_id`) for a person who had none — people added through Roles & Access were
-- inserted without one (`core/router.py::_ensure_employee`). Fixed in code for every writer: the one
-- inserter `_log_payroll_change` now builds identity from the stored record through
-- `storeops/payroll_log_identity.resolve_log_identity`, which mints a missing id first; the Roles insert
-- now mints. This migration repairs the rows written before that fix.
--
-- MEASURED in live data, read-only, 2026-10-03 (3,098 log rows in all; 18 with employee_id NULL):
--   source_table='employees' (pay edits) — ALL resolvable, ALL filled by this file:     6 rows
--       Cellular Services (house, 00000000-…-0001)  1  (id 231 -> E231, 2026-08-09 pay_rate)
--       Vzone             (f4f1c16e-…)              2  (id 237 -> E237, 2026-10-02 pay_basis, pay_amount)
--       NY LOGISTICS      (ba084e25-…)              3  (id 270 -> E270, 2026-09-26 pay_rate/basis/amount)
--   source_table='tenants' (Luxelink, lunch default) — a tenant-level row, no person:   1 row, left NULL
--   source_table='manual_hours', 'manual_hours_delete' (Luxelink) — REPEAT deletes of an  11 rows, left
--       entry already gone: no person, no date, no hours were recorded because nothing     NULL and
--       was deleted. They are not a missing number; they are rows for a change that did    REPORTED
--       not happen. The writer now records nothing in that case (`delete_manual_hours`).
--       Filling a person onto them would make a non-event look like a real delete, so they
--       are deliberately not touched here.
--
-- HOW IT STAYS REVERTIBLE: every row this file fills is recorded first in
-- `storeops.payroll_change_log_id_backfill` (log id + the value written), so the revert un-fills
-- exactly those rows and nothing a later writer set.
--
-- REVERT:
--   update storeops.payroll_change_log l
--      set employee_id = null
--     from storeops.payroll_change_log_id_backfill b
--    where l.id = b.log_id and l.org_id = b.org_id and l.employee_id = b.filled_employee_id;
--   drop table if exists storeops.payroll_change_log_id_backfill;

create table if not exists storeops.payroll_change_log_id_backfill (
  log_id             uuid primary key,
  org_id             uuid not null,
  filled_employee_id text not null,
  source_employee_pk text not null,
  applied_at         timestamptz not null default now()
);
comment on table storeops.payroll_change_log_id_backfill is
  'Rows of storeops.payroll_change_log whose NULL employee_id migration 1041 filled from the same-org '
  'storeops.employees record (index §19.42). Exists so the backfill can be reverted exactly.';

grant select, insert, update, delete on storeops.payroll_change_log_id_backfill to service_role;
alter table storeops.payroll_change_log_id_backfill enable row level security;

-- (1) Record what will be filled. Org-scoped join; the employees pk is matched as text because
--     payroll_change_log.source_id is text. Re-running inserts nothing new (on conflict do nothing).
insert into storeops.payroll_change_log_id_backfill (log_id, org_id, filled_employee_id, source_employee_pk)
select l.id, l.org_id, btrim(e.employee_id), e.id::text
  from storeops.payroll_change_log l
  join storeops.employees e
    on e.org_id = l.org_id
   and e.id::text = l.source_id
 where l.source_table = 'employees'
   and (l.employee_id is null or btrim(l.employee_id) = '')
   and nullif(btrim(e.employee_id), '') is not null
on conflict (log_id) do nothing;

-- (2) Fill exactly those rows, and only while they are still blank (idempotent; never overwrites).
update storeops.payroll_change_log l
   set employee_id = b.filled_employee_id
  from storeops.payroll_change_log_id_backfill b
 where l.id = b.log_id
   and l.org_id = b.org_id
   and (l.employee_id is null or btrim(l.employee_id) = '');

notify pgrst, 'reload schema';

-- Expected on the 2026-10-03 data: 6 rows recorded and filled (1 house, 2 Vzone, 3 NY LOGISTICS).
select org_id, count(*) as rows_filled
  from storeops.payroll_change_log_id_backfill
 group by org_id
 order by org_id;
