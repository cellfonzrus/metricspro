-- 856 — system access log (owner 2026-08-16: attribute who accesses the system, incl. during an
-- ungated/open-app window). One row per HTTP request, written best-effort by AccessLogMiddleware.
-- Captures the resolved actor (or anonymous in open mode), path, status, IP, and client GPS.
CREATE TABLE IF NOT EXISTS core.access_log (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID,                                  -- resolved active tenant (null when anonymous)
  actor_auth_id  TEXT,                                  -- supabase auth id of the signed-in human (null = anon)
  actor_email    TEXT,
  actor_role     TEXT,
  anonymous      BOOLEAN NOT NULL DEFAULT false,        -- true = no signed-in identity (open/ungated mode)
  method         TEXT,
  path           TEXT,
  query          TEXT,
  status         INT,                                   -- HTTP response status
  ip             TEXT,
  user_agent     TEXT,
  gps_lat        NUMERIC,
  gps_lng        NUMERIC,
  gps_accuracy_m NUMERIC,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS access_log_org_idx    ON core.access_log(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS access_log_actor_idx  ON core.access_log(actor_auth_id, created_at DESC);
CREATE INDEX IF NOT EXISTS access_log_ip_idx     ON core.access_log(ip, created_at DESC);
CREATE INDEX IF NOT EXISTS access_log_anon_idx   ON core.access_log(created_at DESC) WHERE anonymous;

-- Service-role only, like the other core audit tables (impersonation / crm_lookup_audit).
ALTER TABLE core.access_log ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON core.access_log FROM anon, authenticated;
GRANT ALL ON core.access_log TO service_role;
