-- 956_data_sources_sweep_cron.sql — the portal-pull sweep self-schedules (mig 922/940/950 pattern)
--
-- WHY. POST /commcalc/data-sources/sweep/run-due is the scheduled entrypoint for EVERY portal login in
-- the platform — VidaPay / T-CETRA, b2bsoft, and (owner directive 2026-09-04) the three merchant
-- processors: PayAnywhere/Payments Hub, TransFirst TransLink and ClientLine/BusinessTrack. It has been
-- a working endpoint since mig 241 — but mig 241 shipped its pg_cron job as a COMMENTED-OUT block with
-- "Run this ONCE in the Supabase SQL editor, replacing <NOTIFY_RUN_SECRET>". Nothing in the repo
-- proves anyone ever did, and nothing re-registers it after a secret rotation or a lost job.
--
-- That is precisely the defect mig 950 fixed for the google-reviews sweep four days ago: "an
-- automation whose repair step is a human click defeats the automation" (owner, 2026-09-01). Shipping
-- three NEW daily portal scrapes onto a scheduler that may not be scheduled would put the owner's
-- external-credit-card tally on the same footing as the reviews sweep — silently never running.
--
-- THE FIX (mig 950 mirrored exactly, in the commcalc schema because the endpoint + data_source live
-- there): a SECURITY DEFINER function the BACKEND calls as service_role on EVERY boot (main.py
-- startup hook _data_sources_sweep_cron_startup -> commcalc/router._ensure_data_sources_cron) so the
-- job is (re)scheduled automatically with the CURRENT URL + NOTIFY_RUN_SECRET. No human SQL, no flag
-- day on a rotation, and a lost cron job self-heals on the next deploy. NO literal secret is embedded
-- in this file; the backend passes its own env values at call time.
--
-- WHICH URL. Portal pulls launch Chromium, so /data-sources/sweep/run-due calls
-- require_browser_service(): on a split deploy the API service (SERVICE_ROLE=api) refuses it. The
-- backend therefore passes BROWSER_SERVICE_URL when that is set and falls back to API_PUBLIC_URL for
-- the default single-service deploy — so the cron always points at a process that may run a browser.
--
-- ONE GLOBAL JOB. The endpoint already walks EVERY org's enabled data_source rows and fires only those
-- whose next_run_at has passed, advancing each source's own next_run_at from its own frequency/hour
-- BEFORE the pulls run (the advance-then-background pattern). So one job serves all tenants and a
-- quiet tick is a single indexed read. HOURLY cadence: the per-source schedule, not the tick, decides
-- when a pull actually happens — an hourly tick just bounds how late after the configured hour a daily
-- pull starts. Deliberately NOT the */30 of mig 241's draft: portal rate-limit backoff (mig 244)
-- counts attempts, and a tighter tick buys nothing when every source is on a daily schedule.
--
-- NOT money-moving: this only INGESTS processor-reported figures. It books nothing and pays nothing.
--
-- Fail-soft exactly like mig 922/940/950: a missing secret, or pg_cron/pg_net not installed, returns a
-- notice string instead of raising — boot must never fail because scheduling was unavailable.

create or replace function commcalc.ensure_data_sources_cron(p_url text, p_secret text)
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
  -- The SQL the cron runs every tick: POST the secret-gated run-due entrypoint. %L safely quotes each
  -- literal (the secret never appears in this migration — only in cron.job's stored command, exactly
  -- like the email-sweep / google-reviews / account-recompute jobs).
  v_cmd := format(
    'select net.http_post(url := %L, headers := jsonb_build_object(%L, %L, %L, %L), body := %L::jsonb);',
    rtrim(p_url, '/') || '/api/v1/commcalc/data-sources/sweep/run-due',
    'Content-Type', 'application/json',
    'X-Notify-Secret', p_secret,
    '{}'
  );
  -- Idempotent unschedule-then-schedule: cron.schedule() with an existing job name already REPLACES
  -- it; the explicit unschedule additionally clears any half-state from a failed prior registration
  -- (including a job a human created by hand from mig 241's draft, which carries the SAME name and
  -- would otherwise be left running alongside this one). Both sides are fail-soft.
  begin
    perform cron.unschedule('data-sources-run-due');
  exception when others then
    null;  -- job not scheduled yet / cron absent — the schedule below decides
  end;
  begin
    v_jobid := cron.schedule('data-sources-run-due', '0 * * * *', v_cmd);
  exception when others then
    return 'cron unavailable (pg_cron/pg_net not installed?): ' || SQLERRM;
  end;
  return 'scheduled job ' || coalesce(v_jobid::text, '?');
end;
$fn$;

revoke all on function commcalc.ensure_data_sources_cron(text, text) from public;
-- anon/authenticated (the frontend anon key) must NOT be able to schedule crons; only the backend
-- service role (mig 922/940/950 precedent, verbatim posture).
do $$ begin
  begin revoke all on function commcalc.ensure_data_sources_cron(text, text) from anon, authenticated; exception when others then null; end;
  begin grant execute on function commcalc.ensure_data_sources_cron(text, text) to service_role; exception when others then null; end;
end $$;

notify pgrst, 'reload schema';
select 'Migration 956 — data-source portal sweep cron auto-scheduler installed (backend self-registers on boot)' as status;

-- To confirm it is scheduled: select jobname, schedule, active from cron.job where jobname = 'data-sources-run-due';
-- To see recent runs:        select * from cron.job_run_details where jobid = (select jobid from cron.job where jobname='data-sources-run-due') order by start_time desc limit 10;
-- Endpoint replies live in net._http_response (pg_net is async; 403 there = secret mismatch).
--
-- REVERT:
--   select cron.unschedule('data-sources-run-due');
--   drop function if exists commcalc.ensure_data_sources_cron(text, text);
