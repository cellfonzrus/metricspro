-- 701_core_job_run.sql — central background-job audit + per-tenant money-write guard config.
--
-- WHY: every `/run-due`-style background job (notify sweep, commission recompute, asset upload wipe) now
-- runs under core.run_for_tenant(org_id, job_name, job), which:
--   (1) asserts the tenant exists + is active (fail-closed — no writes to a phantom/dead tenant),
--   (2) REFUSES an anomalous org-wide money write BEFORE it happens (the 2026-07-13 plan-mode "$0
--       incident" shape: a recompute that replaces a whole tenant's commission rows with all-$0), and
--   (3) records an audit row here (running -> succeeded / failed / refused / skipped), plus a
--       core.failure_log entry on failure/refusal so it surfaces on the admin /failures page.
--
-- SAFE: additive + idempotent. The guard's tenant-assert + money-anomaly logic are pure Python and work
-- with only mig 055 present; this table is the AUDIT sink and the per-tenant policy override. Every write
-- to it is best-effort try/except in the backend, so nothing breaks until this migration runs — the guard
-- simply runs un-audited (still refusing bad writes) beforehand.

-- ── core.job_run: one row per guarded background-job execution ─────────────────────────────────────
create table if not exists core.job_run (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null,
  job_name     text not null,                       -- 'notify.subscription' | 'commission.recompute' | 'asset.upload_wipe' | ...
  status       text not null default 'running',     -- 'running' | 'succeeded' | 'failed' | 'refused' | 'skipped'
  money_scope  text not null default 'none',        -- 'none' | 'partial' | 'org'  (what class of write the job does)
  detail       jsonb,                               -- guard summary / money_writes / error trace
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  duration_ms  integer
);
create index if not exists job_run_org_started on core.job_run(org_id, started_at desc);
create index if not exists job_run_org_status  on core.job_run(org_id, status);
create index if not exists job_run_job_started on core.job_run(job_name, started_at desc);

-- RLS: open_all (backend uses the service key + passes org_id; matches core.failure_log and every module table).
alter table core.job_run enable row level security;
do $$ begin
  create policy open_all on core.job_run for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
grant all on core.job_run to anon, authenticated, service_role;

-- ── per-tenant money-write anomaly policy (null = code defaults) ─────────────────────────────────────
-- Shape (all keys optional; missing keys fall back to the conservative code defaults in run_for_tenant.py):
--   { "enabled": true,               -- master on/off for this tenant
--     "mode": "refuse",              -- "refuse" (severity error) | "park" (park-and-alert, severity warning)
--     "block_zero_org_write": true,  -- an org-wide write of N>0 rows whose $ total is 0 is anomalous
--     "max_drop_pct": 100,           -- refuse an org-wide total that drops >= this % vs the prior total
--     "min_rows_to_guard": 1 }
-- Defaults trip ONLY on a wipe-to-$0, so a normal recompute (numbers move up/down) never false-positives.
alter table storeops.tenants
  add column if not exists money_guard_config jsonb;

notify pgrst, 'reload schema';
select '701 complete — core.job_run + storeops.tenants.money_guard_config' as status;
