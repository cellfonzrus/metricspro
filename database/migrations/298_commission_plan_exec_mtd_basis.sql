-- 298_commission_plan_exec_mtd_basis.sql
-- mod-commission · band 200–299 · additive + idempotent + safe to re-run.
--
-- WHAT IT IS FOR (owner 2026-08-30): the "Calculate from Executive MTD" panel is an ADDITIONAL, very simple
-- way to define a plan's commission — one $ rate per Exec MTD activation category (New / Port / BYOD /
-- Tablet / Home Internet / Edge / Upgrade) + an accessory %. The owner wants that setup to SAVE on the plan
-- (so it persists and can become the month-1 pay basis for Chicago and every tenant), alongside the existing
-- rules. This migration adds the two columns that hold it:
--   commission_basis text NOT NULL DEFAULT 'rules'  -- 'rules' (today's rule engine) | 'exec_mtd'
--   mtd_rates        jsonb                            -- {category -> $rate, "accessory_pct" -> fraction}
--
-- 💰 MOVES NO MONEY BY ITSELF. Every plan defaults commission_basis='rules' → byte-identical to today. The
-- pay path reads these columns only once the exec-mtd pay basis is explicitly wired (a separate step); a
-- plan flipped to 'exec_mtd' keeps its rules and keeps paying by them until then, so nothing silently zeroes.
-- Additive + idempotent; run in the Supabase SQL editor. Independent of 296/297.

DO $$
BEGIN
  IF to_regclass('commcalc.commission_plan') IS NULL THEN
    RAISE NOTICE 'commcalc.commission_plan does not exist yet — run migration 059 first; 298 skipped.';
    RETURN;
  END IF;

  ALTER TABLE commcalc.commission_plan
    ADD COLUMN IF NOT EXISTS commission_basis text NOT NULL DEFAULT 'rules';
  ALTER TABLE commcalc.commission_plan
    ADD COLUMN IF NOT EXISTS mtd_rates jsonb;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'commission_plan_basis_chk') THEN
    ALTER TABLE commcalc.commission_plan
      ADD CONSTRAINT commission_plan_basis_chk CHECK (commission_basis IN ('rules', 'exec_mtd'));
  END IF;
END $$;

COMMENT ON COLUMN commcalc.commission_plan.commission_basis IS
  'How this plan computes commission: ''rules'' (the rule engine, default, byte-identical to today) or '
  '''exec_mtd'' (per-category rates applied to the Executive MTD per-employee numbers). Reading it in the '
  'pay path is a separate, explicit step; until then an ''exec_mtd'' plan still pays by its rules. mig 298.';
COMMENT ON COLUMN commcalc.commission_plan.mtd_rates IS
  'JSON rate map for the exec_mtd basis: {activation,port,byod,tablet,home_internet,edge,upgrade -> $rate} '
  'plus accessory_pct (fraction, e.g. 0.10). Null until the owner saves the Exec-MTD basis. mig 298.';

GRANT USAGE ON SCHEMA commcalc TO anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.commission_plan TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';

-- ── REVERT (additive-only; touches no payout number) ────────────────────────────────────────────────
-- ALTER TABLE commcalc.commission_plan DROP CONSTRAINT IF EXISTS commission_plan_basis_chk;
-- ALTER TABLE commcalc.commission_plan DROP COLUMN IF EXISTS mtd_rates;
-- ALTER TABLE commcalc.commission_plan DROP COLUMN IF EXISTS commission_basis;
-- NOTIFY pgrst, 'reload schema';
