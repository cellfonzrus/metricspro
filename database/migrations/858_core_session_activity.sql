-- 858 — session activity, backing store for server-side session controls (Security Controls Spec §1, P0).
--
-- MetricsPro delegates authentication to Supabase (JWT). Until now there was NO server-side idle
-- timeout, absolute session lifetime, or revocation: a token was valid until Supabase's own expiry.
-- This table lets the backend enforce an idle window and an absolute lifetime, keyed on the durable
-- `session_id` claim that Supabase carries across access-token refreshes.
--
-- One row per browser session. The middleware upserts last_seen_at (write-throttled) on each
-- authenticated request and reads started_at/last_seen_at to decide whether the session is still
-- alive. `ended_at` / `ended_reason` mark a session the guard closed (idle / absolute / manual revoke),
-- which is also what a future "sign out everywhere" / device-list feature reads.
--
-- Enforcement is GATED behind env SESSION_ENFORCE (default OFF), exactly like MULTI_TENANT_ENFORCE:
-- shipping the table + the write path changes nothing user-visible until an operator turns it on.

CREATE TABLE IF NOT EXISTS core.session_activity (
  session_id    TEXT PRIMARY KEY,                     -- Supabase JWT `session_id` (durable across refresh)
  auth_id       TEXT,                                 -- supabase auth user id
  org_id        UUID,                                 -- active tenant at first sight (informational)
  actor_email   TEXT,
  actor_role    TEXT,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),   -- absolute-lifetime clock
  last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),   -- idle clock
  request_count BIGINT NOT NULL DEFAULT 0,
  last_ip       TEXT,
  ended_at      TIMESTAMPTZ,                          -- set when the guard closes the session
  ended_reason  TEXT                                  -- 'idle' | 'absolute' | 'revoked'
);

CREATE INDEX IF NOT EXISTS session_activity_auth_idx ON core.session_activity(auth_id, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS session_activity_seen_idx ON core.session_activity(last_seen_at DESC);

-- Service-role only, like the other core auth/audit tables.
ALTER TABLE core.session_activity ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON core.session_activity FROM anon, authenticated;
GRANT ALL ON core.session_activity TO service_role;
