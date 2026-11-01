-- 1113_geo_retention_stamp.sql — make the GPS retention promise deletable, for every relation
--
-- WHY THIS EXISTS. Migration 1112 gave each tenant a setting called `punch_geo_retention_days`
-- (house default 180) and NOTHING DELETED ANYTHING. The security pass on that feature found it. The
-- honest finding was that it is the SECOND instance of one class, not a new bug: event check-in GPS
-- has carried `core.marketing_config.checkin_geo_retention_days` since migration 986 with exactly
-- the same hole — declared, not hidden, on the control box as `marketing_checkin_gps_retention` =
-- `unmonitored` ("no purge job runs", index §23.7).
--
-- A retention window that deletes nothing is worse than no window: it reads as a promise. So this
-- fixes the MECHANISM for every relation that stores where a person was (CLAUDE.md 2026-09-20 — name
-- the class, find the siblings, one fact one home, lock it):
--
--   1. storeops.timelog      — a shift's two ends.             NEW stamp column.
--   2. storeops.store_visits — a manager's store visit.         NEW stamp column.
--   3. marketing.marketing_event_checkin — a rep at an event.  ALREADY stamped (mig 986's
--      `purge_after_date`); this file adds only its index, so the sweep reads it the same way.
--
-- WHAT A PURGE REMOVES, AND WHAT IT KEEPS. It clears the COORDINATES (latitude, longitude,
-- accuracy) and KEEPS the verdict and the distance. That is the design, not an accident: management
-- reviews "was this punch at the store, and how far away", and both answers survive. So honouring
-- the promise never changes a flag, a report or an export — only the ability to re-derive one from a
-- raw position. `core/geo_retention.cleared_columns` is the one home for that list and the unit
-- tests assert the verdict columns are absent from it.
--
-- THE STAMP LIVES ON THE ROW. `geo_purge_after_date` is written at punch time from the tenant's own
-- declared window, so the commitment is visible in the data rather than buried in a job somebody has
-- to go and find. The column spelling differs from marketing's existing `purge_after_date` ON
-- PURPOSE: that column is already in production data and renaming a live column to tidy the name
-- would be a migration that buys nothing. Both spellings are registered in ONE place
-- (`core/geo_retention.LOCATION_RELATIONS`) and every caller dereferences it.
--
-- A ROW WITH NO STAMP IS NEVER PURGED ON A GUESS. Punches and visits written before this migration
-- carry no commitment, so the sweep REPORTS them (`no_retention_stamp`) and leaves them alone.
-- Inferring their age from a neighbouring timestamp would invent the very commitment this design
-- exists to make explicit. Clearing that backlog is a deliberate owner decision, not a side effect.
--
-- Additive + idempotent (ADD COLUMN IF NOT EXISTS; indexes IF NOT EXISTS). No RLS change. NOT
-- money-touching: no pay, payable, commission, GP or tax figure reads any column here.
--
-- WHAT BREAKS UNTIL THIS RUNS: nothing. The stamp writer probes for the column exactly as 1112's
-- verdict writer does and omits it when absent, and the sweep endpoint answers
-- `{"ok": false, "error": "... is migration 1113 applied?"}` instead of 500ing a scheduler.
--
-- REVERT:
--   ALTER TABLE storeops.timelog      DROP COLUMN IF EXISTS geo_purge_after_date;
--   ALTER TABLE storeops.store_visits DROP COLUMN IF EXISTS geo_purge_after_date;
--   DROP INDEX IF EXISTS storeops.timelog_geo_purge_due_idx;
--   DROP INDEX IF EXISTS storeops.store_visits_geo_purge_due_idx;
--   DROP INDEX IF EXISTS marketing.marketing_event_checkin_purge_due_idx;
--   -- (the marketing stamp column itself belongs to migration 986 and is NOT dropped here)

-- ═══ 1. storeops.timelog — the stamp for a shift's two ends ═══════════════════════════════════
-- ONE stamp for the whole punch, not one per end: a shift's two fixes are recorded on the same row
-- at the same tenant's window, so two dates could only ever disagree by being wrong.
ALTER TABLE storeops.timelog
  ADD COLUMN IF NOT EXISTS geo_purge_after_date DATE;

COMMENT ON COLUMN storeops.timelog.geo_purge_after_date IS
  'Date on or after which this punch''s COORDINATES may be cleared (the verdict and distance are '
  'kept). Stamped at punch time from storeops.tenants.punch_geo_retention_days. NULL = written '
  'before migration 1113; reported as no_retention_stamp and never purged on a guess. '
  'One home: backend/app/modules/core/geo_retention.py (index §CORE-31).';

-- ═══ 2. storeops.store_visits — the same stamp for the DM check-in ═══════════════════════════
ALTER TABLE storeops.store_visits
  ADD COLUMN IF NOT EXISTS geo_purge_after_date DATE;

COMMENT ON COLUMN storeops.store_visits.geo_purge_after_date IS
  'Date on or after which this visit''s COORDINATES may be cleared (the verdict and distance are '
  'kept). Same window and same one home as storeops.timelog.geo_purge_after_date (index §CORE-31).';

-- ═══ 3. The sweep's read path, on all three relations ════════════════════════════════════════
-- PARTIAL indexes on "has a stamp": the sweep only ever asks for rows carrying a commitment, and an
-- unstamped backlog (which may be large and is deliberately never purged) stays out of the index.
CREATE INDEX IF NOT EXISTS timelog_geo_purge_due_idx
  ON storeops.timelog (geo_purge_after_date)
  WHERE geo_purge_after_date IS NOT NULL;

CREATE INDEX IF NOT EXISTS store_visits_geo_purge_due_idx
  ON storeops.store_visits (geo_purge_after_date)
  WHERE geo_purge_after_date IS NOT NULL;

CREATE INDEX IF NOT EXISTS marketing_event_checkin_purge_due_idx
  ON marketing.marketing_event_checkin (purge_after_date)
  WHERE purge_after_date IS NOT NULL;

-- ═══ GRANTS ═══════════════════════════════════════════════════════════════════════════════════
-- "A migration grants what it creates" (CLAUDE.md). No relation is CREATED here, but the grants are
-- restated so this file is self-evident and so a relation whose grant was lost to an earlier change
-- is repaired by running it. service_role ONLY: the sweep runs server-side, the browser never reads
-- these tables, and an employee's coordinates are the last thing that should become anon-readable.
GRANT SELECT, INSERT, UPDATE, DELETE ON storeops.timelog                   TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON storeops.store_visits              TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON marketing.marketing_event_checkin  TO service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1113 complete — a retention stamp on storeops.timelog and storeops.store_visits, '
       'the sweep''s partial index on all three location relations, and the GPS retention promise '
       'now deletable for every relation that stores where a person was' AS status;
