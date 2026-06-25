-- 047_sales_feed_recon.sql — daily B2B sales feed gets its OWN table (Theme 5 storage discriminator).
--
-- THE PROBLEM: the monthly "Sales Transaction Details" upload (upload_type='sales') and the daily B2B
-- feed (upload_type='daily_sales') BOTH landed in commcalc.raw_sales. The monthly upload replaces the
-- period (delete-by-period then insert), which WIPED any daily-feed rows for that month — so the two
-- could never coexist to be reconciled.
--
-- THE FIX (storage discriminator = a separate table, NOT a source column):
--   raw_sales stays the single AUTHORITATIVE monthly ledger, untouched. Six consumers read it by period
--   with no source filter (commission calculator, P&L/coa, sales_analyzer, discrepancy_engine, the
--   daily_sales_actuals targets RPC, and closing _b2b_day) — a source column would force all six to
--   filter or silently double-count. A separate table keeps them all correct with zero changes.
--   The daily B2B feed lands here instead; the recon (GET /commcalc/sales-recon) compares the two at
--   trans_id grain.
--
-- Same column grain as raw_sales (the daily feed is the same Sales-Transaction-Details shape).
-- Idempotent. Re-running is safe.

CREATE TABLE IF NOT EXISTS commcalc.daily_sales_feed (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  period        TEXT NOT NULL,                 -- month-name label, e.g. 'June 2026' (derived per row)
  period_month  INT,
  period_year   INT,
  store         TEXT,
  salesperson   TEXT,
  user_login    TEXT,
  contract_type TEXT,
  department    TEXT,
  category      TEXT,
  product_desc  TEXT,
  product_id    NUMERIC,
  gp            NUMERIC,
  ext_price     NUMERIC,
  trans_id      TEXT,
  trans_date    DATE,
  mdn           TEXT,
  serial_1      TEXT,
  register      TEXT,
  tender_type   TEXT,
  voided        TEXT,
  trans_type    TEXT,
  customer      TEXT,
  email         TEXT,
  customer_no   TEXT,
  uploaded_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS daily_sales_feed_period ON commcalc.daily_sales_feed (org_id, period);
CREATE INDEX IF NOT EXISTS daily_sales_feed_trans  ON commcalc.daily_sales_feed (org_id, trans_id);
CREATE INDEX IF NOT EXISTS daily_sales_feed_date   ON commcalc.daily_sales_feed (org_id, trans_date);

-- RLS open_all (matches the rest of commcalc.*).
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['commcalc.daily_sales_feed'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;
