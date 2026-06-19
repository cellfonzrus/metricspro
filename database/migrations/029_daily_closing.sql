-- 029_daily_closing.sql — DM store-visit Phase 3: the daily closing sheet.
-- Run this in the Supabase SQL editor (Claude cannot run SQL).
--
-- Mirrors the existing Google "Envelopes Data (Responses)" sheet: ONE row per rep per day
-- (cash/card split for store + ePay, accessory sales, Zelle/CashApp/other, activation/upgrade
-- counts, envelope-photo link, remarks). The DM verifies each evening that every employee
-- submitted, enters the per-store totals, and the totals reconcile against B2B actual daily sales
-- (commcalc.daily_sales_actuals). Today the data is uploaded from the sheet; later it switches to
-- in-app entry (this schema supports both via the `source` column + manual row endpoints).

-- ── One row per rep per day (the closing-sheet line) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.daily_closing (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  period          TEXT,                      -- YYYY-MM (derived from close_date)
  close_date      DATE,
  submitted_at    TIMESTAMPTZ,               -- the sheet "Timestamp"
  sfid            TEXT,                       -- Salesforce store id (joins store_mapping.salesforce_id)
  store_name      TEXT,                       -- short name as typed on the sheet
  store_code      TEXT,                       -- resolved from sfid
  store_address   TEXT,                       -- resolved canonical address
  employee_name   TEXT,
  store_cash      NUMERIC DEFAULT 0,
  store_cc        NUMERIC DEFAULT 0,
  epay_cash       NUMERIC DEFAULT 0,
  epay_cc         NUMERIC DEFAULT 0,
  acc_sale        NUMERIC DEFAULT 0,
  other_account   NUMERIC DEFAULT 0,          -- Zelle / CashApp / other
  upgrade_count   INT DEFAULT 0,
  new_line_count  INT DEFAULT 0,
  postpaid_count  INT DEFAULT 0,
  envelope_picture TEXT,                       -- drive link to the deposit-envelope photo
  remarks         TEXT,
  source          TEXT NOT NULL DEFAULT 'sheet_upload',  -- sheet_upload | manual
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS daily_closing_date  ON commcalc.daily_closing(org_id, close_date);
CREATE INDEX IF NOT EXISTS daily_closing_store ON commcalc.daily_closing(org_id, store_code, close_date);

-- ── DM evening verification + entered totals, per store per day ───────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.daily_closing_verification (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  close_date      DATE NOT NULL,
  store_code      TEXT NOT NULL,
  store_name      TEXT,
  verified        BOOLEAN DEFAULT false,
  verified_by     TEXT,
  verified_at     TIMESTAMPTZ,
  dm_store_cash   NUMERIC,                    -- DM-entered totals across all rep rows
  dm_store_cc     NUMERIC,
  dm_epay_cash    NUMERIC,
  dm_epay_cc      NUMERIC,
  dm_acc_sale     NUMERIC,
  dm_other        NUMERIC,
  note            TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, close_date, store_code)
);

-- ── RLS: open_all (report tables; backend uses the service key) ───────────────────────────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['daily_closing','daily_closing_verification']
  LOOP
    EXECUTE format('ALTER TABLE commcalc.%I ENABLE ROW LEVEL SECURITY', t);
    BEGIN
      EXECUTE format('CREATE POLICY open_all ON commcalc.%I FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXCEPTION WHEN OTHERS THEN NULL; END;
    EXECUTE format('GRANT ALL ON commcalc.%I TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 029 complete — commcalc.daily_closing + daily_closing_verification ready' AS status;
