-- 273_commission_financing_tier_rates.sql — TIERED, TARGET-BASED per-unit rates for a commission rule.
--
-- OWNER DIRECTIVE (in-chat 2026-08-04): "…target based commission payout right now we have flat payment,
-- need it tiered levels."
-- OWNER ANSWERS (in-chat 2026-08-04, verbatim): "achieved rate applies to that months sales, attainment
-- is monthly" — the tier a store REACHES sets the per-unit rate for EVERY financing unit of that month
-- (whole-month retroactive, NOT marginal per band), and attainment is measured MONTHLY against the
-- store's monthly financing target for the period.
--
-- WHY THIS SHAPE: the plan engine ALREADY has a tiers concept (commcalc.commission_tier: highest
-- min_count <= the rep's metric wins -> a MULTIPLIER on every rule marked `tiered`). That is a
-- plan-wide multiplier, which cannot express "financing pays $X/unit at tier 1 and $Y/unit at tier 2
-- while nothing else about the plan changes". Rather than building a parallel tier system, this
-- migration EXTENDS the same table with three optional columns:
--
--   rule_id            NULL (every row today) = a plan-wide MULTIPLIER tier, exactly as it works now.
--                      Set  = the tier applies to ONE rule only.
--   unit_rate          NULL (every row today) = keep multiplying. Set = the per-unit DOLLAR RATE that
--                      replaces the rule's flat `amount` when this tier is reached.
--   min_attainment_pct NULL = the tier threshold is the existing `min_count` (a unit count).
--                      Set  = the threshold is ATTAINMENT % against the store's financing target
--                      (commcalc.financing_target, migration 272).
--
-- and commission_rule with three optional columns that say how a rule reads those tiers:
--
--   financing_vendor_key  which financing vendor this rule pays for (migration 272). Only used to pick
--                         the vendor's target row; NULL = the store's whole-financing target.
--   unit_tier_scope       'store' (default) = the tier is decided by the STORE's financing units against
--                         the store's target, and every rep at that store then earns the tier's rate on
--                         their OWN units. 'rep' = each rep's own units decide their own tier.
--   unit_tier_mode        'whole_month' (default, the owner's rule) = every unit of the month pays the
--                         achieved tier's rate. 'marginal' = each unit pays the rate of the band it
--                         falls in. Only meaningful with count-based thresholds.
--
-- ⚠️ MONEY, BUT INERT. Running this migration adds NULLABLE columns and writes NO rows. With no
-- rule-scoped tier rows, `financing_tiers.build_context()` returns active=False and the commission
-- engine's payout is BYTE-IDENTICAL to today, line for line. NO RATE, THRESHOLD OR DOLLAR VALUE IS
-- SEEDED ANYWHERE — the levels are the owner's to type in the plan editor.

-- ── 1. tier rows may be scoped to ONE rule and carry a per-unit RATE ──────────────────────────────
ALTER TABLE commcalc.commission_tier
  ADD COLUMN IF NOT EXISTS rule_id            UUID,
  ADD COLUMN IF NOT EXISTS unit_rate          NUMERIC,
  ADD COLUMN IF NOT EXISTS min_attainment_pct NUMERIC,
  ADD COLUMN IF NOT EXISTS label              TEXT;

CREATE INDEX IF NOT EXISTS commission_tier_rule_idx
  ON commcalc.commission_tier (org_id, rule_id) WHERE rule_id IS NOT NULL;

COMMENT ON COLUMN commcalc.commission_tier.rule_id IS
  'NULL = a plan-wide multiplier tier (the original behaviour, unchanged). Set = this tier applies to '
  'that single commcalc.commission_rule and, with unit_rate set, replaces its flat per-unit amount.';
COMMENT ON COLUMN commcalc.commission_tier.unit_rate IS
  'Per-unit dollar rate earned when this tier is reached. NULL = this row is a multiplier tier.';
COMMENT ON COLUMN commcalc.commission_tier.min_attainment_pct IS
  'Tier threshold expressed as ATTAINMENT % of the store''s financing target (100 = target met). NULL = '
  'use min_count (a plain unit count). A store with NO target set reaches no attainment tier at all — '
  'the rule keeps paying its flat amount and the run reports "no target set" rather than guessing 0%.';

-- ── 2. how a rule reads its tiers ────────────────────────────────────────────────────────────────
ALTER TABLE commcalc.commission_rule
  ADD COLUMN IF NOT EXISTS financing_vendor_key TEXT,
  ADD COLUMN IF NOT EXISTS unit_tier_scope      TEXT,
  ADD COLUMN IF NOT EXISTS unit_tier_mode       TEXT;

COMMENT ON COLUMN commcalc.commission_rule.financing_vendor_key IS
  'commcalc.financing_vendor.vendor_key this rule pays for (migration 272). Used to pick which financing '
  'target the rule''s attainment tiers measure against, and to tie the Financing report''s units to the '
  'rule that pays them. NULL = not a financing rule (every rule today).';
COMMENT ON COLUMN commcalc.commission_rule.unit_tier_scope IS
  'NULL/''store'' = the STORE''s financing units decide the tier and every rep at that store earns that '
  'tier''s rate on their own units. ''rep'' = each rep''s own units decide their own tier.';
COMMENT ON COLUMN commcalc.commission_rule.unit_tier_mode IS
  'NULL/''whole_month'' = the achieved tier''s rate applies to EVERY unit of the month (owner rule, '
  '2026-08-04). ''marginal'' = each unit pays the rate of the band it falls in (count thresholds only).';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 273 complete — commission_tier.rule_id/unit_rate/min_attainment_pct/label + '
       'commission_rule.financing_vendor_key/unit_tier_scope/unit_tier_mode (tiered per-unit financing '
       'rates; NO rows written, NO rate seeded, engine byte-identical until tiers are configured)' AS status;
