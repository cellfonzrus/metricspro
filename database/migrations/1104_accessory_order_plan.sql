-- 1104_accessory_order_plan.sql — THE ACCESSORY ORDER PLAN'S TWO DECLARATIONS (index §COMMCALC-49)
--
-- OWNER ASK 2026-10-29: plan accessory purchases off the last few months of device sales, after a
-- human has said which handset models are CURRENT and which are END OF LIFE, splitting the case
-- order across a declared colour mix (house default: blacks and blues 70%, pinks and reds 30%).
--
-- WHAT THIS DOES, AND WHAT IT DELIBERATELY DOES NOT DO
-- ─────────────────────────────────────────────────────────────────────────────────────────────────
-- It adds COLUMNS to two tables that already exist. It creates NO new table, because both facts
-- already have a home and a sibling table would drift from it within a month:
--
--   · the per-model RULING goes on `commcalc.device_model_alias` (mig 095 / 096), which is already
--     the platform's registry of what we know about a device model — the raw-string → canonical-model
--     map the device forecast reads. One model, one row, one place.
--   · the colour MIX, the attach plan and the default windows go on `commcalc.accessory_config`
--     (mig 208), the org's existing accessory-vocabulary row — the same extension shape mig 1031
--     used for `portout_fraud_rules`.
--
-- NO MONEY MOVES. Every column here is NULL on arrival. The report that reads them plans QUANTITIES
-- TO BUY; it reads no payout, writes no ledger and feeds no P&L line, GP figure, commission or tax
-- number. Until somebody rules on a model the report projects nothing at all and says so, so running
-- this file changes no figure anywhere on the platform — it only makes the declarations storable.
--
-- NOTHING IS SWITCHED ON. The owner's longer-term ask is for this plan to become an automated order
-- fed to the vendor's store (§51's `order_transport` → `shopify_draft_order` → `vendor_customer`).
-- This file wires none of that and turns nothing on.
--
-- ADDITIVE + IDEMPOTENT. Safe to run twice. Run it as ONE statement batch in the Supabase SQL Editor
-- (one Run, which commits itself — never split a BEGIN from its COMMIT across two Runs).
--
-- REVERT:
--   ALTER TABLE commcalc.device_model_alias
--     DROP COLUMN IF EXISTS lifecycle,
--     DROP COLUMN IF EXISTS lifecycle_declared_by,
--     DROP COLUMN IF EXISTS lifecycle_declared_at;
--   ALTER TABLE commcalc.accessory_config
--     DROP COLUMN IF EXISTS accessory_order_case_mix,
--     DROP COLUMN IF EXISTS accessory_order_attach_plan,
--     DROP COLUMN IF EXISTS accessory_order_lookback_months,
--     DROP COLUMN IF EXISTS accessory_order_horizon_months;
--   ALTER TABLE commcalc.device_model_alias DROP CONSTRAINT IF EXISTS dma_lifecycle_declared;
--   (Reverting loses the rulings and the declared mix; the report falls back to the house mix and to
--   reporting every model as awaiting a ruling, which is its correct un-declared behaviour.)

-- ── 1. THE PER-MODEL RULING ──────────────────────────────────────────────────────────────────────
-- NULL means NOBODY HAS RULED, and that is a distinct, load-bearing state: the report refuses to
-- project an un-ruled model rather than treating it as current (which would be the report deciding)
-- or as retired (which would read as a decision to stop ordering). Hence no DEFAULT.
ALTER TABLE commcalc.device_model_alias
  ADD COLUMN IF NOT EXISTS lifecycle             TEXT,         -- 'current' | 'eol' | NULL = not yet ruled
  ADD COLUMN IF NOT EXISTS lifecycle_declared_by TEXT,         -- who said so
  ADD COLUMN IF NOT EXISTS lifecycle_declared_at TIMESTAMPTZ;  -- and when

-- The vocabulary is CLOSED at the database, so a typo cannot become a third silent state that the
-- report would read as "not ruled". NULL stays allowed — it IS the un-ruled state.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'dma_lifecycle_declared') THEN
    ALTER TABLE commcalc.device_model_alias
      ADD CONSTRAINT dma_lifecycle_declared
      CHECK (lifecycle IS NULL OR lifecycle IN ('current', 'eol'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS dma_lifecycle ON commcalc.device_model_alias (org_id, lifecycle);

COMMENT ON COLUMN commcalc.device_model_alias.lifecycle IS
  'Accessory Order Plan (index COMMCALC-49): a human ruling that this device model is still being '
  'sold (current) or is being retired (eol). NULL means nobody has ruled yet, which the report '
  'reports as waiting rather than projecting in either direction. Written by '
  'POST /api/v1/commcalc/accessory-order-plan/lifecycle.';
COMMENT ON COLUMN commcalc.device_model_alias.lifecycle_declared_by IS
  'Who recorded the lifecycle ruling. A planning decision that moves an order has an author.';
COMMENT ON COLUMN commcalc.device_model_alias.lifecycle_declared_at IS
  'When the lifecycle ruling was recorded. A ruling older than the handset range it covers is the '
  'thing a planner needs to see before trusting the order.';

-- ── 2. THE COLOUR MIX, THE ATTACH PLAN AND THE DEFAULT WINDOWS ───────────────────────────────────
-- All four NULL on arrival → the report uses the house defaults the owner stated (70/30 on the case
-- colours, one of each accessory per device sold, a 3-month look-back and a 2-month plan). A tenant
-- that wants something else DECLARES it; per-tenant CODE is what RULE TWO forbids, and a per-tenant
-- config row is not a patch.
ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS accessory_order_case_mix        JSONB,
  ADD COLUMN IF NOT EXISTS accessory_order_attach_plan     JSONB,
  ADD COLUMN IF NOT EXISTS accessory_order_lookback_months INTEGER,
  ADD COLUMN IF NOT EXISTS accessory_order_horizon_months  INTEGER;

COMMENT ON COLUMN commcalc.accessory_config.accessory_order_case_mix IS
  'Accessory Order Plan (index COMMCALC-49): the colour groups a case order is split across, as '
  '[{"key","label","share"}]. Shares may be percentages or fractions; which was meant is decided by '
  'the total, and a total that is not 100% is normalised and reported as normalised rather than '
  'silently rescaled. NULL = the house mix (blacks and blues 70%, pinks and reds 30%).';
COMMENT ON COLUMN commcalc.accessory_config.accessory_order_attach_plan IS
  'How many of each accessory to order per device sold, as [{"key","label","per_device",'
  '"colour_split"}]. NULL = the house plan: one case (split by colour), one screen protector, one '
  'USB-C to USB-C cable and one charging cube per device.';
COMMENT ON COLUMN commcalc.accessory_config.accessory_order_lookback_months IS
  'Default months of sales history the plan averages over. NULL = 3, the owner''s own default. '
  'Clamped to 1..24 by the endpoint, and overridable per request.';
COMMENT ON COLUMN commcalc.accessory_config.accessory_order_horizon_months IS
  'Default months the plan orders for. NULL = 2, the owner''s own default. Clamped to 1..12 by the '
  'endpoint, and overridable per request.';

-- ── 3. REACHABILITY (index §CORE-13 — a migration grants what it touches) ────────────────────────
-- Neither relation is created here, and both already carry their grants from mig 095/096 and 208.
-- Re-stated anyway, idempotently: from 2026-10-30 Supabase stops granting new `public` tables
-- automatically, and the backend reaches every relation through PostgREST. service_role only — the
-- browser never reads either table directly, so an `anon` grant would widen exposure, not fix
-- anything.
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.device_model_alias TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.accessory_config   TO service_role;

-- ── 4. WHAT TO CHECK AFTER RUNNING IT ────────────────────────────────────────────────────────────
-- Expect four accessory_order_* columns and three lifecycle columns, every value NULL:
--
--   SELECT column_name FROM information_schema.columns
--    WHERE table_schema='commcalc' AND table_name='accessory_config'
--      AND column_name LIKE 'accessory_order%' ORDER BY 1;
--
--   SELECT count(*) AS models, count(lifecycle) AS ruled
--     FROM commcalc.device_model_alias WHERE org_id = '00000000-0000-0000-0000-000000000001';
--
-- `ruled` is 0 immediately after this runs, which is correct: the rulings are made on the report's
-- own screen, not by this file.
