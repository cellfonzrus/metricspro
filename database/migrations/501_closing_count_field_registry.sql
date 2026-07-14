-- 501_closing_count_field_registry.sql
-- CONFIGURABLE CLOSING-SHEET ACTIVATION-COUNT FIELDS per tenant, mirroring target_field_registry
-- (mig 070) and closing_tender_def (mig 111). Today upgrade_count/new_line_count/postpaid_count are
-- FIXED columns hard-coded into the rep submit form, the DM verify view, and the B2B count-mismatch
-- recon (closing_summary + closing_recon). A tenant whose activation taxonomy differs (no postpaid,
-- or a BYOD / port-in / device-financing split) has nowhere to put it.
--
-- DOCTRINE: additive / idempotent. An EMPTY config -> the backend falls back to the hardcoded 3 fields
-- (upgrade_count / new_line_count / postpaid_count, recon_class upgrade / activation / activation), so
-- every existing tenant behaves BYTE-FOR-BYTE identically until it opts in.
--
-- NOT THE CLOSING GATE: recon_class here only buckets the count-mismatch discrepancy shown on the DM
-- verify view + the /closing/recon sheet — it is always a FLAG (informational), never a block. It does
-- not touch the cash-short/credit-over close gate (that's the separate money-recon path in
-- _money_issues / _gate_row, untouched by this migration).

-- 1) Per-tenant count-field definitions (the standard 3 OR fully custom). No rows -> use the hardcoded 3.
create table if not exists commcalc.closing_count_field_def (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null,
  field_key    text not null,               -- 'upgrade_count'/'new_line_count'/'postpaid_count' (standard,
                                             -- writes the physical column) OR a custom slug (writes into
                                             -- daily_closing.counts jsonb)
  label        text,                        -- display label on the closing sheet / DM verify view
  sort_order   int     default 0,
  is_standard  boolean default false,       -- true = one of the built-in 3 (physical-column field)
  is_active    boolean default true,
  recon_class  text    default 'other',     -- 'activation' | 'upgrade' | 'other' — which B2B count-recon
                                             -- bucket this field rolls into (flag-only, never a gate/block)
  created_at   timestamptz not null default now(),
  unique (org_id, field_key)
);
create index if not exists closing_count_field_def_org on commcalc.closing_count_field_def(org_id);

-- 2) Custom count amounts on the closing sheet, beyond the 3 physical columns — {field_key: count}.
--    The standard 3 fields keep writing their physical columns (backward-compat with the rollup
--    dashboard + the Google-sheet upload ingestion, neither of which this migration changes).
alter table commcalc.daily_closing add column if not exists counts jsonb;

-- RLS: open_all (config table; the backend uses the service key) — mirrors mig 111.
alter table commcalc.closing_count_field_def enable row level security;
do $$ begin
  create policy open_all on commcalc.closing_count_field_def for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
grant all on commcalc.closing_count_field_def to anon, authenticated, service_role;

-- No seed: the hard-coded 3 fields remain the default; this registry is overlay-only, so an empty
-- table reproduces today's behaviour exactly.

notify pgrst, 'reload schema';
select '501 complete — commcalc.closing_count_field_def + daily_closing.counts ready' as status;
