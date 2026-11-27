-- 1132_per_market_work_week_and_open_days.sql
-- ────────────────────────────────────────────────────────────
-- A WORK WEEK BELONGS TO A MARKET, AND WHICH DAYS A STORE OPENS IS DECLARABLE (owner 2026-11-26)
--
-- Owner (Sanjot), verbatim: *"Work week for all companies starts on a Monday and the nds kn a Sunday ,
-- luxelink Chicago stores start on Thursday and end on Wednesday , luxelink ny stores are Monday to
-- Sunday"* and *"All store open 7 days a week"*.
--
-- THE DEFECT THAT SENTENCE EXPOSES, measured on production the same day.
-- `storeops.tenants.work_week_start_dow` (mig 085) holds ONE week start per tenant. Luxelink is ONE
-- tenant with TWO markets, and those markets are its two companies (Chicago = 13 stores / 10 open,
-- NY = 11 stores / 10 open). Its column reads 3 (Thursday), which is right for Chicago and wrong for
-- every NY store — so today 10 open NY stores are bucketed Thu–Wed while the owner says they run
-- Mon–Sun. That is not a display detail: the same number anchors the schedule grid, the weekly hours
-- budget, the pay period the approvals board opens on, and the Mon–Sun grid the rota is emailed as.
--   Cellfonz R Us 0 (Mon) · Luxelink 3 (Thu) · NY LOGISTICS 0 (Mon) · Vzone 0 (Mon)
--
-- THE CLASS, not the instance. Two classes, and both are already named in CLAUDE.md:
--
--  1. A FACT WITH THE WRONG GRAIN. "Which seven days is a week" was modelled per TENANT because the
--     first tenant to need it had one answer. A tenant whose markets differ cannot say so at all —
--     there is no row to put the second answer in. Widening the grain is the fix; special-casing
--     Luxelink in code would be the per-tenant CODE that RULE TWO forbids.
--
--  2. THE SAME FACT READ FROM FIVE PLACES. `work_week_start_dow` is selected off `tenants` in
--     `storeops/payroll_approval._week_start_dow`, `storeops/router.py` TWICE (the tenant-settings
--     read and its own private `_work_week_bounds` copy), `storeops/payroll_run_api.py` and
--     `storeops/schedule_week_api.py`. Five copies of one question is the duplicate defect the index
--     rules forbid, and widening the grain in four of them would leave the fifth answering the old
--     way. So this migration ships with ONE home — `storeops/work_week.py` — that every caller
--     dereferences, and `backend/harness_work_week.py` fails the build if any module outside it
--     reads the column again.
--
-- WHAT IS DECLARED HERE, AND AT WHICH GRAIN
--
--   • `storeops.market_work_week` — the week start for ONE market of ONE tenant. Absent means the
--     market has not been given one, and the tenant's own `work_week_start_dow` answers. So this
--     table being EMPTY is exactly today's behaviour, and a market needs a row here only to DIFFER
--     from its tenant. Nothing is seeded, for the reason mig 1131 states: four identical rows would
--     be four places for one fact to drift.
--   • `storeops.stores.open_dows` / `storeops.tenants.open_dows` — which weekdays a store is
--     EXPECTED to have somebody on, per store with the tenant as the default. NULL means UNDECLARED,
--     which is not the same as "open no days": the scheduling flow (§STOREOPS-15) deliberately NAMES
--     the days with nobody on and refuses to call them gaps while nothing is declared, because
--     assuming a seven-day week would be a house assumption standing in for a tenant's own words.
--     With `{0,1,2,3,4,5,6}` declared, that same step starts reporting a gap, with no code change.
--
-- MARKET IS A CANONICAL ANSWER, NOT A STRING THIS TABLE INVENTS. The market a store belongs to is
-- resolved through `app/core/scope.store_market_resolver` / `market_by_code` — the canonical union
-- index (§14x) — and a row here is keyed on the market name that resolver returns. The lock pins the
-- new read in `harness_market_resolution_guard.py` so it cannot become a sixth vocabulary.
--
-- A HOUSE DEFAULT IS STILL CORRECT FOR THE WEEK START, and wrong for the open days. The 2026-10-30
-- directive bans a default that is really one tenant's VOCABULARY; it names a POLICY — a rate, a
-- window, a switch — as where a house default stays right. Monday is the week start's documented
-- house default (mig 085) and stays. Open days are a tenant's own statement about its own stores, so
-- there is NO default behind them: undeclared stays undeclared and the surface says so.
--
-- NOT MONEY, with one thing stated rather than hidden. No rate, payout, commission or tax column is
-- touched, and no amount moves. But the week start anchors which DATES a pay period covers, so
-- declaring a market's own week start changes which days appear on an approvals board and on a
-- payroll run's default period. It does not change any rate or any hour already recorded. The
-- backend reads this through one home that reports whether the answer was DECLARED and at what level,
-- so a surface can always say where its week came from.
--
-- Additive and idempotent. Nothing is seeded.
--
-- REVERT:
--   DROP TABLE IF EXISTS storeops.market_work_week;
--   ALTER TABLE storeops.stores  DROP COLUMN IF EXISTS open_dows;
--   ALTER TABLE storeops.tenants DROP COLUMN IF EXISTS open_dows;
-- ────────────────────────────────────────────────────────────

-- ── the week start, per market ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.market_work_week (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL,
  -- The canonical market name, as `app/core/scope` resolves it. Compared case-insensitively by the
  -- one home, because a market typed 'chicago' and a market resolved 'Chicago' are the same market
  -- and a phantom second market is how $289 once landed in a store that did not exist (§13e).
  market              TEXT NOT NULL,
  -- 0=Monday .. 6=Sunday, the SAME convention as `storeops.tenants.work_week_start_dow`. Stated
  -- here because this product has already had one module read that column as if 0 were Sunday and
  -- open every approvals board a day early (see payroll_approval ①).
  work_week_start_dow INT  NOT NULL,
  updated_by          TEXT,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- ONE week start per market. A second row would be two answers to one question.
  UNIQUE (org_id, market)
);

CREATE INDEX IF NOT EXISTS market_work_week_org
  ON storeops.market_work_week (org_id);

-- A weekday or a database error — never a silently-ignored typo. 7 would mean an eighth day.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'market_work_week_dow_chk'
      AND conrelid = 'storeops.market_work_week'::regclass
  ) THEN
    ALTER TABLE storeops.market_work_week
      ADD CONSTRAINT market_work_week_dow_chk
      CHECK (work_week_start_dow BETWEEN 0 AND 6);
  END IF;
END $$;

-- ── which days a store opens ────────────────────────────────────────────────────────────────────
-- Per store, with the tenant as the default. NULL = UNDECLARED on both, and an EMPTY array is a
-- real declaration meaning "expected open on no day" — the two are deliberately distinguishable,
-- because "we have not said" and "never" must not read the same.
ALTER TABLE storeops.stores  ADD COLUMN IF NOT EXISTS open_dows INT[];
ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS open_dows INT[];

COMMENT ON COLUMN storeops.stores.open_dows IS
  'Weekdays this store is expected to have somebody on, 0=Mon..6=Sun. NULL = not declared for this '
  'store (the tenant''s storeops.tenants.open_dows answers); an empty array is a declaration, not an '
  'absence. Read ONLY through app/modules/storeops/work_week.py.';
COMMENT ON COLUMN storeops.tenants.open_dows IS
  'The tenant''s default for storeops.stores.open_dows. NULL = not declared, and the scheduling flow '
  'then NAMES uncovered days without calling them gaps (index §STOREOPS-15).';
COMMENT ON TABLE storeops.market_work_week IS
  'The work-week start for one market of one tenant, 0=Mon..6=Sun. Absent = the tenant''s own '
  'storeops.tenants.work_week_start_dow answers. Read ONLY through '
  'app/modules/storeops/work_week.py (index §STOREOPS-17).';

-- Every weekday in a declared set is a weekday. Applied to both columns.
--
-- WRITTEN WITH `<@`, THE ARRAY CONTAINMENT OPERATOR, AND NOT WITH `unnest`. The first version of
-- this file expressed the same rule as `(SELECT bool_and(d BETWEEN 0 AND 6) FROM unnest(...))`,
-- which Postgres refuses outright: `0A000 cannot use subquery in check constraint`. The owner hit
-- that on 2026-11-27 pasting this file, and because the editor runs the file as ONE transaction the
-- whole thing rolled back — nothing was half-applied. `open_dows <@ ARRAY[0,1,2,3,4,5,6]` is an
-- immutable scalar expression, so it is legal here, and it answers the three cases the same way:
-- every day declared TRUE, a set holding an 8th day FALSE, an EMPTY declared set TRUE (an empty
-- array is contained in anything), and NULL left to the `IS NULL` guard in front of it.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'stores_open_dows_chk' AND conrelid = 'storeops.stores'::regclass
  ) THEN
    ALTER TABLE storeops.stores
      ADD CONSTRAINT stores_open_dows_chk
      CHECK (open_dows IS NULL OR open_dows <@ ARRAY[0,1,2,3,4,5,6]);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'tenants_open_dows_chk' AND conrelid = 'storeops.tenants'::regclass
  ) THEN
    ALTER TABLE storeops.tenants
      ADD CONSTRAINT tenants_open_dows_chk
      CHECK (open_dows IS NULL OR open_dows <@ ARRAY[0,1,2,3,4,5,6]);
  END IF;
END $$;

-- ── RLS, matching every sibling in this schema ──────────────────────────────────────────────────
ALTER TABLE storeops.market_work_week ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'storeops' AND tablename = 'market_work_week' AND policyname = 'open_all'
  ) THEN
    CREATE POLICY open_all ON storeops.market_work_week FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

-- A MIGRATION GRANTS WHAT IT CREATES (house convention; from 2026-10-30 Supabase no longer grants
-- new tables automatically, and the backend reaches every relation through PostgREST).
-- `service_role` only: the browser never reads this table, so an `anon` grant would widen exposure
-- rather than fix anything.
GRANT SELECT, INSERT, UPDATE, DELETE ON storeops.market_work_week TO service_role;
