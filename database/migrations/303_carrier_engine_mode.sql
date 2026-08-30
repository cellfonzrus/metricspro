-- 303_carrier_engine_mode.sql
-- mod-commission · band 200–299 spill → 303 (296–302 taken). Additive + idempotent + safe to re-run.
--
-- WHAT IT IS FOR (audit fix #1, owner 2026-08-30): today the payout ENGINE for a tenant is chosen by
-- substring-matching the literal string "boost" against the carrier's code/name (_resolve_carrier_mode,
-- router.py). That is the one place carrier LANGUAGE is branched in code — rename a Boost carrier, or
-- name any carrier something containing "boost", and its reps are silently routed to the wrong pay engine.
-- This column makes the engine choice EXPLICIT CONFIG, and is exactly the switch the new-tenant onboarding
-- wizard's "which carrier?" answer will set.
--
-- ✅ DEFAULT-SAFE. engine_mode is NULL for every existing carrier row, and the selector treats NULL as
-- "fall back to the historical name-substring heuristic" — so behavior is byte-identical until someone
-- sets an explicit value. Nothing is flipped by running this.
--
--   legacy_boost = the legacy verified Boost KPI-tier engine (calculator.py)
--   plan         = pay ONLY from configurable Commission Plans / Payout Schedules (commission_engine)

ALTER TABLE commcalc.carrier
  ADD COLUMN IF NOT EXISTS engine_mode text;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'carrier_engine_mode_chk'
  ) THEN
    ALTER TABLE commcalc.carrier
      ADD CONSTRAINT carrier_engine_mode_chk
      CHECK (engine_mode IS NULL OR engine_mode IN ('legacy_boost', 'plan'));
  END IF;
END $$;

COMMENT ON COLUMN commcalc.carrier.engine_mode IS
  'Explicit payout engine for this carrier: legacy_boost | plan. NULL => selector falls back to the '
  'legacy name-substring heuristic (byte-identical to pre-303 behavior). Set by the tenant/onboarding wizard.';

NOTIFY pgrst, 'reload schema';
