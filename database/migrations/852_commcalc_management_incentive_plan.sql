-- 852_commcalc_management_incentive_plan.sql   (commcalc schema)
--
-- MANAGEMENT INCENTIVE PLAN — a per-MANAGER, store-AGGREGATED incentive with target-based component
-- payouts plus qualification-gated flat bonuses. One framework for EVERY management level (district
-- manager, market manager, regional, …): a plan is built once and ASSIGNED to whoever should get it,
-- and different plans can be set up for different levels. It reuses the SAME assignment basis as the
-- employee commission_plan engine (migration 059): scope precedence employee > role > store > market >
-- default, so "assign an employee to a plan" works exactly like it does for commissions.
--
-- This is deliberately NOT modeled on the per-sale-line commission_plan engine itself: that engine
-- multiplies a rate by each individual sale line and has no concept of "$8,000/store target across 7
-- stores" or a "cash deposited twice weekly" gate. A management incentive is scored once per manager
-- per period against the roll-up of the stores that manager is responsible for.
--
-- OWNER SPEC (2026-08-15) — the first plan seeded is the Total Wireless DEFAULT for a district manager,
-- fully overridable by any tenant (clone → edit; a tenant row wins over the house default). Two sections:
--
--   A. STORE PERFORMANCE — component payouts, rate × actual aggregated across the manager's stores,
--      shown against a per-store target, capped at the target opportunity by default:
--        Accessory Sales  2%     $8,000/store × 7 stores  → $1,120 full
--        VHI / FIOS       $2/ea  10/store    × 6 stores  → $120 full
--        Edge Activations $5/ea  10/store    × 6 stores  → $300 full
--   B. BUSINESS PERFORMANCE — flat bonuses gated by metrics:
--        Consolidated / Overall   $300 (default, management-editable)  — earned on passing ALL
--                                 qualification metrics (Zulu, 3MR, TWP, Address Checks from the KPI
--                                 module + Cash Deposit compliance).
--        Inventory Control        $250  — earned when NO device was in stock >10 days at any point in
--                                 the period (from inventory aging).
--
-- MONEY POSTURE: nothing here computes or pays. These tables hold the PLAN DEFINITION only. Computation
-- (per-manager per-period) and any accrual into the ledger live in code and write their own rows in
-- commcalc.management_incentive_payout (below), which is a draft→approved→paid record, never auto-paid.
--
-- Additive + idempotent: CREATE IF NOT EXISTS, RLS open_all like every sibling commcalc table, no seed
-- in the SQL (the Total Wireless default is seeded from code, never-clobber, so the wording/amounts are
-- correctable in a normal deploy — same rationale as the training/support seeds).

-- ── 1. Plan header ───────────────────────────────────────────────────────────────────────────────
create table if not exists commcalc.management_incentive_plan (
  id                        uuid primary key default gen_random_uuid(),
  org_id                    uuid not null,
  carrier_id                uuid,                     -- FK-by-convention to commcalc.carrier; NULL = any carrier
  name                      text not null,
  level                     text,                     -- free label for the management level this plan targets
                                                      -- ('district_manager','market_manager','regional',…); display only
  is_active                 boolean not null default true,
  is_default                boolean not null default false,   -- the house/Total-Wireless default (seeded)
  period_type               text not null default 'monthly',  -- 'monthly' (only mode for now)
  consolidated_bonus_amount numeric default 300,      -- management-editable; the "Overall" bonus $
  notes                     text,
  updated_by                text,                     -- 'seed' when written by the platform seeder (never-clobber)
  created_at                timestamptz default now(),
  updated_at                timestamptz default now(),
  unique (org_id, name)
);

-- ── 2. Store-performance components (rate × actual vs per-store target) ───────────────────────────
-- kind:          'percent'  → payout = rate × actual_dollars           (rate 0.02 = 2%)
--                'per_unit' → payout = rate × actual_units             (rate = $ per unit)
-- metric_source: which actual to pull ('accessory_gp' | 'vhi_fios_count' | 'edge_count' | ...);
--                resolved in code against existing sales actuals, aggregated over the manager's stores.
-- target_per_store + store_count define the OPPORTUNITY (goal); store_count NULL = use the manager's
--                own store-set size from the org tree. cap_at_target = pay no more than the opportunity.
create table if not exists commcalc.management_incentive_component (
  id              uuid primary key default gen_random_uuid(),
  plan_id         uuid not null references commcalc.management_incentive_plan(id) on delete cascade,
  org_id          uuid not null,
  label           text not null,
  kind            text not null check (kind in ('percent', 'per_unit')),
  rate            numeric not null default 0,
  metric_source   text not null,
  target_per_store numeric default 0,
  store_count     integer,                            -- NULL = the manager's actual store count
  cap_at_target   boolean not null default true,
  sort            integer default 0
);

-- ── 3. Flat bonuses (consolidated / inventory / other) ───────────────────────────────────────────
-- kind:      'consolidated'      → the Overall bonus; amount comes from the plan header (editable).
--            'inventory_selloff' → the standalone inventory-control bonus.
--            'flat'              → any other tenant-defined flat bonus.
-- gated_by:  'qualifiers'      → earned only when the plan's qualifiers all pass (the $300 default).
--            'inventory_aging' → earned when no device exceeded config.max_days in stock in the period.
--            'manual'          → management marks earned/not on the statement.
--            'none'            → always paid.
-- config:    per-kind knobs, e.g. {"max_days": 10} for inventory_aging.
create table if not exists commcalc.management_incentive_bonus (
  id         uuid primary key default gen_random_uuid(),
  plan_id    uuid not null references commcalc.management_incentive_plan(id) on delete cascade,
  org_id     uuid not null,
  label      text not null,
  kind       text not null check (kind in ('consolidated', 'inventory_selloff', 'flat')),
  amount     numeric default 0,                       -- ignored for 'consolidated' (uses the header amount)
  gated_by   text not null default 'none' check (gated_by in ('qualifiers', 'inventory_aging', 'manual', 'none')),
  config     jsonb default '{}'::jsonb,
  sort       integer default 0
);

-- ── 4. Qualification metrics (the gate for the consolidated bonus) ────────────────────────────────
-- metric_key: 'zulu' | 'tmr3' | 'twp' | 'address_checks' | 'cash_deposit' | (tenant-defined).
-- source:     'kpi'          → pulled from the KPI module (carrier_kpi_metric / uploaded KPIs).
--             'cash_deposit' → derived from the bank-deposit / closing cash data.
--             'inventory'    → derived from inventory aging.
--             'manual'       → entered on the statement.
-- op/threshold: pass test, e.g. zulu lt 5, tmr3 gt 75, twp gt 80, address_checks gt 50.
-- config:     metric knobs, e.g. cash_deposit {"day": "sat", "max_amount": 0, "days": 2}.
-- applies_to: which bonus this gates ('consolidated' by default).
create table if not exists commcalc.management_incentive_qualifier (
  id          uuid primary key default gen_random_uuid(),
  plan_id     uuid not null references commcalc.management_incentive_plan(id) on delete cascade,
  org_id      uuid not null,
  metric_key  text not null,
  label       text,
  source      text not null default 'kpi' check (source in ('kpi', 'cash_deposit', 'inventory', 'manual')),
  op          text not null default 'gte' check (op in ('lt', 'lte', 'gt', 'gte', 'eq')),
  threshold   numeric,
  unit        text default 'percent',
  config      jsonb default '{}'::jsonb,
  applies_to  text not null default 'consolidated',
  sort        integer default 0
);

-- ── 5. Assignment — who a plan applies to (SAME shape/precedence as commission_plan_assignment) ───
-- scope: 'employee' | 'role' | 'market' | 'store' | 'default'. The Total Wireless default is seeded as
-- scope='role', scope_value='district_manager'. Assigning a specific manager = an 'employee'-scope row,
-- which wins over role/market/default (precedence employee>role>store>market>default), exactly like the
-- employee commission plan. A tenant's own plan with a higher priority overrides the house default.
create table if not exists commcalc.management_incentive_assignment (
  id          uuid primary key default gen_random_uuid(),
  plan_id     uuid not null references commcalc.management_incentive_plan(id) on delete cascade,
  org_id      uuid not null,
  scope       text not null default 'role' check (scope in ('employee', 'role', 'market', 'store', 'default')),
  scope_value text,
  priority    integer default 0,
  created_at  timestamptz default now()
);

-- ── 6. Computed payout records (the statement + the accrual source; draft → approved → paid) ───────
-- One row per manager per period per plan. `breakdown` holds the full computed detail (each component's
-- actual/target/payout, each qualifier's value/pass, each bonus earned) so the statement + PDF render
-- from a stored snapshot and pay is auditable. `qualified` = did the consolidated gate pass. Management
-- edits (override amount / earned) are stored here, never on the plan definition.
create table if not exists commcalc.management_incentive_payout (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid not null,
  plan_id         uuid,
  employee_id     text not null,
  employee_name   text,
  period          text not null,                      -- 'YYYY-MM'
  store_codes     text[],                             -- the manager's store set used for this computation
  breakdown       jsonb default '{}'::jsonb,
  component_total numeric default 0,
  bonus_total     numeric default 0,
  total           numeric default 0,
  qualified       boolean,
  status          text not null default 'draft' check (status in ('draft', 'approved', 'paid')),
  override_note   text,
  decided_by      text,
  decided_by_name text,
  decided_at      timestamptz,
  created_at      timestamptz default now(),
  updated_at      timestamptz default now(),
  unique (org_id, plan_id, employee_id, period)
);

create index if not exists mgmt_incentive_plan_org on commcalc.management_incentive_plan (org_id);
create index if not exists mgmt_incentive_component_plan on commcalc.management_incentive_component (plan_id);
create index if not exists mgmt_incentive_bonus_plan on commcalc.management_incentive_bonus (plan_id);
create index if not exists mgmt_incentive_qualifier_plan on commcalc.management_incentive_qualifier (plan_id);
create index if not exists mgmt_incentive_assignment_plan on commcalc.management_incentive_assignment (plan_id);
create index if not exists mgmt_incentive_assignment_scope on commcalc.management_incentive_assignment (org_id, scope, scope_value);
create index if not exists mgmt_incentive_payout_lookup on commcalc.management_incentive_payout (org_id, period, employee_id);

-- RLS: open_all to the app roles (service_role does the real work), same posture as every commcalc table.
do $$
declare t text;
begin
  foreach t in array array['management_incentive_plan','management_incentive_component','management_incentive_bonus',
                           'management_incentive_qualifier','management_incentive_assignment','management_incentive_payout']
  loop
    execute format('alter table commcalc.%I enable row level security', t);
    execute format('drop policy if exists open_all on commcalc.%I', t);
    execute format('create policy open_all on commcalc.%I for all to anon, authenticated using (true) with check (true)', t);
    execute format('grant all on commcalc.%I to anon, authenticated, service_role', t);
  end loop;
end $$;

notify pgrst, 'reload schema';

select 'Migration 852 complete — commcalc.management_incentive_* (plan, component, bonus, qualifier, assignment, payout)' as status;

-- REVERT (undo — no payout is computed here, so this touches no paid money):
-- drop table if exists commcalc.management_incentive_payout;
-- drop table if exists commcalc.management_incentive_assignment;
-- drop table if exists commcalc.management_incentive_qualifier;
-- drop table if exists commcalc.management_incentive_bonus;
-- drop table if exists commcalc.management_incentive_component;
-- drop table if exists commcalc.management_incentive_plan;
