-- 916_store_entity_registry.sql   (storeops schema; SSOT Phase 1 — Migration A, follows 915)
--
-- FOUNDATION for the single-source-of-truth for STORE identity (design blueprint Part 3a). PURELY
-- ADDITIVE: adds a stable machine key to the existing store master and ONE unified store-alias table.
-- It changes NO computed money and rewrites NO reader — a stable surrogate key is introduced with no
-- reader required to consult it yet (the resolver that will read these tables, app/core/identity.py,
-- is wired into NOTHING in Phase 1).
--
-- WHY (audit finding, blueprint Part 1A): there is no stable surrogate key for a physical store
-- anywhere. Every table keys on a free-text `store_code` / `store_address`, so a store's identity is
-- whatever text a given writer happened to use, and the five scattered resolvers drift. This migration
-- gives every `storeops.stores` row ONE `entity_id` (the machine key future development indexes on;
-- the short human `store_code` stays canonical per the owner's 2026-08-11 ruling) and a unified
-- `storeops.store_alias` table that generalises the two alias tables that already exist
-- (commcalc.store_aliases, commcalc.store_merchant_id) into one shape.
--
-- Additive + idempotent, every statement single-line-safe for the tenant SQL runner. NOT NULL is set
-- only AFTER the backfill UPDATE. RLS enabled + GRANT ALL to service_role (the backend uses the service
-- role, which bypasses RLS; the frontend anon key is auth-only — same posture as mig 280). No existing
-- column dropped or retyped, no money math touched.
--
-- REVERT (paste and run to undo — touches no payroll number, drops only additive objects):
--   drop table if exists storeops.store_alias;
--   drop index if exists storeops.stores_entity_uq;
--   alter table storeops.stores alter column entity_id drop not null;
--   alter table storeops.stores alter column entity_id drop default;
--   alter table storeops.stores drop column if exists entity_id;
--   notify pgrst, 'reload schema';

-- ── Stable surrogate key on the store master ────────────────────────────────────────────────────
alter table storeops.stores add column if not exists entity_id uuid;
update storeops.stores set entity_id = gen_random_uuid() where entity_id is null;
alter table storeops.stores alter column entity_id set default gen_random_uuid();
alter table storeops.stores alter column entity_id set not null;
create unique index if not exists stores_entity_uq on storeops.stores (org_id, entity_id);

-- ── Unified store-alias table (every code/spelling/carrier-variant is an ALIAS of one entity) ────
create table if not exists storeops.store_alias (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null,
  -- code | address | carrier_code | salesforce_id | merchant_id | sales_file_spelling
  alias_kind   text not null,
  alias_value  text not null,
  entity_id    uuid not null,
  source       text,                       -- which seed vocabulary produced this alias
  confidence   text default 'seeded',
  created_at   timestamptz default now()
);

-- One alias string per (org, kind) — matches commcalc.store_aliases' UNIQUE(org_id, lower(trim(alias)))
-- posture so a given spelling can belong to at most one entity within its kind.
create unique index if not exists store_alias_uq
  on storeops.store_alias (org_id, alias_kind, lower(trim(alias_value)));
create index if not exists store_alias_entity_idx on storeops.store_alias (org_id, entity_id);
create index if not exists store_alias_lookup_idx
  on storeops.store_alias (org_id, lower(trim(alias_value)));

alter table storeops.store_alias enable row level security;
grant all on storeops.store_alias to service_role;

notify pgrst, 'reload schema';

select 'Migration 916 complete — storeops.stores.entity_id + storeops.store_alias (SSOT store registry)' as status;
