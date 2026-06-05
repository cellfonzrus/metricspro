-- MIGRATION 004: NEW MODULE TEMPLATE
-- Copy this file when adding any new module
-- Replace 'assets' with your module name
-- This NEVER touches commcalc.* or storeops.* schemas

CREATE SCHEMA IF NOT EXISTS assets;

-- Example tables for Assets / Phone Lending module
CREATE TABLE IF NOT EXISTS assets.phones (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  imei TEXT UNIQUE NOT NULL,
  model TEXT, brand TEXT, color TEXT,
  purchase_price NUMERIC, purchase_date DATE,
  status TEXT DEFAULT 'available' CHECK (status IN ('available','lent','sold','missing')),
  store_code TEXT, notes TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS assets.lending (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  phone_id UUID REFERENCES assets.phones(id),
  borrower_name TEXT, borrower_phone TEXT,
  lent_date DATE NOT NULL,
  expected_return DATE,
  actual_return DATE,
  deposit_amount NUMERIC DEFAULT 0,
  monthly_payment NUMERIC DEFAULT 0,
  total_paid NUMERIC DEFAULT 0,
  status TEXT DEFAULT 'active' CHECK (status IN ('active','returned','overdue','written_off')),
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS assets.payments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  lending_id UUID REFERENCES assets.lending(id),
  amount NUMERIC NOT NULL,
  payment_date DATE NOT NULL,
  payment_method TEXT,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS
DO $$ DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['assets.phones','assets.lending','assets.payments'] LOOP
    BEGIN
      EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
      EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXCEPTION WHEN OTHERS THEN NULL; END;
  END LOOP;
END $$;

GRANT USAGE ON SCHEMA assets TO anon, authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA assets TO anon, authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA assets TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 004 complete — assets schema ready (template)' as status;
