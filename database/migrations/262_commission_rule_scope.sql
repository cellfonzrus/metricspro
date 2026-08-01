-- 262_commission_rule_scope.sql   (mod-commission, band 200-299)
--
-- RULE-LEVEL SCOPE — "this rule applies HERE, not everywhere".
--
-- ⚠️ MONEY-ADJACENT. This migration MOVES NOTHING BY ITSELF: both columns land NULL, and NULL means
-- "applies everywhere", which is exactly today's behaviour.
--
-- OWNER DIRECTIVE 2026-08-01, verbatim:
--   "All activations are being paid $10 flat , this is only for NY employees, but this empluee is in
--    Chicago."
-- luxelink context: the NY/NJ stores have sold since February; the IL stores started in July. ~28 July
-- rows show Chicago-store reps collecting the $10 "Activations" $/unit rule that was written for NY.
--
-- THE CONFIG-FIRST CHECK, AND ITS HONEST ANSWER
-- ─────────────────────────────────────────────
-- PARTIAL YES. Scoping ALREADY exists at PLAN level: commcalc.commission_plan_assignment carries
-- scope 'employee' | 'role' | 'store' | 'market' | 'default' with precedence employee > role > store >
-- market > default. A tenant can therefore fix this TODAY, with no code, by cloning the plan (there is
-- a Clone button — template_clone.py), deleting the $10 Activations rule from the clone, and assigning
-- the clone to the IL market/stores. That is a REAL answer and it is written up in the handoff.
--
-- What does NOT exist is scoping ONE RULE. The plan-clone answer duplicates every other rule into a
-- second plan, so from then on every future rate change has to be made twice and the two plans drift —
-- which is the failure mode this table is meant to prevent. Hence these two columns: the SAP-clean
-- form of the same decision, with the plan-clone route still available for tenants who prefer it.
--
-- WHAT IT ADDS
--   commcalc.commission_rule.applies_scope_kind   TEXT  'store' | 'market' | 'employee' | NULL
--   commcalc.commission_rule.applies_scope_value  TEXT  comma-separated values, matched case- and
--                                                       punctuation-insensitively
--
-- NULL/blank on either column = UNSCOPED = applies to every rep the plan is assigned to = today.
-- A scoped rule pays only where the rep's own store / market / name matches. Store matching reuses the
-- SAME alias-resolved store identities a store-scope plan assignment uses (mig 249), so a rule and an
-- assignment cannot disagree about what "4640-A W Diversey Ave" is.
--
-- NOTHING IS SEEDED. Which stores count as "NY" is the OWNER'S mapping (store_mapping.market /
-- storeops stores), picked from a dropdown in the rule editor — never typed, never guessed here. The
-- luxelink NY seed is a PROPOSAL in the park record awaiting the owner's confirmation, not SQL.
--
-- UNTIL THIS RUNS: plan_pay_gate.rule_scope() reads the absent keys as "unscoped" and every rule
-- applies everywhere — i.e. today. Running this migration changes NO payout; it makes the scope
-- SETTABLE (RULE TWO).
--
-- ADDITIVE + IDEMPOTENT. No new table, so no RLS/GRANT clause (commission_rule carries its own
-- posture). No GRANT, no CREATE POLICY, no anon/authenticated (AGENT_CONTRACT §5).

DO $$
BEGIN
  IF to_regclass('commcalc.commission_rule') IS NULL THEN
    RAISE NOTICE 'commcalc.commission_rule missing — run migration 059 first; 262 skipped.';
    RETURN;
  END IF;

  ALTER TABLE commcalc.commission_rule
    ADD COLUMN IF NOT EXISTS applies_scope_kind  TEXT,
    ADD COLUMN IF NOT EXISTS applies_scope_value TEXT;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'commission_rule_applies_scope_kind_chk') THEN
    ALTER TABLE commcalc.commission_rule
      ADD CONSTRAINT commission_rule_applies_scope_kind_chk
      CHECK (applies_scope_kind IS NULL
             OR applies_scope_kind IN ('store', 'market', 'employee'));
  END IF;
END $$;

COMMENT ON COLUMN commcalc.commission_rule.applies_scope_kind IS
  'WHERE this rule applies (mig 262): ''store'' | ''market'' | ''employee''. NULL = everywhere = the '
  'pre-2026-08-01 behaviour. Complements — does not replace — plan-level assignment scoping.';

COMMENT ON COLUMN commcalc.commission_rule.applies_scope_value IS
  'Comma-separated values for applies_scope_kind (mig 262), matched case- and punctuation-'
  'insensitively. Store values additionally match the alias-resolved store identities (mig 249), so a '
  'rule scope and a store-scope plan assignment cannot disagree about the same POS store string.';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 262 complete — commission_rule.applies_scope_kind / applies_scope_value, both NULL. '
       'NULL = applies everywhere = today. No rule was scoped and no payout moved.' AS status;
