-- 967_doc_expiry_alert_cron.sql — the lease / insurance expiry notifications self-schedule
-- (mig 922 / 940 / 950 / 956 self-scheduling precedent, mirrored exactly).
--
-- WHY: owner directive 2026-09-05 — "send a notification when a coi is expir[ing] or th[e] lease is
-- getting over at least 60 days in advance or as per lease requirement". A notification that only
-- fires when a human remembers to click is not a notification. The 2026-09-04 google-reviews
-- incident was exactly this: an endpoint that called itself a "pg_cron entrypoint" for months while
-- no migration ever scheduled it, so it only ever ran on a manual click. This file exists so that
-- cannot repeat here.
--
-- THE JOB: POST /storeops/doc-expiry/run-due, secret-gated with the SAME NOTIFY_RUN_SECRET every
-- other run-due entrypoint uses (no new env var). ONE GLOBAL JOB: the endpoint walks every org's
-- leases and policies itself and sends only the milestones that are due and not already logged in
-- storeops.alert_log (mig 433) — a quiet tick is a couple of indexed reads. DAILY at 13:00 UTC
-- (~8-9am US Eastern): expiry is a day-granularity signal, so an hourly tick would buy nothing and
-- risk duplicate mail if the dedupe write ever failed.
--
-- The backend calls this idempotent RPC as service_role on EVERY boot
-- (storeops/router._ensure_doc_expiry_alert_cron), so the CURRENT API_PUBLIC_URL + NOTIFY_RUN_SECRET
-- are re-embedded on every deploy: no human SQL, no flag day on a secret rotation, and a lost job
-- self-heals. NO literal secret lives in this file.
--
-- Fail-soft exactly like mig 922/940/950: a missing secret, or pg_cron/pg_net not installed, returns
-- a notice string instead of raising — boot must never fail because scheduling was unavailable.
--
-- MONEY: none. Sends expiry email to the configured contacts; books nothing.

create or replace function storeops.ensure_doc_expiry_alert_cron(p_url text, p_secret text)
returns text
language plpgsql
security definer
set search_path = public
as $fn$
declare
  v_jobid bigint;
  v_cmd   text;
begin
  if coalesce(p_url, '') = '' or coalesce(p_secret, '') = '' then
    return 'skipped: url or secret not configured';
  end if;
  v_cmd := format(
    'select net.http_post(url := %L, headers := jsonb_build_object(%L, %L, %L, %L));',
    rtrim(p_url, '/') || '/api/v1/storeops/doc-expiry/run-due',
    'Content-Type', 'application/json',
    'X-Notify-Secret', p_secret
  );
  begin
    perform cron.unschedule('doc-expiry-alerts-run-due');
  exception when others then
    null;  -- not scheduled yet / cron absent — the schedule below decides
  end;
  begin
    v_jobid := cron.schedule('doc-expiry-alerts-run-due', '0 13 * * *', v_cmd);
  exception when others then
    return 'cron unavailable (pg_cron/pg_net not installed?): ' || SQLERRM;
  end;
  return 'scheduled job ' || coalesce(v_jobid::text, '?');
end;
$fn$;

revoke all on function storeops.ensure_doc_expiry_alert_cron(text, text) from public;
-- anon/authenticated (the frontend anon key) must NOT be able to schedule crons; service role only
-- (mig 922/940/950 posture, verbatim).
do $$ begin
  begin revoke all on function storeops.ensure_doc_expiry_alert_cron(text, text) from anon, authenticated; exception when others then null; end;
  begin grant execute on function storeops.ensure_doc_expiry_alert_cron(text, text) to service_role; exception when others then null; end;
end $$;

notify pgrst, 'reload schema';
select 'Migration 967 — lease/insurance expiry alert cron auto-scheduler installed (backend self-registers on boot; daily 13:00 UTC)' as status;

-- To confirm it is scheduled: select jobname, schedule, active from cron.job where jobname = 'doc-expiry-alerts-run-due';
-- To see recent runs:        select * from cron.job_run_details where jobid = (select jobid from cron.job where jobname='doc-expiry-alerts-run-due') order by start_time desc limit 10;
--
-- REVERT:
--   select cron.unschedule('doc-expiry-alerts-run-due');
--   drop function if exists storeops.ensure_doc_expiry_alert_cron(text, text);
--   (Expiry notifications then only fire from the manual "Send due notices now" button; nothing
--    else changes.)
