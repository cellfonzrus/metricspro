-- 924_data_lineage.sql — the SYSTEM DATA-LINEAGE REGISTRY (owner 2026-08-26)
--
-- WHY (owner directive): "the system is not small anymore and any change later will become a nightmare to
-- check what was done and what is missed. We need the full schematic of the system and to connect all the
-- relevant fields which feed off each other — if bill payments, activations, upgrades or any ingested data
-- touches any item, all relevant fields need to be updated. That can only be done if we have a list of
-- related items stored in a table and how each one is affected, documented as CODE next to them and also in
-- simple ENGLISH."
--
-- This is that table. Each row is ONE EDGE of the dependency graph: a SOURCE item (an ingested data item or
-- a derived metric) → an AFFECTED item downstream, annotated with WHERE the source enters the system, WHICH
-- surface shows the effect, a CODE reference (file:function — the "documented as code"), and a plain-ENGLISH
-- description of how a change propagates. Query it to answer "if X changes, what must update, and where in
-- the code does that happen?" — the map that keeps future changes from missing a dependent field.
--
-- It is DOCUMENTATION, not a runtime dependency: nothing computes money or display from this table. It is
-- populated by a companion seed (925) built from a full codebase audit, and mirrored in docs/DATA_LINEAGE.md
-- (the same content in prose + a diagram). Keeping both in sync is the point — the table is queryable, the
-- doc is readable.
--
-- Additive + idempotent; single-line-safe for the tenant SQL runner. RLS on + GRANT to service_role (same
-- posture as migs 208 / 916 / 923). No money touched.
--
-- REVERT:  drop table if exists commcalc.data_lineage;  notify pgrst, 'reload schema';

create table if not exists commcalc.data_lineage (
  id             uuid primary key default gen_random_uuid(),
  -- The item that CHANGES (the cause). Stable snake_case key, e.g. 'activations', 'bill_payments',
  -- 'accessories', 'upgrades', 'daily_cash', 'sales_transactions', 'activation_details_report'.
  source_key     text not null,
  source_label   text,
  -- HOW/WHERE the source enters the system: an endpoint, sweep, or upload type (e.g.
  -- 'POST /commcalc/upload/sales', 'email-sweep', 'Activation Details custom import', 'daily closing').
  entry_point    text,
  -- The downstream item AFFECTED (the effect). Same key vocabulary as source_key.
  affected_key   text not null,
  affected_label text,
  -- WHERE the effect is visible / consumed: a report, page, or endpoint (e.g. 'Executive MTD',
  -- '/commcalc/activation-counts', 'Daily Targets conversion', 'commission payout').
  surface        text,
  -- Nature of the edge: 'ingest' (raw capture), 'display' (a shown number), 'pay' (money/commission),
  -- 'target' (attainment/trending), 'recon' (a reconciliation consumes it). PAY edges are the ones that
  -- must never move silently.
  kind           text not null default 'display',
  -- TRUE when the affected item updates AUTOMATICALLY the moment the source changes (they share one code
  -- path — the single-source ideal). FALSE means a human/second step is required — the wiring gaps to close.
  auto_updated   boolean not null default true,
  -- The "documented as CODE" reference: file:function (or file:line) where the effect is implemented, so a
  -- future change can jump straight to the code that must stay consistent.
  effect_code    text,
  -- The "simple ENGLISH" description: how a change in the source propagates to the affected item.
  effect_english text,
  seq            int not null default 0,
  updated_at     timestamptz default now()
);

create index if not exists data_lineage_source_idx   on commcalc.data_lineage (source_key);
create index if not exists data_lineage_affected_idx on commcalc.data_lineage (affected_key);
create index if not exists data_lineage_kind_idx     on commcalc.data_lineage (kind);

alter table commcalc.data_lineage enable row level security;
grant all on commcalc.data_lineage to service_role;
do $$ begin
  begin grant select on commcalc.data_lineage to authenticated; exception when others then null; end;
end $$;

notify pgrst, 'reload schema';

select 'Migration 924 complete — commcalc.data_lineage (system dependency/lineage registry; seed in 925)' as status;
