-- 940_account_recompute_cron.sql — the statement auto-recompute self-schedules (finance Phase 1,
-- FINANCE_PLATFORM_ROADMAP.md; owner pain 2026-09-02: "journal entries never showed up")
--
-- WHY: POST /account/run-due (account/autocompute.recompute_due — the staleness-gated sweep that
-- recomputes the current + prior period statements for every tenant whose own data is newer than
-- its snapshots) has existed since finance-7, but NO pg_cron job ever called it (index §19 gap 13).
-- So books recomputed only on a human click, and the owner hit the exact failure twice on
-- 2026-09-02: journal entries entered at 03:05Z against a snapshot computed 02:30Z — an entered
-- amount that "never showed up" until someone pressed Recompute. An automation whose repair step is
-- a human click defeats the automation (owner, 2026-09-01 — the mig 921/922 email-sweep incident).
--
-- THE FIX (the mig 922 self-scheduling pattern, mirrored exactly): install a SECURITY DEFINER
-- function the BACKEND calls as service_role on EVERY boot (main.py startup hook, alongside the
-- email-sweep registration) so the job is (re)scheduled automatically with the CURRENT
-- API_PUBLIC_URL + NOTIFY_RUN_SECRET — no human SQL, no flag day on a secret rotation, and a lost
-- cron job self-heals on the next deploy. NO literal secret is embedded in this file; the backend
-- passes its own env values at call time (the same values verify_notify_secret checks).
--
-- ONE GLOBAL JOB: /account/run-due already walks every tenant (org-scoped under
-- core.run_for_tenant, money_scope='none') and SKIPS every up-to-date period, so there is exactly
-- ONE job for the whole system — not one per tenant. Every-2-hours cadence: the sweep is cheap on
-- a quiet tick (timestamp probes only; recompute happens ONLY where a fresh ingest/journal edit
-- landed), and the P&L/BS staleness banner + Recompute button still cover the intra-tick window.
-- Job name + schedule are HARD-CODED here; only the backend's own URL + secret are parameters, and
-- EXECUTE is granted to service_role ONLY, so the public API can never schedule an arbitrary cron.
--
-- DETERMINISM: this changes WHEN compute runs, never WHAT it computes (statement_engine
-- .compute_and_store, byte-identical numbers — harness_statement_engine.py). NOT money-moving:
-- the sweep only rebuilds snapshots from the tenant's own already-booked data.
--
-- Fail-soft exactly like mig 922: a missing secret, or pg_cron/pg_net not installed, returns a
-- notice string instead of raising — boot must never fail because scheduling was unavailable.

create or replace function commcalc.ensure_account_recompute_cron(p_url text, p_secret text)
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
  -- exactly like the email-sweep / epay / dlar / vip jobs).
  v_cmd := format(
    'select net.http_post(url := %L, headers := jsonb_build_object(%L, %L, %L, %L));',
    rtrim(p_url, '/') || '/api/v1/account/run-due',
    'Content-Type', 'application/json',
    'X-Notify-Secret', p_secret
  );
  -- Idempotent unschedule-then-schedule: cron.schedule() with an existing job name already
  -- REPLACES it; the explicit unschedule additionally clears any half-state from a failed prior
  -- registration. Both sides are fail-soft.
  begin
    perform cron.unschedule('account-recompute-run-due');
  exception when others then
    null;  -- job not scheduled yet / cron absent — the schedule below decides
  end;
  begin
    -- Every 2 hours. Each tick recomputes ONLY stale (current + prior period) statements; a quiet
    -- tick is just timestamp probes. The staleness banner's Recompute button covers "right now".
    v_jobid := cron.schedule('account-recompute-run-due', '0 */2 * * *', v_cmd);
  exception when others then
    return 'cron unavailable (pg_cron/pg_net not installed?): ' || SQLERRM;
  end;
  return 'scheduled job ' || coalesce(v_jobid::text, '?');
end;
$fn$;

revoke all on function commcalc.ensure_account_recompute_cron(text, text) from public;
-- anon/authenticated (the frontend anon key) must NOT be able to schedule crons; only the backend
-- service role (mig 922 precedent, verbatim posture).
do $$ begin
  begin revoke all on function commcalc.ensure_account_recompute_cron(text, text) from anon, authenticated; exception when others then null; end;
  begin grant execute on function commcalc.ensure_account_recompute_cron(text, text) to service_role; exception when others then null; end;
end $$;

notify pgrst, 'reload schema';
select 'Migration 940 — account-recompute cron auto-scheduler installed (backend self-registers on boot)' as status;

-- To confirm it is scheduled: select jobname, schedule, active from cron.job where jobname = 'account-recompute-run-due';
-- To see recent runs:        select * from cron.job_run_details where jobid = (select jobid from cron.job where jobname='account-recompute-run-due') order by start_time desc limit 10;
--
-- REVERT:
--   select cron.unschedule('account-recompute-run-due');
--   drop function if exists commcalc.ensure_account_recompute_cron(text, text);
