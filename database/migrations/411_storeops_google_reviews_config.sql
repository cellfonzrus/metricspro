-- 411_storeops_google_reviews_config.sql — Google Reviews module (Phase 1), config layer.
-- mod-people, band 400-499. Owner directive 2026-07-27: pull each store's Google rating, highlight
-- it to scheduled employees, and (Phase 2 of this same package, migration 413) trigger an
-- action-plan when a store falls below its target rating.
--
-- RULE TWO (SAP-configurable): the Google Places API key + the default target rating are tenant
-- config, never hard-coded — set from an admin UI, read via GET /storeops/google-reviews/config.
--
-- storeops.google_review_config — ONE row per org. Holds the Google Places API key, so it gets the
-- SAME hardened credential posture as commcalc.vip_sweep_config / dlar_sweep_config /
-- epay_sweep_config: RLS on, NO anon/authenticated policy, grants revoked, service_role only. The
-- admin page talks to the backend API, which masks the key on every read and never returns it raw.
CREATE TABLE IF NOT EXISTS storeops.google_review_config (
  org_id                 UUID PRIMARY KEY,
  api_key                TEXT,                            -- Google Places API (New) key; backend-only
  enabled                BOOLEAN     NOT NULL DEFAULT false,   -- off until a key is set + turned on
  target_default         NUMERIC     NOT NULL DEFAULT 4.7,     -- default Google-rating target, every store
  notify_on_new_reviews  BOOLEAN     NOT NULL DEFAULT true,
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by             TEXT
);
ALTER TABLE storeops.google_review_config ENABLE ROW LEVEL SECURITY;
-- intentionally NO anon/authenticated policy -> the public key gets RLS-denied (same as VIP/DLAR/epay).
DROP POLICY IF EXISTS open_all ON storeops.google_review_config;
REVOKE ALL ON storeops.google_review_config FROM anon, authenticated;
GRANT ALL ON storeops.google_review_config TO service_role;

-- storeops.google_review_store — per-store Google Place resolution cache + manual override, and an
-- optional per-store target override (NULL = inherit google_review_config.target_default). No secret
-- lives here, so it uses the normal open_all posture (the backend still enforces org_id scoping on
-- every call regardless of RLS, same as every other storeops config table).
CREATE TABLE IF NOT EXISTS storeops.google_review_store (
  id                     BIGSERIAL PRIMARY KEY,
  org_id                 UUID NOT NULL,
  store_code             TEXT NOT NULL,
  place_id               TEXT,
  place_id_source        TEXT NOT NULL DEFAULT 'auto' CHECK (place_id_source IN ('auto', 'manual')),
  resolved_address       TEXT,           -- the address Google actually matched, for an admin sanity-check
  resolved_display_name  TEXT,
  target_override        NUMERIC,        -- NULL = inherit the org default
  last_place_lookup_at   TIMESTAMPTZ,
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_google_review_store_org_code
  ON storeops.google_review_store (org_id, store_code);

-- storeops.google_review_sweep_config — ONE row per org: schedule + run-status for the auto-pull
-- (same shape as commcalc's *_sweep_config tables, minus credentials — the api_key lives on
-- google_review_config above). Drives GET/PUT /storeops/google-reviews/sweep-config,
-- POST /storeops/google-reviews/sweep/run-due (secret-gated pg_cron entrypoint) and
-- POST /storeops/google-reviews/sweep/run-now (manual "Refresh now").
CREATE TABLE IF NOT EXISTS storeops.google_review_sweep_config (
  org_id          UUID PRIMARY KEY,
  enabled         BOOLEAN     NOT NULL DEFAULT false,
  frequency       TEXT        NOT NULL DEFAULT 'daily',    -- daily | weekly
  day_of_week     INT         NOT NULL DEFAULT 0,          -- 0=Mon..6=Sun (weekly)
  hour            INT         NOT NULL DEFAULT 6,          -- hour-of-day in `timezone`
  timezone        TEXT        NOT NULL DEFAULT 'America/New_York',
  next_run_at     TIMESTAMPTZ,
  last_run_at     TIMESTAMPTZ,
  last_attempt_at TIMESTAMPTZ,
  last_status     TEXT,                                    -- ok | error | running | idle
  last_detail     TEXT,                                    -- summary or error (no secrets)
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['storeops.google_review_store', 'storeops.google_review_sweep_config'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

-- Seed a default (disabled, no key) config + sweep row for every existing tenant, so the admin page
-- always has something to read/PUT against instead of a hard 404. Idempotent — never overwrites.
-- SECURITY INVOKER (default): only ever called from a migration/service-role context, never exposed
-- to app traffic, so EXECUTE is granted to service_role only (matches the credential table above).
CREATE OR REPLACE FUNCTION storeops.seed_google_review_config(p_org uuid)
RETURNS void LANGUAGE plpgsql AS $fn$
BEGIN
  INSERT INTO storeops.google_review_config (org_id) VALUES (p_org) ON CONFLICT (org_id) DO NOTHING;
  INSERT INTO storeops.google_review_sweep_config (org_id) VALUES (p_org) ON CONFLICT (org_id) DO NOTHING;
END;
$fn$;
GRANT EXECUTE ON FUNCTION storeops.seed_google_review_config(uuid) TO service_role;

DO $seed$
DECLARE t record;
BEGIN
  PERFORM storeops.seed_google_review_config('00000000-0000-0000-0000-000000000001');
  BEGIN
    FOR t IN SELECT org_id FROM storeops.tenants LOOP
      PERFORM storeops.seed_google_review_config(t.org_id);
    END LOOP;
  EXCEPTION WHEN undefined_table THEN
    NULL;  -- storeops.tenants absent in a bare env -> house seed above still applied
  END;
END $seed$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 411 complete — storeops.google_review_config + google_review_store + google_review_sweep_config' AS status;
