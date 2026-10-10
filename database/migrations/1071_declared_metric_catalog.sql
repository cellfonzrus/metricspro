-- 1071_declared_metric_catalog.sql
-- WHAT THIS BUSINESS DECLARES AT THE END OF A DAY, AND WHAT A UNIT IS WORTH — one registry, read by
-- the closing form, the incentive rules and (next) the targets editor.
--
-- Owner directive 2026-10-10 (NY Logistics / UPS Store): "the categories on teh submit daily closing
-- should be available for the admin to add based on the business requirement and also when you add a
-- category it should be available in teh cincentive payout plan to be assesed to be paid out … i dont
-- wnat them to be hard coded it should be a design effect".
--
-- THE CLASS. mig 501 already let an admin declare categories, but only as COUNTS, only per store-day,
-- and with no notion of what a unit is worth. So three facts had no home: a category's PRICE, a
-- category's MONEY, and WHO declared it. Without the third, "which employee generated the most
-- revenue today" is unanswerable — a daily_closing row carries exactly one employee_name, whoever
-- submitted it, and crediting them with the store's day would be a fabricated attribution.
--
-- DOCTRINE: additive / idempotent / REVERT-noted. Every new column has a default that reproduces mig
-- 501 exactly, so a tenant that has declared nothing — and Cellfonz R Us, Luxelink and Vzone, which
-- have — behaves byte-for-byte as it does today. No existing row is rewritten.
--
-- NOT MONEY-TOUCHING AS APPLIED: this creates empty config tables and nullable columns. It computes,
-- stores and pays nothing on its own; an incentive is only owed once an admin writes a rule row.
--
-- REVERT:
--   drop table if exists commcalc.incentive_rule_assignment;
--   drop table if exists commcalc.incentive_rule;
--   drop table if exists commcalc.closing_metric_line;
--   alter table commcalc.closing_count_field_def
--     drop column if exists value_kind, drop column if exists declared_by,
--     drop column if exists unit_price, drop column if exists in_revenue;

-- ─────────────────────────────────────────────────────────────────────────────────────────────────
-- 1) The declared-category registry grows the three missing facts.
-- ─────────────────────────────────────────────────────────────────────────────────────────────────
alter table commcalc.closing_count_field_def
  add column if not exists value_kind  text    not null default 'count',   -- 'count'|'amount'|'both'
  add column if not exists declared_by text    not null default 'store',   -- 'store'|'employee'
  add column if not exists unit_price  numeric,                            -- $ per unit; null = none
  add column if not exists in_revenue  boolean;                            -- null = derive (see below)

comment on column commcalc.closing_count_field_def.value_kind is
  'How the category is measured. count = a tally (drop-offs). amount = a dollar figure (printing
   sales). both = a tally that also carries its own dollars. Default ''count'' reproduces mig 501.';
comment on column commcalc.closing_count_field_def.declared_by is
  'Who declares it. ''store'' = one figure for the store-day (mig 501). ''employee'' = a line per
   person, which is what makes per-employee revenue and per-employee incentive possible.';
comment on column commcalc.closing_count_field_def.unit_price is
  'Dollars a single unit earns, so a COUNTED category can join gross revenue without the rep having
   to type a dollar figure. Null = the category carries no price.';
comment on column commcalc.closing_count_field_def.in_revenue is
  'Does this category''s money count as the day''s gross revenue. NULL means derive it: an
   amount-valued category, or a counted one with a unit price, is revenue; a bare tally is not.
   Set it false to track a volume that is not a sale.';

do $$ begin
  alter table commcalc.closing_count_field_def
    add constraint closing_count_field_def_value_kind_ck
    check (value_kind in ('count', 'amount', 'both'));
exception when duplicate_object then null; end $$;

do $$ begin
  alter table commcalc.closing_count_field_def
    add constraint closing_count_field_def_declared_by_ck
    check (declared_by in ('store', 'employee'));
exception when duplicate_object then null; end $$;

-- ─────────────────────────────────────────────────────────────────────────────────────────────────
-- 2) The richer declarations. `daily_closing.counts` (mig 501) stays EXACTLY as it is and keeps
--    serving count-only, store-level categories; anything with money or a person on it lands here.
--    Two physical stores, ONE reader: `closing/metric_catalog.declared()` folds them. Nothing else
--    may read either store directly — `harness_metric_catalog_lock.py` fails the build if it does.
-- ─────────────────────────────────────────────────────────────────────────────────────────────────
create table if not exists commcalc.closing_metric_line (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null,
  store_code     text not null,
  close_date     date not null,
  metric_key     text not null,          -- -> closing_count_field_def.field_key
  employee_name  text,                   -- null = declared for the store, not a person
  units          numeric default 0,
  amount         numeric default 0,
  closing_row_id uuid,                   -- the commcalc.daily_closing row this was submitted with
  submitted_by   uuid,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (org_id, store_code, close_date, metric_key, employee_name)
);
create index if not exists closing_metric_line_org_day
  on commcalc.closing_metric_line(org_id, close_date);
create index if not exists closing_metric_line_store_day
  on commcalc.closing_metric_line(org_id, store_code, close_date);
create index if not exists closing_metric_line_employee
  on commcalc.closing_metric_line(org_id, employee_name, close_date);

-- NOTE on the unique key: `employee_name` is nullable and Postgres treats NULLs as distinct in a
-- unique constraint, so a store-level line is de-duplicated by the writer (delete-then-insert for the
-- store-day), exactly as the closing form already replaces a day's counts on a corrected resubmit.

-- ─────────────────────────────────────────────────────────────────────────────────────────────────
-- 3) The incentive rules. The SHAPES live in code (per_unit / percent_of_amount / threshold_pool);
--    every rule, category, rate and threshold is a row here. RULE TWO: no tenant, carrier or product
--    name appears in code.
-- ─────────────────────────────────────────────────────────────────────────────────────────────────
create table if not exists commcalc.incentive_rule (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null,
  rule_key     text not null,              -- stable slug, used by the audit trail
  label        text not null,              -- 'Daily Cash Pool'
  description  text,                       -- the plain-English line the admin screen shows
  rule_kind    text not null,              -- 'per_unit' | 'percent_of_amount' | 'threshold_pool'
  qualifier    jsonb not null default '{"scope":"none"}'::jsonb,
  payout       jsonb not null default '{}'::jsonb,
  is_active    boolean default true,
  sort_order   int default 0,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  updated_by   uuid,
  unique (org_id, rule_key)
);
create index if not exists incentive_rule_org on commcalc.incentive_rule(org_id, is_active);

comment on column commcalc.incentive_rule.qualifier is
  'The gate, as {scope, basis, metric_keys[], op, threshold}. scope ''store''|''employee''|''none'';
   basis ''revenue''|''units''|''amount'' (revenue with no metric_keys = the day''s declared gross);
   op gte|gt|lte|lt|eq. Several metric_keys are COMBINED — that is how "overnight + express together
   must reach 5" is said without naming either in code. A gate that cannot be verified fails CLOSED.';
comment on column commcalc.incentive_rule.payout is
  'What it pays, as {basis, metric_keys[], rate, split, cap}. per_unit: rate = $ per unit of the
   employee''s own count. percent_of_amount: rate = a fraction of the employee''s own dollars.
   threshold_pool: rate x the STORE''s figure makes a pool, divided on `split`
   (hours | individual_sales | equal). A pool whose split cannot be measured is reported unallocated,
   never quietly turned into an equal share.';

do $$ begin
  alter table commcalc.incentive_rule
    add constraint incentive_rule_kind_ck
    check (rule_kind in ('per_unit', 'percent_of_amount', 'threshold_pool'));
exception when duplicate_object then null; end $$;

-- Who a rule applies to. Same scope vocabulary and the same precedence as the employee commission
-- plan and the management incentive (employee > role > store > market > default) — resolved by
-- `commcalc/management_incentive.resolve_plan`, never by a second ladder.
create table if not exists commcalc.incentive_rule_assignment (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid not null,
  rule_id     uuid not null references commcalc.incentive_rule(id) on delete cascade,
  scope       text not null default 'default',   -- employee|role|store|market|default
  scope_value text,
  priority    int default 0,
  created_at  timestamptz not null default now()
);
create index if not exists incentive_rule_assignment_rule
  on commcalc.incentive_rule_assignment(rule_id);
create index if not exists incentive_rule_assignment_org
  on commcalc.incentive_rule_assignment(org_id, scope);

do $$ begin
  alter table commcalc.incentive_rule_assignment
    add constraint incentive_rule_assignment_scope_ck
    check (scope in ('employee', 'role', 'store', 'market', 'default'));
exception when duplicate_object then null; end $$;

-- ─────────────────────────────────────────────────────────────────────────────────────────────────
-- 4) RLS — config + declaration tables served by the backend's service key. Mirrors mig 501 / 111.
-- ─────────────────────────────────────────────────────────────────────────────────────────────────
alter table commcalc.closing_metric_line        enable row level security;
alter table commcalc.incentive_rule             enable row level security;
alter table commcalc.incentive_rule_assignment  enable row level security;
do $$ begin
  create policy open_all on commcalc.closing_metric_line
    for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
do $$ begin
  create policy open_all on commcalc.incentive_rule
    for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
do $$ begin
  create policy open_all on commcalc.incentive_rule_assignment
    for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
grant all on commcalc.closing_metric_line       to anon, authenticated, service_role;
grant all on commcalc.incentive_rule            to anon, authenticated, service_role;
grant all on commcalc.incentive_rule_assignment to anon, authenticated, service_role;

-- NO SEED. The registry is overlay-only: an empty table reproduces today's behaviour exactly, for
-- every tenant. NY Logistics' own categories and rules are typed on the admin screens, which is the
-- point of the change — a tenant's rules are data, never a migration.

notify pgrst, 'reload schema';
select '1071 complete — declared-metric catalog, per-employee declarations and incentive rules ready'
       as status;
