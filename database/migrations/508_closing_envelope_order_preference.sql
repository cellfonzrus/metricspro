-- 508_closing_envelope_order_preference.sql — mod-retail-ops band 500-599.
--
-- Q15 (OWNER DIRECTIVE 2026-08-04): the fewest-envelopes SELECTION OBJECTIVE (envelope.select_envelopes)
-- is unchanged — this only adds the TIE-BREAK order as a tenant config knob: "oldest_first" (default,
-- unchanged behaviour) | "newest_first". Consumed by GET /closing/envelope-plan via
-- commcalc.envelope_payout_config.order_preference, surfaced on the Envelope Payout Setup page
-- (/closing/envelope-config).
--
-- Depends on migration 507 (commcalc.envelope_payout_config) — guarded below so this is a safe no-op
-- if it happens to run before 507 (belt-and-suspenders; the PENDING SQL run order in the retail-ops
-- handoff lists 507 before 508). SAFE: additive + idempotent, degrades gracefully — until this runs,
-- GET /closing/envelope-config reads the coded default ('oldest_first', byte-identical to today's
-- single fixed order), and PUT /closing/envelope-config saves every OTHER field unaffected (this one
-- column is independently try/except-guarded in closing/router.py's put_envelope_config).

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'commcalc' AND table_name = 'envelope_payout_config'
  ) THEN
    ALTER TABLE commcalc.envelope_payout_config
      ADD COLUMN IF NOT EXISTS order_preference TEXT NOT NULL DEFAULT 'oldest_first';

    IF NOT EXISTS (
      SELECT 1 FROM pg_constraint WHERE conname = 'envelope_payout_config_order_preference_chk'
    ) THEN
      ALTER TABLE commcalc.envelope_payout_config
        ADD CONSTRAINT envelope_payout_config_order_preference_chk
        CHECK (order_preference IN ('oldest_first', 'newest_first'));
    END IF;
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT '508 complete — commcalc.envelope_payout_config.order_preference ready (no-op if 507 has not run yet)' AS status;
