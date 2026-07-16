-- 210_commission_installment_edit_m1gate.sql — EDITABLE sale-triggered installment schedules
--   + a MONTH-1 "paid at activation" gate option. Money-touching (owner directive 2026-07-16).
--   Extends the mig-201 sale-triggered installment engine. ADDITIVE + IDEMPOTENT + BOOST-SAFE.
--
-- BAND: 200-299 (mod-commission). 200-209 are TAKEN (209 = parked what-if package) → this is 210.
--
-- WHAT THIS ADDS (all additive — pre-existing schedules keep behaving EXACTLY as today):
--
--   1. plan_installment_schedule.m1_gate — how month_index 1 confirms it may be paid:
--        'inherit'            (DEFAULT) → month 1 behaves per the existing gate_mode/gate_from_month.
--                                          Byte-identical to pre-mig-210.
--        'activation_payment' → month 1 is GATED on the SALE's OWN activation payment (the customer
--                                          paid their first month at the register), NOT on carrier
--                                          residual. Months 2..N keep the schedule's existing gate.
--      This is DISTINCT from gate_from_month=2 ("month 1 always pays, ungated"): here month 1 IS
--      gated, on the sale's activation payment. See sale_installment_engine._activation_payment_met.
--
--   2. plan_installment_schedule.updated_by — audit: the auth uid of who last edited the schedule.
--      (updated_at already exists from mig 201; the API now stamps it on every write.)
--
--   3. commission_org_config.activation_payment_matcher (JSONB, NULL = engine's seeded default) —
--      per-TENANT config of WHAT COUNTS as "payment received at activation" (RULE TWO: no hard-coded
--      product names/categories/carriers). Shape:
--        {"departments":[...], "categories":[...], "product_keywords":[...],
--         "value_field":"ext_price", "min_amount":0.01}
--      A sale line qualifies when it is a PAYMENT/PLAN/AIRTIME line (dept OR category OR product-desc
--      keyword) AND shows money collected (value_field >= min_amount). Seeded default lives in the
--      engine (DEFAULT_ACTIVATION_PAYMENT_MATCHER); this column overrides it per org.
--
--   4. plan_installment_schedule_audit — compact who/when/what edit trail (before/after JSONB) for
--      the money config. Written best-effort by the API; a missing table never blocks a save.
--
-- DEGRADES GRACEFULLY until run: the engine reads m1_gate via .get() (None → 'inherit'), the matcher
--   loader falls back to the code default when the column is absent, and the write endpoints try/except
--   the audit + updated_by so schedules still save. Run in the Supabase SQL editor.

-- ── 1. m1_gate + updated_by on the schedule ───────────────────────────────────────────────────────
ALTER TABLE commcalc.plan_installment_schedule
  ADD COLUMN IF NOT EXISTS m1_gate    TEXT NOT NULL DEFAULT 'inherit',  -- 'inherit' | 'activation_payment'
  ADD COLUMN IF NOT EXISTS updated_by TEXT;                             -- auth uid of last editor (audit)

-- ── 2. per-tenant activation-payment matcher (RULE TWO configurable) ──────────────────────────────
ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS activation_payment_matcher JSONB;            -- NULL = engine seeded default

-- ── 3. edit audit trail (who / when / what changed) ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.plan_installment_schedule_audit (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,                        -- no house default (XM-5)
  schedule_id  UUID,                                 -- the schedule edited (may be gone after a delete)
  action       TEXT NOT NULL,                        -- 'create' | 'update' | 'delete'
  changed_by   TEXT,                                 -- auth uid (or 'web' when unresolved)
  before_json  JSONB,                                -- schedule + lines snapshot BEFORE the change
  after_json   JSONB,                                -- schedule + lines snapshot AFTER the change
  changed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS plan_installment_schedule_audit_lookup
  ON commcalc.plan_installment_schedule_audit (org_id, schedule_id, changed_at DESC);

-- ── RLS open_all (matches every commcalc table today) ─────────────────────────────────────────────
DO $$
BEGIN
  EXECUTE 'ALTER TABLE commcalc.plan_installment_schedule_audit ENABLE ROW LEVEL SECURITY';
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='plan_installment_schedule_audit' AND policyname='open_all') THEN
    EXECUTE 'CREATE POLICY open_all ON commcalc.plan_installment_schedule_audit FOR ALL USING (true) WITH CHECK (true)';
  END IF;
END $$;

-- ── 5. DUAL-CATEGORY item mapping (owner scope expansion 2026-07-16) ───────────────────────────────
-- Each sales item (commcalc.item_mapping, keyed by item_key = SKU else upper(product_desc)) now carries
-- TWO configurable classification dimensions, IN ADDITION to the existing item_type:
--     sales_category — the MASTER / sales-classification dimension (activation, upgrade, accessory, …)
--     kpi_category   — the KPI-reporting dimension (protection, wireless_home_internet, …)
-- STABLE INTERFACE (a parallel "custom report" package consumes these): read
--   commcalc.item_mapping.item_key, .sales_category, .kpi_category (both nullable; NULL = unassigned)
--   joined to raw_sales / daily_sales_feed by item_key = _item_key(sku, product_desc).
-- The M1 "paid at activation" gate (mig 210 §1) treats an item mapped to sales_category OR kpi_category =
-- 'activation_payment' (with money collected) as a first-month payment — the mapping IS the matcher, with
-- the engine's seeded heuristic (DEFAULT_ACTIVATION_PAYMENT_MATCHER) as the fallback until items are mapped.
ALTER TABLE commcalc.item_mapping
  ADD COLUMN IF NOT EXISTS sales_category TEXT,   -- master/sales-classification dimension (nullable)
  ADD COLUMN IF NOT EXISTS kpi_category   TEXT;   -- KPI-reporting dimension (nullable)
CREATE INDEX IF NOT EXISTS item_mapping_salescat_idx ON commcalc.item_mapping (org_id, sales_category);
CREATE INDEX IF NOT EXISTS item_mapping_kpicat_idx   ON commcalc.item_mapping (org_id, kpi_category);

-- ── 6. per-tenant EDITABLE category value lists for each dimension (RULE TWO — no hard-coded lists) ─
CREATE TABLE IF NOT EXISTS commcalc.item_category_config (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,                       -- no house default (XM-5)
  dimension   TEXT NOT NULL,                        -- 'sales' | 'kpi'
  value       TEXT NOT NULL,                        -- canonical key stored on item_mapping.*_category
  label       TEXT,                                 -- display label
  is_active   BOOLEAN NOT NULL DEFAULT true,
  sort_order  INT NOT NULL DEFAULT 100,
  source      TEXT NOT NULL DEFAULT 'seed',         -- 'seed' | 'manual'
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (org_id, dimension, value)
);
CREATE INDEX IF NOT EXISTS item_category_config_lookup
  ON commcalc.item_category_config (org_id, dimension, is_active);

-- Seed the HOUSE org's default value lists (other tenants are seeded on first read by the backend, so the
-- lists are multi-tenant without touching core's seed_tenant_defaults). Idempotent.
INSERT INTO commcalc.item_category_config (org_id, dimension, value, label, sort_order, source) VALUES
  ('00000000-0000-0000-0000-000000000001','sales','activation','Activation',10,'seed'),
  ('00000000-0000-0000-0000-000000000001','sales','upgrade','Upgrade',20,'seed'),
  ('00000000-0000-0000-0000-000000000001','sales','accessory','Accessory',30,'seed'),
  ('00000000-0000-0000-0000-000000000001','sales','swap','Swap',40,'seed'),
  ('00000000-0000-0000-0000-000000000001','sales','bill_payment','Bill payment',50,'seed'),
  ('00000000-0000-0000-0000-000000000001','sales','rebate','Rebate',60,'seed'),
  ('00000000-0000-0000-0000-000000000001','sales','activation_payment','Activation payment',70,'seed'),
  ('00000000-0000-0000-0000-000000000001','sales','misc_other','Other',80,'seed'),
  ('00000000-0000-0000-0000-000000000001','kpi','protection','Protection',10,'seed'),
  ('00000000-0000-0000-0000-000000000001','kpi','wireless_home_internet','Wireless home internet',20,'seed'),
  ('00000000-0000-0000-0000-000000000001','kpi','activation_payment','Activation payment',30,'seed'),
  ('00000000-0000-0000-0000-000000000001','kpi','accessory','Accessory',40,'seed'),
  ('00000000-0000-0000-0000-000000000001','kpi','plan','Plan',50,'seed'),
  ('00000000-0000-0000-0000-000000000001','kpi','other','Other',60,'seed')
ON CONFLICT (org_id, dimension, value) DO NOTHING;

DO $$
BEGIN
  EXECUTE 'ALTER TABLE commcalc.item_category_config ENABLE ROW LEVEL SECURITY';
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='item_category_config' AND policyname='open_all') THEN
    EXECUTE 'CREATE POLICY open_all ON commcalc.item_category_config FOR ALL USING (true) WITH CHECK (true)';
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 210 complete — plan_installment_schedule.m1_gate + updated_by, '
       'commission_org_config.activation_payment_matcher, plan_installment_schedule_audit, '
       'item_mapping.sales_category/kpi_category, item_category_config (+house seeds)' AS status;
