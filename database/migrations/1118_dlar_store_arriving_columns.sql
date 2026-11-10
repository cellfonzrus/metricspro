-- 1118_dlar_store_arriving_columns.sql
-- THE 20 STORE-DLAR COLUMNS THE PORTAL WAS ALREADY SENDING THAT NOTHING READ
-- Owner, 2026-11-10: "Use those other 21 columns and make them a part of the kpi reporting also
-- filtered by the kpi and out regular filters and send option".
--
-- EVIDENCE, not a wish list. The 2026-11-10 12:00 UTC DLAR sweep over 28 stores recorded, in
-- `core.job_run.detail.evidence.fields.dlar.unconsumed`, exactly 21 source fields arriving that the
-- store mapping did not read. One of them (`non_selling_days`) is the carrier's new spelling of a
-- column we ALREADY have (`zero_selling_days`) and is repaired in code as an alternative spelling,
-- not as a second column — so 20 new columns land here.
--
-- WHY THIS IS ADDITIVE AND SAFE. Every column is nullable with no default. A store-month that
-- predates this migration reads NULL, which `portal_cell`/`dlar_sweep` already means as "the source
-- did not send this" — distinct from a measured 0 ("the store sold none"). Nothing is backfilled and
-- no existing column, row or figure is touched; no money column is created or changed.
--
-- NUMERIC, not integer, on purpose: `avg_platinum_points` and the two percent-change figures are
-- fractional at source, and a feed landing a fraction into an integer column would silently round.
--
-- REVERT: alter table commcalc.raw_dlar_store
--           drop column if exists activations_total, drop column if exists auxiliary_device_acts,
--           drop column if exists swaps_total, drop column if exists rpl_count,
--           drop column if exists starlink_activations, drop column if exists starlink_sales,
--           drop column if exists total_trade_ins, drop column if exists value_trade_ins,
--           drop column if exists upgrade_value_trade_ins, drop column if exists offer_trade_ins,
--           drop column if exists trade_in_unlocked_activations, drop column if exists byod_total,
--           drop column if exists byod_unlocked_activations, drop column if exists protection_dop,
--           drop column if exists protection_upgrades, drop column if exists avg_platinum_points,
--           drop column if exists total_health_checks, drop column if exists projection,
--           drop column if exists mom_percent_change, drop column if exists yoy_percent_change;
--         (Dropping them loses the landed figures; a re-run of the sweep re-lands the current period
--          only, because the DLAR is month-to-date cumulative and the portal keeps no history.)

-- ── the figures, one column per source field (COUNT kind in dlar_sweep.STORE_FIELDS) ────────────
alter table commcalc.raw_dlar_store
  add column if not exists activations_total             numeric(14,4),
  add column if not exists auxiliary_device_acts         numeric(14,4),
  add column if not exists swaps_total                   numeric(14,4),
  add column if not exists rpl_count                     numeric(14,4),
  add column if not exists starlink_activations          numeric(14,4),
  add column if not exists starlink_sales                numeric(14,4),
  add column if not exists total_trade_ins               numeric(14,4),
  add column if not exists value_trade_ins               numeric(14,4),
  add column if not exists upgrade_value_trade_ins       numeric(14,4),
  add column if not exists offer_trade_ins               numeric(14,4),
  add column if not exists trade_in_unlocked_activations numeric(14,4),
  add column if not exists byod_total                    numeric(14,4),
  add column if not exists byod_unlocked_activations     numeric(14,4),
  add column if not exists protection_dop                numeric(14,4),
  add column if not exists protection_upgrades           numeric(14,4),
  add column if not exists avg_platinum_points           numeric(14,4),
  add column if not exists total_health_checks           numeric(14,4),
  add column if not exists projection                    numeric(14,4),
-- ── and the two RATE-kind figures (already percents at source, 0-100) ───────────────────────────
  add column if not exists mom_percent_change            numeric(14,4),
  add column if not exists yoy_percent_change            numeric(14,4);

comment on column commcalc.raw_dlar_store.activations_total is
  'Carrier portal field `activations_total`, landed under its own name. It appeared in the same run '
  'in which `gross_activation_quantity` stopped arriving and is the LIKELY successor to gross_adds, '
  'but that is an owner decision (requested 2026-11-10) and not a basis for restating a figure reps '
  'are scored against. Until it is ruled, read this column, not gross_adds, for the current period.';
comment on column commcalc.raw_dlar_store.zero_selling_days is
  'Non-selling days. The source renamed its field to `non_selling_days` (measured 2026-11-10); the '
  'column keeps its name and `dlar_sweep.STORE_FIELDS` declares both spellings. SAME fact, one home.';
comment on column commcalc.raw_dlar_store.mom_percent_change is
  'Month-over-month change, a percent (0-100 scale) as the source publishes it — NOT a fraction. '
  'Same reading rule as every other RATE column on this table (see kpi_failing.port_in_rate).';
comment on column commcalc.raw_dlar_store.yoy_percent_change is
  'Year-over-year change, a percent (0-100 scale) as the source publishes it — NOT a fraction.';
comment on column commcalc.raw_dlar_store.projection is
  'The source''s projected UNIT figure. The projected PERCENT to target is the separate column '
  'psa_projected; the two are different questions and neither is derived from the other.';

-- ── A MIGRATION GRANTS WHAT IT TOUCHES (house convention) ───────────────────────────────────────
-- This file creates no new relation, so the grant below is a RE-STATEMENT of the grant
-- commcalc.raw_dlar_store already carries, kept because `grant select, …` is idempotent and because
-- a column added to a granted table inherits the table's privileges rather than needing its own.
-- `service_role` only: the browser never reads this table directly, so an `anon` grant would widen
-- exposure of a tenant's carrier figures rather than fix anything.
grant select, insert, update, delete on commcalc.raw_dlar_store to service_role;
