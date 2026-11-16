-- 1122_weekly_performance_review.sql — THE WEEKLY PERFORMANCE REVIEW: the tenant's review policy,
-- the stored pack, and the Management Overview tile that opens it. Band 1000+ (platform).
-- Additive + idempotent (safe to re-run). NOT money-touching: nothing here reads or writes a rate,
-- plan, tier, payout or any calculation input — the report only READS surfaces that already exist.
--
-- OWNER (2026-11-16), verbatim: "Add the performance deck to the management tile but allow access
-- only for the admins , make this report every week at the end of the week so the same can be
-- reviewed every monday with the dm, it should have invenotry aging for rteh repsective carrier
-- also as one of the items to review., make a separate report for each market and one consolidated
-- for the entire company , is there any way we can make this on demand" — and, the same evening,
-- "let us make it on demand".
--
-- WHAT IT IS. The in-app form of the LuxeLink performance deck built for the 17 Nov 2026 Zoom: one
-- review pack per declared market, one consolidated for the company, seven items, every figure read
-- from the surface that already owns it (the GP report, the pay engine's rows, the accessory target
-- plan, the shared sales cells, the Daily Action Plan's verdict, the aging report's per-carrier
-- windows and the one payroll authority). The fold is pure — backend/app/modules/commcalc/
-- weekly_review.py — and this migration adds only the two rows it needs: a POLICY row and a
-- SNAPSHOT row.
--
-- DUPLICATE CHECK (build gate — what was searched in docs/SYSTEM_DATA_FLOW_INDEX.md and why none of
-- it is this):
--   · commcalc.zero_sales_config (1016) / epay alert config — per-report cadence + alert switches.
--     Same SHAPE, a different report; a shared table would make one report's closing weekday the
--     other's alert threshold.
--   · commcalc.month_focus (1065) — what management DECLARED for a month, written by a human. This
--     is GENERATED and read-only; nothing on it is a declaration.
--   · commcalc.account_statements / the GP snapshot — stored P&L figures. The pack QUOTES those; it
--     never stores a second copy of a money figure anybody reconciles against. The snapshot here is
--     the whole pack as it was shown, for the Monday meeting to open, and no surface reconciles to
--     it.
--   · commcalc.targets/{period}/report-cards (§59.9) — per-MANAGER check-off of assigned items.
--     A different grain (a manager, not a market) and a different question (was it met, not what
--     happened this week); the review quotes the same one homes rather than re-deriving them.
--   · commcalc.ui_label_override scope 'tiles' key 'management-overview' (948/1002/1016) — THE
--     Management Overview layout. REUSED below, with the mig-1002 append precedent verbatim; no
--     second dashboard registry is created.
--
-- ACCESS. Administrators only, enforced in the API (`weekly_review.review_allowed`, mirrored in
-- frontend rbac.ts). The tile below is a link, and a link is not a permission: the refusal lives on
-- the server, so a typed URL is refused the same way.
--
-- DEGRADES: until this runs, the report still works on demand — `_weekly_review_config` falls back
-- to the house defaults and the snapshot write is best-effort — the standing weekly run simply has
-- nowhere to record what it made, and the tile is absent.
--
-- REVERT:
--   -- turn the standing run off for one tenant (no DDL needed):
--   --   UPDATE commcalc.weekly_review_config SET weekly_run_enabled = false WHERE org_id = '<org>';
--   -- undo this migration entirely:
--   --   DROP TABLE IF EXISTS commcalc.weekly_review_run;
--   --   DROP TABLE IF EXISTS commcalc.weekly_review_config;
--   --   UPDATE commcalc.ui_label_override
--   --      SET label = jsonb_set(label::jsonb, '{tiles}',
--   --            (SELECT coalesce(jsonb_agg(t), '[]'::jsonb)
--   --               FROM jsonb_array_elements(label::jsonb -> 'tiles') t
--   --              WHERE t ->> 'title' <> 'Weekly Performance Review'))::text
--   --    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--   --      AND scope = 'tiles' AND key = 'management-overview';
--   --   NOTIFY pgrst, 'reload schema';

BEGIN;

-- ── 1. THE POLICY ROW — one per tenant. POLICY ONLY (RULE TWO): a weekday and a switch, never a
--      market, a carrier or a store. A tenant with no row answers with the house defaults.
CREATE TABLE IF NOT EXISTS commcalc.weekly_review_config (
  org_id              UUID PRIMARY KEY,
  -- 0 = Monday … 6 = Sunday. The week the pack reviews is the last one that CLOSED on this day, so
  -- the Monday meeting always reads a whole week rather than the part-week it sits in.
  week_ends_on        SMALLINT,
  -- The standing end-of-week run. OFF by default: a tenant turns it on when it wants the pack
  -- waiting on Monday. On demand works regardless, and is the main way the report is produced.
  weekly_run_enabled  BOOLEAN,
  -- Extra addresses for the standing run, when one is wired to send. Empty = nobody extra.
  recipients          JSONB,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $p$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'weekly_review_config_week_ends_on_chk'
                    AND conrelid = 'commcalc.weekly_review_config'::regclass) THEN
    ALTER TABLE commcalc.weekly_review_config
      ADD CONSTRAINT weekly_review_config_week_ends_on_chk
      CHECK (week_ends_on IS NULL OR week_ends_on BETWEEN 0 AND 6);
  END IF;
END $p$;

COMMENT ON TABLE commcalc.weekly_review_config IS
  'THE WEEKLY PERFORMANCE REVIEW''S POLICY, per tenant (owner 2026-11-16). week_ends_on = the weekday a review week closes on (0 Monday … 6 Sunday; house default Sunday); weekly_run_enabled = whether POST /commcalc/weekly-review/run-due generates the pack on that day. NULL on any column = the house default in weekly_review.HOUSE_CONFIG. Holds no tenant vocabulary: markets come from core.scope.market_index and carriers from each carrier''s own declared aging window.';

-- ── 2. THE SNAPSHOT — the pack as it was generated, one row per (tenant, week). A convenience, not
--      a source: nothing reconciles to it, and the report recomputes on demand from the live reads.
CREATE TABLE IF NOT EXISTS commcalc.weekly_review_run (
  org_id        UUID        NOT NULL,
  week_ending   DATE        NOT NULL,
  period        TEXT,
  generated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  generated_by  TEXT,
  pack          JSONB       NOT NULL,
  PRIMARY KEY (org_id, week_ending)
);

CREATE INDEX IF NOT EXISTS weekly_review_run_org_generated_idx
  ON commcalc.weekly_review_run (org_id, generated_at DESC);

COMMENT ON TABLE commcalc.weekly_review_run IS
  'THE GENERATED WEEKLY REVIEW PACK, one row per (tenant, week ending) — what the Monday meeting opens. Written by POST /commcalc/weekly-review/run-now (on demand) and /run-due (the standing end-of-week run). READ-ONLY EVIDENCE: no money, pay or target is derived from it and no surface reconciles against it; the report recomputes from the live one homes every time it is opened.';

-- ── 3. GRANTS — the backend reaches every relation through PostgREST, and since 2026-10-30 Supabase
--      no longer grants new public-facing tables automatically. service_role ONLY: the browser never
--      reads these tables (harness_data_api_grant_guard.py, index §CORE-13).
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.weekly_review_config TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.weekly_review_run    TO service_role;

-- ── 4. THE MANAGEMENT OVERVIEW TILE — the mig-1002 append precedent verbatim: UPDATE, never INSERT,
--      because mig 948 already seeded key='management-overview' for the house org, and the guard at
--      the end of the WHERE makes a re-run a no-op instead of a second tile.
UPDATE commcalc.ui_label_override
   SET label = jsonb_set(
         label::jsonb,
         '{tiles}',
         (label::jsonb -> 'tiles') || '[
           {"title":"Weekly Performance Review","icon":"📋",
            "desc":"The Monday review pack: profit by store, sales and lines growth, the accessory gap against target, commission by rep, reps behind, inventory aging per carrier and payroll - one report per market and one consolidated. Administrators only.",
            "items":[{"href":"/commcalc/weekly-review","label":"Weekly Performance Review"}]}
         ]'::jsonb
       )::text,
       updated_at = now()
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND scope   = 'tiles'
   AND key     = 'management-overview'
   AND NOT (label::jsonb -> 'tiles') @> '[{"title":"Weekly Performance Review"}]'::jsonb;

-- Post-flight: the tile must be present exactly once and the layout must still parse as the shape
-- the hub reads. A half-applied dashboard is a blank screen for every manager, so this rolls back
-- rather than leaving one. (A database where mig 948 never ran has no house layout row at all —
-- that is `n = 0` and is tolerated: the report is reachable from its URL and from search, and the
-- tile appears when the layout exists.)
DO $$
DECLARE n INT; v INT; layouts INT;
BEGIN
  SELECT count(*) INTO layouts
    FROM commcalc.ui_label_override
   WHERE org_id = '00000000-0000-0000-0000-000000000001'
     AND scope = 'tiles' AND key = 'management-overview';
  IF layouts = 0 THEN
    RAISE NOTICE 'no house management-overview layout on this database; the tile was not added';
    RETURN;
  END IF;
  SELECT count(*), max((label::jsonb ->> 'version')::int)
    INTO n, v
    FROM commcalc.ui_label_override,
         LATERAL jsonb_array_elements(label::jsonb -> 'tiles') t
   WHERE org_id = '00000000-0000-0000-0000-000000000001'
     AND scope = 'tiles' AND key = 'management-overview'
     AND t ->> 'title' = 'Weekly Performance Review';
  IF n <> 1 THEN
    RAISE EXCEPTION 'expected exactly 1 "Weekly Performance Review" tile on the house management-overview layout, found %', n;
  END IF;
  IF v IS NULL THEN
    RAISE EXCEPTION 'house management-overview layout lost its version key';
  END IF;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
