-- 213_commission_contract_type_map.sql — PER-ORG contract-type → activation-bucket mapping (RULE TWO).
--
-- WHY: the DISPLAY activation classifier (_sales_cell_agg → classify_contract_type in calculator.py) is a
-- HARD-CODED keyword list tuned to Boost/generic Contract Type labels ('Activation', 'Port-In', 'AAL',
-- 'BYOD', 'Upgrade', 'IDV', …). For a Total-carrier tenant whose b2bsoft POS emits DIFFERENT Contract Type
-- strings (e.g. a prepaid label the keyword set never sees), classify_contract_type returns None → the line
-- is neither an activation, an upgrade, nor a BYOD → the Sales Report / Executive MTD / Daily-Targets
-- activation & upgrade counts read 0 even though the sales are present (the luxelink "achieved activations
-- = 0 with targets set" report, 2026-07-17).
--
-- FIX: a per-org, admin-editable map {<contract_type value> : 'premium'|'upgrade'|'byod'|'none'} so a
-- tenant can map its OBSERVED Contract Type values (pick-don't-type over commcalc.sales-fields
-- contract_types, RULE THREE) to the display activation buckets WITHOUT a code change. A mapped value wins;
-- an UNMAPPED value falls back to the hard-coded classify_contract_type — so this only ADDS recognition for
-- labels the classifier misses. 'none' force-excludes a label that would otherwise falsely match.
--
-- SCOPE — DISPLAY ONLY, NO PAY CHANGE: this map is consumed exclusively by the shared DISPLAY aggregation
-- (_sales_cell_agg → Sales Report / Executive MTD / Daily Targets). The Boost payout path (calculator.py
-- classify_contract_type) and the plan-mode payout path (commission_engine.py) DO NOT read this column, so
-- no commission/payout number moves. calculator.py is untouched.
--
-- BOOST-SAFE / GRACEFUL DEGRADE: default '{}' (empty) → _resolve_ct_bucket falls straight through to
-- classify_contract_type → the house/Boost display numbers stay BYTE-IDENTICAL. The resolver reads this
-- column in its OWN defensive query and falls back to '{}' when the column/row is absent, so the feature
-- works BEFORE this migration runs and a missing column can never break classification. Additive +
-- idempotent + re-runnable. Reuses commcalc.accessory_config (mig 208) rather than a parallel table.
--
-- NO SEED: the correct mapping depends on the tenant's LIVE Contract Type values (not statically known), so
-- nothing is seeded — the owner maps them in Sales Report → Classification settings → "Contract type →
-- activation bucket" (options come from the org's real observed values). An empty map = today's behavior.

ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS contract_type_map JSONB NOT NULL DEFAULT '{}'::jsonb;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 213 complete — commcalc.accessory_config.contract_type_map installed (per-org; empty default = byte-identical)' AS status;
