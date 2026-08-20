-- 901_vision_structures.sql — Vision: map Google Home "structures" (homes) to companies.
--
-- OWNER DIRECTIVE 2026-08-20 (sanjot@): "I have 4 different homes set up for the Google home app so
-- it should ask which one to connect to which company as a rule."
--
-- THE PROBLEM THIS CLOSES, WHICH IS AN ISOLATION BUG AND NOT A CONVENIENCE
-- ────────────────────────────────────────────────────────────────────────
-- A Device Access authorization is granted per GOOGLE ACCOUNT, and one account can own several
-- homes. Migration 900's camera sync imported every camera the account could see, so an operator
-- who linked an account owning four homes pulled all four homes' cameras into whichever company
-- happened to run the sync — including cameras belonging to a different company, or to their own
-- house. In a multi-tenant platform that is a cross-tenant leak with a friendly UI in front of it.
--
-- The fix is an explicit, per-tenant allowlist of structures, and it FAILS CLOSED: a home that has
-- not been deliberately assigned to a company imports NOTHING. Adding a fifth home in the Google
-- Home app therefore cannot silently add cameras to a tenant — someone has to say where it belongs.
--
-- RULE TWO: which home belongs to which company is tenant DATA, not a constant. The same Google
-- account can legitimately serve two companies on this platform, each seeing only its own homes.
--
-- SAFE: additive + idempotent. Re-runnable. Touches no money column and no existing module.
-- SECURITY: RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5).

BEGIN;

CREATE SCHEMA IF NOT EXISTS core;

-- One row per (company, Google home) the operator has deliberately connected. A home Google can see
-- but that has NO row here is unassigned, and unassigned means its cameras never import.
CREATE TABLE IF NOT EXISTS core.vision_structure (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,
  structure_id       TEXT NOT NULL,          -- the id from enterprises/<p>/structures/<this>
  structure_name     TEXT,                    -- what the operator called the home in Google Home
  enabled            BOOLEAN NOT NULL DEFAULT true,

  -- Optional convenience: a home usually IS a store, so a newly synced camera from this home can be
  -- pre-assigned to this store code instead of landing unassigned. Never overrides a camera whose
  -- store an operator has already set by hand.
  default_store_code TEXT,

  assigned_by        TEXT,
  assigned_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, structure_id)
);
CREATE INDEX IF NOT EXISTS vision_structure_org ON core.vision_structure (org_id);

-- Stamp the home onto each camera so the settings list can group by it, and so a later "this home
-- moved companies" question has an answer without re-querying Google.
ALTER TABLE core.vision_camera ADD COLUMN IF NOT EXISTS structure_id   TEXT;
ALTER TABLE core.vision_camera ADD COLUMN IF NOT EXISTS structure_name TEXT;
CREATE INDEX IF NOT EXISTS vision_camera_structure ON core.vision_camera (org_id, structure_id);

DO $$
DECLARE t RECORD;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables
           WHERE schemaname = 'core' AND tablename = 'vision_structure' LOOP
    EXECUTE format('ALTER TABLE core.%I ENABLE ROW LEVEL SECURITY', t.tablename);
    EXECUTE format('REVOKE ALL ON core.%I FROM anon, authenticated', t.tablename);
    EXECUTE format('GRANT ALL ON core.%I TO service_role', t.tablename);
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';

COMMIT;
