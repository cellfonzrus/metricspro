-- 034_cash_pickup.sql
-- DM cash-envelope pickup tracking + the recipient to notify after each pickup.
-- One pickup row per (date, store, rep) envelope. Survives closing-sheet re-uploads (keyed by the
-- logical envelope, not the daily_closing row id, which is replaced on re-sync). store_code /
-- employee_name default '' so the UNIQUE upsert key never trips on NULLs.

CREATE TABLE IF NOT EXISTS commcalc.cash_pickup (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  close_date    DATE NOT NULL,
  store_code    TEXT NOT NULL DEFAULT '',
  store_name    TEXT,
  employee_name TEXT NOT NULL DEFAULT '',
  amount        NUMERIC DEFAULT 0,          -- cash collected snapshot (store_cash + epay_cash)
  picked_up     BOOLEAN DEFAULT true,
  picked_up_by  TEXT,                       -- DM name
  picked_up_at  TIMESTAMPTZ DEFAULT now(),
  note          TEXT,
  created_at    TIMESTAMPTZ DEFAULT now(),
  UNIQUE (org_id, close_date, store_code, employee_name)
);
CREATE INDEX IF NOT EXISTS cash_pickup_date ON commcalc.cash_pickup (org_id, close_date);

-- Who receives the post-pickup notification (email + WhatsApp). User-configurable in the UI.
CREATE TABLE IF NOT EXISTS commcalc.cash_pickup_config (
  org_id             UUID PRIMARY KEY,
  recipient_name     TEXT,
  recipient_email    TEXT,
  recipient_whatsapp TEXT,                  -- 10-digit or +country-code number
  notify_email       BOOLEAN DEFAULT true,
  notify_whatsapp    BOOLEAN DEFAULT true,
  updated_at         TIMESTAMPTZ DEFAULT now()
);

-- RLS: blanket open_all to match sibling commcalc tables.
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['commcalc.cash_pickup', 'commcalc.cash_pickup_config'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;
