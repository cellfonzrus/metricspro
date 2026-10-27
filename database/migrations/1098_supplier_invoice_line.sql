-- 1098_supplier_invoice_line.sql
-- WHAT A SUPPLIER BILLED US, line by line — so every shipment a supplier charged for can be checked
-- against the sale that should have collected for it.
--
-- Owner, 2026-10-27, verbatim: "i want to pull in invoices from ups and then compare to the sales
-- reports to make sure every single shipment is being paid for and cash collected by the sales reps,
-- the time should be recorded so incase the payment is not taken then it shoudl be addressed with the
-- employees … make it as a recon under supplier / distribuot".
--
-- WHAT THIS IS, AND WHAT IT IS NOT. This is the LANDING table of a pull feed: one row per
-- (org, source, invoice, shipment, service, charge day), upserted on that key so a re-pull RESTATES
-- an invoice instead of duplicating it. It is the SUPPLIER's claim, kept beside — never merged into —
-- what the POS says was sold and collected. Nothing in this table books: no P&L, GP, payout or
-- commission path reads it. It exists to be COMPARED, and a disagreement is reported to a human.
--
-- SUPPLIER-NEUTRAL BY DESIGN, AND THE SIBLING IS NAMED. commcalc.vip_invoices /
-- vip_invoice_lines (mig 008) already hold DISTRIBUTOR invoices. They are not reused and they are not
-- ignored:
--   · they are keyed on one portal's internal invoice id (vip_id bigint) and carry NO supplier and
--     NO source_id column, so a second supplier's invoices landed in them would be indistinguishable
--     from the first's;
--   · they are read by the P&L (coa.build_inputs -> vip_fees / vip_ap), the balance sheet, device
--     COGS and the Distributor Invoices report (index §15v), so widening them is a money change on
--     paths another piece of work owns.
-- So "a supplier invoice" gets a home that names no supplier, and vip_invoices is registered in the
-- index as the EXCUSED SIBLING that should fold into this table later, with that reason written down.
-- Two homes for one question is a defect; an unstated one is a worse defect.
--
-- DUPLICATE CHECK (build gate — index §16/§17, the §COMMCALC-07 register and the lineage registry
-- were searched):
--   · commcalc.vip_invoices / vip_invoice_lines / vip_invoice_devices (mig 008) — above.
--   · commcalc.asset_ledger (consignment / device payable) — per UNIT owed to a distributor, not per
--     invoice line billed by any supplier. Different question, untouched.
--   · commcalc.portal_metric_day (mig 1078) — the POS side of a daily comparison, at day x store x
--     worker x declared category. It is the OTHER side of the recon this table feeds, which is
--     exactly why it is not where these rows go.
--   · commcalc.raw_sales_invoice / raw_sales_invoice_tender (mig 1012) — OUR invoices to customers
--     with their tender split. That is the cash-collected side, read by the recon, not written here.
--   · commcalc.store_expenses — a declared monthly expense matrix, not a per-shipment charge.
--   REUSED, with no new column anywhere: commcalc.data_source (the login registry — portal_url,
--   portal_reports, portal_calibration, portal_window_days, session_state all already exist),
--   commcalc.report_pull_map (mig 207 — how a consumer finds this feed's table and column spelling),
--   commcalc.connector_route_policy (mig 998 — no row needed: no rows reads as OPEN), the one store
--   resolver (index §64), the one pull loop (§COMMCALC-07) and the mig-956 cron.
--
-- RULE TWO. No supplier, tenant, portal, menu wording or service name is named in this file or in the
-- code that writes it. The connector KIND is 'supplier_invoice'; the portal's address, its report
-- list, its selectors, its date filters and its column vocabulary are all values on the tenant's own
-- data_source row.
--
-- A BLANK CHARGE IS NOT A ZERO CHARGE, and the column allows null for that reason. A $0.00 asserts
-- the supplier billed nothing for this shipment; a blank asserts nothing at all. The feed SKIPS a
-- line whose charge it cannot read and reports it, so a recon is never handed a zero it would close
-- as "collected in full".
--
-- NOT MONEY-TOUCHING AS APPLIED: creates one empty table and one config row. Nothing is recomputed,
-- no existing row is read or rewritten, and until an operator declares a portal and a report on a
-- source row the feed pulls nothing at all.
--
-- REVERT: DROP TABLE IF EXISTS commcalc.supplier_invoice_line;
--         DELETE FROM commcalc.report_pull_map
--           WHERE report_key = 'supplier_invoice_line'
--             AND org_id = '00000000-0000-0000-0000-000000000001';
--         NOTIFY pgrst, 'reload schema';

create table if not exists commcalc.supplier_invoice_line (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid        not null,
  source_id       uuid,                        -- commcalc.data_source.id (which login pulled it)
  supplier_key    text,                        -- the connector KIND ('supplier_invoice'), never a name
  report_key      text,                        -- the report as the TENANT declared it on the source row
  invoice_number  text        not null,
  invoice_date    date,
  due_date        date,
  -- WHEN THE CHARGE HAPPENED, which is the day a sale should exist for — not the day the invoice was
  -- raised. A recon keyed on the invoice date would look for the sale in the wrong week.
  charge_date     date        not null,
  -- store: the supplier's own spelling is kept AND the canonical resolution, because a charge the
  -- supplier billed is evidence whether or not we can place it. An unplaced row is REPORTED and
  -- excluded from store totals — never attributed to nobody and read as zero.
  store_code      text,
  store_key       text        not null default '',   -- normalized spelling
  store_label     text,
  -- THE RECON KEYS. shipment_ref is the supplier's own per-shipment identifier; counter_ref is what
  -- the counter typed when the shipment was created (a receipt or transaction number). Both are kept
  -- verbatim AND normalized, because the two sides of the match print them differently and comparing
  -- raw spellings is how a shipment reads as unbilled on one side and uncollected on the other.
  shipment_ref    text,
  shipment_key    text        not null default '',
  counter_ref     text,
  counter_key     text        not null default '',
  service         text,                        -- what the supplier called the charge, verbatim
  service_key     text        not null default '',
  quantity        numeric,                     -- null = the export said nothing, NOT zero
  weight          numeric,
  amount          numeric,                     -- null never lands: the feed skips and reports instead
  source_line     jsonb,                       -- the export's own row, so nothing is lost
  created_at      timestamptz not null default now()
);

-- THE NATURAL KEY. Matches supplier_invoice_sweep.ON_CONFLICT exactly (the harness reads both and
-- fails if they drift). An invoice's own line numbering is not in every export, so the key is what
-- identifies the CHARGE: the invoice, the shipment, the service and the day it happened.
create unique index if not exists supplier_invoice_line_key
  on commcalc.supplier_invoice_line
     (org_id, source_id, invoice_number, shipment_key, service_key, charge_date);

create index if not exists supplier_invoice_line_store_day
  on commcalc.supplier_invoice_line (org_id, store_code, charge_date);
create index if not exists supplier_invoice_line_shipment
  on commcalc.supplier_invoice_line (org_id, shipment_key)
  where shipment_key <> '';
create index if not exists supplier_invoice_line_counter
  on commcalc.supplier_invoice_line (org_id, counter_key)
  where counter_key <> '';
create index if not exists supplier_invoice_line_invoice
  on commcalc.supplier_invoice_line (org_id, invoice_number);
create index if not exists supplier_invoice_line_unplaced
  on commcalc.supplier_invoice_line (org_id, supplier_key, store_label)
  where store_code is null;

comment on table commcalc.supplier_invoice_line is
  'What a supplier billed us, at org x source x invoice x shipment x service x charge day. The SUPPLIER side of the shipment-collection check (owner 2026-10-27): written only by the supplier_invoice feed (supplier_invoice_sweep.ingest_report), read-only for every consumer, and never merged into the POS tables that say what was sold and collected. No money books from this table; a disagreement is reported to a human, never silently reconciled. Supplier-neutral on purpose — commcalc.vip_invoices is the distributor-only sibling that should fold into this table later (see the header of mig 1098).';
comment on column commcalc.supplier_invoice_line.charge_date is
  'The day the charge happened (shipped / pickup / service date), falling back to the invoice date only when the export carries no charge date. This is the day a matching sale should exist for — a recon keyed on invoice_date would look for the sale in the wrong week.';
comment on column commcalc.supplier_invoice_line.shipment_key is
  'Normalized shipment reference (report_columns.norm, the same rule the recon compares under). Empty string when the export carried none — and a report that carries none for every line says so in its warnings, because it means the recon cannot match shipment-for-shipment.';
comment on column commcalc.supplier_invoice_line.quantity is
  'Null means the export said nothing on this row. It is deliberately NOT zero: a zero asserts a fact the blank does not.';

do $$ begin
  execute 'alter table commcalc.supplier_invoice_line enable row level security';
exception when others then null; end $$;
do $$ begin
  execute 'create policy supplier_invoice_line_all on commcalc.supplier_invoice_line for all using (true) with check (true)';
exception when others then null; end $$;
-- A MIGRATION GRANTS WHAT IT CREATES (house convention): the backend reaches every relation through
-- PostgREST, and from 2026-10-30 Supabase no longer grants new public tables automatically. The
-- browser never reads this table, so service_role only — an anon grant would widen exposure of a
-- supplier's billing detail rather than fix anything.
grant select, insert, update, delete on commcalc.supplier_invoice_line to service_role;

-- commcalc.report_pull_map (mig 207) is how a consumer finds a pulled feed's table and column
-- spelling WITHOUT hardcoding either — the same contract migs 955 and 1078 wrote for the two feeds
-- before this one. One HOUSE row, inherited by every tenant; a tenant row for the same report_key
-- overrides it. sort_order 63 — 60/61 are the merchant feed's and 62 is the POS-report feed's.
insert into commcalc.report_pull_map
  (org_id, report_key, display_name, target_table, column_map, param_spec, export_pref, enabled,
   sort_order, processor)
values
  ('00000000-0000-0000-0000-000000000001', 'supplier_invoice_line',
   'Supplier invoices (per invoice/shipment/service/day)',
   'commcalc.supplier_invoice_line',
   '{"day":"charge_date","invoice":"invoice_number","shipment":"shipment_ref",'
   '"counter_ref":"counter_ref","service":"service","quantity":"quantity","amount":"amount",'
   '"store_code":"store_code","due":"due_date"}'::jsonb,
   '{}'::jsonb, 'csv', true, 63, 'supplier_invoice')
on conflict (org_id, report_key) do update
  set target_table = excluded.target_table,
      column_map   = excluded.column_map,
      display_name = excluded.display_name,
      processor    = excluded.processor,
      updated_at   = now();

notify pgrst, 'reload schema';
select '1098 complete — supplier invoices have a supplier-neutral landing table; nothing pulls until a source declares a portal and a report'
       as status;
