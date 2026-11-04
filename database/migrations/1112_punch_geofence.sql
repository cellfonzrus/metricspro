-- 1112_punch_geofence.sql — "was this punch made at the store it is for?"
--
-- OWNER ASK 2026-11-01 (verbatim): *"the sales reps when they log in should capture the gos location
-- so they are not clocking out at their homes and the geolocation when they clock out from somewhere
-- else than their usual location which should match the store address should be reported as flag to
-- the management"*.
--
-- WHAT WAS ALREADY HERE, AND WHAT WAS MISSING
-- ───────────────────────────────────────────
--   · storeops.timelog has CAPTURED a clock-in fix since migration 045 (gps_lat / gps_lng /
--     gps_accuracy_m) and both the kiosk and the phone app send one. The clock-OUT captured
--     NOTHING — which is why the punch the owner is worried about is the one nobody could see.
--   · core/geo.evaluate_checkin has been THE geofence decision since migration 986 (index §23).
--     Nothing judged a punch with it.
--   · storeops.stores holds an `address` and NO COORDINATES, so there was nothing to measure a
--     distance to. That missing input is the substance of this migration.
--
-- So: three column groups and nothing else. No new table, no second geofence, no per-tenant code.
--   1. storeops.stores   — the store's MAP PIN (the tenant's own declaration; no default exists).
--   2. storeops.tenants  — the geofence POLICY (radius / accuracy limit / switch / GPS retention),
--                          house defaults 150 m / 200 m / ON / 180 days, matching
--                          core/store_geofence.DEFAULT_CONFIG and the §23.5 retention posture.
--   3. storeops.timelog  — the CLOCK-OUT's own fix, plus the stored verdict for each end of the
--                          punch. The clock-out never overwrites the clock-in's coordinates: a
--                          shift's two ends are two different facts.
--   4. storeops.store_visits — the same two verdict columns for the DM check-in, which has captured
--                          a fix since migration 027 and never judged one. THE SIBLING PATH
--                          (CLAUDE.md 2026-09-20: fix the class, not the instance).
--
-- Additive + idempotent (ADD COLUMN IF NOT EXISTS; the CHECK constraints are added only when
-- missing, the same guarded idiom as migrations 409 and 421). No RLS change — all four relations
-- already carry their existing posture. NOT money-touching: nothing here is read by a pay, payable
-- or P&L path.
--
-- WHAT BREAKS UNTIL THIS RUNS: nothing, and that is enforced in code, not hoped for.
--   · POST /storeops/timeclock/clock-in and /clock-out PROBE for these columns (a cached
--     try/except read, the same posture as `_timeclock_432_present`) and treat ANY failure as "not
--     applied": the punch is recorded exactly as it is today, with no verdict and no clock-out fix.
--   · GET /storeops/timeclock/geo-config answers with the code defaults and `available=false`;
--     its Save action is the only thing that refuses until this lands.
--   · The Attendance Exceptions board simply has no off-site rows to show, because no verdict has
--     been stored. It does not error and does not hide its other exception types.
-- Pinned by backend/tests/integration/test_punch_geofence_endpoint.py §M.
--
-- REVERT:
--   ALTER TABLE storeops.stores DROP COLUMN IF EXISTS geo_lat, DROP COLUMN IF EXISTS geo_lng,
--     DROP COLUMN IF EXISTS geofence_radius_m, DROP COLUMN IF EXISTS geo_pin_source,
--     DROP COLUMN IF EXISTS geo_pinned_at, DROP COLUMN IF EXISTS geo_pinned_by;
--   ALTER TABLE storeops.tenants DROP COLUMN IF EXISTS punch_geo_enabled,
--     DROP COLUMN IF EXISTS punch_geofence_radius_m, DROP COLUMN IF EXISTS punch_geo_max_accuracy_m,
--     DROP COLUMN IF EXISTS punch_geo_retention_days;
--   ALTER TABLE storeops.timelog DROP COLUMN IF EXISTS clock_out_gps_lat,
--     DROP COLUMN IF EXISTS clock_out_gps_lng, DROP COLUMN IF EXISTS clock_out_gps_accuracy_m,
--     DROP COLUMN IF EXISTS clock_in_geo_decision, DROP COLUMN IF EXISTS clock_in_geo_distance_m,
--     DROP COLUMN IF EXISTS clock_out_geo_decision, DROP COLUMN IF EXISTS clock_out_geo_distance_m;
--   ALTER TABLE storeops.store_visits DROP COLUMN IF EXISTS check_in_geo_decision,
--     DROP COLUMN IF EXISTS check_in_geo_distance_m;
--   (plus the four CHECK constraints named below)

-- ═══ 1. THE STORE'S MAP PIN ═══════════════════════════════════════════════════════════════════
-- The tenant's OWN declaration. No default and no geocoding: a coordinate guessed from an address
-- string would be a fence nobody declared, and an unpinned store correctly answers "could not
-- verify" (core/store_geofence.store_pin → None) rather than accusing anyone.
--
-- `geofence_radius_m` is NULL by design — the store inherits its tenant's radius until somebody
-- declares that THIS store needs a different one (a mall kiosk is not a standalone shop).
ALTER TABLE storeops.stores
  ADD COLUMN IF NOT EXISTS geo_lat           NUMERIC(10,8),
  ADD COLUMN IF NOT EXISTS geo_lng           NUMERIC(11,8),
  ADD COLUMN IF NOT EXISTS geofence_radius_m INTEGER,
  ADD COLUMN IF NOT EXISTS geo_pin_source    TEXT,          -- how the pin was set: 'manual' | 'punch_cluster'
  ADD COLUMN IF NOT EXISTS geo_pinned_at     TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS geo_pinned_by     TEXT;          -- who declared it (audit; a pin changes who gets flagged)

-- A pin is BOTH coordinates or NEITHER. Half a pin is the shape that silently places a store on the
-- prime meridian; the code already refuses it, and the database should not be able to hold it.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'stores_geo_pin_both_chk') THEN
    ALTER TABLE storeops.stores
      ADD CONSTRAINT stores_geo_pin_both_chk
      CHECK ((geo_lat IS NULL) = (geo_lng IS NULL));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'stores_geofence_radius_chk') THEN
    ALTER TABLE storeops.stores
      ADD CONSTRAINT stores_geofence_radius_chk
      CHECK (geofence_radius_m IS NULL OR (geofence_radius_m >= 10 AND geofence_radius_m <= 20000));
  END IF;
END $$;

-- ═══ 2. THE GEOFENCE POLICY (per tenant, house defaults behind it) ════════════════════════════
-- A radius, an accuracy limit, a switch and a retention window are POLICY, which is exactly what a
-- house default is right for (owner 2026-10-30: the one place a default is BANNED is a tenant's own
-- vocabulary — and the PIN above has no default for that reason).
--
-- ON for every tenant from day one, and safely inert: with no store pinned, no verdict can be
-- produced, so the default accuses nobody and starts working the moment a pin is declared.
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS punch_geo_enabled        BOOLEAN NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS punch_geofence_radius_m  INTEGER NOT NULL DEFAULT 150,
  ADD COLUMN IF NOT EXISTS punch_geo_max_accuracy_m INTEGER NOT NULL DEFAULT 200,
  -- SENSITIVE PERSONAL DATA (index §23.5). An employee's coordinates are kept for this many days so
  -- the commitment lives in the tenant's own row rather than in a sentence in a document. House 180,
  -- the same figure marketing_config.checkin_geo_retention_days already carries.
  ADD COLUMN IF NOT EXISTS punch_geo_retention_days INTEGER NOT NULL DEFAULT 180;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tenants_punch_geofence_radius_chk') THEN
    ALTER TABLE storeops.tenants
      ADD CONSTRAINT tenants_punch_geofence_radius_chk
      CHECK (punch_geofence_radius_m >= 10 AND punch_geofence_radius_m <= 20000);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tenants_punch_geo_accuracy_chk') THEN
    ALTER TABLE storeops.tenants
      ADD CONSTRAINT tenants_punch_geo_accuracy_chk
      CHECK (punch_geo_max_accuracy_m > 0 AND punch_geo_max_accuracy_m <= 20000);
  END IF;
END $$;

-- ═══ 3. THE PUNCH: the clock-out's own fix, and a verdict for each end ════════════════════════
-- The clock-in keeps migration 045's three columns, so not one stored punch moves. The clock-out
-- gets its OWN three: overwriting the clock-in's fix would destroy the only evidence of where the
-- shift began, and "arrived at the store, left from home" is precisely the pattern being looked for.
--
-- The verdict is STORED rather than recomputed on read. A pin that moves later must not silently
-- restate who was flagged last month — the row records what was true against the fence that was
-- declared at the time, and `core/store_geofence.PUNCH_COLUMNS` is the one home for which column
-- holds which fact.
ALTER TABLE storeops.timelog
  ADD COLUMN IF NOT EXISTS clock_out_gps_lat        NUMERIC(10,8),
  ADD COLUMN IF NOT EXISTS clock_out_gps_lng        NUMERIC(11,8),
  ADD COLUMN IF NOT EXISTS clock_out_gps_accuracy_m INTEGER,
  ADD COLUMN IF NOT EXISTS clock_in_geo_decision    TEXT,
  ADD COLUMN IF NOT EXISTS clock_in_geo_distance_m  INTEGER,
  ADD COLUMN IF NOT EXISTS clock_out_geo_decision   TEXT,
  ADD COLUMN IF NOT EXISTS clock_out_geo_distance_m INTEGER;

-- The decision vocabulary is core/geo.DECISIONS, pinned here so a typo in a future writer cannot
-- invent a verdict the review board has no rule for. NULL means "never measured", which is a
-- different and honest thing from any of the five words.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'timelog_geo_decision_chk') THEN
    ALTER TABLE storeops.timelog
      ADD CONSTRAINT timelog_geo_decision_chk CHECK (
        (clock_in_geo_decision IS NULL OR clock_in_geo_decision IN
          ('inside','outside','unverified_no_fix','unverified_no_target','unverified_accuracy'))
        AND
        (clock_out_geo_decision IS NULL OR clock_out_geo_decision IN
          ('inside','outside','unverified_no_fix','unverified_no_target','unverified_accuracy'))
      );
  END IF;
END $$;

-- The board asks for one window's off-site punches. Partial indexes so that read never scans the
-- whole clock history to find the few rows that were flagged.
CREATE INDEX IF NOT EXISTS timelog_clock_in_off_site_idx
  ON storeops.timelog (org_id, work_date)
  WHERE clock_in_geo_decision = 'outside';
CREATE INDEX IF NOT EXISTS timelog_clock_out_off_site_idx
  ON storeops.timelog (org_id, work_date)
  WHERE clock_out_geo_decision = 'outside';

-- ═══ 4. THE SIBLING: the DM's store-visit check-in ════════════════════════════════════════════
-- It has captured a fix since migration 027 and has never judged one. Fixing the punch and leaving
-- this is "the same defect wearing a hat" (CLAUDE.md 2026-09-20), so it gets the same two columns
-- and the same one decision — not a second rule of its own.
ALTER TABLE storeops.store_visits
  ADD COLUMN IF NOT EXISTS check_in_geo_decision   TEXT,
  ADD COLUMN IF NOT EXISTS check_in_geo_distance_m INTEGER;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'store_visits_geo_decision_chk') THEN
    ALTER TABLE storeops.store_visits
      ADD CONSTRAINT store_visits_geo_decision_chk CHECK (
        check_in_geo_decision IS NULL OR check_in_geo_decision IN
          ('inside','outside','unverified_no_fix','unverified_no_target','unverified_accuracy'));
  END IF;
END $$;

-- ═══ GRANTS ═══════════════════════════════════════════════════════════════════════════════════
-- "A migration grants what it creates" (CLAUDE.md). No relation is CREATED here — all four already
-- exist and already carry their grants — but the grants are restated so this file is self-evident
-- and so a relation whose grant was lost to an earlier change is repaired by running it.
-- service_role only: the browser never reads these tables, and an employee's coordinates are the
-- last thing that should become anon-readable.
GRANT SELECT, INSERT, UPDATE, DELETE ON storeops.stores       TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON storeops.tenants      TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON storeops.timelog      TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON storeops.store_visits TO service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1112 complete — store map pin (storeops.stores), punch geofence policy + GPS '
       'retention (storeops.tenants), the clock-out''s own fix and a stored verdict for each end of '
       'a punch (storeops.timelog), and the same verdict on the DM store visit (storeops.store_visits)'
       AS status;
