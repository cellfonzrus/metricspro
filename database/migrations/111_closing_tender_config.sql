-- 111_closing_tender_config.sql
-- CONFIGURABLE CLOSING-SHEET TENDER FIELDS per tenant + a SMART value→tender mapping for the recon.
-- Every tenant's POS labels differ; today the 7 tenders (CANON_TENDERS) and the _canon_tender substring
-- rules are hardcoded, so a POS's "Financing" / "Dish SmartPay" / "Klarna" mis-buckets. This makes both
-- the tender field SET (standard OR custom) and the raw-label→tender MAP tenant-editable, and lets the
-- admin pick 3-way vs regular (2-way) recon. On the Total side the POS report AND the X-report are both
-- b2bsoft, so both legs go through the same mapping.
--
-- DOCTRINE: additive / idempotent. An EMPTY config → the backend falls back to the hardcoded
-- CANON_TENDERS + _canon_tender, so every existing tenant behaves byte-for-byte identically until it
-- opts in. Mirrors target_field_registry (mig 070) and flag_rules.acima_tenders (mig 094).

-- 1) Per-tenant tender field definitions (the standard 7 OR fully custom). No rows → use CANON_TENDERS.
create table if not exists commcalc.closing_tender_def (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null,
  tender_key       text not null,                 -- slug, e.g. 'cash','credit','financing','dish_smartpay'
  label            text,                           -- display label on the closing sheet
  sort_order       int     default 0,
  is_standard      boolean default false,          -- true = one of the built-in 7 tenders
  is_active        boolean default true,
  recon_class      text    default 'other',        -- 'cash' | 'card' | 'other' — drives the cash/credit gate + 2-way recon
  include_in_total boolean default true,
  created_at       timestamptz not null default now(),
  unique (org_id, tender_key)
);
create index if not exists closing_tender_def_org on commcalc.closing_tender_def(org_id);

-- 2) Per-tenant raw-label → tender map, per report (x_report vs sales; 'both' = either). This is the
--    tenant-editable replacement for the hardcoded _canon_tender substring rules. No rule matches a raw
--    label → the backend falls back to _canon_tender. Lower `priority` is tested first (specific before generic).
create table if not exists commcalc.closing_tender_map (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null,
  tender_key     text not null,
  report         text not null default 'both',     -- 'x_report' | 'sales' | 'both'
  source_labels  text[]  default '{}',             -- raw POS Tender Type values that collapse into this tender
  match_mode     text    default 'substring',      -- 'exact' | 'substring'
  priority       int     default 100,
  created_at     timestamptz not null default now(),
  unique (org_id, tender_key, report)
);
create index if not exists closing_tender_map_org on commcalc.closing_tender_map(org_id, report);

-- 3) Custom tender amounts on the closing sheet, beyond the physical t_* columns — {tender_key: amount}.
--    The standard tenders keep writing their t_* columns (backward-compat with every existing recon);
--    custom tenders live here.
alter table commcalc.daily_closing   add column if not exists tenders jsonb;
alter table commcalc.closing_attempt add column if not exists tenders jsonb;

-- 4) Per-tenant recon mode (3-way vs regular 2-way) + a master flag that the tenant uses custom tenders.
alter table storeops.tenants
  add column if not exists closing_recon_mode     text    default '3way',   -- '3way' | '2way'
  add column if not exists closing_tenders_custom boolean default false;

-- RLS: open_all (config/report tables; the backend uses the service key)
alter table commcalc.closing_tender_def enable row level security;
alter table commcalc.closing_tender_map enable row level security;
do $$ begin
  create policy open_all on commcalc.closing_tender_def for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
do $$ begin
  create policy open_all on commcalc.closing_tender_map for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
grant all on commcalc.closing_tender_def to anon, authenticated, service_role;
grant all on commcalc.closing_tender_map to anon, authenticated, service_role;

notify pgrst, 'reload schema';
select '111 complete — closing_tender_def + closing_tender_map + daily_closing.tenders + tenant recon_mode ready' as status;
