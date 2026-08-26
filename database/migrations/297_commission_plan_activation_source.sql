-- 297_commission_plan_activation_source.sql
-- mod-commission · band 200–299 · additive + idempotent + safe to re-run · single-line-safe
--
-- ⚠️ MONEY-ADJACENT — READ THIS HEADER BEFORE RUNNING.
--
-- WHAT IT IS FOR
-- mig 296 added commcalc.commission_org_config.activation_source ('raw_sales' default | 'activation_details')
-- so a tenant could pay activations from the ingested "Activation Details" report instead of from raw_sales.
-- But that switch is ORG-WIDE. The Chicago/Luxelink org (854f6d7b-6590-4e4d-88ab-646f560d4f4c) holds BOTH
-- the NY reps AND 13 Chicago stores in ONE org. The owner wants the report-sourced activations for the NY
-- plan ONLY — flipping the org-wide switch would ZERO every Chicago activation (Chicago is not in the NY-only
-- report). This migration moves the control to the PLAN so it is opt-in per plan, not per org.
--
-- WHAT THIS MIGRATION DOES
-- Adds ONE column on the PLAN. It seeds nothing and turns nothing on for anybody:
--     commcalc.commission_plan.activation_source  text NOT NULL DEFAULT 'inherit'
--       'inherit'            — DEFAULT for every existing + future plan. The plan defers to the ORG-level
--                              commcalc.commission_org_config.activation_source (mig 296), which itself
--                              defaults 'raw_sales'. So every existing plan is BYTE-IDENTICAL: 'inherit' ->
--                              org 'raw_sales' -> today's raw_sales activations, unchanged.
--       'raw_sales'          — this plan ALWAYS classifies activations from the POS raw_sales feed, even if
--                              the ORG-level switch is flipped to 'activation_details'. This is how a
--                              Chicago plan stays on raw_sales while an org-level flip would otherwise catch
--                              it. (With the recommended posture — org left 'raw_sales', only the NY plan
--                              flipped — this is equivalent to 'inherit'; it exists so a plan can PIN raw_sales.)
--       'activation_details' — reps whose EFFECTIVE (paying) plan is THIS plan are paid activations from the
--                              ingested "Activation Details" report (deduped per activation, Upgrade/Other/
--                              Returns excluded, premium/byod), and THAT REP'S raw_sales activations are
--                              suppressed so nothing is double-counted. Scoped to the reps on this plan only —
--                              a rep on any other (inherit/raw_sales) plan is completely unaffected.
--                              Accessories and every non-activation rule keep reading raw_sales. SINGLE
--                              SOURCE per rep. Only flat_per_unit and pct_mrc pay on these lines; pct_gp /
--                              pct_price / pct_price_over_cost are refused ($0, with a note) — the report
--                              carries no cost/price column.
--
-- RESOLUTION ORDER, per rep (commission_engine):
--     the rep's EFFECTIVE plan's activation_source, unless 'inherit', then the org-level
--     commission_org_config.activation_source, then 'raw_sales'.
-- A rep pays under exactly ONE plan (the most-specific assignment wins: employee > role > store > market >
-- default), so the "effective plan" is unambiguous. See the engine's per-rep gate for the multi-assignment
-- rule and the ambiguity flag.
--
-- 💰 THIS MIGRATION MOVES NO MONEY BY ITSELF. Every plan defaults 'inherit', and even after a plan is flipped
-- nothing changes until the owner runs POST /commcalc/calculate/{period}. Compare the Activation Details
-- /activation-counts total for the reps on that plan against their raw_sales activation count BEFORE flipping,
-- so the delta is visible before it is paid.
--
-- UNTIL THIS RUNS: commission_engine reads commission_plan.activation_source defensively (the plan dict is
-- loaded with select("*"), and the engine does p.get("activation_source") with an 'inherit' default), so a
-- missing column degrades every plan to 'inherit' -> the mig-296 org behaviour -> today's behaviour. A
-- missing column here can never silently revert the org-level setting or any other plan/tier column.
--
-- Run in the Supabase SQL editor. Independent of 296 (org column) — any order; the engine handles either
-- column being absent.

DO $$
BEGIN
  IF to_regclass('commcalc.commission_plan') IS NULL THEN
    RAISE NOTICE 'commcalc.commission_plan does not exist yet — run migration 059 first; 297 skipped.';
    RETURN;
  END IF;

  ALTER TABLE commcalc.commission_plan
    ADD COLUMN IF NOT EXISTS activation_source text NOT NULL DEFAULT 'inherit';

  -- constrain to the three values the engine understands; anything else already degrades to 'inherit' in
  -- code, this just stops a bad value being stored in the first place.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'commission_plan_activation_source_chk') THEN
    ALTER TABLE commcalc.commission_plan
      ADD CONSTRAINT commission_plan_activation_source_chk
      CHECK (activation_source IN ('inherit', 'raw_sales', 'activation_details'));
  END IF;
END $$;

COMMENT ON COLUMN commcalc.commission_plan.activation_source IS
  'Where the commission engine takes activations from for the reps whose EFFECTIVE (paying) plan is this '
  'plan. ''inherit'' (default/NULL, byte-identical) = defer to the org-level '
  'commission_org_config.activation_source (mig 296; itself defaults ''raw_sales''). ''raw_sales'' = always '
  'classify this plan''s activations from the POS raw_sales feed, even if the org-level switch is flipped '
  '(pins a plan to raw_sales — e.g. Chicago). ''activation_details'' = pay this plan''s reps'' activations '
  'from the ingested b2b "Activation Details" report (deduped per activation, Upgrade/Other/Returns '
  'excluded, premium/byod) and SUPPRESS THAT REP''S raw_sales activations so nothing is double-counted; '
  'accessories and every non-activation rule keep reading raw_sales. Scoped to the reps on this plan only — '
  'other plans are untouched. MONEY-ADJACENT: takes effect on the next Calculate. Only flat_per_unit and '
  'pct_mrc pay on these lines. mig 297.';

-- ── Grants (explicit — mirror the commission_plan posture set in mig 232; the plan editor UI reads/writes
--    plans over PostgREST as authenticated).
GRANT USAGE ON SCHEMA commcalc TO anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.commission_plan TO anon, authenticated, service_role;

-- RLS posture: commission_plan follows the org-wide open_all policy set at table creation (mig 059) and
-- carried by mig 232; this migration adds a column only and does not change the table's RLS.

NOTIFY pgrst, 'reload schema';

-- ── HOW TO ENABLE IT FOR THE NY PLAN (MOVES MONEY on the next Calculate — do after previewing the delta) ──
-- Leave the org-level switch at its default 'raw_sales' (so Chicago is untouched), and flip ONLY the NY plan:
--
-- UPDATE commcalc.commission_plan
--    SET activation_source = 'activation_details'
--  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
--    AND name  = 'Total Employee Comp NY';   -- the NY plan; confirm the exact name first
-- -- then run Calculate for the period(s) concerned. Chicago plans stay 'inherit' -> org 'raw_sales'.
--
-- To PIN a plan to raw_sales regardless of any future org-level flip (belt-and-braces for Chicago):
-- UPDATE commcalc.commission_plan SET activation_source = 'raw_sales'
--  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' AND name <> 'Total Employee Comp NY';

-- ── REVERT (paste and run to undo — touches no payout number by itself; drops only additive objects) ──
-- ALTER TABLE commcalc.commission_plan
--   DROP CONSTRAINT IF EXISTS commission_plan_activation_source_chk;
-- ALTER TABLE commcalc.commission_plan
--   DROP COLUMN IF EXISTS activation_source;
-- NOTIFY pgrst, 'reload schema';
-- (After revert the engine's p.get("activation_source") falls back to 'inherit' and every plan reads the
--  org-level setting — mig-296 behaviour; with the org left at 'raw_sales' that is today's behaviour.)
