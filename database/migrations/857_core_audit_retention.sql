-- 857 — audit-log retention + prune (Security Controls Spec §3, P0).
--
-- The access log (mig 856) and the failure log grow without bound. This adds a retention function
-- and (best-effort) schedules it daily via pg_cron. It deliberately does NOT touch the impersonation
-- log or crm_lookup_audit — those are the audit-of-record and are kept indefinitely / WORM.
--
-- The function is the single source of truth for the delete logic; the backend exposes
-- POST /api/v1/core/audit/prune/run-due (secret-guarded, same shape as /notify/run-due) so the sweep
-- works whether or not pg_cron is available in this project. Returns the number of rows removed from
-- each table so the caller (and job_run) can report it.
--
-- Retention windows are ARGUMENTS with safe defaults (access log 365d, failure log 180d), so an
-- operator can prune more or less aggressively without a code change.

CREATE OR REPLACE FUNCTION core.prune_audit_logs(
  p_access_retain_days  INT DEFAULT 365,
  p_failure_retain_days INT DEFAULT 180
) RETURNS TABLE(table_name TEXT, rows_deleted BIGINT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = core, public
AS $$
DECLARE
  v_access  BIGINT := 0;
  v_failure BIGINT := 0;
BEGIN
  -- access_log: high-volume request telemetry. Guard windows to a sane floor so a bad/zero argument
  -- can never wipe recent security-relevant history (min 30 days retained).
  p_access_retain_days  := GREATEST(COALESCE(p_access_retain_days, 365), 30);
  p_failure_retain_days := GREATEST(COALESCE(p_failure_retain_days, 180), 30);

  DELETE FROM core.access_log
   WHERE created_at < now() - make_interval(days => p_access_retain_days);
  GET DIAGNOSTICS v_access = ROW_COUNT;

  -- failure_log exists only in projects that ran mig 112; tolerate its absence.
  BEGIN
    DELETE FROM core.failure_log
     WHERE created_at < now() - make_interval(days => p_failure_retain_days);
    GET DIAGNOSTICS v_failure = ROW_COUNT;
  EXCEPTION WHEN undefined_table THEN
    v_failure := 0;
  END;

  RETURN QUERY VALUES ('access_log', v_access), ('failure_log', v_failure);
END;
$$;

REVOKE ALL ON FUNCTION core.prune_audit_logs(INT, INT) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION core.prune_audit_logs(INT, INT) TO service_role;

-- Best-effort daily schedule at 04:10 UTC. Guarded: only runs if pg_cron is installed AND the job is
-- not already registered. If pg_cron is absent this whole block is a no-op and the backend run-due
-- endpoint remains the way the sweep fires.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    IF NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'prune_audit_logs_daily') THEN
      PERFORM cron.schedule('prune_audit_logs_daily', '10 4 * * *',
                            'SELECT core.prune_audit_logs();');
    END IF;
  END IF;
EXCEPTION WHEN OTHERS THEN
  -- never fail the migration over an optional schedule
  NULL;
END;
$$;
