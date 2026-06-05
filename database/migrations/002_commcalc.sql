-- MIGRATION 002: COMMCALC SCHEMA
-- All CommCalc tables in isolated commcalc schema
-- Adding new module NEVER touches these tables

-- Store mapping (master store list)
CREATE TABLE IF NOT EXISTS commcalc.store_mapping (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  store_code TEXT,
  store_address TEXT NOT NULL,
  city TEXT, state TEXT, market TEXT,
  salesforce_id TEXT, door_id TEXT,
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(org_id, store_code)
);

-- Raw sales from EPay
CREATE TABLE IF NOT EXISTS commcalc.raw_sales (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  period TEXT NOT NULL, period_month INT, period_year INT,
  store TEXT, salesperson TEXT, user_login TEXT,
  department TEXT, category TEXT, product_desc TEXT,
  product_id NUMERIC, gp NUMERIC, ext_price NUMERIC,
  trans_id TEXT, trans_date DATE, contract_type TEXT,
  mdn TEXT, serial_1 TEXT, register TEXT, tender_type TEXT,
  voided TEXT, trans_type TEXT, sku TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS raw_sales_period ON commcalc.raw_sales(org_id, period);

-- Raw payment detail
CREATE TABLE IF NOT EXISTS commcalc.raw_payment_detail (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  period TEXT NOT NULL, period_month INT, period_year INT,
  business_address TEXT, payment_type TEXT, amount NUMERIC,
  mdn TEXT, imei TEXT, payment_date DATE,
  rep_username TEXT, sequence TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS raw_payment_period ON commcalc.raw_payment_detail(org_id, period);

-- Raw MI report
CREATE TABLE IF NOT EXISTS commcalc.raw_mi (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  period TEXT NOT NULL, period_month INT, period_year INT,
  salesforce_id TEXT, actual_mi_payout NUMERIC, actual_atu_payout NUMERIC,
  phone_number TEXT, subscriber_status TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Raw DLAR rep
CREATE TABLE IF NOT EXISTS commcalc.raw_dlar_rep (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  period TEXT NOT NULL, period_month INT, period_year INT,
  rep_name TEXT, store TEXT, atu_pct NUMERIC, protect_pct NUMERIC,
  byod_pct NUMERIC, family_plan_pct NUMERIC, tmr3 NUMERIC,
  aal_conversion NUMERIC, bounty NUMERIC, split TEXT, ga_prepaid NUMERIC,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Raw DLAR store
CREATE TABLE IF NOT EXISTS commcalc.raw_dlar_store (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  period TEXT NOT NULL, period_month INT, period_year INT,
  store_code TEXT, salesforce_id TEXT, address TEXT,
  total_acts INT, port_pct NUMERIC, psa_projected NUMERIC,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Product catalog (device costs)
CREATE TABLE IF NOT EXISTS commcalc.raw_catalog (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  product_id NUMERIC, product_desc TEXT, cost NUMERIC, sku TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Payment category mappings
CREATE TABLE IF NOT EXISTS commcalc.payment_categories (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  description TEXT NOT NULL,
  category TEXT NOT NULL CHECK (category IN ('Commission','Re-imbursement','MDF','Chargeback')),
  is_active BOOLEAN DEFAULT true,
  UNIQUE(org_id, description)
);

-- Commission settings per period
CREATE TABLE IF NOT EXISTS commcalc.payout_config (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  period TEXT NOT NULL,
  upgrade_flat NUMERIC DEFAULT 20, premium_flat NUMERIC DEFAULT 5,
  byod_flat NUMERIC DEFAULT 3, byod_extra_spiff NUMERIC DEFAULT 0,
  trade_in_spiff NUMERIC DEFAULT 20, acima_spiff NUMERIC DEFAULT 25,
  acc_rate NUMERIC DEFAULT 0.10, setup_fee_rate NUMERIC DEFAULT 0.10,
  kpi_atu_target NUMERIC DEFAULT 55, kpi_protect_target NUMERIC DEFAULT 80,
  kpi_boostapp_target NUMERIC DEFAULT 65, kpi_familyplan_target NUMERIC DEFAULT 45,
  kpi_byod_target NUMERIC DEFAULT 35, kpi_tmr3_target NUMERIC DEFAULT 70,
  kpi_aal_target NUMERIC DEFAULT 5,
  tier_100_min_kpis INT DEFAULT 7, tier_75_min_kpis INT DEFAULT 5,
  tier_75_pct NUMERIC DEFAULT 0.75, tier_50_pct NUMERIC DEFAULT 0.50,
  straight_line BOOLEAN DEFAULT false,
  acc_target_enabled BOOLEAN DEFAULT false, acc_target_pct NUMERIC DEFAULT 0.10,
  custom_spiffs JSONB DEFAULT '[]',
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(org_id, period)
);

-- Calculated rep commissions
CREATE TABLE IF NOT EXISTS commcalc.rep_commissions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  period TEXT NOT NULL, period_month INT, period_year INT,
  epay_salesperson TEXT, storeops_name TEXT, store TEXT,
  tier NUMERIC, tier_source TEXT, kpis_met INT, total_kpis INT,
  kpi_values JSONB,
  premium_acts INT, byod_acts INT, upgrade_acts INT,
  premium_comm NUMERIC, byod_comm NUMERIC, upgrade_comm NUMERIC,
  acc_comm NUMERIC, setup_fee_comm NUMERIC, trade_in_comm NUMERIC,
  acima_comm NUMERIC DEFAULT 0, custom_comm NUMERIC DEFAULT 0,
  acc_target NUMERIC DEFAULT 0,
  subtotal NUMERIC, total_payout NUMERIC,
  boost_commission NUMERIC, boost_reimbursement NUMERIC,
  calculated_by TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS rep_comm_period ON commcalc.rep_commissions(org_id, period);

-- Store KPIs
CREATE TABLE IF NOT EXISTS commcalc.store_kpis (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  period TEXT NOT NULL, period_month INT, period_year INT,
  store_code TEXT, store_address TEXT, market TEXT,
  atu_pct NUMERIC, protect_pct NUMERIC, byod_pct NUMERIC,
  family_plan_pct NUMERIC, tmr3 NUMERIC, psa_projected NUMERIC, port_pct NUMERIC,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Flags
CREATE TABLE IF NOT EXISTS commcalc.flags (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  period TEXT NOT NULL, period_month INT, period_year INT,
  flag_type TEXT NOT NULL, source TEXT, severity TEXT,
  store_address TEXT, epay_salesperson TEXT,
  mdn TEXT, imei TEXT, amount NUMERIC,
  description TEXT, coaching_note TEXT,
  action_taken TEXT, reviewed_by TEXT, reviewed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS flags_period ON commcalc.flags(org_id, period);

-- Store expenses
CREATE TABLE IF NOT EXISTS commcalc.store_expenses (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  period TEXT NOT NULL, store_code TEXT NOT NULL,
  expense_name TEXT NOT NULL, expense_type TEXT, amount NUMERIC DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Name map (EPay login → StoreOps name)
CREATE TABLE IF NOT EXISTS commcalc.name_map (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  epay_login TEXT, epay_salesperson TEXT, storeops_name TEXT,
  confirmed BOOLEAN DEFAULT false,
  UNIQUE(org_id, epay_login)
);

-- Calc status
CREATE TABLE IF NOT EXISTS commcalc.calc_status (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  period TEXT NOT NULL,
  calc_status TEXT DEFAULT 'pending',
  calc_finished_at TIMESTAMPTZ,
  save_errors JSONB,
  UNIQUE(org_id, period)
);

-- RSK activations
CREATE TABLE IF NOT EXISTS commcalc.rsk_activations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  period TEXT, store_code TEXT, rep_name TEXT,
  mdn TEXT, imei TEXT, act_date DATE, contract_type TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Open RLS for all commcalc tables (org_id scoped via API)
DO $$ DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'commcalc.store_mapping','commcalc.raw_sales','commcalc.raw_payment_detail',
    'commcalc.raw_mi','commcalc.raw_dlar_rep','commcalc.raw_dlar_store',
    'commcalc.raw_catalog','commcalc.payment_categories','commcalc.payout_config',
    'commcalc.rep_commissions','commcalc.store_kpis','commcalc.flags',
    'commcalc.store_expenses','commcalc.name_map','commcalc.calc_status',
    'commcalc.rsk_activations'
  ] LOOP
    BEGIN
      EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
      EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
      EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXCEPTION WHEN OTHERS THEN RAISE NOTICE 'Skipped %: %', t, SQLERRM;
    END;
  END LOOP;
END $$;

GRANT USAGE ON SCHEMA commcalc TO anon, authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA commcalc TO anon, authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA commcalc TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 002 complete — commcalc schema ready' as status;
