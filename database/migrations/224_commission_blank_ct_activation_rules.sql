-- 224_commission_blank_ct_activation_rules.sql — PER-ORG blank-contract_type activation RULES (RULE TWO).
--
-- WHY: an MA/Total-language tenant (luxelink) emits POS sales where the MAJORITY of transactions carry NO
-- Contract Type at all (~77% of luxelink's July 2026 lines: 4,688 blank-ct lines / 2,283 transactions).
-- The contract-type map (mig 213) can't help — there is no label to map. So the Sales Report / Executive
-- MTD / Daily-Targets ACTIVATION counts read 0 for those real activations (the "957 Pennsylvania Avenue
-- activations = 0" report, 2026-07-18) even though the transaction plainly IS an activation: it has a
-- branded-handset device line + a rate-plan line, just no Contract Type string.
--
-- FIX: a per-org, admin-editable list of TRANSACTION-LEVEL rules that classify a BLANK-contract_type
-- transaction into a display activation bucket from OTHER, reliably-populated line fields (department /
-- category / product_desc / trans_type). The MECHANISM is generic code; the VOCABULARY (which dept/cat/
-- product patterns mean "device line" / "plan line" / "SIM line") is per-org CONFIG (pick-don't-type over
-- the org's OBSERVED department/category values in Sales Report → Classification settings, RULE THREE).
--
-- RULE SHAPE (jsonb array; tried IN ORDER, first match wins so precedence is config-controlled):
--   [{ "bucket": "premium"|"upgrade"|"byod",
--      "all_of": [ {"field":"department","contains_any":["BrandedHandset"]},      -- a device line
--                  {"field":"department","contains_any":["Rtr"]} ],               -- a rate-plan line
--      "none_of": [ ... ] }]                                                       -- optional exclusions
-- A cond matches when >=1 line's <field> (case-insensitively) CONTAINS any contains_any / EQUALS any
-- equals_any. A rule fires when EVERY all_of cond is met by some line AND NO none_of cond is met.
--
-- SCOPE — DISPLAY ONLY, NO PAY CHANGE: consumed exclusively by the shared DISPLAY aggregation
-- (_sales_cell_agg → Sales Report / Executive MTD / Daily Targets). The Boost payout path (calculator.py)
-- and plan-mode payout (commission_engine.py) DO NOT read this column → no commission/payout number moves.
-- (Making blank-ct activations PAY is a separate, owner-gated, money-touching step.)
--
-- BOOST-SAFE / GRACEFUL DEGRADE: default '[]' (empty) → the blank-ct engine is a no-op → house/Boost display
-- numbers stay BYTE-IDENTICAL. _accessory_config reads this column in its OWN defensive query and falls back
-- to '[]' when the column/row is absent, so the feature works BEFORE this migration runs and a missing column
-- can never break classification. Additive + idempotent + re-runnable. Reuses commcalc.accessory_config
-- (mig 208) rather than a parallel table.
--
-- NO CODE SEED: the correct patterns depend on the tenant's LIVE observed dept/cat values, so nothing is
-- hard-coded in code. An OPTIONAL, clearly-marked seed for the luxelink org is provided COMMENTED-OUT below
-- (SEED DATA — matches the observed July shapes) that an operator may run after confirming the org_id; it is
-- NOT applied automatically and touches only that one org's config row.

ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS activation_rules JSONB NOT NULL DEFAULT '[]'::jsonb;

NOTIFY pgrst, 'reload schema';

-- ── OPTIONAL SEED (SEED DATA — NOT auto-applied). Uncomment + set :LUX to luxelink's org_id to enable the
--    observed-shape rules for that ONE org. Order: BYOD (SIM + plan, no branded device) BEFORE PREMIUM
--    (branded device + plan) so a SIM-only activation isn't mislabeled premium.
-- UPDATE commcalc.accessory_config SET activation_rules = '[
--   {"bucket":"byod",
--    "all_of":[{"field":"category","contains_any":["SimMarketplace"]},
--              {"field":"department","contains_any":["Rtr"]}],
--    "none_of":[{"field":"department","contains_any":["BrandedHandset"]}]},
--   {"bucket":"premium",
--    "all_of":[{"field":"department","contains_any":["BrandedHandset"]},
--              {"field":"department","contains_any":["Rtr"]}]}
-- ]'::jsonb
-- WHERE org_id = ':LUX';

SELECT 'Migration 224 complete — commcalc.accessory_config.activation_rules installed (per-org; empty [] default = byte-identical)' AS status;
