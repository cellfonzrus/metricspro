-- 232_commission_plan_tier_and_gate_lookup.sql — CONFIGURABLE tier attainment + multi-month gate lookup
--                                                (RULE TWO; owner directive 2026-07-25, luxelink).
--
-- OWNER DIRECTIVE (2026-07-25, luxelink org 854f6d7b-6590-4e4d-88ab-646f560d4f4c): "employee commissions
-- are NOT calculating per the tier commissions and Commission Plans configured". Required behaviour:
--   1. every employee is paid from the Commission Plan ASSIGNED to them, INCLUDING tier attainment;
--   2. multi-month: M1 pays IMMEDIATELY at activation (no gate), the later month pays ONLY when the
--      residual was actually RECEIVED (evidence = the carrier's commission file — VidaPay raw_ma_* here).
-- Both must be UNIVERSAL config, never a luxelink branch.
--
-- WHAT THIS MIGRATION ADDS (all NULL/'sale'/'raw' defaults ⇒ EVERY existing plan + tenant is BYTE-IDENTICAL
-- until an admin opts in; no pay number moves on the SQL alone, and nothing moves at all until the owner
-- runs Calculate):
--
-- ── A. commission_plan: how the TIER metric is counted ────────────────────────────────────────────
--   Today commcalc.commission_engine._tier_multiplier compares the plan's tiers against `qualifying_units`
--   = the number of RULE-MATCHED, qualifying sale LINES summed across every rule in the plan. The plan's
--   declared `base_tier_metric` ('activations'|'upgrades'|'boxes') is READ ONLY as an on/off switch — the
--   actual count is line-shaped, so (a) one activation that rings 3 lines counts 3, (b) a line matched by
--   two rules counts twice, and (c) 13 accessory lines satisfy a tier that says "10 activations".
--   These four columns let the plan DEFINE its metric with the same matcher shape a commission_rule uses:
--     tier_count_basis  NULL/'rule_units' = today's behaviour (byte-identical) | 'lines' | 'transactions'
--     tier_match_field / tier_match_op / tier_match_value = WHICH lines count (same vocabulary as
--       commission_rule.match_field, incl. the synthetic 'accessory' and the new 'activation_bucket').
--   e.g. basis='transactions', field='activation_bucket', op='in', value='premium,byod' ⇒ the tier counts
--   DISTINCT activation transactions, which is what "tier commissions" means to a human.
--
--   tier_below_min_multiplier: today a rep BELOW the lowest tier gets 1.0 (FULL pay) — a plan whose lowest
--   tier is "30 units → 0.5×" silently pays 1.0× to a rep who sold 5. NULL keeps that; set 0.5 (or 0) to
--   define the floor explicitly.
--
-- ── B. commission_org_config.plan_ct_resolution: blank / tenant-specific Contract Type in the MONEY path ─
--   commission_rule.match_field='contract_type' compares the RAW raw_sales.contract_type. ~77% of luxelink's
--   July lines carry a BLANK Contract Type (mig 224 header), so any contract_type-keyed rule or tier
--   silently never pays those lines. The tenant ALREADY configures the answer for the DISPLAY path
--   (accessory_config.contract_type_map, mig 213 + accessory_config.activation_rules, mig 224).
--     'raw'    (default) = today: compare the raw column only. BYTE-IDENTICAL.
--     'mapped'           = a contract_type rule ALSO matches the line's RESOLVED activation bucket
--                          ('premium'|'upgrade'|'byod') from that same per-org config. Strictly a SUPERSET
--                          ⇒ MORE lines can match ⇒ PAY CAN GO UP. Owner-flipped, then recalc.
--   (The synthetic match_field 'activation_bucket' is available regardless and is inert unless a rule uses
--   it — no migration needed for that; it is listed here so the vocabulary lives in one place.)
--
-- ── C. installment_gate_source_config.ma_lookup_periods: WHERE the residual evidence is read from ──────
--   The master-agent paid gate (mig 223) reads raw_ma_commission for the SALE (activation) period only,
--   because that row carries the device's forward M1-M6 schedule and is refreshed cumulatively. If a
--   tenant's statement instead posts month N's payout in the PAY month's file — or the sale month's file is
--   never re-pulled — month N is withheld forever even though the dealer WAS paid.
--     'sale' (default) = today. BYTE-IDENTICAL.
--     'pay'            = read the pay period's statement.
--     'both'           = read BOTH and NET them (base + adjustment rows across both periods are summed,
--                        exactly like today's multi-row netting, so a clawback still cannot read as paid).
--
-- SAFETY: additive + idempotent + re-runnable. Every consumer reads these columns defensively (.get() with
-- the safe default) so the code works BEFORE this runs and a missing column can never break a page or a
-- calc. NO seed rows — nothing is turned on for anybody by this file.

-- ── A. plan-level tier attainment config ──────────────────────────────────────────────────────────
ALTER TABLE commcalc.commission_plan
  ADD COLUMN IF NOT EXISTS tier_count_basis          TEXT;      -- NULL/'rule_units' | 'lines' | 'transactions'
ALTER TABLE commcalc.commission_plan
  ADD COLUMN IF NOT EXISTS tier_match_field          TEXT;      -- same vocabulary as commission_rule.match_field
ALTER TABLE commcalc.commission_plan
  ADD COLUMN IF NOT EXISTS tier_match_op             TEXT;      -- equals | contains | in
ALTER TABLE commcalc.commission_plan
  ADD COLUMN IF NOT EXISTS tier_match_value          TEXT;
ALTER TABLE commcalc.commission_plan
  ADD COLUMN IF NOT EXISTS tier_below_min_multiplier NUMERIC;   -- NULL = 1.0 (today); explicit floor otherwise

COMMENT ON COLUMN commcalc.commission_plan.tier_count_basis IS
  'How the tier metric is counted: NULL/''rule_units'' = legacy (matched qualifying rule LINES summed across rules, byte-identical), ''lines'' = matched lines, ''transactions'' = DISTINCT matched trans_id. With ''lines''/''transactions'' the tier_match_* matcher selects which lines count.';
COMMENT ON COLUMN commcalc.commission_plan.tier_below_min_multiplier IS
  'Multiplier applied when the rep is BELOW the plan''s lowest tier min_count. NULL = 1.0 (today''s behaviour: full pay below the first tier).';

-- ── B. per-tenant contract-type resolution in the PAY path ────────────────────────────────────────
ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS plan_ct_resolution TEXT NOT NULL DEFAULT 'raw';   -- 'raw' | 'mapped'

COMMENT ON COLUMN commcalc.commission_org_config.plan_ct_resolution IS
  '''raw'' (default, byte-identical) = a commission_rule with match_field=''contract_type'' compares the RAW raw_sales.contract_type. ''mapped'' = it ALSO matches the line''s resolved activation bucket (premium/upgrade/byod) from accessory_config.contract_type_map (mig 213) + accessory_config.activation_rules (mig 224), so blank / tenant-specific Contract Type values can pay. MONEY: ''mapped'' is a superset ⇒ pay can increase on the next Calculate.';

-- ── C. master-agent gate: which statement period(s) prove the residual was received ───────────────
ALTER TABLE commcalc.installment_gate_source_config
  ADD COLUMN IF NOT EXISTS ma_lookup_periods TEXT NOT NULL DEFAULT 'sale';   -- 'sale' | 'pay' | 'both'

COMMENT ON COLUMN commcalc.installment_gate_source_config.ma_lookup_periods IS
  'Which raw_ma_commission period(s) the paid gate reads as month-N evidence: ''sale'' (default, byte-identical — the activation month''s cumulatively-refreshed row), ''pay'' (the paying month''s statement), ''both'' (union, NETTED across periods so a clawback still cannot read as paid).';

-- ── Grants (explicit, per the sequence-grant lesson — ALTER DEFAULT PRIVILEGES exists but is not relied on)
GRANT USAGE ON SCHEMA commcalc TO anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.commission_plan               TO anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.commission_org_config         TO anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.installment_gate_source_config TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';

-- ── OPTIONAL SEEDS (NOT auto-applied — every one of these MOVES MONEY on the next Calculate). Run only
--    after the gate-impact preview + the owner's explicit go-ahead. Confirm the org_id first.
--
-- (1) luxelink: contract_type rules also match the mapped activation bucket (needs accessory_config
--     .activation_rules populated — see mig 224's optional seed):
-- INSERT INTO commcalc.commission_org_config (org_id, plan_ct_resolution)
-- VALUES ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', 'mapped')
-- ON CONFLICT (org_id) DO UPDATE SET plan_ct_resolution = 'mapped', updated_at = NOW();
--
-- (2) luxelink: the "Total Employee Comp Chicago" plan tiers on DISTINCT activation transactions:
-- UPDATE commcalc.commission_plan
--    SET tier_count_basis = 'transactions', tier_match_field = 'activation_bucket',
--        tier_match_op = 'in', tier_match_value = 'premium,byod'
--  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' AND name ILIKE 'Total Employee Comp%';
--
-- (3) luxelink: M1 pays at activation, months 2+ stay residual-gated (this is EXISTING mig-201 config,
--     repeated here because it is part of the same owner directive):
-- UPDATE commcalc.plan_installment_schedule SET gate_from_month = 2, updated_at = NOW()
--  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' AND name ILIKE '%3MR%';
--
-- (4) luxelink: accept residual evidence from BOTH the activation month's and the paying month's MA file:
-- INSERT INTO commcalc.installment_gate_source_config
--   (org_id, carrier_id, carrier_mode, gate_source, ma_lookup_periods, notes)
-- VALUES ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'plan',
--         'ma_commission', 'both', 'luxelink: residual evidence may land in either the activation-month or the paying-month VidaPay file.')
-- ON CONFLICT (org_id, carrier_id, carrier_mode) DO UPDATE SET ma_lookup_periods = 'both', updated_at = NOW();

SELECT 'Migration 232 complete — commission_plan tier_count_basis/tier_match_*/tier_below_min_multiplier, '
       'commission_org_config.plan_ct_resolution, installment_gate_source_config.ma_lookup_periods '
       '(all defaults = byte-identical; no seeds applied)' AS status;
