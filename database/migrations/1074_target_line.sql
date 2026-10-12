-- 1074_target_line.sql
-- ────────────────────────────────────────────────────────────
-- A TARGET FOR A CATEGORY THE BUSINESS DECLARED (owner directive 2026-10-10, index §COMMCALC-01)
--
-- Owner: "the target needs to be set for the gross revenue per day / week / month or backward
-- calculate what is needed to reach a certain level ... but i dont wnat them to be hard coded it
-- should be a design effect"
--
-- THE DEFECT THIS CLOSES. commcalc.targets (mig 006) carries one PHYSICAL COLUMN per category —
-- activations_monthly, upgrades_monthly, accessories_monthly, plus byod_pct. That is why a tenant
-- selling drop-offs and printing cannot set a target: there is no column to put it in, and adding
-- one per category would be the per-tenant schema change RULE TWO forbids.
--
-- TWO PHYSICAL STORES, ONE READER — the same shape as mig 1072's closing_metric_line and the
-- envelope_report.is_modern_row precedent (index §29.7). The four built-in categories KEEP their
-- columns on commcalc.targets and are read byte-for-byte as before, so no existing tenant's target
-- moves and nothing needs backfilling. A DECLARED category's target is a row here. Nobody reads
-- either store directly: commcalc/targets_engine.derive_monthly_by_cat is the one fold, and
-- harness_target_axis_lock.py fails the build on a caller that reaches past it.
--
-- A row naming a built-in category is REFUSED at save, because two homes for one number is the
-- divergence this design exists to prevent. The CHECK below cannot express that (it would have to
-- know the engine's vocabulary), so it is enforced by the writer and asserted by the proof harness;
-- the fold also ignores such a row defensively if one is ever inserted by hand.
--
-- GRAIN. The engine's arithmetic is monthly, spread over the scope's scheduled hours; a week or day
-- figure is a WINDOW over that same spread (targets_engine.grain_windows), never a second
-- calculation. So `grain` exists for a tenant that wants to DECLARE a target at week or day grain
-- directly, and 'month' is the default because that is what the engine reverse-calculates from.
--
-- Additive and idempotent. No seed: a tenant with no rows here behaves exactly as it did before.
--
-- REVERT:
--   DROP TABLE IF EXISTS commcalc.target_line;
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commcalc.target_line (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  store_code   TEXT NOT NULL,
  period       TEXT NOT NULL,
  metric_key   TEXT NOT NULL,
  grain        TEXT NOT NULL DEFAULT 'month',
  target_value NUMERIC NOT NULL DEFAULT 0,
  notes        TEXT,
  updated_by   UUID,
  updated_at   TIMESTAMPTZ DEFAULT NOW(),
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  -- One target per category per grain per store-period. A second row for the same four would be
  -- two answers to one question.
  UNIQUE (org_id, store_code, period, metric_key, grain)
);

CREATE INDEX IF NOT EXISTS target_line_period
  ON commcalc.target_line (org_id, period);
CREATE INDEX IF NOT EXISTS target_line_store_period
  ON commcalc.target_line (org_id, store_code, period);

-- The grain vocabulary, mirroring targets_engine.GRAINS. Added separately and guarded so re-running
-- the migration does not fail on an existing constraint.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'target_line_grain_chk'
      AND conrelid = 'commcalc.target_line'::regclass
  ) THEN
    ALTER TABLE commcalc.target_line
      ADD CONSTRAINT target_line_grain_chk
      CHECK (grain IN ('day', 'week', 'month'));
  END IF;
END $$;

-- A target is a goal, never a negative. A tenant that wants "no target" leaves the row out or sets
-- 0; a negative would make `need` and `pace` meaningless.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'target_line_value_chk'
      AND conrelid = 'commcalc.target_line'::regclass
  ) THEN
    ALTER TABLE commcalc.target_line
      ADD CONSTRAINT target_line_value_chk
      CHECK (target_value >= 0);
  END IF;
END $$;

-- RLS, mirroring mig 006's posture on commcalc.targets: org scoping is enforced in the application
-- layer (every sensitive query is org-scoped and CI checks it), so the policy is permissive here
-- rather than a second, divergent gate.
ALTER TABLE commcalc.target_line ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'commcalc' AND tablename = 'target_line' AND policyname = 'open_all'
  ) THEN
    CREATE POLICY open_all ON commcalc.target_line FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

COMMENT ON TABLE commcalc.target_line IS
  'Monthly/weekly/daily target for a DECLARED closing category (mig 1072 catalog). The four '
  'built-in categories keep their columns on commcalc.targets; targets_engine.derive_monthly_by_cat '
  'is the one reader that folds both. Index §COMMCALC-01.';
COMMENT ON COLUMN commcalc.target_line.metric_key IS
  'A key from closing/metric_catalog.build(). A built-in category name is refused at save.';

SELECT '1074_target_line complete' AS status;
