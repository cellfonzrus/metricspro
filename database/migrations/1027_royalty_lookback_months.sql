-- 1027_royalty_lookback_months.sql — how far back the multi-month royalty upload reaches (index §37.10).
--
-- OWNER (2026-09-26): "option to upload royalty reports for multiple months should be created for the ups store
-- going back up to 24 months".
--
-- The lookback is a PER-ORG SETTING, never a constant in code (CLAUDE.md RULE TWO): one nullable column on the
-- existing royalty config row (mig 1022). NULL = the house default in account/royalty.CONFIG_DEFAULT
-- ("lookback_months": 24), exactly as every other royalty_config column reads its default. The window is this month
-- and `lookback_months` months before it; a report whose own header period falls outside it is refused by the
-- batch preview (the single import is unchanged).
--
-- Additive and idempotent. Moves no money and changes no stored report: until an org sets a value, every org reads 24.
-- Before this is applied the batch upload still works (the column is absent → the default 24); only SAVING a
-- different lookback is refused, naming this migration.
--
-- REVERT:
--   ALTER TABLE commcalc.royalty_config DROP COLUMN IF EXISTS lookback_months;
--   NOTIFY pgrst, 'reload schema';

ALTER TABLE commcalc.royalty_config
  ADD COLUMN IF NOT EXISTS lookback_months INTEGER;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'royalty_config_lookback_months_range') THEN
    ALTER TABLE commcalc.royalty_config
      ADD CONSTRAINT royalty_config_lookback_months_range
      CHECK (lookback_months IS NULL OR lookback_months BETWEEN 1 AND 120);
  END IF;
END $$;

COMMENT ON COLUMN commcalc.royalty_config.lookback_months IS
  'How many months before this month the multi-month royalty upload accepts (mig 1027, index §37.10). NULL = the house default in account/royalty.CONFIG_DEFAULT (24). Bounds mirror royalty.LOOKBACK_BOUNDS.';

NOTIFY pgrst, 'reload schema';
