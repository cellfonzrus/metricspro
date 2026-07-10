-- 112_failure_log.sql — system Failure Logs module + configurable clock-in face-match sensitivity
--
-- WHY: reps with a valid enrolled face were REJECTED at kiosk clock-in because the match threshold (0.55)
-- was STRICTER than face-api's 0.60 default → false rejects for the same person. This adds:
--   (1) a per-tenant, admin-configurable face-match sensitivity (default 0.60; higher = looser = fewer
--       false rejects), and
--   (2) a general FAILURE LOG so the system records failures (face mismatch, etc.) WITH a how-to-fix note,
--       visible to admins (RBAC-gated, grantable to others), so the same issue is diagnosable next time.
--
-- SAFE: additive + idempotent. Everything degrades gracefully — the code defaults the face threshold to
-- 0.60 and best-effort try/excepts every failure-log write, so nothing breaks until this migration runs.

create table if not exists core.failure_log (
  id            uuid primary key default gen_random_uuid(),
  org_id        uuid not null,
  category      text not null,                    -- 'face_mismatch' | 'clock_in_location' | 'upload_rejected' | 'sweep_error' | 'other'
  severity      text not null default 'warning',  -- 'info' | 'warning' | 'error'
  source        text,                             -- where it was raised, e.g. 'kiosk/clock-in'
  employee_id   uuid,
  employee_name text,
  store_code    text,
  message       text not null,
  detail        jsonb,
  remediation   text,                             -- how to fix (auto-filled from the code registry by category)
  status        text not null default 'open',     -- 'open' | 'resolved' | 'ignored'
  resolved_by   text,
  resolved_note text,
  created_at    timestamptz not null default now(),
  resolved_at   timestamptz
);
create index if not exists failure_log_org_created on core.failure_log(org_id, created_at desc);
create index if not exists failure_log_org_status  on core.failure_log(org_id, status);
create index if not exists failure_log_org_cat     on core.failure_log(org_id, category);

-- Configurable clock-in face sensitivity + which failure categories to log, per tenant.
alter table storeops.tenants
  add column if not exists face_match_threshold            numeric default 0.60,  -- kiosk face-match distance cutoff; higher = looser. face-api default 0.60.
  add column if not exists failure_log_disabled_categories jsonb;                 -- categories to NOT log (null/[] = log everything)

-- RLS: open_all (the backend uses the service key + passes org_id; matches every other module table)
alter table core.failure_log enable row level security;
do $$ begin
  create policy open_all on core.failure_log for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
grant all on core.failure_log to anon, authenticated, service_role;

notify pgrst, 'reload schema';
select '112 complete — core.failure_log + tenants.face_match_threshold + failure_log_disabled_categories' as status;
