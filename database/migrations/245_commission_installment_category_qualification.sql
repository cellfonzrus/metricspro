-- 245_commission_installment_category_qualification.sql — WHICH DEVICE CATEGORIES qualify for the
-- multi-month (sale-triggered) payout, per tenant. Additive + idempotent + safe to re-run.
--
-- OWNER DIRECTIVE 2026-07-27 (verbatim): "the tablet dont qualify for the monthly payout, need a
-- provision to be added in the commision plan, what to exclude and what to include, like phones,
-- tablets, home internet, sim accessories, these will be default checked and the user should remove
-- them as needed, tablet and sim excluded by default".
--
-- WHAT THE ENGINE DOES WITH THIS (sale_installment_engine + installment_category.py):
--   Every multi-month chain (ONE activation) is classified into exactly one device category —
--   phone | tablet | home_internet | sim | accessory | unknown — from the tenant's own rules, then the
--   product catalog (migs 230/231), then the POS Department/Category/Product Desc/SKU columns that
--   raw_sales already stores, then the serial's own shape (14-17 digits = IMEI = a device, 18-22 =
--   ICCID = a SIM). A chain whose category is UNCHECKED pays NOTHING: no ledger row, no withheld flag
--   (it is not held pending residual — it does not qualify), and it is COUNTED + WARNED so the operator
--   sees it in the Run Calculation / preview warnings. Never a silent zero.
--
--   *** DEFAULTS ARE IN CODE, NOT IN DATA. *** installment_category.DEFAULT_QUALIFICATION =
--   phone ON · home_internet ON · accessory ON · TABLET OFF · SIM OFF · unknown ON (an activation we
--   could not classify still pays, loudly, rather than silently disappearing). This migration
--   deliberately does NOT stamp a row on any tenant: every existing tenant picks the owner's defaults
--   up through the config-missing fallback, and a tenant that wants something else saves its own.
--
-- ⚠ READ BEFORE RUNNING (money): with SIM unchecked, a BYOD activation whose receipt carries only a
--   SIM kit + a rate plan (no handset) classifies as `sim` and stops paying its M1..M6 chain. In the
--   owner's July sample those are real activations paying 5% of the PLAN's MRC ($3.25 on $65, $1.50 on
--   $30). If BYOD should keep paying, tick "SIM / SIM kits" back on at
--   /commcalc/plan-installments → Qualifying device categories. Nothing moves until POST /calculate.
--
-- THREE LAYERS (each falls back to the next): schedule → org → code defaults.
--
-- UNTIL THIS RUNS: every read is wrapped and degrades to the code defaults, so the feature is live
-- without it — the migration only makes the settings PERSISTABLE + editable in the UI.

-- ── 1. per-ORG default include/exclude set ────────────────────────────────────────────────────────
ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS installment_category_qualification JSONB;

COMMENT ON COLUMN commcalc.commission_org_config.installment_category_qualification IS
  'Multi-month device-category include/exclude, e.g. {"phone":true,"tablet":false,"home_internet":true,"sim":false,"accessory":true,"unknown":true}. NULL = engine defaults (tablet + sim excluded).';

-- ── 2. per-SCHEDULE override (a plan may pay tablets even when the org default does not) ──────────
ALTER TABLE commcalc.plan_installment_schedule
  ADD COLUMN IF NOT EXISTS qualifying_categories JSONB;

COMMENT ON COLUMN commcalc.plan_installment_schedule.qualifying_categories IS
  'Per-schedule device-category include/exclude (same shape as commission_org_config.installment_category_qualification). NULL/empty = inherit the org setting.';

-- ── 3. tenant-editable classification rules (the built-in rules stay in code as the fallback tail) ─
CREATE TABLE IF NOT EXISTS commcalc.installment_category_rule (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  category_key  TEXT NOT NULL,                      -- phone | tablet | home_internet | sim | accessory
  match_field   TEXT NOT NULL DEFAULT 'product_desc', -- product_desc|department|category|sku|catalog_category|serial_kind
  match_op      TEXT NOT NULL DEFAULT 'contains',    -- contains | equals | word | in
  match_value   TEXT NOT NULL,
  priority      INTEGER NOT NULL DEFAULT 100,        -- LOWER WINS across the activation's lines
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  note          TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by    UUID
);

CREATE INDEX IF NOT EXISTS installment_category_rule_org_idx
  ON commcalc.installment_category_rule (org_id, is_active, priority);

CREATE UNIQUE INDEX IF NOT EXISTS installment_category_rule_uq
  ON commcalc.installment_category_rule (org_id, category_key, match_field, match_op, lower(match_value));

COMMENT ON TABLE commcalc.installment_category_rule IS
  'Per-tenant device-category rules for multi-month qualification. Evaluated BEFORE the built-in rules; the built-ins remain as the fallback tail so one tenant rule can never make everything "unknown".';

-- ── 4. SECURITY — service-role only (Gate-1 N2, 2026-07-27) ──────────────────────────────────────
-- WHY THIS BLOCK EXISTS: a NEW table in commcalc inherits NOTHING. Migration 002's
-- `GRANT ALL ON ALL TABLES IN SCHEMA commcalc TO anon, authenticated` is a ONE-TIME grant over the
-- tables that existed then, and no ALTER DEFAULT PRIVILEGES is relied on anywhere in this repo
-- (mig 232 says so explicitly). So without this block the table ships with **no RLS and no grants** —
-- exactly the shape migration 414 shipped in the people band, which is the lesson being applied here.
--
-- WHY service-role-ONLY rather than the older `open_all` + GRANT-to-anon pattern (migs 230/717):
-- this is tenant PAY CONFIGURATION — the rules decide which activations earn a multi-month payout —
-- so it belongs with the agency config tables, not with the openly-granted reference tables. The
-- precedent is migration 220, which locked all six commcalc.agency_* tables to service-role only and
-- is LIVE; `agency.py` reads `agency_link` through PostgREST against it, which proves the backend
-- connects as service_role (`database.py`: `SUPABASE_SERVICE_KEY or SUPABASE_KEY`). This block also
-- adds the EXPLICIT `GRANT ... TO service_role` that migration 220 left implicit.
--
-- Idempotent: safe to re-run, and safe to run on a database where the table already exists.
ALTER TABLE commcalc.installment_category_rule ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS open_all ON commcalc.installment_category_rule;  -- undo any prior open policy
REVOKE ALL ON commcalc.installment_category_rule FROM anon, authenticated;
GRANT USAGE ON SCHEMA commcalc TO service_role;
GRANT ALL ON commcalc.installment_category_rule TO service_role;

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 245 complete — multi-month device-category qualification '
       '(commission_org_config.installment_category_qualification + plan_installment_schedule.qualifying_categories '
       '+ commcalc.installment_category_rule). Defaults live in code: tablet + SIM excluded.' AS status;
