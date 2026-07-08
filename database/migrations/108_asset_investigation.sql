-- 108_asset_investigation.sql
-- Per-device investigation notes for Inventory Aging: a "physically missing" flag + a remark the user
-- records when a phone shows in aging but isn't physically in the store. Kept in a SIDE table keyed by
-- ESN/IMEI because asset_ledger is WIPED & re-inserted on every upload — this survives re-uploads and
-- re-attaches by device id. Idempotent / additive.

create table if not exists commcalc.asset_investigation (
  id                 uuid primary key default gen_random_uuid(),
  org_id             uuid not null,
  esn_imei           text not null,
  physically_missing boolean default false,
  remark             text,
  investigated_by    text,
  updated_at         timestamptz default now(),
  unique (org_id, esn_imei)
);
create index if not exists asset_investigation_missing on commcalc.asset_investigation (org_id, physically_missing);

alter table commcalc.asset_investigation enable row level security;
drop policy if exists open_all on commcalc.asset_investigation;
create policy open_all on commcalc.asset_investigation for all to anon, authenticated using (true) with check (true);
grant all on commcalc.asset_investigation to anon, authenticated, service_role;
