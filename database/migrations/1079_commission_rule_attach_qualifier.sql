-- 1079_commission_rule_attach_qualifier.sql
-- ATTACH QUALIFIER — a commission rule may declare a condition measured over the WHOLE SALE, and a
-- LESSER declared rate for the lines whose sale falls short.
--
-- OWNER DIRECTIVE 2026-10-12 (verbatim): "Payment for edge on total which is defined as $25 must be
-- accompanied with atleast $25 worth of accessories , add this logic in the system not as hard coded
-- but as a logic platform wide which might be used by other carriers also , label the commission as
-- $25 acc is must to be qualified for $25 commission otherwise the commision falls back to $10 or
-- whatever is paid to Chicago market"
--
-- THE CLASS, not the instance (owner directive 2026-09-20). Every gate in the plan engine could only
-- turn a payment OFF — `plan_pay_gate` suppresses (scope, exclusion, unit dedup) and `financing_tiers`
-- re-rates from a MONTH's attainment. Nothing could say "this line earns a LESSER declared rate
-- because of a fact about the SALE it belongs to". These columns are that mechanism, for any carrier
-- and any condition, and the code home is backend/app/modules/commcalc/attach_qualifier.py
-- (index §COMMCALC-06). RULE TWO: no carrier, tenant, product, market or dollar figure in code.
--
-- "WHATEVER IS PAID TO CHICAGO MARKET" NEEDS NOTHING NEW. Migration 262 already lets a rule state the
-- market, store or employee it applies to (owner directive 2026-08-01, the NY/Chicago case). A tenant
-- that wants the fallback to BE another market's rate enters that rate as `qualifier_fallback_amount`,
-- or leaves it NULL and lets that market's own scoped rule pay instead.
--
-- ADDITIVE AND IDEMPOTENT. Every column is nullable with no default, and the code reads a NULL
-- `qualifier_min` as NO QUALIFIER — so applying this migration changes NOTHING by itself and every
-- existing payout stays byte-identical until someone declares a condition on a rule.
--
-- MONEY NOTICE. Applying this file moves no money and alters no stored payout. What would move pay is
-- declaring a qualifier on a rule and then running Calculate. Measured read-only on 2026-10-12
-- against the live Total tenant's two $25 `edge` rules (`tender_type` matchers), counting DISTINCT
-- sales and standing in for the live accessory flag with that tenant's own
-- `department in (Accessories, C2Wireless)` declaration:
--     Jul 2026    2 sales,  0 qualified, 2 short     $50.00 -> $20.00    -$30.00
--     Aug 2026    7 sales,  6 qualified, 1 short    $175.00 -> $160.00   -$15.00
--     Sep 2026    8 sales,  4 qualified, 4 short    $200.00 -> $140.00   -$60.00
--     Oct to date 2 sales,  2 qualified, 0 short     $50.00 -> $50.00     $0.00
--   Four-month total $475.00 -> $370.00 (-$105.00), at a $25 minimum with a $10 fallback.
--   ONE OF THOSE SALES CARRIED $24.99 OF ACCESSORIES — a penny short cost the rep $15 under the rule
--   as literally stated. Surfaced for the owner rather than rounded away in code.
--
-- REVERT: the columns are additive and inert while NULL. To revert the BEHAVIOUR without dropping
-- anything, clear the condition:
--     UPDATE commcalc.commission_rule SET qualifier_min = NULL WHERE qualifier_min IS NOT NULL;
-- To revert the SCHEMA (only if no rule has declared a qualifier):
--     ALTER TABLE commcalc.commission_rule
--       DROP COLUMN IF EXISTS qualifier_min,
--       DROP COLUMN IF EXISTS qualifier_measure,
--       DROP COLUMN IF EXISTS qualifier_scope,
--       DROP COLUMN IF EXISTS qualifier_match_field,
--       DROP COLUMN IF EXISTS qualifier_match_op,
--       DROP COLUMN IF EXISTS qualifier_match_value,
--       DROP COLUMN IF EXISTS qualifier_fallback_amount,
--       DROP COLUMN IF EXISTS qualifier_label;

BEGIN;

-- The condition lives ON THE RULE, beside the rate it qualifies and the scope columns of mig 262,
-- because it is a property of that rate and nothing else reads it. A separate table would be a second
-- home for one rule's own terms — the duplicate defect the index rules forbid.
ALTER TABLE commcalc.commission_rule
  ADD COLUMN IF NOT EXISTS qualifier_min              NUMERIC,
  ADD COLUMN IF NOT EXISTS qualifier_measure          TEXT,
  ADD COLUMN IF NOT EXISTS qualifier_scope            TEXT,
  ADD COLUMN IF NOT EXISTS qualifier_match_field      TEXT,
  ADD COLUMN IF NOT EXISTS qualifier_match_op         TEXT,
  ADD COLUMN IF NOT EXISTS qualifier_match_value      TEXT,
  ADD COLUMN IF NOT EXISTS qualifier_fallback_amount  NUMERIC,
  ADD COLUMN IF NOT EXISTS qualifier_label            TEXT;

-- The vocabularies are allowlists in code (`attach_qualifier.resolve`, §66 posture: an unruled value
-- reads as the house default rather than becoming a third behaviour). These constraints are the same
-- answer at the schema boundary, so a hand-written UPDATE cannot store a value the engine will
-- silently ignore. NULL stays legal everywhere and means "not declared".
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'commission_rule_qualifier_measure_chk'
                    AND conrelid = 'commcalc.commission_rule'::regclass) THEN
    ALTER TABLE commcalc.commission_rule
      ADD CONSTRAINT commission_rule_qualifier_measure_chk
      CHECK (qualifier_measure IS NULL OR qualifier_measure IN ('amount', 'gp', 'count'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'commission_rule_qualifier_scope_chk'
                    AND conrelid = 'commcalc.commission_rule'::regclass) THEN
    ALTER TABLE commcalc.commission_rule
      ADD CONSTRAINT commission_rule_qualifier_scope_chk
      CHECK (qualifier_scope IS NULL OR qualifier_scope IN ('transaction'));
  END IF;
  -- A minimum of zero or below is not a condition, and the code already reads it as "no qualifier".
  -- Refusing it here stops a 0 becoming a second spelling of OFF that only the code understands.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'commission_rule_qualifier_min_chk'
                    AND conrelid = 'commcalc.commission_rule'::regclass) THEN
    ALTER TABLE commcalc.commission_rule
      ADD CONSTRAINT commission_rule_qualifier_min_chk
      CHECK (qualifier_min IS NULL OR qualifier_min > 0);
  END IF;
END $$;

COMMENT ON COLUMN commcalc.commission_rule.qualifier_min IS
  'The minimum the SALE must carry for this rule to pay its full rate. NULL = no condition, which is '
  'every rule until one is declared. One home: commcalc/attach_qualifier.py (index COMMCALC-06).';
COMMENT ON COLUMN commcalc.commission_rule.qualifier_measure IS
  'How the qualifying sibling lines are measured: amount (their selling price, the default) | gp '
  '(their gross profit) | count (how many). "Worth $25" is what the customer paid, hence the default.';
COMMENT ON COLUMN commcalc.commission_rule.qualifier_scope IS
  'What "the sale" means. Only transaction today: every line sharing this one trans_id, whoever rang '
  'it, because what the customer bought is not an accident of who touched the till.';
COMMENT ON COLUMN commcalc.commission_rule.qualifier_match_field IS
  'WHICH sibling lines count, in this table''s own match_field vocabulary (default the synthetic '
  'accessory flag). The accessory answer comes from the tenant''s curated accessory definition via '
  'plan_pay_gate.accessory_predicate — never from a classifier of its own.';
COMMENT ON COLUMN commcalc.commission_rule.qualifier_match_op IS
  'equals | contains | in — the same operators match_op already takes. Default equals.';
COMMENT ON COLUMN commcalc.commission_rule.qualifier_match_value IS
  'The value those sibling lines must carry. Default yes, which pairs with the accessory flag.';
COMMENT ON COLUMN commcalc.commission_rule.qualifier_fallback_amount IS
  'The LESSER flat rate a line earns when its sale falls short. NULL = it earns nothing, stated '
  'rather than implied. A flat fallback cannot be applied to a percentage rule: the engine refuses '
  'that pairing and reports it instead of silently changing nothing.';
COMMENT ON COLUMN commcalc.commission_rule.qualifier_label IS
  'Optional override for the on-screen sentence. Left NULL the sentence is DERIVED from the figures '
  'above, so a tenant with other numbers reads their own wording and no figure is hard-coded.';

COMMIT;
