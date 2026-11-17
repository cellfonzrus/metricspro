-- 1125_period_goal.sql
-- ────────────────────────────────────────────────────────────
-- WHERE MUST THIS STORE'S YEAR LAND (owner ask 2026-11-17, index §COMMCALC-79)
--
-- Owner, verbatim: "The goal is to make the store a platinum store which is 1 Million in annual
-- sale including the commission earned - make this user filled entry but fill it for me where the
-- targets have to be declared".
--
-- WHY THIS IS NOT A ROW IN commcalc.target_line (the CLAUDE.md duplicate check, stated in the
-- schema so nobody re-derives it):
--
--   1. `target_line.grain` ('day' | 'week' | 'month') names a WINDOW over the monthly spread
--      targets_engine already computes — a smaller slice of a month that is already known. A year
--      is the opposite direction: the figure that GOVERNS the months, not a slice of one. Adding
--      'year' to that CHECK would hand `derive_monthly_by_cat` a row it must then learn to skip.
--   2. A target_line row names exactly ONE `metric_key`, and its unique key is built on that. A
--      goal is measured over a SET of categories ("sales including the commission earned"), which
--      that shape cannot express without one row per category and no way to say they are one goal.
--
-- THE ARITHMETIC IS NOT DUPLICATED. commcalc/annual_goal.progress dereferences
-- targets_engine.rate_to_close — the same home the per-day backward calculation dereferences —
-- with the MONTH as its unit. harness_annual_goal.py §E fails the build if it grows its own copy.
--
-- WHAT COUNTS IS DECLARED, NEVER DEFAULTED (owner directive 2026-10-30). `metric_keys` holds the
-- tenant's own declared categories (mig 1072). An empty array means NOTHING IS MEASURED and the
-- surface says so in words — it does NOT mean "every category", and it does not mean zero. A store
-- that traded all year reading as having sold nothing is the exact defect §COMMCALC-49.10 shipped.
-- "Commission earned" is therefore expressed by ticking the category that holds it, so no code here
-- learns what that phrase means for a shipping store versus a wireless one.
--
-- NO SEED. A tenant with no row here behaves exactly as it did before: no goal, nothing shown. The
-- owner's own $1,000,000 row is a separate one-Run file he applies and can read before he does, so
-- this migration stays tenant-neutral (RULE TWO).
--
-- Additive and idempotent.
--
-- REVERT:
--   DROP TABLE IF EXISTS commcalc.period_goal;
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commcalc.period_goal (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  -- '' is THE WHOLE COMPANY, the same spelling the targets surfaces already use for an org-wide
  -- figure. NOT NULL with a '' default so the unique key below cannot be defeated by a NULL.
  store_code   TEXT NOT NULL DEFAULT '',
  -- The horizon's FIRST period: 'YYYY' for a year, 'YYYY-MM' for a quarter's opening month.
  -- commcalc/annual_goal.months_elapsed is the only reader of this spelling.
  period       TEXT NOT NULL,
  grain        TEXT NOT NULL DEFAULT 'year',
  goal_value   NUMERIC NOT NULL DEFAULT 0,
  -- The declared categories the goal is measured over (keys from closing/metric_catalog.build()).
  -- EMPTY = nothing measured, said out loud. Never read as "all".
  metric_keys  TEXT[] NOT NULL DEFAULT '{}',
  -- The tenant's own name for the tier ("Platinum"). Display only; no code branches on it.
  label        TEXT,
  notes        TEXT,
  updated_by   UUID,
  updated_at   TIMESTAMPTZ DEFAULT NOW(),
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  -- One goal per scope per horizon. A second row for the same four would be two answers to
  -- "where must this year land".
  UNIQUE (org_id, store_code, period, grain)
);

CREATE INDEX IF NOT EXISTS period_goal_org_period
  ON commcalc.period_goal (org_id, period);

-- The horizon vocabulary, mirroring commcalc/annual_goal.GRAINS. Added separately and guarded so
-- re-running the migration does not fail on an existing constraint.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'period_goal_grain_chk'
      AND conrelid = 'commcalc.period_goal'::regclass
  ) THEN
    ALTER TABLE commcalc.period_goal
      ADD CONSTRAINT period_goal_grain_chk
      CHECK (grain IN ('year', 'quarter'));
  END IF;
END $$;

-- A goal is something to reach, never a negative. Mirrors mig 1074's posture on target_value: a
-- tenant wanting "no goal" leaves the row out, and a negative would make `need` and the run-rate
-- verdict meaningless.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'period_goal_value_chk'
      AND conrelid = 'commcalc.period_goal'::regclass
  ) THEN
    ALTER TABLE commcalc.period_goal
      ADD CONSTRAINT period_goal_value_chk
      CHECK (goal_value >= 0);
  END IF;
END $$;

-- RLS, mirroring mig 1074's posture on commcalc.target_line: org scoping is enforced in the
-- application layer (every sensitive query is org-scoped and CI checks it), so the policy is
-- permissive here rather than a second, divergent gate.
ALTER TABLE commcalc.period_goal ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'commcalc' AND tablename = 'period_goal' AND policyname = 'open_all'
  ) THEN
    CREATE POLICY open_all ON commcalc.period_goal FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

-- A MIGRATION GRANTS WHAT IT CREATES (house convention; from 2026-10-30 Supabase no longer grants
-- new tables automatically, and the backend reaches every relation through PostgREST).
-- service_role only: the browser never reads this table, so an anon grant would widen exposure
-- rather than fix anything. Enforced by harness_data_api_grant_guard.py.
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.period_goal TO service_role;

COMMENT ON TABLE commcalc.period_goal IS
  'Where a scope''s YEAR (or quarter) must land, measured over a SET of declared categories '
  '(mig 1072 catalog). Not a target_line row: that names one category and its grain is a window '
  'over the monthly spread, while a goal governs the months. Index §COMMCALC-79.';
COMMENT ON COLUMN commcalc.period_goal.metric_keys IS
  'Declared category keys the goal is measured over. EMPTY = nothing measured, and the surface '
  'says so — never read as "every category" and never as zero.';
COMMENT ON COLUMN commcalc.period_goal.period IS
  '''YYYY'' for a year, ''YYYY-MM'' for a quarter''s opening month. Read only by '
  'commcalc/annual_goal.months_elapsed.';

SELECT '1125_period_goal complete' AS status;
