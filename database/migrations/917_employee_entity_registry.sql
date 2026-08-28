-- 917_employee_entity_registry.sql   (storeops schema; SSOT Phase 1 — Migration B, follows 916)
--
-- FOUNDATION for the single-source-of-truth for EMPLOYEE identity (design blueprint Part 3a). PURELY
-- ADDITIVE, exactly like 916 but for people: adds a stable machine key to the existing employee master
-- and ONE unified employee-alias table. Changes NO computed money, rewrites NO payroll reader; the
-- resolver that will read these tables (app/core/identity.py) is wired into NOTHING in Phase 1.
--
-- WHY (audit finding, blueprint Part 1B): a person's identity is carried as a numeric `employees.id`
-- in some tables and a business `employee_id` in others, plus a POS `salesperson` name and an
-- `epay_login` in the raw feeds — reconciled today by five partial bridges (_canon_person,
-- _rep_canon_map, _emp_id_variants, business_id_alias_map, _resolve_plan_for). This gives every
-- `storeops.employees` row ONE `entity_id` (the business `employee_id` stays the human key) and a
-- unified `storeops.employee_alias` table so every id form / name variant hangs off one real person.
--
-- Additive + idempotent, single-line-safe. NOT NULL set only AFTER the backfill UPDATE. RLS enabled +
-- GRANT ALL to service_role (backend service role bypasses RLS; anon key is auth-only — mig 280
-- posture). No existing column dropped or retyped, no hours/rate math touched.
--
-- REVERT (paste and run to undo — touches no payroll number, drops only additive objects):
--   drop table if exists storeops.employee_alias;
--   drop index if exists storeops.employees_entity_uq;
--   alter table storeops.employees alter column entity_id drop not null;
--   alter table storeops.employees alter column entity_id drop default;
--   alter table storeops.employees drop column if exists entity_id;
--   notify pgrst, 'reload schema';

-- ── Stable surrogate key on the employee master ─────────────────────────────────────────────────
alter table storeops.employees add column if not exists entity_id uuid;
update storeops.employees set entity_id = gen_random_uuid() where entity_id is null;
alter table storeops.employees alter column entity_id set default gen_random_uuid();
alter table storeops.employees alter column entity_id set not null;
create unique index if not exists employees_entity_uq on storeops.employees (org_id, entity_id);

-- ── Unified employee-alias table (every id form / name variant is an ALIAS of one real person) ───
create table if not exists storeops.employee_alias (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null,
  -- business_id | numeric_id | pos_name | epay_login | name_variant
  alias_kind   text not null,
  alias_value  text not null,
  entity_id    uuid not null,
  source       text,                       -- which seed vocabulary produced this alias
  confidence   text default 'seeded',
  created_at   timestamptz default now()
);

create unique index if not exists employee_alias_uq
  on storeops.employee_alias (org_id, alias_kind, lower(trim(alias_value)));
create index if not exists employee_alias_entity_idx on storeops.employee_alias (org_id, entity_id);
create index if not exists employee_alias_lookup_idx
  on storeops.employee_alias (org_id, lower(trim(alias_value)));

alter table storeops.employee_alias enable row level security;
grant all on storeops.employee_alias to service_role;

notify pgrst, 'reload schema';

select 'Migration 917 complete — storeops.employees.entity_id + storeops.employee_alias (SSOT employee registry)' as status;
