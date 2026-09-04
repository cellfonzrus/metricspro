-- 950_google_reviews_sweep_cron.sql — the google-reviews sweep self-schedules (mig 922/940 pattern)
--
-- WHY: POST /storeops/google-reviews/sweep/run-due has called itself a "pg_cron entrypoint" since
-- mig 411, but NO migration ever scheduled a pg_cron job for it — so the "daily 6am" sweep in
-- storeops.google_review_sweep_config never fired on its own; reviews only pulled when a human
-- clicked "Refresh now". Found 2026-09-04 root-causing the owner's "still not able to pull google
-- reviews" (the 2026-08-17/20 runs were those manual clicks; they failed 20/20 upstream at Google
-- and nothing ever retried). An automation whose repair step is a human click defeats the
-- automation (owner, 2026-09-01 — the mig 921/922 email-sweep incident).
--
-- THE FIX (mig 940 self-scheduling precedent, mirrored exactly, in the storeops schema because the
-- endpoint + config live in storeops): a SECURITY DEFINER function the BACKEND calls as
-- service_role on EVERY boot (main.py startup hook _google_reviews_sweep_cron_startup →
-- storeops/router._ensure_google_reviews_sweep_cron) so the job is (re)scheduled automatically
-- with the CURRENT API_PUBLIC_URL + NOTIFY_RUN_SECRET — no human SQL, no flag day on a secret
-- rotation, and a lost cron job self-heals on the next deploy. NO literal secret is embedded in
-- this file; the backend passes its own env values at call time (the same values
-- verify_notify_secret checks on the endpoint).
--
-- ONE GLOBAL JOB: /google-reviews/sweep/run-due already walks EVERY org's sweep-config row and
-- fires only those with enabled=true AND next_run_at <= now() (each firing recomputes that org's
-- own next_run_at from its frequency/hour/timezone) — so one job serves all tenants, and a quiet
-- tick is a single indexed read. Every-15-minutes cadence (same as the email sweep): the per-org
-- schedule, not the tick, decides when a sweep actually runs; 15 min just bounds how late after
-- the configured hour it starts. Job name + schedule are HARD-CODED here; only the backend's own
-- URL + secret are parameters, and EXECUTE is granted to service_role ONLY, so the public API can
-- never schedule an arbitrary cron.
--
-- NOT money-moving: the sweep reads Google Places ratings/reviews into google_review_snapshot /
-- google_review_item and materializes action-plan rows — no payroll or commission figures.
--
-- Fail-soft exactly like mig 922/940: a missing secret, or pg_cron/pg_net not installed, returns a
-- notice string instead of raising — boot must never fail because scheduling was unavailable.

create or replace function storeops.ensure_google_reviews_sweep_cron(p_url text, p_secret text)
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
  -- The SQL the cron runs every tick: POST the secret-gated run-due entrypoint. %L safely quotes
  -- each literal (the secret never appears in this migration — only in cron.job's stored command,
  -- exactly like the email-sweep / account-recompute jobs).
  v_cmd := format(
    'select net.http_post(url := %L, headers := jsonb_build_object(%L, %L, %L, %L));',
    rtrim(p_url, '/') || '/api/v1/storeops/google-reviews/sweep/run-due',
    'Content-Type', 'application/json',
    'X-Notify-Secret', p_secret
  );
  -- Idempotent unschedule-then-schedule: cron.schedule() with an existing job name already
  -- REPLACES it; the explicit unschedule additionally clears any half-state from a failed prior
  -- registration. Both sides are fail-soft.
  begin
    perform cron.unschedule('google-reviews-sweep-run-due');
  exception when others then
    null;  -- job not scheduled yet / cron absent — the schedule below decides
  end;
  begin
    v_jobid := cron.schedule('google-reviews-sweep-run-due', '*/15 * * * *', v_cmd);
  exception when others then
    return 'cron unavailable (pg_cron/pg_net not installed?): ' || SQLERRM;
  end;
  return 'scheduled job ' || coalesce(v_jobid::text, '?');
end;
$fn$;

revoke all on function storeops.ensure_google_reviews_sweep_cron(text, text) from public;
-- anon/authenticated (the frontend anon key) must NOT be able to schedule crons; only the backend
-- service role (mig 922/940 precedent, verbatim posture).
do $$ begin
  begin revoke all on function storeops.ensure_google_reviews_sweep_cron(text, text) from anon, authenticated; exception when others then null; end;
  begin grant execute on function storeops.ensure_google_reviews_sweep_cron(text, text) to service_role; exception when others then null; end;
end $$;

notify pgrst, 'reload schema';
select 'Migration 950 — google-reviews sweep cron auto-scheduler installed (backend self-registers on boot)' as status;

-- To confirm it is scheduled: select jobname, schedule, active from cron.job where jobname = 'google-reviews-sweep-run-due';
-- To see recent runs:        select * from cron.job_run_details where jobid = (select jobid from cron.job where jobname='google-reviews-sweep-run-due') order by start_time desc limit 10;
--
-- REVERT:
--   select cron.unschedule('google-reviews-sweep-run-due');
--   drop function if exists storeops.ensure_google_reviews_sweep_cron(text, text);
