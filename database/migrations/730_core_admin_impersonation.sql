-- 730_core_admin_impersonation.sql — ADMIN "VIEW AS EMPLOYEE" (impersonation) audit + policy.
-- Band 700–799 (mod-platform-core). Additive + idempotent (safe to re-run).
--
-- OWNER DIRECTIVE 2026-08-06:
--   "the admin should have the option to login the app with an employees login directly from the
--    roles and config menu incase needed to replicate an issue they are facing but not able to clock
--    in or clock out without password, rest all functions should work"
--
-- WHAT THIS CREATES
--   (1) core.impersonation_session — ONE append-only row per "view as employee" session: who did it,
--       to whom, in which tenant, when it started, when it must expire, when it actually ended and
--       why, plus the source IP / device. This row is the ANCHOR the signed grant is checked against
--       on EVERY request, so "Exit" and the expiry are real revocations, not client-side wishes.
--   (2) core.impersonation_action — the append-only WRITE JOURNAL. The middleware writes one row per
--       MUTATING request made inside an impersonated session BEFORE the handler runs (fail-closed),
--       so everything written while an admin wears an employee's face stays attributable to the real
--       human. Also carries the start / stop / re-auth / denial events.
--   (3) core.impersonation_reauth — the single-use "the employee just typed their password" markers
--       that unlock ONE clock-in or clock-out. UNIQUE (imp_session_id, auth_session_id) is the
--       replay control: one Supabase sign-in session may mint at most ONE marker, so keeping a
--       refresh token from a past password entry buys nothing.
--   (4) storeops.tenants.impersonation_policy (jsonb) — the tenant-configurable knobs (RULE TWO):
--       {"enabled":true,"max_minutes":45,"reauth_minutes":5,"reauth_token_max_age_s":120}.
--       NULL / absent ⇒ the code defaults, so behaviour is identical before and after this runs.
--
-- IMMUTABILITY. These are audit tables. A trigger forbids DELETE outright and forbids rewriting the
--   identity columns; `ended_at` is write-once. The backend's service role bypasses RLS, so the
--   trigger — not a policy — is what actually protects the record.
--
-- MULTI-TENANT (RULE ONE): every table has org_id uuid NOT NULL + an index on it.
--
-- SECURITY (contract §5): RLS ENABLED, ZERO policies, and NO grant to anon/authenticated — ever.
--   Only service_role is granted; the backend is the only door.
--
-- DEGRADES GRACEFULLY: until this runs, POST /core/impersonation/start cannot write its audit row
--   and therefore FAILS CLOSED — no grant is minted, so the feature is simply absent and NOTHING
--   else changes (a request with no x-impersonate header never touches any of this).
--
-- After running, reload PostgREST's schema cache:  NOTIFY pgrst, 'reload schema';

CREATE SCHEMA IF NOT EXISTS core;

-- ── (1) the session record ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS core.impersonation_session (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID        NOT NULL,           -- the tenant the session is PINNED to
  actor_auth_id     TEXT        NOT NULL,           -- the REAL human (supabase auth id)
  actor_email       TEXT,
  actor_name        TEXT,
  actor_role        TEXT,
  target_auth_id    TEXT        NOT NULL,           -- the employee being viewed as
  target_app_user   TEXT,                           -- storeops.app_users.id (string form)
  target_email      TEXT,
  target_name       TEXT,
  target_role       TEXT,
  reason            TEXT,                           -- optional "what are you reproducing?"
  started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at        TIMESTAMPTZ NOT NULL,           -- hard server-side expiry
  ended_at          TIMESTAMPTZ,                    -- write-once
  end_reason        TEXT,                           -- 'exit' | 'expired' | 'revoked' | 'target_revoked'
  ip                TEXT,
  user_agent        TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS impersonation_session_org_idx     ON core.impersonation_session(org_id, started_at DESC);
CREATE INDEX IF NOT EXISTS impersonation_session_actor_idx   ON core.impersonation_session(actor_auth_id, started_at DESC);
CREATE INDEX IF NOT EXISTS impersonation_session_target_idx  ON core.impersonation_session(target_auth_id, started_at DESC);
CREATE INDEX IF NOT EXISTS impersonation_session_open_idx    ON core.impersonation_session(ended_at) WHERE ended_at IS NULL;

ALTER TABLE core.impersonation_session ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON core.impersonation_session FROM anon, authenticated;
GRANT ALL ON core.impersonation_session TO service_role;

-- ── (2) the write journal + event log ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS core.impersonation_action (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id        UUID        NOT NULL REFERENCES core.impersonation_session(id),
  org_id            UUID        NOT NULL,
  actor_auth_id     TEXT        NOT NULL,
  target_auth_id    TEXT        NOT NULL,
  kind              TEXT        NOT NULL DEFAULT 'write',  -- write|start|stop|reauth|reauth_used|denied
  method            TEXT,
  path              TEXT,
  query             TEXT,
  status            INT,
  detail            JSONB,
  ip                TEXT,
  user_agent        TEXT,
  at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS impersonation_action_session_idx ON core.impersonation_action(session_id, at DESC);
CREATE INDEX IF NOT EXISTS impersonation_action_org_idx     ON core.impersonation_action(org_id, at DESC);

ALTER TABLE core.impersonation_action ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON core.impersonation_action FROM anon, authenticated;
GRANT ALL ON core.impersonation_action TO service_role;

-- ── (3) single-use re-authentication markers (the clock-in / clock-out carve-out) ───────────────
CREATE TABLE IF NOT EXISTS core.impersonation_reauth (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  imp_session_id    UUID        NOT NULL REFERENCES core.impersonation_session(id),
  org_id            UUID        NOT NULL,
  target_auth_id    TEXT        NOT NULL,
  nonce             TEXT        NOT NULL UNIQUE,     -- carried inside the signed marker
  auth_session_id   TEXT        NOT NULL,            -- the Supabase sign-in session that proved the password
  issued_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at        TIMESTAMPTZ NOT NULL,
  consumed_at       TIMESTAMPTZ,
  consumed_action   TEXT,
  ip                TEXT,
  -- ONE marker per Supabase sign-in session per impersonation session: an admin who kept a refresh
  -- token from an earlier password entry cannot mint a second unlock.
  UNIQUE (imp_session_id, auth_session_id)
);
CREATE INDEX IF NOT EXISTS impersonation_reauth_session_idx ON core.impersonation_reauth(imp_session_id, issued_at DESC);
CREATE INDEX IF NOT EXISTS impersonation_reauth_org_idx     ON core.impersonation_reauth(org_id, issued_at DESC);

ALTER TABLE core.impersonation_reauth ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON core.impersonation_reauth FROM anon, authenticated;
GRANT ALL ON core.impersonation_reauth TO service_role;

-- ── (4) append-only enforcement (the service role bypasses RLS, so this is the real protection) ──
CREATE OR REPLACE FUNCTION core.impersonation_audit_guard() RETURNS trigger AS $fn$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'core.% is an append-only audit table', TG_TABLE_NAME;
  END IF;
  IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'impersonation_session' THEN
    IF NEW.id <> OLD.id
       OR NEW.org_id <> OLD.org_id
       OR NEW.actor_auth_id <> OLD.actor_auth_id
       OR NEW.target_auth_id <> OLD.target_auth_id
       OR NEW.started_at <> OLD.started_at THEN
      RAISE EXCEPTION 'core.impersonation_session identity columns are immutable';
    END IF;
    IF OLD.ended_at IS NOT NULL AND NEW.ended_at IS DISTINCT FROM OLD.ended_at THEN
      RAISE EXCEPTION 'core.impersonation_session.ended_at is write-once';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'impersonation_action' THEN
    -- only the status back-fill may ever change on a journal row
    IF NEW.id <> OLD.id OR NEW.session_id <> OLD.session_id OR NEW.org_id <> OLD.org_id
       OR NEW.actor_auth_id <> OLD.actor_auth_id OR NEW.target_auth_id <> OLD.target_auth_id
       OR NEW.kind <> OLD.kind OR NEW.at <> OLD.at
       OR NEW.method IS DISTINCT FROM OLD.method OR NEW.path IS DISTINCT FROM OLD.path THEN
      RAISE EXCEPTION 'core.impersonation_action rows are immutable except for the status back-fill';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'impersonation_reauth' THEN
    IF OLD.consumed_at IS NOT NULL AND NEW.consumed_at IS DISTINCT FROM OLD.consumed_at THEN
      RAISE EXCEPTION 'core.impersonation_reauth markers are single-use';
    END IF;
  END IF;
  RETURN NEW;
END
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS impersonation_session_guard ON core.impersonation_session;
CREATE TRIGGER impersonation_session_guard
  BEFORE UPDATE OR DELETE ON core.impersonation_session
  FOR EACH ROW EXECUTE FUNCTION core.impersonation_audit_guard();

DROP TRIGGER IF EXISTS impersonation_action_guard ON core.impersonation_action;
CREATE TRIGGER impersonation_action_guard
  BEFORE UPDATE OR DELETE ON core.impersonation_action
  FOR EACH ROW EXECUTE FUNCTION core.impersonation_audit_guard();

DROP TRIGGER IF EXISTS impersonation_reauth_guard ON core.impersonation_reauth;
CREATE TRIGGER impersonation_reauth_guard
  BEFORE UPDATE OR DELETE ON core.impersonation_reauth
  FOR EACH ROW EXECUTE FUNCTION core.impersonation_audit_guard();

-- ── (5) tenant-configurable policy (RULE TWO) ───────────────────────────────────────────────────
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS impersonation_policy JSONB;

COMMENT ON COLUMN storeops.tenants.impersonation_policy IS
  'Admin "view as employee" policy. {"enabled":bool,"max_minutes":int,"reauth_minutes":int,'
  '"reauth_token_max_age_s":int}. NULL = code defaults (enabled, 45 / 5 / 120). Clamped server-side.';

-- Nothing is seeded on purpose: the ROLE PERMISSION `impersonate` is DEFAULT-DENY for every existing
-- and every future role. An administrator must consciously tick "Sign in as an employee" on a role at
-- /admin/roles before anyone — including a super-admin — can use this.

NOTIFY pgrst, 'reload schema';
