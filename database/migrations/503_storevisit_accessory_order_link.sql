-- 503_storevisit_accessory_order_link.sql
-- TENANT-CONFIGURABLE accessory-reorder link on the DM Store Visit flow. Today the "Order accessories"
-- button on the store-visit checklist (Phase 1) is hard-coded to https://www.vaccessorize.com (a
-- Boost-house accessory distributor) in BOTH the backend response (storevisit/router.py) and a
-- frontend constant — a non-Boost tenant (e.g. Total Wireless / Luxelink) has no distributor there and
-- gets sent to the wrong site, with no way to change it (2026-07-16 luxelink-parity audit finding).
--
-- DOCTRINE: additive / idempotent. No row for a tenant -> the backend falls back to the SAME
-- vAccessorize URL as today, so the house org (and every tenant that hasn't set this) is byte-for-byte
-- unchanged.

create table if not exists storeops.store_visit_config (
  org_id                  uuid primary key,
  accessory_order_url     text,          -- null -> code default (vAccessorize)
  accessory_order_label   text,          -- null -> code default ("Order on vAccessorize.com")
  updated_at              timestamptz not null default now()
);

-- RLS: open_all (small per-tenant settings row; the backend uses the service key) — mirrors mig 111/501.
alter table storeops.store_visit_config enable row level security;
do $$ begin
  create policy open_all on storeops.store_visit_config for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
grant all on storeops.store_visit_config to anon, authenticated, service_role;

-- No seed: no row for a tenant reproduces today's hard-coded vAccessorize link exactly.

notify pgrst, 'reload schema';
select '503 complete — storeops.store_visit_config (tenant-configurable accessory-order link) ready' as status;
