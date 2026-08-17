-- 863 — audit-log tamper-evidence (Security Controls Spec §3, item 13).
--
-- Makes the append-only security logs WORM: UPDATE is never allowed, and DELETE is allowed ONLY from
-- inside the retention job (core.prune_audit_logs), which sets a transaction-local flag before it
-- prunes. Ad-hoc tampering — even with the service role — is blocked at the database. The app only ever
-- INSERTs/SELECTs these tables (verified), so this changes no application behaviour.
--
-- Scope: access_log, login_attempt, export_event (pruned → delete allowed via the flag) and
-- crm_lookup_audit (audit-of-record, never pruned → all deletes blocked). session_activity and ip_block
-- are intentionally MUTABLE (last_seen updates, unblock/expiry) and are NOT WORM. The impersonation log
-- already has its own WORM trigger and is left untouched.

CREATE OR REPLACE FUNCTION core.worm_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'WORM: %.% is append-only; updates are not permitted', TG_TABLE_SCHEMA, TG_TABLE_NAME;
  ELSIF TG_OP = 'DELETE' THEN
    -- Allowed only inside the retention job, which sets this transaction-local flag.
    IF current_setting('app.worm_allow_delete', true) IS DISTINCT FROM 'on' THEN
      RAISE EXCEPTION 'WORM: %.% rows can only be removed by the retention job (core.prune_audit_logs)',
        TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS worm_access_log       ON core.access_log;
DROP TRIGGER IF EXISTS worm_login_attempt    ON core.login_attempt;
DROP TRIGGER IF EXISTS worm_export_event     ON core.export_event;
DROP TRIGGER IF EXISTS worm_crm_lookup_audit ON core.crm_lookup_audit;

CREATE TRIGGER worm_access_log       BEFORE UPDATE OR DELETE ON core.access_log
  FOR EACH ROW EXECUTE FUNCTION core.worm_guard();
CREATE TRIGGER worm_login_attempt    BEFORE UPDATE OR DELETE ON core.login_attempt
  FOR EACH ROW EXECUTE FUNCTION core.worm_guard();
CREATE TRIGGER worm_export_event     BEFORE UPDATE OR DELETE ON core.export_event
  FOR EACH ROW EXECUTE FUNCTION core.worm_guard();
CREATE TRIGGER worm_crm_lookup_audit BEFORE UPDATE OR DELETE ON core.crm_lookup_audit
  FOR EACH ROW EXECUTE FUNCTION core.worm_guard();

-- Re-define the retention job to grant itself the transaction-local delete permission first. Same
-- 4-arg signature as mig 862 (CREATE OR REPLACE, no drop needed).
CREATE OR REPLACE FUNCTION core.prune_audit_logs(
  p_access_retain_days  INT DEFAULT 365,
  p_failure_retain_days INT DEFAULT 180,
  p_login_retain_days   INT DEFAULT 90,
  p_export_retain_days  INT DEFAULT 365
) RETURNS TABLE(table_name TEXT, rows_deleted BIGINT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = core, public
AS $$
DECLARE
  v_access  BIGINT := 0;
  v_failure BIGINT := 0;
  v_login   BIGINT := 0;
  v_export  BIGINT := 0;
BEGIN
  -- Authorize WORM deletes for THIS transaction only (the triggers check this flag).
  PERFORM set_config('app.worm_allow_delete', 'on', true);

  p_access_retain_days  := GREATEST(COALESCE(p_access_retain_days, 365), 30);
  p_failure_retain_days := GREATEST(COALESCE(p_failure_retain_days, 180), 30);
  p_login_retain_days   := GREATEST(COALESCE(p_login_retain_days, 90), 14);
  p_export_retain_days  := GREATEST(COALESCE(p_export_retain_days, 365), 30);

  DELETE FROM core.access_log   WHERE created_at < now() - make_interval(days => p_access_retain_days);
  GET DIAGNOSTICS v_access = ROW_COUNT;

  BEGIN
    DELETE FROM core.failure_log WHERE created_at < now() - make_interval(days => p_failure_retain_days);
    GET DIAGNOSTICS v_failure = ROW_COUNT;
  EXCEPTION WHEN undefined_table THEN
    v_failure := 0;
  END;

  DELETE FROM core.login_attempt WHERE created_at < now() - make_interval(days => p_login_retain_days);
  GET DIAGNOSTICS v_login = ROW_COUNT;

  DELETE FROM core.export_event  WHERE created_at < now() - make_interval(days => p_export_retain_days);
  GET DIAGNOSTICS v_export = ROW_COUNT;

  RETURN QUERY VALUES ('access_log', v_access), ('failure_log', v_failure),
                      ('login_attempt', v_login), ('export_event', v_export);
END;
$$;

REVOKE ALL ON FUNCTION core.prune_audit_logs(INT, INT, INT, INT) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION core.prune_audit_logs(INT, INT, INT, INT) TO service_role;
