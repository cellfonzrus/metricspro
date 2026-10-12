-- 1078_portal_metric_day.sql
-- WHAT THE BACK-OFFICE PORTAL SAYS A STORE SOLD — per day, per worker, per declared category — so it
-- can be checked against what the reps declared at closing.
--
-- Owner directive 2026-10-12, verbatim: the portal's daily report "should be checked against what the
-- employees declare daily … but i dont wnat them to be hard coded it should be a design effect".
--
-- WHAT THIS IS, AND WHAT IT IS NOT. This is the LANDING table of a pull feed: one row per
-- (org, source, business_date, store, worker, declared category), upserted on that key so a re-pull
-- RESTATES a day instead of duplicating it. It is the portal's claim, kept beside — never merged
-- into — what the reps declared. Nothing in this table is money that books: no P&L, GP, payout or
-- commission path reads it. It exists to be COMPARED, and a disagreement is reported to a human.
--
-- DUPLICATE CHECK (build gate — index §16/§17 and the lineage registry were searched):
--   · commcalc.raw_sales / raw_sales_product (migs 002 / 1011) — the wireless POS line and
--     by-product exports. NOT reused, and mig 1011 is itself the reason: it exists because two report
--     kinds sharing one table let one kind's slice-replace delete 48,875 rows of the other. Those
--     tables are slice-replaced per store x date x kind and carry no source_id, no report_key and no
--     declared-category axis; a pull feed upserted on a natural key cannot share them without
--     re-creating that defect.
--   · commcalc.merchant_settlement_day (mig 955) — the card feed's landing table. Same MECHANISM
--     (and that mechanism is now shared: index §COMMCALC-05), different GRAIN and different subject:
--     money by card brand, not units and dollars by declared category and worker.
--   · commcalc.closing_metric_line (mig 1072) — what the REPS declared. Deliberately separate: a
--     portal's figure written into the tenant's own declarations would destroy the very comparison
--     this feed exists for.
--   · commcalc.daily_closing.counts (mig 501), pos_tender_summary, pos_product_sales — all read; none
--     answers "what does an external report say this category did for this worker on this day".
--   REUSED, with no new column anywhere: commcalc.data_source (the login registry — portal_url,
--   portal_reports, portal_calibration, portal_window_days, session_state all already exist),
--   commcalc.report_pull_map (mig 207 — how a consumer finds this feed's table and column spelling),
--   commcalc.connector_route_policy (mig 998 — no row needed: no rows reads as OPEN),
--   storeops / account store identity (the one store resolver), and the mig-956 cron.
--
-- RULE TWO. No vendor, tenant, portal, menu wording or category is named in this file or in the code
-- that writes it. The connector KIND is 'pos_report'; the portal's address, its report list, its
-- selectors and its line-label -> category mapping are all values on the tenant's own data_source row.
--
-- NOT MONEY-TOUCHING AS APPLIED: creates one empty table and one config row. Nothing is recomputed,
-- no existing row is read or rewritten, and until an operator declares a portal on a source row the
-- feed pulls nothing at all.
--
-- REVERT: DROP TABLE IF EXISTS commcalc.portal_metric_day;
--         DELETE FROM commcalc.report_pull_map
--           WHERE report_key = 'portal_declared_metric'
--             AND org_id = '00000000-0000-0000-0000-000000000001';
--         NOTIFY pgrst, 'reload schema';

create table if not exists commcalc.portal_metric_day (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid        not null,
  source_id      uuid,                        -- commcalc.data_source.id (which login pulled it)
  portal_key     text,                        -- the connector KIND ('pos_report'), never a vendor
  report_key     text,                        -- the report as the TENANT declared it on the source row
  business_date  date        not null,
  -- store: the portal's own spelling is kept AND the canonical resolution, because a figure the
  -- portal published is evidence whether or not we can place it. An unplaced row is REPORTED and
  -- excluded from store totals — never attributed to nobody and read as zero.
  store_code     text,
  store_label    text,
  store_key      text        not null default '',   -- normalized spelling; part of the natural key
  -- worker: the portal's spelling, UNRESOLVED on purpose. "Which spelling is which employee" has its
  -- own one home (index §STOREOPS-01); a feed that guessed would be that defect in a new place.
  employee_name  text,
  employee_key   text        not null default '',
  -- the declared category this line was matched to, and HOW it was matched. A consumer that reads
  -- metric_key without reading metric_basis treats a text coincidence as a tenant declaration — the
  -- §66 rule, in the second place that needed it. 'line_map' / 'label' are declared; anything else is
  -- not evidence of the tenant's intent.
  metric_key     text        not null,
  metric_basis   text,
  line_label     text,                        -- what the portal called it, kept verbatim
  units          numeric     not null default 0,
  amount         numeric     not null default 0,
  source_line    integer,                     -- which line of the export this came from
  created_at     timestamptz not null default now()
);

-- THE NATURAL KEY. Matches pos_report_sweep.ON_CONFLICT exactly (the harness reads both and fails if
-- they drift). The two *_key columns are normalized spellings, so two spellings of one
-- store-day-worker-category restate each other instead of landing twice.
create unique index if not exists portal_metric_day_key
  on commcalc.portal_metric_day (org_id, source_id, business_date, store_key, employee_key, metric_key);

create index if not exists portal_metric_day_store_day
  on commcalc.portal_metric_day (org_id, store_code, business_date);
create index if not exists portal_metric_day_metric
  on commcalc.portal_metric_day (org_id, metric_key, business_date);
create index if not exists portal_metric_day_unplaced
  on commcalc.portal_metric_day (org_id, portal_key, store_label)
  where store_code is null;

comment on table commcalc.portal_metric_day is
  'What an external back-office POS report says a store sold, at org x source x business_date x store x worker x declared category. The PORTAL side of the daily declared-category check (owner 2026-10-12): written only by the pos_report feed (pos_report_sweep.ingest_report), read-only for every consumer, and never merged into commcalc.closing_metric_line, which is what the reps declared. No money books from this table; a disagreement with the declaration is reported to a human, never silently reconciled.';
comment on column commcalc.portal_metric_day.metric_basis is
  'HOW the report line was matched to the declared category: line_map (the tenant declared this mapping) | label (the portal''s wording is the category''s own label) | label_part (one-and-only-one containment match) | ambiguous | unmatched. Only line_map and label are the tenant''s declared intent — a surface reading metric_key without this column treats a text coincidence as evidence.';
comment on column commcalc.portal_metric_day.store_key is
  'Normalized store spelling (the feed''s own norm()), part of the natural key so a re-pull restates a day. Empty string when the report named no store.';

do $$ begin
  execute 'alter table commcalc.portal_metric_day enable row level security';
exception when others then null; end $$;
do $$ begin
  execute 'create policy portal_metric_day_all on commcalc.portal_metric_day for all using (true) with check (true)';
exception when others then null; end $$;
grant all on commcalc.portal_metric_day to anon, authenticated, service_role;

-- commcalc.report_pull_map (mig 207) is how a consumer finds a pulled feed's table and column
-- spelling WITHOUT hardcoding either — the same contract mig 955 wrote for the settlement feed. One
-- HOUSE row, inherited by every tenant; a tenant row for the same report_key overrides it.
insert into commcalc.report_pull_map
  (org_id, report_key, display_name, target_table, column_map, param_spec, export_pref, enabled,
   sort_order, processor)
values
  ('00000000-0000-0000-0000-000000000001', 'portal_declared_metric',
   'Back-office POS report (per store/day/worker/declared category)',
   'commcalc.portal_metric_day',
   '{"day":"business_date","metric":"metric_key","basis":"metric_basis","units":"units",'
   '"amount":"amount","store_code":"store_code","employee":"employee_name"}'::jsonb,
   '{}'::jsonb, 'csv', true, 62, 'pos_report')
on conflict (org_id, report_key) do update
  set target_table = excluded.target_table,
      column_map   = excluded.column_map,
      display_name = excluded.display_name,
      processor    = excluded.processor,
      updated_at   = now();

notify pgrst, 'reload schema';
select '1078 complete — the portal''s declared-category report has a landing table; nothing pulls until a source declares a portal'
       as status;
