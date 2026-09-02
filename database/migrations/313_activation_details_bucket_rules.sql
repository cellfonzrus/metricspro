-- 313_activation_details_bucket_rules.sql — PER-ORG Activation-Details bucket classification rules
-- (RULE TWO — config, never code). mod-commission · band 200–299 spill → follows 312.
--
-- WHY (owner-approved fix, 2026-09-01 reconciliation report, applied 2026-09-02):
--   1. EDGE OVER-MATCH: the Activation-Details TYPE classifier matched the token 'edge' in the
--      product/plan NAME as well as the Contract Type, so every "Motorola Edge" DEVICE
--      (Port/Activation/Upgrade contract) landed in the Edge column — LuxeLink Aug-2026: all 16
--      "Edge" rows were Motorola Edge handsets, zero were Edge-program contracts. The b2b portal's
--      own Edge column is CONTRACT-TYPE-driven.
--   2. 'BYOD Upgrade' inflated the DISPLAYED Upgrade column (b2b shows it as its own family). It
--      stays EXCLUDED from Total Activation exactly like Upgrade — only the displayed column moves.
--
-- WHAT THIS ADDS: one JSONB column on the existing per-org commcalc.accessory_config row (the
-- mig-208/214 config surface — reused, never a parallel table):
--
--   activation_details_rules: {
--     "edge_contract_tokens":           ["edge"],           -- whole-word contains on Contract Type
--     "edge_name_tokens":               [],                 -- substring on SP-PO/product/category NAME
--     "upgrade_hidden_contract_tokens": ["byod upgrade"]    -- routes to the hidden 'BYOD Upgrade' bucket
--   }
--
-- Missing column / row / keys → the HOUSE DEFAULTS above (resolved in
-- backend/app/modules/commcalc/activation_bucketing.resolve_rules; loaded by router.py
-- _activation_details_rules with its OWN defensive query, mirroring mig-214 billpay_products — the
-- feature works BEFORE this migration runs and an absent column can never break the resolver).
--
-- DEFAULT-BEHAVIOR NOTE (deliberate, owner-approved): the house default itself IS the fix — Edge
-- matches contract_type only (word-boundary) and 'BYOD Upgrade' leaves the displayed Upgrade
-- column. A tenant whose Edge program is only identifiable by plan NAME opts name-matching back in
-- per org via edge_name_tokens; upgrade_hidden_contract_tokens: [] restores the single Upgrade
-- family. Total-Activation exclusion semantics are IDENTICAL (both Upgrade families excluded).
--
-- 💰 MONEY POSTURE: DISPLAY + basis classification only. The one plan-rate interaction (a device
-- formerly mis-bucketed 'Edge' whose contract type is 'Upgrade' now pays the plan's Upgrade rate
-- instead of its Edge rate) is a CORRECTION of the over-match, surfaced in the 2026-09-02 report.
--
-- Additive + idempotent + re-runnable. PostgREST cannot DDL — the owner runs this by hand.
--
-- REVERT:
--   ALTER TABLE commcalc.accessory_config DROP COLUMN IF EXISTS activation_details_rules;
--   (Code-side house defaults then apply to every org; the old pre-313 name-matching behavior only
--    returns by reverting the backend commit that introduced activation_bucketing.py.)

ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS activation_details_rules JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN commcalc.accessory_config.activation_details_rules IS
  'Per-org Activation-Details TYPE classification rules (mig 313; RULE TWO). Keys: '
  'edge_contract_tokens (whole-word contains on Contract Type -> Edge; house default ["edge"]), '
  'edge_name_tokens (substring on SP-PO/product/category name -> Edge; house default [] — the '
  'Motorola-Edge-device over-match fix), upgrade_hidden_contract_tokens (contract-type contains '
  'tokens routed to the hidden ''BYOD Upgrade'' bucket — excluded from Total Activation like '
  'Upgrade but not displayed in the Upgrade column; house default ["byod upgrade"]). {} / missing '
  'keys = house defaults, resolved in backend activation_bucketing.resolve_rules.';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 313 complete — commcalc.accessory_config.activation_details_rules installed (per-org; {} default = house rules: contract-type Edge, hidden BYOD Upgrade)' AS status;
