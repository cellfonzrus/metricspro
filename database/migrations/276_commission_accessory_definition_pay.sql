-- 276_commission_accessory_definition_pay.sql   (mod-commission, band 200-299)
--
-- THE ACCESSORY DEFINITION AS A PAY BASIS — per-tenant switch, DEFAULT OFF.
--
-- ── THE BUG THIS CLOSES (owner-reported 2026-08-05, luxelink July 2026) ──────────────────────────
-- The owner mapped products as accessories on Commissions -> Accessory Definition (mig 257:
-- commcalc.accessory_definition_map), wrote a Commission-Plan rule `accessory equals yes` paying
-- pct_price, re-ran July — and transaction 3207's Screen Protector / Case lines STILL paid $0.
--
-- CAUSE: two different accessory surfaces, and the money path reads the OTHER one.
--   * What the owner mapped ............ commcalc.accessory_definition_map  (mig 257)
--   * What the plan engine's synthetic
--     `accessory` match_field reads .... accessory_catalog.AccessoryClassifier — i.e.
--                                        accessory_config.departments / .categories /
--                                        .product_keywords, plus the raw_catalog category layer.
-- Migration 257 says so out loud ("Nothing that decides a payout reads any of it ... not
-- commission_engine.py"). Adopting the definition as a pay basis was filed as an OWNER DECISION in
-- docs/handoffs/commission.md and never built. This migration + its code change build it, INERT.
--
-- ── THIS MIGRATION MOVES $0 ──────────────────────────────────────────────────────────────────────
-- It adds ONE nullable-with-default-false boolean. Every existing tenant reads FALSE, the engine takes
-- exactly the branch it takes today, and no stored payout number changes. Money moves only after a
-- human switches it ON for a tenant AND presses Run Commission.
--
-- ── WHAT IT DOES WHEN SWITCHED ON ────────────────────────────────────────────────────────────────
-- The synthetic `accessory` stamp becomes  legacy-sets OR catalog OR the tenant's own CONFIRMED
-- Accessory Definition  — strictly ADDITIVE: it can only add accessory lines, never remove one, so no
-- line that pays today stops paying. Set-up fees stay non-accessories (accessory_definition.classify
-- checks the set-up-fee keywords first — standing owner rule 2026-07-17).
--
-- SCOPE: the PAY path only (commission_engine.preview's `accessory` match_field). The Sales Report /
-- GP / P&L / Sales Analyzer accessory numbers are deliberately NOT touched — unifying those ~8
-- surfaces is a separate owner decision ([[accessory-flow-divergences]]).
--
-- ── UNTIL THIS RUNS ──────────────────────────────────────────────────────────────────────────────
-- The loader's SELECT fails and degrades to FALSE, so the engine behaves exactly as it does today and
-- the toggle in the UI reports itself as unavailable, naming this file. Nothing breaks.
--
-- ADDITIVE + IDEMPOTENT: safe to re-run.

ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS definition_drives_pay BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN commcalc.accessory_config.definition_drives_pay IS
  'MONEY SWITCH (mig 276), default FALSE. When true, commission_engine.preview stamps the synthetic '
  '`accessory` match_field as YES when the tenant''s CONFIRMED accessory_definition_map / field rule '
  '(mig 257) says accessory, in ADDITION to the legacy department/category/keyword sets and the '
  'raw_catalog category layer (accessory_catalog.AccessoryClassifier). Strictly additive — it can only '
  'ADD accessory lines, never remove one. Affects the PAY path only; the Sales Report / GP / P&L / '
  'Analyzer accessory classifiers are unchanged. Changing it moves money on the next recalculation.';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 276 complete — accessory_config.definition_drives_pay (default false). No payout '
       'number changes until a tenant switches it on AND recalculates.' AS status;
