-- 859 — login attempt ledger + account lockout (Security Controls Spec §1, item 6).
--
-- IMPORTANT CONTEXT: primary sign-in goes browser → Supabase Auth directly (supabase.auth.
-- signInWithPassword), so it does NOT pass through this backend. The AUTHORITATIVE brute-force control
-- is therefore Supabase Auth's own rate limiting (operator config) plus the per-IP limiter added in
-- this phase. This ledger is DEFENSE IN DEPTH + VISIBILITY: it records every attempt (failed logins are
-- otherwise invisible to us) and drives a short per-email soft lockout the login page enforces around
-- the Supabase call. See docs/SECURITY_DAILY_QUESTIONS.md.

CREATE TABLE IF NOT EXISTS core.login_attempt (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email      TEXT,                                  -- lowercased email attempted
  ip         TEXT,
  success    BOOLEAN NOT NULL DEFAULT false,
  user_agent TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS login_attempt_email_idx ON core.login_attempt(email, created_at DESC);
CREATE INDEX IF NOT EXISTS login_attempt_ip_idx    ON core.login_attempt(ip, created_at DESC);

ALTER TABLE core.login_attempt ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON core.login_attempt FROM anon, authenticated;
GRANT ALL ON core.login_attempt TO service_role;

-- Fold login_attempt into the retention sweep (mig 857). The old 2-arg function must be dropped first
-- so the new 3-arg version isn't an ambiguous overload for the zero-arg pg_cron call
-- (SELECT core.prune_audit_logs();).
DROP FUNCTION IF EXISTS core.prune_audit_logs(INT, INT);

CREATE OR REPLACE FUNCTION core.prune_audit_logs(
  p_access_retain_days  INT DEFAULT 365,
  p_failure_retain_days INT DEFAULT 180,
  p_login_retain_days   INT DEFAULT 90
) RETURNS TABLE(table_name TEXT, rows_deleted BIGINT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = core, public
AS $$
DECLARE
  v_access  BIGINT := 0;
  v_failure BIGINT := 0;
  v_login   BIGINT := 0;
BEGIN
  p_access_retain_days  := GREATEST(COALESCE(p_access_retain_days, 365), 30);
  p_failure_retain_days := GREATEST(COALESCE(p_failure_retain_days, 180), 30);
  p_login_retain_days   := GREATEST(COALESCE(p_login_retain_days, 90), 14);

  DELETE FROM core.access_log
   WHERE created_at < now() - make_interval(days => p_access_retain_days);
  GET DIAGNOSTICS v_access = ROW_COUNT;

  BEGIN
    DELETE FROM core.failure_log
     WHERE created_at < now() - make_interval(days => p_failure_retain_days);
    GET DIAGNOSTICS v_failure = ROW_COUNT;
  EXCEPTION WHEN undefined_table THEN
    v_failure := 0;
  END;

  DELETE FROM core.login_attempt
   WHERE created_at < now() - make_interval(days => p_login_retain_days);
  GET DIAGNOSTICS v_login = ROW_COUNT;

  RETURN QUERY VALUES ('access_log', v_access), ('failure_log', v_failure), ('login_attempt', v_login);
END;
$$;

REVOKE ALL ON FUNCTION core.prune_audit_logs(INT, INT, INT) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION core.prune_audit_logs(INT, INT, INT) TO service_role;
