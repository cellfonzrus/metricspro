-- 923_metric_source_of_truth.sql — per-metric SOURCE OF TRUTH + auto-reconciliation config (owner 2026-08-26)
--
-- WHY (owner directive): "the Sales Report, the custom report and executive mtd should be populated from the
-- SAME data so if one changes all changes — no need to update or wire it. Activation Details becomes the basis
-- of truth for activations for every b2bsoft tenant; Bill Payment Transactions the basis for bill payments,
-- reconciled with the processor (VidaPay for Total, ePay for Boost). The two sources should PROVE the ingest
-- is good when they match, and FLAG when they don't."
--
-- This migration adds ONE tiny per-org, per-metric config table that names, for a given metric, which ingested
-- report is the AUTHORITATIVE source, and which secondary source to auto-reconcile it against. It is the seam
-- that lets a tenant switch Executive MTD / Sales Report activations onto the Activation Details basis WITHOUT
-- any code change or re-wiring — the reader (_metric_source in router.py) consults this table.
--
-- EMPTY = BYTE-IDENTICAL. A tenant with NO row here (every existing org, the house/Boost org) keeps the exact
-- current behavior: activations come from the shared sales aggregation (_sales_cell_agg), Total Activation is
-- unchanged. The override + the b2b-consistent Total Activation (which EXCLUDES Upgrade, matching the b2bsoft
-- MTD report and /activation-counts) light up ONLY for an org that inserts an enabled row. Nothing here moves
-- any PAY number — the override is DISPLAY/recon only; commission stays on its own explicit basis.
--
-- Additive + idempotent; every statement single-line-safe for the tenant SQL runner. RLS enabled + GRANT ALL
-- to service_role (the backend uses the service role, which bypasses RLS; the frontend anon key is auth-only —
-- same posture as migs 208 / 916).
--
-- REVERT (paste and run to undo — drops only this additive table, touches no money):
--   drop table if exists commcalc.metric_source_of_truth;
--   notify pgrst, 'reload schema';

create table if not exists commcalc.metric_source_of_truth (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null,
  -- WHICH metric this row governs. 'activations' → Total Activation on Exec MTD / Sales Report / Targets;
  -- 'bill_payments' → bill-payment quantity/amount. Extendable; the reader only acts on metrics it knows.
  metric         text not null,
  -- WHICH ingested report is authoritative for that metric:
  --   'sales_agg'          — the shared _sales_cell_agg over raw_sales / daily feed (the historical default)
  --   'activation_details' — the b2b "Activation Details" custom import (distinct Serial#, Upgrade excluded)
  --   'bill_payments'      — the b2b "Bill Payment Transactions" custom import
  source         text not null,
  -- OFF by default per row is meaningless (a row exists to turn something on) — but `enabled=false` lets a
  -- tenant keep the config while temporarily reverting to the historical default without deleting the row.
  enabled        boolean not null default true,
  -- The secondary source to AUTO-RECONCILE `source` against (e.g. 'sales_agg'). When they agree within
  -- `tolerance`, the ingest is proven good; when they diverge, metric_recon flags it. NULL → no auto-recon.
  reconcile_with text,
  -- The carrier's payment PROCESSOR for a bill-payment three-way recon: 'vidapay' (Total) | 'epay' (Boost).
  -- NULL for the activation metric. Configurable per carrier as the owner specified.
  processor      text,
  -- WHO to prompt to upload the missing report when a mismatch cannot be auto-remediated by re-running the
  -- sweep (a user id / email). NULL → the mismatch is only surfaced, never assigned.
  assigned_user  text,
  -- Allowed absolute delta between the two sources before a mismatch is flagged (0 = exact match required).
  tolerance      numeric not null default 0,
  updated_at     timestamptz default now()
);

-- ONE authoritative row per (org, metric) — the reader upserts on this so a tenant never ends up with two
-- divergent source rows for the same metric (the same failure mode mig 208 fixed for accessory_config).
create unique index if not exists metric_sot_uq
  on commcalc.metric_source_of_truth (org_id, metric);

alter table commcalc.metric_source_of_truth enable row level security;
grant all on commcalc.metric_source_of_truth to service_role;

notify pgrst, 'reload schema';

select 'Migration 923 complete — commcalc.metric_source_of_truth (per-metric source-of-truth + recon config)' as status;
