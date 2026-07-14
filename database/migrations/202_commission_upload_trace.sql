-- 202_commission_upload_trace.sql
-- UNIVERSAL UPLOAD TRACE (owner mandate 2026-07-14: "debug-first … nothing blocking a simple excel
-- upload … the system should first debug"). ONE trace record per ingest, from EVERY path — manual
-- upload, the email sweep, the FTP sweep, and the feed→raw_sales promotion — so "I uploaded a file and
-- the page shows nothing" is answerable from data, not guesswork: which ORG it landed in, how many rows
-- came in vs saved, per-period + per-day counts, the guard/shrink outcome, duration, and any error.
--
-- Additive + idempotent (safe to re-run). Degrades gracefully if unrun: every writer wraps the insert in
-- try/except → a missing table never breaks an upload; GET /commcalc/upload-trace returns an empty list
-- with a hint. Carrier/tenant-neutral (no tenant-specific columns). Multi-tenant: org_id NOT NULL + index.

create table if not exists commcalc.upload_trace (
  id            uuid primary key default gen_random_uuid(),
  org_id        uuid        not null,
  created_at    timestamptz not null default now(),
  source        text        not null default 'manual',  -- manual | email_sweep | ftp_sweep | promotion
  filename      text,
  upload_type   text,                                    -- daily_sales | sales | inventory_aging | mi_report | …
  target_table  text,                                    -- raw_sales | daily_sales_feed | …
  rows_in       integer,                                 -- rows parsed from the file
  rows_saved    integer,                                 -- rows actually written
  status        text,                                    -- ok | partial | skipped | error
  skipped       text,                                    -- price_guard | price_guard_partial | inventory_no_stores | …
  guard         jsonb,                                   -- shrink / guard detail (the exact refusal reasons)
  periods       jsonb,                                   -- {"July 2026": 4533} — per-period saved counts
  date_counts   jsonb,                                   -- {"2026-07-01": 349, …} — per-trans_date saved counts
  duration_ms   integer,
  note          text,
  error         text
);

create index if not exists upload_trace_org_created on commcalc.upload_trace (org_id, created_at desc);
create index if not exists upload_trace_org_type    on commcalc.upload_trace (org_id, upload_type);

-- RLS open_all (matches the rest of commcalc.*; tenant isolation is enforced in the API layer via org_id).
do $$
declare t text;
begin
  foreach t in array array['commcalc.upload_trace'] loop
    execute format('alter table %s enable row level security', t);
    execute format('drop policy if exists open_all on %s', t);
    execute format('create policy open_all on %s for all to anon, authenticated using (true) with check (true)', t);
    execute format('grant all on %s to anon, authenticated, service_role', t);
  end loop;
end $$;
