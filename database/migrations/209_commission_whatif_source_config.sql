-- 209_commission_whatif_source_config.sql — carrier-agnostic What-If source resolution (RULE TWO)
--
-- WHY: the What-If / Scenario Analysis page (commcalc.whatif) was hard-coded with BOOST comp payment
-- types — the legacy _RATE_DEFAULTS (premium/byod/upgrade flat spiffs + acc/setup rates + KPI-tier knobs)
-- for the employee-payout projector, and raw_mi MI+ATU for the BYOD-residual + carrier-income views. That
-- is one carrier's model baked into an analysis tool. This table makes the What-If SOURCE SELECTION config,
-- not code constants, so the same tool works for every carrier: Boost pays/residuals from ePay (raw_mi),
-- master-agent-fed carriers (Total/VidaPay/Luxelink) from the MA reports (raw_ma_daily_tx +
-- raw_ma_commission), and the residual order-type string ("Postpaid Residual Order") + which numeric
-- column holds the residual $ + the sign normalization are all EDITABLE, never constants.
--
-- Resolution (in whatif.py `_whatif_source_config`): (1) the org's row for the exact carrier_id →
-- (2) the org's mode-default row (carrier_id = nil, carrier_mode = 'boost'|'plan') → (3) the HOUSE
-- mode-default row (every tenant inherits these seeds) → (4) a code fallback per mode. So every tenant
-- gets sensible defaults from the two seeded house rows with NO per-tenant seed, and an admin can override
-- per carrier from the What-If ⚙️ Sources panel (mirrors carrier_kpi_metric / report_pull_map).
--
-- SAFE: additive + idempotent. Nothing existing changes; whatif.py degrades to the SAME code defaults when
-- this table is absent (Boost byte-identical). RLS open_all (matches every commcalc table today).

CREATE TABLE IF NOT EXISTS commcalc.whatif_source_config (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  carrier_id    UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',  -- nil = mode-default row
  carrier_mode  TEXT NOT NULL DEFAULT 'boost',   -- used when carrier_id is nil: 'boost' | 'plan'
  -- BYOD-residual source (What-If tab 2)
  residual_source       TEXT NOT NULL DEFAULT 'boost_mi_atu',  -- boost_mi_atu | ma_daily_tx | none
  residual_order_type   TEXT,                    -- ma_daily_tx: the order-type string, e.g. 'Postpaid Residual Order'
  residual_amount_field TEXT NOT NULL DEFAULT 'merchant_invoice', -- which raw_ma_daily_tx numeric col holds residual $
  residual_sign         TEXT NOT NULL DEFAULT 'as_is',  -- as_is | negate | abs  (negative = income → normalize)
  -- company payout / carrier income source (What-If tab 4)
  income_source         TEXT NOT NULL DEFAULT 'boost_comp_mi_atu', -- boost_comp_mi_atu | ma
  -- per-product retail cost enrichment (degrades gracefully until mig 207 creates the table)
  retail_cost_source    TEXT NOT NULL DEFAULT 'none',   -- none | ma_pr_activation
  is_active     BOOLEAN NOT NULL DEFAULT true,
  notes         TEXT,
  updated_at    TIMESTAMPTZ DEFAULT NOW(),
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, carrier_id, carrier_mode)
);
CREATE INDEX IF NOT EXISTS whatif_source_config_org ON commcalc.whatif_source_config (org_id);

COMMENT ON TABLE commcalc.whatif_source_config IS
  'Config-driven source selection for the carrier-agnostic What-If page. One row per (org, carrier) or a mode-default row (carrier_id=nil). Every tenant inherits the two seeded HOUSE mode-default rows unless it overrides. Editable at /commcalc/whatif ⚙️ Sources. Resolved in commcalc.whatif._whatif_source_config.';

-- Seed the two HOUSE mode-default rows (every tenant inherits them; admins override per carrier).
-- boost mode = today's behavior byte-identical (raw_mi MI+ATU + Comprehensive Comp).
-- plan mode  = master-agent-fed carriers (raw_ma_daily_tx residual + raw_ma_commission M1-M6 + rebate).
INSERT INTO commcalc.whatif_source_config
  (org_id, carrier_id, carrier_mode, residual_source, residual_order_type,
   residual_amount_field, residual_sign, income_source, retail_cost_source, notes)
VALUES
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 'boost',
   'boost_mi_atu', NULL, 'merchant_invoice', 'as_is', 'boost_comp_mi_atu', 'none',
   'House/Boost default — residual from ePay raw_mi (MI+ATU); carrier income from Comprehensive Comp + MI+ATU.'),
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 'plan',
   'ma_daily_tx', 'Postpaid Residual Order', 'merchant_invoice', 'negate', 'ma', 'ma_pr_activation',
   'Master-agent (VidaPay/Total) default — residual from raw_ma_daily_tx "Postpaid Residual Order" rows '
   '(negative = paid to us, sign-normalized to income), joined with raw_ma_commission (M1-M6 + rebate per IMEI/phone).')
ON CONFLICT (org_id, carrier_id, carrier_mode) DO NOTHING;

ALTER TABLE commcalc.whatif_source_config ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='whatif_source_config' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.whatif_source_config FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;
