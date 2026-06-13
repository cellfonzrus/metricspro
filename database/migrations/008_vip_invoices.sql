-- 008_vip_invoices.sql
-- VIP Wireless dealer-portal invoices (scraped via tools/vip_scraper in the commcalc repo).
-- Three tables: invoice headers, line items, and the device (Serial/IMEI/SIM) table that
-- only appears on device-purchase invoices. IMEI is the join key onto the asset reports.
--
-- Run this in the Supabase SQL editor (Claude cannot run SQL).

-- ── Invoice headers (from POST /Invoice/InvoiceList) ─────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.vip_invoices (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL,
  vip_id           BIGINT,                 -- portal internal Id (stable per invoice)
  invoice_number   TEXT,
  order_number     TEXT,
  location         TEXT,                   -- Location = store address on the portal
  company_id       BIGINT,
  email            TEXT,
  status           TEXT,                   -- Open / Paid In Full / Voided
  sub_total        NUMERIC,
  shipping         NUMERIC,
  discount         NUMERIC,
  other_cost       NUMERIC,
  other_deductions NUMERIC,
  tax              NUMERIC,
  grand_total      NUMERIC,
  note             TEXT,
  created_on       TIMESTAMPTZ,            -- invoice date (CreatedOn)
  transaction_date TIMESTAMPTZ,
  due_date         TIMESTAMPTZ,
  period           TEXT,                   -- derived from created_on, e.g. "June 2026"
  period_month     INT,
  period_year      INT,
  created_at       TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(org_id, vip_id)
);
CREATE INDEX IF NOT EXISTS vip_invoices_period   ON commcalc.vip_invoices(org_id, period);
CREATE INDEX IF NOT EXISTS vip_invoices_location ON commcalc.vip_invoices(org_id, location);
CREATE INDEX IF NOT EXISTS vip_invoices_number   ON commcalc.vip_invoices(org_id, invoice_number);

-- ── Line items (from GET /invoicedetails/{Id}, "Name/SKU/Price/Quantity/Total") ──
CREATE TABLE IF NOT EXISTS commcalc.vip_invoice_lines (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  vip_invoice_id BIGINT,                   -- references vip_invoices.vip_id
  invoice_number TEXT,
  location       TEXT,
  status         TEXT,
  created_on     TIMESTAMPTZ,
  name           TEXT,                     -- product / fee name
  note           TEXT,                     -- the .invoice-note description
  sku            TEXT,
  price          NUMERIC,
  quantity       NUMERIC,
  total          NUMERIC,
  period         TEXT,
  period_month   INT,
  period_year    INT,
  created_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS vip_invoice_lines_inv    ON commcalc.vip_invoice_lines(org_id, vip_invoice_id);
CREATE INDEX IF NOT EXISTS vip_invoice_lines_period ON commcalc.vip_invoice_lines(org_id, period);

-- ── Devices (from the Serial Number / Product Name / IMEI / SIM table) ────────
CREATE TABLE IF NOT EXISTS commcalc.vip_invoice_devices (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  vip_invoice_id BIGINT,                   -- references vip_invoices.vip_id
  invoice_number TEXT,
  location       TEXT,
  created_on     TIMESTAMPTZ,
  serial         TEXT,
  product_name   TEXT,
  imei           TEXT,
  sim            TEXT,
  period         TEXT,
  period_month   INT,
  period_year    INT,
  created_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS vip_invoice_devices_imei ON commcalc.vip_invoice_devices(org_id, imei);
CREATE INDEX IF NOT EXISTS vip_invoice_devices_inv  ON commcalc.vip_invoice_devices(org_id, vip_invoice_id);

-- ── RLS + grants (match sibling commcalc tables: blanket open_all) ────────────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'commcalc.vip_invoices','commcalc.vip_invoice_lines','commcalc.vip_invoice_devices'
  ] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
  END LOOP;
END $$;

GRANT USAGE ON SCHEMA commcalc TO anon, authenticated;
GRANT ALL ON commcalc.vip_invoices        TO anon, authenticated;
GRANT ALL ON commcalc.vip_invoice_lines   TO anon, authenticated;
GRANT ALL ON commcalc.vip_invoice_devices TO anon, authenticated;
