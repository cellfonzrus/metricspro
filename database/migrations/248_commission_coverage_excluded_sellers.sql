-- 248_commission_coverage_excluded_sellers.sql
-- mod-commission · band 200–299 · additive + idempotent + safe to re-run
--
-- WHAT IT IS FOR
-- The Commission-Plans "Plan coverage" panel lists every seller with sales but NO plan attached. On the
-- owner's 2026-07-28 report that list contained POS artifacts — "Office, Back" and "ar, Rush" — which are
-- a till / back-office login, not commissionable people. They can never be assigned a plan, so they sat
-- permanently at the top of a list the owner is meant to drive to zero.
--
-- This adds a TENANT-CONFIGURABLE list of such sellers (RULE TWO: config table + admin UI, never a
-- hard-coded name list), plus an optional override for the "looks like a POS artifact" word hints the
-- diagnosis uses to SUGGEST candidates.
--
-- 💰 IT CANNOT MOVE A NUMBER. The engine reads `coverage_excluded_sellers` ONLY inside
-- commission_engine.preview(coverage=True) while building the diagnostic panel — the payout loop never
-- sees it. An excluded seller has no plan attached, so they pay $0 with or without this list, and they
-- are still REPORTED (in a collapsed "excluded" note) rather than hidden.
--
-- UNTIL THIS RUNS: GET /commcalc/commission-plans/coverage-excluded returns an empty list with a
-- "run migration 248" note, PUT returns a 500 naming this file, and the coverage panel behaves exactly
-- as it does today (nothing is excluded). Nothing else changes and nothing 500s.
--
-- Run in the Supabase SQL editor. Independent of 249 — either order.

-- commission_org_config is created by 201_commission_zero_core.sql; guard so this file is standalone-safe.
DO $$
BEGIN
  IF to_regclass('commcalc.commission_org_config') IS NULL THEN
    RAISE NOTICE 'commcalc.commission_org_config does not exist yet — run migration 201 first; 248 skipped.';
    RETURN;
  END IF;

  -- confirmed POS-artifact sellers: ["Office, Back", "ar, Rush"] (stored as written in raw_sales;
  -- the engine compares them name-order-insensitively via _canon_person).
  ALTER TABLE commcalc.commission_org_config
    ADD COLUMN IF NOT EXISTS coverage_excluded_sellers jsonb;

  -- optional per-tenant override of the artifact WORD hints (default list lives in
  -- commission_engine.DEFAULT_ARTIFACT_HINTS). NULL/empty = use the code default.
  ALTER TABLE commcalc.commission_org_config
    ADD COLUMN IF NOT EXISTS coverage_artifact_hints jsonb;
END $$;

COMMENT ON COLUMN commcalc.commission_org_config.coverage_excluded_sellers IS
  'jsonb array of raw_sales.salesperson strings this tenant has confirmed are NOT commissionable sellers '
  '(POS tills / back-office logins). DIAGNOSTIC ONLY — read exclusively by '
  'commission_engine.preview(coverage=True); it cannot change any payout.';
COMMENT ON COLUMN commcalc.commission_org_config.coverage_artifact_hints IS
  'jsonb array of lower-cased words that make a "seller" name look like a POS artifact. NULL/empty = the '
  'engine default (commission_engine.DEFAULT_ARTIFACT_HINTS). Hint only — nothing is auto-excluded.';

-- RLS posture: this table is reached only through the backend service role (AGENT_CONTRACT §5).
-- No anon/authenticated grants, no open policies. Re-asserted here because the column set changed.
ALTER TABLE commcalc.commission_org_config ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON commcalc.commission_org_config FROM anon, authenticated;
GRANT ALL ON commcalc.commission_org_config TO service_role;

NOTIFY pgrst, 'reload schema';

-- ── OPTIONAL, OWNER-CONFIRMED SEED (do NOT paste blind — it is per tenant) ──────────────────────
-- The house org's two artifacts from the 2026-07-28 report. Uncomment only after confirming these are
-- genuinely not people. The same thing is doable from the UI (Plan coverage → "not a seller").
--
-- UPDATE commcalc.commission_org_config
--    SET coverage_excluded_sellers = '["Office, Back", "ar, Rush"]'::jsonb
--  WHERE org_id = '00000000-0000-0000-0000-000000000001';
