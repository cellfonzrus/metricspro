-- 291_report_definitions_carrier_scope.sql   (mod-commission, band 200-299)
--
-- OWNER DIRECTIVE 2026-08-09: "we need to filter out the uploads and auto import based on what
-- carrier is chosen, all this is very confusing for me who is designing the system let alone the
-- user."
--
-- WHY. `commcalc.carrier` is already per-tenant (Boost Mobile -> Cellfonz house, Total Wireless ->
-- Luxelink, Verizon -> Vzone) and `raw_mi` even carries a `carrier_id`, but
-- `commcalc.report_definitions` has NO carrier column -- so neither the Imports UI nor the sweep
-- registry can tell which reports belong to the carrier a tenant actually runs.
--
-- Measured live before writing this: the Boost house tenant carries TWO VidaPay/Total rows,
-- `Ma Commission` and `MA Daily TX`, both with empty label / source_name / target_table. They are
-- inert (no target_table = they route nowhere) and belong to a carrier the tenant does not run --
-- pure confusion on the imports list. Luxelink carries three more MA rows that ARE the right
-- carrier but were typed by hand and never wired to a table (`MA Commission Details`,
-- `MA Dailt TX SubMA` (sic), `MA marketplace Fulfilment Handset Orders`).
--
-- NOTE, correcting an earlier reading: the two `sales_trend` rows are NOT duplicates -- there is one
-- per tenant, which is correct. No dedup is performed here.
--
-- Moves NO payout number, rate, plan or paid/earned column: this is registry metadata governing
-- which reports a tenant is shown and swept. Additive + idempotent.

begin;

-- ── 1. carrier scope on the registry ────────────────────────────────────────────────────────────
-- NULL means CARRIER-AGNOSTIC and is the default, so every existing row keeps showing exactly as it
-- does today. Only a row explicitly tied to a carrier can be filtered out. Sources that are not
-- carrier-scoped at all (VIP distributor invoices, B2B POS inventory, daily closing) stay NULL by
-- design -- do not force them under a carrier.
alter table commcalc.report_definitions
    add column if not exists carrier_id uuid references commcalc.carrier(id) on delete set null;

comment on column commcalc.report_definitions.carrier_id is
    'Which carrier this report belongs to (commcalc.carrier, per-tenant). NULL = carrier-agnostic '
    '(VIP / B2B / closing sources) and always shown. A row naming a carrier the tenant does not run '
    'is hidden from the Imports list and skipped by the sweep. Owner directive 2026-08-09.';

create index if not exists report_definitions_carrier_idx
    on commcalc.report_definitions (org_id, carrier_id);

-- ── 2. repair Luxelink's three hand-typed MA rows ───────────────────────────────────────────────
-- Right carrier, wrong wiring: no target_table means the Imports page offers a row that can never
-- accept a file. Point them at the tables the MA uploads actually write, and at the MA upload page.
-- Keyed by the exact typed report_key so nothing else can be caught.
update commcalc.report_definitions d
   set label          = 'MA Commission Details',
       target_table   = 'raw_ma_commission',
       upload_endpoint= '/commcalc/ma-upload',
       period_mode    = 'data',
       carrier_id     = (select c.id from commcalc.carrier c
                          where c.org_id = d.org_id and c.name ilike 'total%' limit 1),
       updated_at     = now()
 where d.report_key = 'MA Commission Details';

update commcalc.report_definitions d
   set label          = 'MA Daily TX (SubMA)',
       target_table   = 'raw_ma_daily_tx',
       upload_endpoint= '/commcalc/ma-upload',
       period_mode    = 'data',
       carrier_id     = (select c.id from commcalc.carrier c
                          where c.org_id = d.org_id and c.name ilike 'total%' limit 1),
       updated_at     = now()
 where d.report_key = 'MA Dailt TX SubMA';

update commcalc.report_definitions d
   set label          = 'MA Marketplace Handset Fulfilment Orders',
       target_table   = 'raw_ma_fulfillment',
       upload_endpoint= '/commcalc/ma-upload',
       period_mode    = 'data',
       carrier_id     = (select c.id from commcalc.carrier c
                          where c.org_id = d.org_id and c.name ilike 'total%' limit 1),
       updated_at     = now()
 where d.report_key = 'MA marketplace Fulfilment Handset Orders';

-- ── 3. stamp the carrier on the rows that plainly belong to one ─────────────────────────────────
-- ePay (MI/ATU, Comprehensive Comp, Payment Detail) and DLAR are Boost-side; only stamp them where
-- the tenant actually has a Boost carrier, so a non-Boost tenant is untouched.
update commcalc.report_definitions d
   set carrier_id = (select c.id from commcalc.carrier c
                      where c.org_id = d.org_id and c.name ilike 'boost%' limit 1),
       updated_at = now()
 where d.carrier_id is null
   and d.report_key in ('mi_report','comp_report','payment_detail','dlar_store','dlar_rep')
   and exists (select 1 from commcalc.carrier c
                where c.org_id = d.org_id and c.name ilike 'boost%');

-- ── 4. retire the two wrong-carrier stubs on the Boost tenant ───────────────────────────────────
-- `Ma Commission` / `MA Daily TX` on the house org: a carrier that tenant does not run, no
-- target_table, no label, no source_name, auto=false -- nothing can reference them and no upload
-- can land through them. Deleted rather than hidden so the registry stops carrying fiction.
-- Deliberately narrow: only rows that are inert in EVERY field, so a real row can never match.
delete from commcalc.report_definitions d
 where d.report_key in ('Ma Commission','MA Daily TX')
   and coalesce(d.target_table,'')    = ''
   and coalesce(d.upload_endpoint,'') = ''
   and coalesce(d.label,'')           = ''
   and d.auto is not true
   and not exists (select 1 from commcalc.carrier c
                    where c.org_id = d.org_id and c.name ilike 'total%');

commit;
