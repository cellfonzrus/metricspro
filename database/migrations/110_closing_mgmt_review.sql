-- 110_closing_mgmt_review.sql
-- 3-Way Tender Recon: management sign-off per store per day. Management records the CASH they
-- physically received and ticks an APPROVED checkbox (each gated by its own role permission —
-- closing_mgmt_cash / closing_mgmt_approve). A daily "discrepancy" report (9pm) flags stores where
-- the management-received cash differs from the system, and a "closing not submitted" report (7:30pm)
-- lists stores with no closing. Both go to the scope recipients (storeops.alert_recipient) by
-- email + WhatsApp. Report times are editable per tenant. Idempotent / additive.

create table if not exists commcalc.closing_mgmt_review (
  id                  uuid primary key default gen_random_uuid(),
  org_id              uuid not null,
  close_date          date not null,
  store_code          text not null,
  mgmt_cash_received  numeric,
  mgmt_cash_by        text,
  mgmt_cash_at        timestamptz,
  approved            boolean default false,
  approved_by         text,
  approved_at         timestamptz,
  note                text,
  updated_at          timestamptz not null default now(),
  created_at          timestamptz not null default now(),
  unique (org_id, close_date, store_code)
);
create index if not exists closing_mgmt_review_date on commcalc.closing_mgmt_review(org_id, close_date);

-- RLS: open_all (report table; backend uses the service key)
alter table commcalc.closing_mgmt_review enable row level security;
do $$ begin
  create policy open_all on commcalc.closing_mgmt_review for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
grant all on commcalc.closing_mgmt_review to anon, authenticated, service_role;

-- Editable daily-report times + master enable (business-local HH:MM). Defaults: 7:30pm / 9:00pm.
alter table storeops.tenants
  add column if not exists closing_missing_report_time text    default '19:30',
  add column if not exists discrepancy_report_time     text    default '21:00',
  add column if not exists closing_reports_enabled      boolean default true;

notify pgrst, 'reload schema';
select '110 complete — commcalc.closing_mgmt_review + tenant report-time columns ready' as status;
