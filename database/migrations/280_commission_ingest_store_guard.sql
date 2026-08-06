-- 280_commission_ingest_store_guard.sql
-- CROSS-TENANT INGEST GUARD (owner-approved 2026-08-06, "fix item 3").
--
-- WHY THIS EXISTS. On 2026-07-14 a Luxelink B2B sales export was ingested under the HOUSE org.
-- Six line items for a Luxelink store (4640-A W Diversey Ave) landed in house commcalc.raw_sales,
-- the July recompute paid a phantom rep $2.9995 out of them, and — because
-- _promote_feed_to_raw_sales carries over any raw_sales row the daily feed lacks — those six rows
-- were re-inserted every hour for three weeks. Nothing detected it. A cleanup without a control
-- means we do it again next quarter; this is the control.
--
-- WHAT IT DOES. Before a sales batch is written, the incoming DISTINCT store strings are resolved
-- against the org's OWN known-store set (commcalc.store_mapping + storeops.stores +
-- commcalc.store_aliases — the same resolver chain /store-unmatched uses). Strings that resolve to
-- nothing are recorded, and — only in 'block' mode — withheld from the write and parked here with
-- their full payload so they can be released later. Nothing is ever silently discarded.
--
-- SAP-CONFIGURABLE (contract RULE TWO). No carrier, tenant or store is named anywhere. Enforcement
-- mode is per-org config with an admin UI; the "known store" set is the org's own real roster, and
-- ALLOWING a store is done by creating a normal commcalc.store_aliases row (the existing
-- pick-don't-type machinery) rather than inventing a parallel allowlist.
--
-- DEFAULT IS 'warn', DELIBERATELY. A hard block on day one would stop a legitimate new store from
-- ever being ingested. 'warn' writes every row exactly as today and only records the flag, so
-- turning this migration on changes NO data and NO number.
--
-- Additive + idempotent. RLS enabled, ZERO policies, ZERO grants (contract §5 — the backend uses
-- the service role, which bypasses RLS; the frontend anon key is auth-only).

-- ── 1. Per-org enforcement config ────────────────────────────────────────────────────────────
create table if not exists commcalc.ingest_store_guard (
  org_id            uuid        not null primary key,
  -- 'off'   = do nothing at all (pre-migration behaviour, byte-identical)
  -- 'warn'  = write EVERY row exactly as today, but record unknown stores for review  [DEFAULT]
  -- 'block' = withhold rows whose store is unknown to this org; park them below intact
  mode              text        not null default 'warn',
  -- Below this many rows an unknown store is recorded but never blocked even in 'block' mode —
  -- a genuinely new store opening usually arrives as a big batch, a mis-file as a handful.
  -- 0 disables the exemption (block everything unknown).
  block_min_rows    integer     not null default 0,
  -- When true, a store string that a human ALLOWS from the review queue is written to
  -- commcalc.store_aliases so it is permanently known (pick-don't-type; no parallel allowlist).
  allow_creates_alias boolean   not null default true,
  notify_on_flag    boolean     not null default true,
  updated_at        timestamptz not null default now(),
  updated_by        text,
  constraint ingest_store_guard_mode_chk check (mode in ('off', 'warn', 'block'))
);
alter table commcalc.ingest_store_guard enable row level security;

-- ── 2. The review queue / parking lot ────────────────────────────────────────────────────────
create table if not exists commcalc.ingest_store_quarantine (
  id            uuid        primary key default gen_random_uuid(),
  org_id        uuid        not null,
  created_at    timestamptz not null default now(),
  store_raw     text        not null,          -- the incoming store string, verbatim
  source        text,                          -- 'manual' | 'email_sweep' | 'ftp_sweep' | 'promotion'
  upload_type   text,                          -- 'daily_sales' | 'sales' | ...
  target_table  text,                          -- 'daily_sales_feed' | 'raw_sales'
  period        text,
  filename      text,
  rows_seen     integer     not null default 0,-- how many incoming rows carried this store string
  rows_withheld integer     not null default 0,-- 0 in 'warn' mode; = rows_seen in 'block' mode
  amount_seen   numeric     not null default 0,-- Σ ext_price, so a flag can be sized in dollars
  sample        jsonb,                         -- a few whole rows, for a human to eyeball
  withheld_rows jsonb,                         -- FULL payload of every withheld row ('block' only)
  status        text        not null default 'pending',   -- pending | allowed | rejected | released
  mode_at_flag  text,
  decided_at    timestamptz,
  decided_by    text,
  decision_note text,
  constraint ingest_store_quarantine_status_chk
    check (status in ('pending', 'allowed', 'rejected', 'released'))
);
alter table commcalc.ingest_store_quarantine enable row level security;

create index if not exists ingest_store_quarantine_org_idx
  on commcalc.ingest_store_quarantine (org_id, status, created_at desc);
create index if not exists ingest_store_quarantine_store_idx
  on commcalc.ingest_store_quarantine (org_id, lower(btrim(store_raw)));

-- ── 3. Seed every existing org at the safe default. Byte-identical behaviour on day one. ─────
-- Sourced from the orgs that already have commcalc data, so no core table is assumed.
insert into commcalc.ingest_store_guard (org_id, mode, updated_by)
select distinct org_id, 'warn', 'migration-280'
  from commcalc.store_mapping
 where org_id is not null
on conflict (org_id) do nothing;

insert into commcalc.ingest_store_guard (org_id, mode, updated_by)
select distinct org_id, 'warn', 'migration-280'
  from commcalc.raw_sales
 where org_id is not null
on conflict (org_id) do nothing;
