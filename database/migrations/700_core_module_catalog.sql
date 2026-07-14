-- 700_core_module_catalog.sql — ONE canonical module registry (platform-core-3)
--
-- WHY: four divergent module lists drifted apart — MODULE_CATALOG (entitlements),
-- _mods (seeded role permissions), _level_role_perms.M (org-level role permissions) and rbac.ts
-- module tags — with an `account` (backend) vs `accounts` (frontend) key mismatch. This table is
-- the SINGLE SOURCE OF TRUTH for module_keys. The in-code MODULE_CATALOG dict is seeded from the
-- SAME values and is the fallback until this runs, so the app is byte-identical whether or not this
-- migration has been applied.
--
-- SCOPE: this is a GLOBAL system registry, NOT tenant data — there is deliberately NO org_id column
-- (tenant entitlement lives in storeops.tenant_modules, which keys off these module_keys). XM-5.
--
-- SAFE: additive + idempotent (create ... if not exists / on conflict do nothing). Re-runnable.
-- Degrades gracefully — every backend reader falls back to the in-code catalog if this is unrun.

create schema if not exists core;

-- Canonical module registry: one row per module_key.
create table if not exists core.module_catalog (
  key            text primary key,                    -- CANONICAL module_key (e.g. 'account', never 'accounts')
  label          text not null,                       -- human label (billing picker, tenant entitlement view)
  sort_order     int  not null default 100,           -- display order in pickers
  is_entitlement boolean not null default true,       -- true = a tenant-entitlement module (mirrors MODULE_CATALOG)
  created_at     timestamptz not null default now()
);

-- Alias map: a legacy / frontend key that MUST resolve to a canonical module_key. This is how the
-- historical frontend tag `accounts` reconciles to the backend canonical `account` without a data
-- migration of stored role permissions.
create table if not exists core.module_alias (
  alias         text primary key,                     -- non-canonical key seen in the wild
  canonical_key text not null references core.module_catalog(key) on delete cascade
);

-- Seed the canonical registry (mirror of app.modules.core.entitlements.MODULE_CATALOG).
insert into core.module_catalog (key, label, sort_order) values
  ('commissions',  'Commissions',        10),
  ('targets',      'Daily Targets',      20),
  ('asset',        'Asset & Inventory',  30),
  ('vip',          'VIP Wireless',       40),
  ('storeops',     'StoreOps',           50),
  ('closing',      'Daily Closing',      60),
  ('notify',       'Notifications',      70),
  ('helpdesk',     'Helpdesk',           80),
  ('hr',           'HR / People',        90),
  ('account',      'Accounting',        100),
  ('ai_assistant', 'AI Assistant',      110)
on conflict (key) do nothing;

-- Reconcile the account/accounts mismatch: canonical is the backend key `account`; `accounts`
-- (the historical frontend nav / roles-editor tag) maps to it.
insert into core.module_alias (alias, canonical_key) values
  ('accounts', 'account')
on conflict (alias) do nothing;

-- RLS: readable/writable via the service key (matches every other core.* table); the registry is
-- global (no org_id) so there is nothing tenant-sensitive to isolate.
alter table core.module_catalog enable row level security;
alter table core.module_alias   enable row level security;
do $$ begin
  create policy open_all on core.module_catalog for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
do $$ begin
  create policy open_all on core.module_alias   for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
grant all on core.module_catalog to anon, authenticated, service_role;
grant all on core.module_alias   to anon, authenticated, service_role;

notify pgrst, 'reload schema';
select '700 complete — core.module_catalog (11 modules) + core.module_alias (accounts→account)' as status;
