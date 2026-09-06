-- 971_system_check_cron.sql — the DAILY system check schedules itself (mig 922/940/950/956 pattern)
--
-- OWNER DIRECTIVE 2026-09-05: "a daily check required to make sure the system is working".
--
-- THE STANDING LESSON THIS OBEYS (mig 950 header, owner 2026-09-01): "an automation whose repair
-- step is a human click defeats the automation". That lesson was learned twice — mig 241 shipped the
-- portal-pull cron as a COMMENTED-OUT block for a human to paste (nothing proves anyone ever did),
-- and mig 411's "daily 6am" review sweep had no job at all, so it only ran when somebody clicked
-- Refresh. A daily health check is the LAST thing that may depend on someone remembering: its whole
-- job is to notice what nobody is looking at. So it registers itself.
--
-- HOW: a SECURITY DEFINER function the BACKEND calls as service_role on EVERY boot
-- (main.py startup hook _system_check_cron_startup → core.control_box_api._ensure_system_check_cron),
-- so the job is (re)scheduled with the CURRENT API_PUBLIC_URL + NOTIFY_RUN_SECRET. No human SQL, no
-- flag day on a secret rotation, and a lost cron job self-heals on the next deploy. NO literal secret
-- is embedded in this file; the backend passes its own env values at call time — the same values
-- verify_notify_secret checks on the endpoint.
--
-- ONE GLOBAL JOB, HOURLY TICK, DAILY WORK: /core/control-box/run-due walks every org's
-- core.system_check_state row and runs only those with enabled=true AND next_run_at <= now(); each
-- firing recomputes that org's own next_run_at from its cadence_hours (default 24). So one job serves
-- all tenants, the per-org row — not the tick — decides when a check actually runs, and the hourly
-- cadence only bounds how late after the due moment it starts. A quiet tick is a single indexed read.
-- Same shape as google-reviews (950) and data-sources (956).
--
-- AND THE WATCHMAN IS WATCHED: the board carries a row about its own last run
-- (control_box.selfcheck_row), so if this job stops producing, the control box goes RED about
-- ITSELF rather than leaving yesterday's green lamps on screen. A stale green is the single most
-- dangerous thing a status board can show.
--
-- NOT money-moving: the run reads health signals and writes core.system_check_run / _state. It
-- touches no payout, rate, plan or commission column.
--
-- Fail-soft exactly like migs 922/940/950/956: a missing secret, or pg_cron/pg_net not installed,
-- returns a notice string instead of raising — boot must never fail because scheduling was
-- unavailable, and the manual "Run check now" button still works.

CREATE OR REPLACE FUNCTION core.ensure_system_check_cron(p_url text, p_secret text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
  v_jobid bigint;
  v_cmd   text;
BEGIN
  IF COALESCE(p_url, '') = '' OR COALESCE(p_secret, '') = '' THEN
    RETURN 'skipped: url or secret not configured';
  END IF;
  -- The SQL the cron runs every tick: POST the secret-gated run-due entrypoint. %L safely quotes
  -- each literal (the secret never appears in this migration — only in cron.job's stored command,
  -- exactly like the email-sweep / account-recompute / reviews / data-sources jobs).
  v_cmd := format(
    'select net.http_post(url := %L, headers := jsonb_build_object(%L, %L, %L, %L));',
    rtrim(p_url, '/') || '/api/v1/core/control-box/run-due',
    'Content-Type', 'application/json',
    'X-Notify-Secret', p_secret
  );
  -- Idempotent unschedule-then-schedule: cron.schedule() with an existing job name already REPLACES
  -- it; the explicit unschedule additionally clears any half-state from a failed prior registration.
  -- Both sides are fail-soft.
  BEGIN
    PERFORM cron.unschedule('system-check-run-due');
  EXCEPTION WHEN OTHERS THEN
    NULL;  -- job not scheduled yet / cron absent — the schedule below decides
  END;
  BEGIN
    v_jobid := cron.schedule('system-check-run-due', '17 * * * *', v_cmd);
  EXCEPTION WHEN OTHERS THEN
    RETURN 'cron unavailable (pg_cron/pg_net not installed?): ' || SQLERRM;
  END;
  RETURN 'scheduled job ' || COALESCE(v_jobid::text, '?');
END;
$fn$;

-- Minute 17 rather than 0: the platform already fires the email sweep, the reviews sweep and the
-- portal pulls on the hour. The health check must not queue behind the very jobs it is measuring,
-- or a busy top-of-hour would make it report its own contention as a subsystem fault.

REVOKE ALL ON FUNCTION core.ensure_system_check_cron(text, text) FROM public;
-- anon/authenticated (the frontend anon key) must NOT be able to schedule crons; only the backend
-- service role (mig 922/940/950/956 precedent, verbatim posture).
DO $$ BEGIN
  BEGIN REVOKE ALL ON FUNCTION core.ensure_system_check_cron(text, text) FROM anon, authenticated; EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN GRANT EXECUTE ON FUNCTION core.ensure_system_check_cron(text, text) TO service_role; EXCEPTION WHEN OTHERS THEN NULL; END;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 971 — daily system-check cron auto-scheduler installed (backend self-registers on boot)' AS status;

-- To confirm it is scheduled: select jobname, schedule, active from cron.job where jobname = 'system-check-run-due';
-- To see recent runs:        select * from cron.job_run_details where jobid = (select jobid from cron.job where jobname='system-check-run-due') order by start_time desc limit 10;
-- To see what it FOUND:      select run_at, lamp, headline from core.system_check_run order by run_at desc limit 10;
--
-- REVERT:
--   select cron.unschedule('system-check-run-due');
--   drop function if exists core.ensure_system_check_cron(text, text);
