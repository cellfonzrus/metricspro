-- 1082_prospect_activation_rules.sql
-- ────────────────────────────────────────────────────────────
-- A TAKEOVER PACK'S OWN ACTIVATION VOCABULARY (owner ask 2026-10-14, index §COMMCALC-03.23)
--
-- Owner: "the activations per month details is also missing which breaks up teh new acts upgrade
-- tablet fios etc"
--
-- WHY A COLUMN AND NOT CODE. The activations-per-month breakup counts through `line_class` — the ONE
-- classifier every live store is counted under (§6f/§19.31) — so the prospect report and a store
-- report mean the same thing by "a new activation". That classifier reads the fact out of whichever
-- COLUMNS an org's files carry it in, by whichever WORDS those files use, and both are config
-- (RULE TWO). The house default in `prospect_eval.house_activation_declaration()` names the two
-- columns a takeover pack has carried so far and generic words only; a tenant whose pack spells
-- either differently declares its own here instead of asking for a code change.
--
-- MEASURED BEFORE SHIPPING, on the live upload (5,495 commission rows, 2026-10-14). The house
-- classifier reading `contract_type` placed 0 of 5,495 rows, because a takeover pack has no such
-- column. The declaration in the code places all 5,495 with nothing left over:
--     5,112 activation + 383 byod = 5,495 · 1,588 add-a-lines · 0 unplaced · 0 excluded
-- 'add' is declared as the add-a-line MODIFIER rather than as a class on purpose: 166 'Add' lines are
-- also bring-your-own, and as a class it would have mis-called every one of them.
--
-- NULL IS THE HOUSE ANSWER, and the code is tolerant of the column's absence (`column_tolerant`), so
-- the platform behaves identically before and after this is applied. Nothing is backfilled, no money
-- moves, and no stored figure restates: the breakup is computed live from the uploaded rows.
--
-- REVERT: ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS prospect_activation_rules;

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS prospect_activation_rules JSONB;

COMMENT ON COLUMN commcalc.commission_org_config.prospect_activation_rules IS
  'Store Takeover Evaluation: this org''s takeover-pack activation vocabulary for line_class — '
  '{"fields": [...], "tokens": {class: [words]}, "add_a_line": [words]}. NULL = the house '
  'declaration in prospect_eval.house_activation_declaration(). Replaces the house map WHOLE when '
  'set, so a deliberately dropped word stays dropped. index §COMMCALC-03.23';
