-- 250: GP-report adoption of the per-org accessory/box classification (owner-approved 2026-07-29).
-- apply_to_gp: OPT-IN per org — when true the GP report (/commcalc/gp) classifies its Acc GP bucket via
-- the org's accessory rules (departments + categories + keywords + catalog layer, i.e. _is_accessory)
-- and its Phone Sales bucket via the org's box_departments, instead of the department-only Boost
-- defaults. Needed for feeds like luxelink's where ONE department (BrandedHandset) holds both phones
-- and accessories and only the Category column discriminates.
-- Default FALSE → every existing org (incl. house/Boost) is byte-identical until an admin ticks
-- "Use these rules for the Gross-Profit report buckets" in Sales Report → Classification settings.
-- DISPLAY ONLY: no payout path reads it. Additive + idempotent; RLS posture unchanged (table created in
-- 208 with RLS enabled, zero anon/authenticated grants).
alter table commcalc.accessory_config
  add column if not exists apply_to_gp boolean not null default false;

comment on column commcalc.accessory_config.apply_to_gp is
  'When true the GP report classifies accessory/device buckets from THIS config (_is_accessory + box_departments) instead of the department-only Boost defaults. Display-only; no payout path reads it. (mig 250)';
