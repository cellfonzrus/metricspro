-- 862 — export audit + governance (Security Controls Spec §3/§4, item 7).
--
-- Exports are generated in the browser (SheetJS / jsPDF), so the server never saw them: un-audited,
-- un-watermarked, unbounded. This records every user-initiated export through one chokepoint
-- (frontend src/lib/export.tsx → POST /core/export-event): who exported which report, how many rows,
-- in what format. The endpoint also returns a server-derived watermark the client stamps on the file,
-- and enforces a row cap (EXPORT_MAX_ROWS). Data access itself is already gated at each API — this adds
-- the DLP layer (attribution, volume visibility, deterrence) on top.

CREATE TABLE IF NOT EXISTS core.export_event (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID,
  actor_auth_id TEXT,
  actor_email   TEXT,
  actor_role    TEXT,
  report        TEXT,
  format        TEXT,                                  -- 'excel' | 'pdf' | 'print'
  total_rows    INT,
  sheets        JSONB,                                 -- [{name, rows}]
  over_cap      BOOLEAN NOT NULL DEFAULT false,
  blocked       BOOLEAN NOT NULL DEFAULT false,        -- refused for exceeding the cap
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS export_event_org_idx   ON core.export_event(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS export_event_actor_idx ON core.export_event(actor_auth_id, created_at DESC);

ALTER TABLE core.export_event ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON core.export_event FROM anon, authenticated;
GRANT ALL ON core.export_event TO service_role;

-- Fold export_event into the retention sweep (drop the 3-arg form first so the zero-arg pg_cron call
-- stays unambiguous).
DROP FUNCTION IF EXISTS core.prune_audit_logs(INT, INT, INT);

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
