-- 014_vip_paygo.sql — VIP asset-lending (PayGo / Pay-As-You-Go) weekly payment ledger.
--
-- The VIP dealer portal bills lent ("asset-lending") devices on a weekly PayGo cycle. Each
-- weekly batch is one payment with N invoices and a grand total. We scrape:
--   - /PaygoPayment/PendingPaymentList   -> the CURRENT week's owed batch (Status = pending)
--   - /PaygoPayment/ApprovedPaymentList  -> the weekly history (Status = approved/complete)
--   - /account/paygo/payments/details/{Id} -> the invoice numbers inside each batch
-- The batch invoice numbers join to vip_invoices.invoice_number (and thus
-- vip_invoice_devices for IMEIs), so the asset-lending ledger reconciles against the
-- already-scraped invoices and against the asset_ledger Friday-billing computation.
--
-- These are REPORT tables (frontend-readable, blanket open_all — same as 008_vip_invoices),
-- written by the backend sweep via service_role. Run this in the Supabase SQL editor.

CREATE TABLE IF NOT EXISTS commcalc.vip_paygo_payments (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  vip_payment_id  BIGINT NOT NULL,          -- portal payment Id (unique per org)
  batch_type      TEXT,                     -- 'pending' (current owed) | 'approved' (history)
  dealer          TEXT,                     -- "Company (full address)" as shown on the portal
  created_on      DATE,                     -- the weekly batch date (MM/DD/YYYY on portal)
  invoice_count   INT,
  amount          NUMERIC,                  -- batch grand total (owed/paid this week)
  amount_overdue  NUMERIC,
  status          TEXT,                     -- portal status text (Complete, etc.)
  period          TEXT, period_month INT, period_year INT,
  swept_at        TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS vip_paygo_payments_uniq  ON commcalc.vip_paygo_payments(org_id, vip_payment_id);
CREATE INDEX IF NOT EXISTS vip_paygo_payments_period ON commcalc.vip_paygo_payments(org_id, period);
CREATE INDEX IF NOT EXISTS vip_paygo_payments_date   ON commcalc.vip_paygo_payments(org_id, created_on);

CREATE TABLE IF NOT EXISTS commcalc.vip_paygo_payment_invoices (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  vip_payment_id  BIGINT NOT NULL,
  invoice_number  TEXT,                     -- joins commcalc.vip_invoices.invoice_number
  dealer          TEXT,                     -- per-invoice door (the batch spans many doors)
  created_on      DATE,                     -- the batch date (denormalized for filtering)
  swept_at        TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS vip_paygo_pinv_uniq ON commcalc.vip_paygo_payment_invoices(org_id, vip_payment_id, invoice_number);
CREATE INDEX IF NOT EXISTS vip_paygo_pinv_inv   ON commcalc.vip_paygo_payment_invoices(org_id, invoice_number);
CREATE INDEX IF NOT EXISTS vip_paygo_pinv_pay   ON commcalc.vip_paygo_payment_invoices(org_id, vip_payment_id);

-- ── RLS + grants (match sibling commcalc report tables: blanket open_all) ────────────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['commcalc.vip_paygo_payments', 'commcalc.vip_paygo_payment_invoices'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
  END LOOP;
END $$;

GRANT USAGE ON SCHEMA commcalc TO anon, authenticated;
GRANT ALL ON commcalc.vip_paygo_payments         TO anon, authenticated;
GRANT ALL ON commcalc.vip_paygo_payment_invoices TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 014 complete — VIP PayGo (asset-lending) ledger ready' as status;
