-- 851_storeops_store_timezone.sql   (storeops schema)
--
-- PER-STORE TIME ZONE — so time-clock and every other business-local calculation is applied in the
-- store's OWN zone, not one zone for the whole tenant.
--
-- THE BUG THIS FIXES (owner report 2026-08-15): the auto-clock-out sweep reads each shift's end_time
-- ("19:00") as a business-local HH:MM and combines it with ONE timezone for the whole tenant
-- (storeops.tenants.timezone, defaulting to America/New_York). A tenant that operates in more than one
-- zone — Chicago stores (America/Chicago, Central) alongside NY/NJ stores (America/New_York, Eastern)
-- — therefore had every Chicago "7 PM" shift read as 7 PM Eastern = 6 PM Central, and the sweep
-- force-clocked-out Chicago reps a full hour early. Time zone must resolve PER STORE.
--
-- RESOLUTION ORDER (implemented in code, storeops.router._biz_tz_for_store):
--   1. storeops.stores.timezone   — this store's own IANA zone, when set (added here)
--   2. storeops.tenants.timezone  — the tenant default (migration 085; set at onboarding)
--   3. settings.BUSINESS_TZ       — the house-wide default (America/New_York)
-- Every level is DATA, not hard-coded logic: no store, market or zone name is baked into the clock.
--
-- Additive + idempotent: ADD COLUMN IF NOT EXISTS; the seed below only writes rows whose timezone is
-- still NULL (never clobbers a value an admin has already chosen), and is keyed on the existing
-- storeops.stores.market values — it assigns no zone to a store whose market it doesn't recognize,
-- leaving it on the tenant default. Re-running is safe.

alter table storeops.stores add column if not exists timezone text;

-- ── Seed the two known multi-zone markets from their existing market label (owner-approved 2026-08-15:
--    "assign Chicago and NY their time zones from the back end … by market"). NULL-only, so a manual
--    override set later in the store settings UI is never overwritten. Case-insensitive on the label. ──
update storeops.stores
   set timezone = 'America/Chicago'
 where timezone is null
   and lower(btrim(market)) in ('chicago', 'chi', 'il', 'illinois');

update storeops.stores
   set timezone = 'America/New_York'
 where timezone is null
   and lower(btrim(market)) in ('ny', 'new york', 'nyc', 'nj', 'new jersey');

notify pgrst, 'reload schema';

select
  coalesce(timezone, '(tenant default)') as zone,
  count(*) as stores
from storeops.stores
group by 1
order by 2 desc;

select 'Migration 851 complete — storeops.stores.timezone added + seeded by market (Chicago=Central, NY/NJ=Eastern)' as status;

-- REVERT (undo — touches no punch, no payroll number):
-- alter table storeops.stores drop column if exists timezone;
