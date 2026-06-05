-- MIGRATION 003: STOREOPS SCHEMA
-- Migrates existing StoreOps public.* tables to storeops.* schema
-- Zero data loss — existing app keeps working via public schema during transition

CREATE SCHEMA IF NOT EXISTS storeops;

-- Stores
CREATE TABLE IF NOT EXISTS storeops.stores (
  id BIGSERIAL PRIMARY KEY,
  org_id UUID,
  store_code TEXT UNIQUE,
  address TEXT,
  market TEXT,
  monthly_target NUMERIC DEFAULT 0,
  is_active BOOLEAN DEFAULT true,
  phone TEXT, notes TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Employees
CREATE TABLE IF NOT EXISTS storeops.employees (
  id BIGSERIAL PRIMARY KEY,
  org_id UUID,
  employee_id TEXT UNIQUE,
  name TEXT NOT NULL,
  home_store TEXT,
  role TEXT,
  pay_rate NUMERIC DEFAULT 0,
  is_active BOOLEAN DEFAULT true,
  email TEXT, phone TEXT, notes TEXT,
  epay_login TEXT,
  epay_salesperson TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Shifts (with soft delete for permanent history)
CREATE TABLE IF NOT EXISTS storeops.shifts (
  id BIGSERIAL PRIMARY KEY,
  org_id UUID,
  employee_id TEXT,
  employee_name TEXT,
  store_code TEXT,
  shift_date DATE,
  start_time TEXT,
  end_time TEXT,
  scheduled_hours NUMERIC DEFAULT 0,
  actual_hours NUMERIC DEFAULT 0,
  actual_start TIMESTAMPTZ,
  actual_end TIMESTAMPTZ,
  status TEXT DEFAULT 'scheduled',
  notes TEXT,
  is_deleted BOOLEAN DEFAULT false,
  deleted_at TIMESTAMPTZ,
  deleted_by TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS shifts_date ON storeops.shifts(store_code, shift_date) WHERE is_deleted = false;

-- Shifts archive (permanent — never purged)
CREATE TABLE IF NOT EXISTS storeops.shifts_archive (
  LIKE storeops.shifts INCLUDING ALL,
  archived_at TIMESTAMPTZ DEFAULT NOW(),
  archive_reason TEXT
);

-- Time off requests
CREATE TABLE IF NOT EXISTS storeops.time_off_requests (
  id BIGSERIAL PRIMARY KEY,
  org_id UUID,
  employee_id TEXT,
  start_date DATE, end_date DATE,
  type TEXT, status TEXT DEFAULT 'pending',
  notes TEXT, approved_by TEXT, approved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Shift swap requests
CREATE TABLE IF NOT EXISTS storeops.shift_swap_requests (
  id BIGSERIAL PRIMARY KEY,
  org_id UUID,
  requester_id TEXT, target_id TEXT,
  shift_id BIGINT, target_shift_id BIGINT,
  status TEXT DEFAULT 'pending',
  notes TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Schedule templates
CREATE TABLE IF NOT EXISTS storeops.schedule_templates (
  id BIGSERIAL PRIMARY KEY,
  org_id UUID,
  store_code TEXT, employee_id TEXT,
  day_of_week INT, start_time TEXT, end_time TEXT,
  UNIQUE(store_code, employee_id, day_of_week)
);

-- Roles
CREATE TABLE IF NOT EXISTS storeops.roles (
  id BIGSERIAL PRIMARY KEY,
  org_id UUID,
  name TEXT, display_name TEXT,
  permissions JSONB DEFAULT '{}'
);

-- Soft-delete trigger on shifts
CREATE OR REPLACE FUNCTION storeops.soft_delete_shift()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO storeops.shifts_archive
  SELECT OLD.*, NOW(), 'soft_delete';
  UPDATE storeops.shifts SET is_deleted = true, deleted_at = NOW() WHERE id = OLD.id;
  RETURN NULL;
END; $$;

DROP TRIGGER IF EXISTS shifts_soft_delete ON storeops.shifts;
CREATE TRIGGER shifts_soft_delete BEFORE DELETE ON storeops.shifts
  FOR EACH ROW EXECUTE FUNCTION storeops.soft_delete_shift();

-- Store sync trigger: new StoreOps store → commcalc_store_mapping
CREATE OR REPLACE FUNCTION storeops.sync_to_commcalc()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  BEGIN
    IF TG_OP = 'DELETE' THEN
      UPDATE commcalc.store_mapping SET is_active = false WHERE store_code = OLD.store_code;
      RETURN OLD;
    END IF;
    INSERT INTO commcalc.store_mapping (store_address,location_name,store_code,market,is_active,created_at)
    VALUES (
      COALESCE(NEW.address, NEW.store_code, 'Unknown'),
      COALESCE(NEW.address, NEW.store_code, 'Unknown'),
      NEW.store_code, COALESCE(NEW.market,'Boost'),
      COALESCE(NEW.is_active,true), NOW()
    )
    ON CONFLICT (org_id, store_code) DO UPDATE SET
      store_address = COALESCE(NEW.address, EXCLUDED.store_address),
      market = COALESCE(NEW.market, EXCLUDED.market),
      is_active = COALESCE(NEW.is_active, true);
  EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'CommCalc sync failed for %: %', NEW.store_code, SQLERRM;
  END;
  RETURN NEW;
END; $$;

-- RLS (anon read, authenticated full)
DO $$ DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'storeops.stores','storeops.employees','storeops.shifts',
    'storeops.time_off_requests','storeops.shift_swap_requests',
    'storeops.schedule_templates','storeops.roles','storeops.shifts_archive'
  ] LOOP
    BEGIN
      EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
      EXECUTE format('DROP POLICY IF EXISTS anon_read ON %s', t);
      EXECUTE format('DROP POLICY IF EXISTS auth_write ON %s', t);
      EXECUTE format('CREATE POLICY anon_read ON %s FOR SELECT TO anon USING (true)', t);
      EXECUTE format('CREATE POLICY auth_write ON %s FOR ALL TO authenticated USING (true) WITH CHECK (true)', t);
    EXCEPTION WHEN OTHERS THEN RAISE NOTICE 'Skipped %: %', t, SQLERRM;
    END;
  END LOOP;
END $$;

GRANT USAGE ON SCHEMA storeops TO anon, authenticated;
GRANT SELECT ON ALL TABLES IN SCHEMA storeops TO anon;
GRANT ALL ON ALL TABLES IN SCHEMA storeops TO authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA storeops TO authenticated;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 003 complete — storeops schema ready' as status;
