-- 249_commission_store_resolution.sql
-- mod-commission · band 200–299 · additive + idempotent + safe to re-run
--
-- ⚠️ MONEY-ADJACENT — READ THIS HEADER BEFORE RUNNING.
--
-- WHAT IT IS FOR
-- commission_engine resolves a rep's MARKET from their raw POS store string with an EXACT, lower-cased
-- lookup against commcalc.store_mapping only:
--     store_market.get(store.lower()) or store_market.get(store.split(" ")[0].lower(), "")
-- The /store-match alias system (commcalc.store_aliases, migration 023 — raw POS string → store_code),
-- which the Daily-Targets store resolver and the Store-Matching UI both already trust, is NEVER
-- consulted. A POS store string that differs at all from store_mapping.store_address therefore yields a
-- BLANK market, and a store-scope assignment (which compares scope_value to the raw POS string the same
-- exact way) can never attach either. That is one of the three bridges behind the owner's 2026-07-28
-- report: 15 sellers listed as "no plan attached" with a blank Market.
--
-- WHAT THIS MIGRATION DOES
-- Adds ONE tenant setting. It seeds nothing and turns nothing on for anybody:
--     commcalc.commission_org_config.store_resolution  text  default 'exact'
--       'exact' — TODAY'S BEHAVIOUR, byte-identical. Every existing row and every tenant that never
--                 touches the setting keeps the store_mapping-only lookup.
--       'alias' — the raw POS store string is additionally resolved through the SHARED chain
--                 (commcalc.store_aliases → store_code → commcalc.store_mapping / storeops.stores) for
--                 the rep's MARKET, and a store-scope assignment may also match the resolved store CODE
--                 or canonical ADDRESS. It is a strict SUPERSET: it can only attach a plan where none
--                 attached before, never detach one.
--
-- 💰 THIS MIGRATION MOVES NO MONEY BY ITSELF. The default is 'exact', and even after a tenant flips the
-- setting nothing changes until the owner runs POST /commcalc/calculate/{period}. Before flipping it,
-- open Commission Plans → 🩺 Plan coverage: every unassigned rep shows a "with alias resolution, plan X
-- would attach by <scope>" preview, and the Stores panel lists exactly which POS strings would start
-- resolving. That preview runs regardless of the setting, so the delta is visible BEFORE it is paid.
--
-- UNTIL THIS RUNS: commission_engine._plan_pay_config falls back to a narrower select and reports
-- store_resolution='exact' — i.e. today's behaviour. The engine's column list is tried widest-first with
-- a narrow fallback precisely so a missing column here cannot make the mig-232 plan_ct_resolution read
-- fail and silently revert a tenant from 'mapped' to 'raw' (which WOULD be a money change).
-- The Plan-coverage alias PREVIEW works without this migration; only the ability to switch the pay path
-- over needs it.
--
-- Run in the Supabase SQL editor. Independent of 248 — either order.

DO $$
BEGIN
  IF to_regclass('commcalc.commission_org_config') IS NULL THEN
    RAISE NOTICE 'commcalc.commission_org_config does not exist yet — run migration 201 first; 249 skipped.';
    RETURN;
  END IF;

  ALTER TABLE commcalc.commission_org_config
    ADD COLUMN IF NOT EXISTS store_resolution text NOT NULL DEFAULT 'exact';

  -- constrain to the two values the engine understands; anything else already degrades to 'exact' in
  -- code, this just stops a bad value being stored in the first place.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'commission_org_config_store_resolution_chk') THEN
    ALTER TABLE commcalc.commission_org_config
      ADD CONSTRAINT commission_org_config_store_resolution_chk
      CHECK (store_resolution IN ('exact', 'alias'));
  END IF;
END $$;

COMMENT ON COLUMN commcalc.commission_org_config.store_resolution IS
  'How a rep''s raw POS store string is resolved to a market and matched to a store-scope commission-plan '
  'assignment. ''exact'' (default) = commcalc.store_mapping exact address/code only (pre-2026-07-28 '
  'behaviour). ''alias'' = also resolve through commcalc.store_aliases → store_code → store_mapping / '
  'storeops.stores. MONEY-ADJACENT: strictly a superset; takes effect on the next Calculate.';

-- RLS posture: backend service role only (AGENT_CONTRACT §5). No anon/authenticated grants.
ALTER TABLE commcalc.commission_org_config ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON commcalc.commission_org_config FROM anon, authenticated;
GRANT ALL ON commcalc.commission_org_config TO service_role;

NOTIFY pgrst, 'reload schema';

-- ── NOT SEEDED ON PURPOSE ───────────────────────────────────────────────────────────────────────
-- Flipping a tenant to 'alias' MOVES MONEY on the next Calculate. Do it from the UI (Multi-month
-- Commission → Tenant pay settings → "Store resolution") after reviewing the Plan-coverage preview, or,
-- with the owner's explicit yes, by hand:
--
-- UPDATE commcalc.commission_org_config SET store_resolution = 'alias'
--  WHERE org_id = '<the tenant>';   -- then run Calculate for the period(s) concerned.
