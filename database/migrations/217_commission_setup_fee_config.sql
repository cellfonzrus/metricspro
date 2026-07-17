-- 217_commission_setup_fee_config.sql — PER-ORG "device set-up fee" identification (RULE TWO).
--
-- OWNER DIRECTIVE 2026-07-17: "add device set up fee as a part of accessory sales and calculate accessory
-- target accordingly / report separately."
--
-- WHY A CONFIG COLUMN: the device set-up fee line is identified across the codebase by the product
-- description substring 'Device Setup Charge' (calculator.py — Boost setup_fee commission; gp_report.py —
-- setup_gp; flags.py — SETUP_FEE_MISSING). That string was HARD-CODED in each engine. This adds a per-org,
-- admin-editable list so a tenant whose POS labels the set-up fee differently can map it WITHOUT a code
-- change, and so the Daily-Targets accessory attainment can COUNT the set-up fee (reported SEPARATELY).
--
-- SCOPE: this migration ONLY touches accessory TARGET attainment + accessory-sales REPORTING. It does NOT
-- change any commission/payout number — the Boost calculator.py already pays the set-up fee as its own
-- 10% setup_fee commission and that code is untouched. The accessory-sales REVENUE column (_sales_cell_agg
-- accessory_rev) is ALSO untouched — the set-up fee is tracked in a SEPARATE accumulator and only folded
-- into the *targets* accessory-achieved (never silently blended into the accessory$ column).
--
-- BOOST-SAFE / GRACEFUL DEGRADE: the resolver (_accessory_config) reads this column in its OWN defensive
-- query and falls back to the code default ['Device Setup Charge'] when the column/row is absent — so the
-- feature works BEFORE this migration runs and a missing column can never break accessory classification.
-- Additive + idempotent + re-runnable.

ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS setup_fee_keywords TEXT[] NOT NULL DEFAULT '{}';

-- Seed every existing org row that has NOT been hand-edited (empty array) with the canonical b2bsoft token,
-- so the set-up fee is recognized out-of-the-box. A hand-edited non-empty list is never clobbered.
UPDATE commcalc.accessory_config
   SET setup_fee_keywords = ARRAY['Device Setup Charge']
 WHERE setup_fee_keywords IS NULL OR setup_fee_keywords = '{}';

-- Ensure the house/Boost org has a config row carrying the default (mig 208 backfilled its accessory
-- config; this guarantees the set-up-fee token exists there too). ON CONFLICT keeps any hand edits.
INSERT INTO commcalc.accessory_config (org_id, setup_fee_keywords)
VALUES ('00000000-0000-0000-0000-000000000001', ARRAY['Device Setup Charge'])
ON CONFLICT (org_id) DO UPDATE
   SET setup_fee_keywords = CASE
         WHEN commcalc.accessory_config.setup_fee_keywords IS NULL
           OR commcalc.accessory_config.setup_fee_keywords = '{}'
         THEN EXCLUDED.setup_fee_keywords
         ELSE commcalc.accessory_config.setup_fee_keywords END;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 217 complete — commcalc.accessory_config.setup_fee_keywords installed (per-org; seeded Device Setup Charge)' AS status;
