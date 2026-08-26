-- 922_email_sweep_cron_autoschedule.sql — the backend self-registers the email-sweep cron (owner 2026-08-26)
--
-- WHY: migration 921 documented the email-sweep pg_cron job, but scheduling it was a manual SQL step — and
-- a tenant will not (and should not) run SQL. This installs a function the BACKEND calls (as service_role)
-- whenever an email mailbox is ENABLED, so the global email-sweep cron is (re)scheduled automatically with
-- no human SQL. Apply this ONCE; after that it is hands-off forever.
--
-- ONE GLOBAL JOB: /email-sweep/run-due already loops every enabled mailbox across every org, so there is
-- exactly ONE cron job for the whole system — not one per tenant. The job name + schedule are HARD-CODED
-- here; only the backend's own URL + notify secret are parameters, and EXECUTE is granted to service_role
-- ONLY (never anon/authenticated), so the public API can't schedule an arbitrary cron.
--
-- SECURITY DEFINER so the service_role caller can reach cron.schedule (owned by a superuser). Fail-soft: a
-- missing secret, or pg_cron/pg_net not installed, returns a notice string instead of raising — the mailbox
-- save that triggers it must never fail because scheduling was unavailable.

create or replace function commcalc.ensure_email_sweep_cron(p_url text, p_secret text)
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
  -- The SQL the cron runs every tick: POST the secret-gated run-due entrypoint. %L safely quotes each literal.
  v_cmd := format(
    'select net.http_post(url := %L, headers := jsonb_build_object(%L, %L, %L, %L));',
    rtrim(p_url, '/') || '/api/v1/commcalc/email-sweep/run-due',
    'Content-Type', 'application/json',
    'X-Notify-Secret', p_secret
  );
  begin
    -- cron.schedule() with an existing job name REPLACES it → idempotent. Every 15 minutes; each mailbox's
    -- own frequency still gates how often it actually sweeps (run-due only runs configs whose next_run_at passed).
    v_jobid := cron.schedule('email-sweep-run-due', '*/15 * * * *', v_cmd);
  exception when others then
    return 'cron unavailable (pg_cron/pg_net not installed?): ' || SQLERRM;
  end;
  return 'scheduled job ' || coalesce(v_jobid::text, '?');
end;
$fn$;

revoke all on function commcalc.ensure_email_sweep_cron(text, text) from public;
-- anon/authenticated (the frontend anon key) must NOT be able to schedule crons; only the backend service role.
do $$ begin
  begin revoke all on function commcalc.ensure_email_sweep_cron(text, text) from anon, authenticated; exception when others then null; end;
  begin grant execute on function commcalc.ensure_email_sweep_cron(text, text) to service_role; exception when others then null; end;
end $$;

notify pgrst, 'reload schema';
select 'Migration 922 — email-sweep cron auto-scheduler installed (backend self-registers on mailbox enable)' as status;
