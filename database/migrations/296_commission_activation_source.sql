-- 296_commission_activation_source.sql
-- mod-commission · band 200–299 · additive + idempotent + safe to re-run · single-line-safe
--
-- ⚠️ MONEY-ADJACENT — READ THIS HEADER BEFORE RUNNING.
--
-- WHAT IT IS FOR
-- commission_engine derives every rep's ACTIVATIONS from the POS `raw_sales` feed (contract_type ->
-- premium/byod/upgrade via the shared classifier). A b2bsoft tenant now also ingests the b2b
-- "Activation Details" custom report, which is the ACTIVATION BASIS OF TRUTH for that carrier (one row
-- per distinct device, insurance/Plan-Option lines collapsed, Returns/cancelled excluded — the exact
-- population /activation-counts already computes). This migration lets a tenant OPT IN to paying
-- activations FROM that report instead of from raw_sales.
--
-- WHAT THIS MIGRATION DOES
-- Adds ONE tenant setting. It seeds nothing and turns nothing on for anybody:
--     commcalc.commission_org_config.activation_source  text  default 'raw_sales'
--       'raw_sales'          — TODAY'S BEHAVIOUR, byte-identical. Every existing row, every NULL, and
--                              every tenant that never touches the setting keeps classifying activations
--                              from raw_sales exactly as before.
--       'activation_details' — activations are paid from the ingested "Activation Details" report:
--                              Detail lines (deduped per activation, Upgrade/Other/Returns excluded,
--                              mapped to activation_bucket premium/byod) are the ONLY source an
--                              activation_bucket rule matches, and raw_sales' own activation_bucket is
--                              SUPPRESSED so no activation is counted twice. Accessories and every
--                              non-activation rule keep reading raw_sales unchanged. SINGLE SOURCE — no
--                              union, no double-count. Only flat_per_unit (the per-activation $) and
--                              pct_mrc (the report carries MRC) are payable on these lines; pct_gp /
--                              pct_price / pct_price_over_cost are refused ($0, with a note) because the
--                              report has no cost/price column.
--
-- 💰 THIS MIGRATION MOVES NO MONEY BY ITSELF. The default is 'raw_sales', and even after a tenant flips
-- the setting nothing changes until the owner runs POST /commcalc/calculate/{period}. Compare the
-- Activation Details /activation-counts total against the raw_sales activation count for the period
-- BEFORE flipping, so the delta is visible before it is paid.
--
-- UNTIL THIS RUNS: commission_engine._plan_pay_config tries the column list widest-first and falls back to
-- the narrower selects, so a missing activation_source column degrades to 'raw_sales' (today's behaviour)
-- WITHOUT making the mig-232 (plan_ct_resolution) / mig-249 (store_resolution) reads fail — a missing
-- column here can never silently revert either of those money settings.
--
-- Run in the Supabase SQL editor. Independent of 232 / 249 — any order.

DO $$
BEGIN
  IF to_regclass('commcalc.commission_org_config') IS NULL THEN
    RAISE NOTICE 'commcalc.commission_org_config does not exist yet — run migration 201 first; 296 skipped.';
    RETURN;
  END IF;

  ALTER TABLE commcalc.commission_org_config
    ADD COLUMN IF NOT EXISTS activation_source text NOT NULL DEFAULT 'raw_sales';

  -- constrain to the two values the engine understands; anything else already degrades to 'raw_sales' in
  -- code, this just stops a bad value being stored in the first place.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'commission_org_config_activation_source_chk') THEN
    ALTER TABLE commcalc.commission_org_config
      ADD CONSTRAINT commission_org_config_activation_source_chk
      CHECK (activation_source IN ('raw_sales', 'activation_details'));
  END IF;
END $$;

COMMENT ON COLUMN commcalc.commission_org_config.activation_source IS
  'Where the commission engine takes a rep''s ACTIVATIONS from. ''raw_sales'' (default/NULL) = classify '
  'activations from the POS raw_sales feed (pre-2026-08-26 behaviour, byte-identical). '
  '''activation_details'' = pay activations from the ingested b2b "Activation Details" custom report '
  '(deduped per activation, Upgrade/Other/Returns excluded, premium/byod), and SUPPRESS raw_sales '
  'activations so nothing is double-counted; accessories and every non-activation rule keep reading '
  'raw_sales. MONEY-ADJACENT: takes effect on the next Calculate. Only flat_per_unit and pct_mrc pay on '
  'these lines. mig 296.';

-- RLS posture: backend service role only (AGENT_CONTRACT §5). No anon/authenticated grants.
ALTER TABLE commcalc.commission_org_config ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON commcalc.commission_org_config FROM anon, authenticated;
GRANT ALL ON commcalc.commission_org_config TO service_role;

NOTIFY pgrst, 'reload schema';

-- ── NOT SEEDED ON PURPOSE ───────────────────────────────────────────────────────────────────────
-- Flipping a tenant to 'activation_details' MOVES MONEY on the next Calculate. Do it from the UI
-- (Multi-month Commission → Tenant pay settings → "Activation source") after comparing /activation-counts
-- to the raw_sales activation count, or, with the owner's explicit yes, by hand:
--
-- UPDATE commcalc.commission_org_config SET activation_source = 'activation_details'
--  WHERE org_id = '<the tenant>';   -- then run Calculate for the period(s) concerned.

-- ── REVERT (paste and run to undo — touches no payout number by itself; drops only additive objects) ──
-- ALTER TABLE commcalc.commission_org_config
--   DROP CONSTRAINT IF EXISTS commission_org_config_activation_source_chk;
-- ALTER TABLE commcalc.commission_org_config
--   DROP COLUMN IF EXISTS activation_source;
-- NOTIFY pgrst, 'reload schema';
-- (After revert the engine's widest-first select falls back automatically and every tenant reads
--  'raw_sales' — today's behaviour.)
