-- 1034_vision_health_cron.sql — the DAILY CAMERA SELF-HEAL schedules itself (mig 971 pattern)
--
-- OWNER DIRECTIVE 2026-10-02: "the cameras are not working live any more — set up a mechanism to
-- check every day if they working and if they are not working trouble shoot autonomously and
-- initiate a fix … Anything which is automated in the system should have this mechanism already
-- built in."
--
-- WHAT WAS WRONG, AND IT WAS NOT THE DETECTION. `GET /vision/status` has known the whole answer
-- since migration 900: whether Google is linked, when its token last worked, how many events
-- arrived, how many cameras are assigned, whether a store PC is alive. It had simply never RUN ON
-- ITS OWN — it answers when somebody opens the settings page. So the first thing to notice dark
-- cameras was a person wondering why a chart was flat, and on this estate that took weeks.
--
-- MOST OF THE FIX IS NOT HERE, AND THAT IS THE POINT. Detection now rides machinery that already
-- existed and that vision was never plugged into:
--   * `register_provider("vision_cameras", …)` in app/modules/vision/attention.py puts every camera
--     fault in the admin attention popup; and
--   * control_box_api._provider_specs turns any registered provider into a super-admin control-box
--     lamp "with no code change and no migration here"; and
--   * mig 971's self-scheduling daily system check already walks every org and evaluates those
--     lamps, hourly tick, per-org cadence.
-- No new table, no new board, no second alert channel. That is what "should have this mechanism
-- already built in" means: the mechanism existed; vision had not registered.
--
-- WHAT THIS MIGRATION ADDS is the one thing the attention framework deliberately will not do:
-- REPAIR. An attention provider must be cheap and read-only — it runs on every login popup — so it
-- can report "Google rejected the token" and must not go and retry it. POST /vision/health/run-due
-- is the repair half:
--
--     assess  ->  auto-fix what is genuinely fixable  ->  RE-ASSESS  ->  escalate what survived
--
--   retry_token      a refresh that failed on a 5xx or a timeout. Asking once more IS the fix.
--   resync_devices   re-list devices — the real repair when a camera was renamed or re-homed in
--                    the Google Home app.
--
-- Everything else escalates naming the thing a PERSON must do. Re-authorising Google, publishing an
-- OAuth consent screen and powering on a store PC are not things software may do on somebody's
-- behalf, and a monitor that pretended otherwise would report "fixed" over a shop still counting
-- nobody. (This is operational self-healing, the data-health monitor's class — NOT the auto-fix
-- pipeline's: nothing here deploys code, and fix_pipeline's Phase-1 rule is untouched.)
--
-- IT SCHEDULES ITSELF, because mig 971 settled this: "an automation whose repair step is a human
-- click defeats the automation" (mig 950, owner 2026-09-01). Mig 241 shipped a cron as a
-- commented-out block for someone to paste and nothing proves anyone ever did; mig 411's "daily
-- 6am" sweep had no job at all. A camera watchdog is the LAST thing that may depend on somebody
-- remembering. So the backend re-registers this job on EVERY boot with its own current
-- APP_PUBLIC_URL + NOTIFY_RUN_SECRET — no human SQL, no flag day on a secret rotation, and a lost
-- job self-heals on the next deploy.
--
-- THE DIAGNOSIS THIS EXISTS FOR. While an OAuth consent screen sits in Testing, Google expires every
-- refresh token after SEVEN DAYS. An estate then goes dark weekly, on the dot, with an error that
-- reads like a random Google outage — so it is reconnected, and dies again the next week, forever.
-- health.assess separates that from a genuine revocation by the gap between token_issued_at and
-- last_error_at, and says both halves out loud: reconnect now, AND publish the consent screen or
-- this recurs every seven days.
--
-- SAFE: creates one function, schedules one job, changes no data. Re-runnable.
-- MONEY: touches no payout, rate, plan or commission column.
-- SECURITY: no literal secret in this file — the backend passes its own env values at call time,
--           the same values verify_notify_secret checks on the endpoint. The endpoint takes no org
--           from the caller (it derives the tenant list itself), so a leaked call cannot target one.

CREATE OR REPLACE FUNCTION core.ensure_vision_health_cron(p_url text, p_secret text)
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
  -- %L safely quotes each literal; the secret lives only in cron.job's stored command, exactly like
  -- the email-sweep / account-recompute / reviews / data-sources / system-check jobs.
  v_cmd := format(
    'select net.http_post(url := %L, headers := jsonb_build_object(%L, %L, %L, %L), body := %L::jsonb);',
    rtrim(p_url, '/') || '/api/v1/vision/health/run-due',
    'Content-Type', 'application/json',
    'X-Notify-Secret', p_secret,
    '{}'
  );
  BEGIN
    PERFORM cron.unschedule('vision-health-run-due');
  EXCEPTION WHEN OTHERS THEN
    NULL;   -- not scheduled yet / cron absent — the schedule below decides
  END;
  BEGIN
    -- 07:40 UTC daily. Off the hour and off :17 (the system check) for the same reason mig 971 gave:
    -- the repair pass must not queue behind the very sweeps it might be measuring. Early enough that
    -- a tenant reads the escalation with their morning numbers, having had the repair attempted
    -- first — so the only alerts that arrive are the ones a person really does have to act on.
    v_jobid := cron.schedule('vision-health-run-due', '40 7 * * *', v_cmd);
  EXCEPTION WHEN OTHERS THEN
    RETURN 'cron unavailable (pg_cron/pg_net not installed?): ' || SQLERRM;
  END;
  RETURN 'scheduled job ' || COALESCE(v_jobid::text, '?');
END;
$fn$;

REVOKE ALL ON FUNCTION core.ensure_vision_health_cron(text, text) FROM PUBLIC;

SELECT 'Migration 1034 — vision health self-heal; backend re-registers the job on every boot'
       AS status;

-- Confirm after the next deploy:
--   select jobname, schedule, active from cron.job where jobname = 'vision-health-run-due';
--   select * from cron.job_run_details
--     where jobid = (select jobid from cron.job where jobname='vision-health-run-due')
--     order by start_time desc limit 10;
--
-- Run it by hand at any time (returns what it found, fixed and escalated, per tenant):
--   curl -s -X POST '<APP_PUBLIC_URL>/api/v1/vision/health/run-due' \
--        -H 'X-Notify-Secret: <NOTIFY_RUN_SECRET>'
