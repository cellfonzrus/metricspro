-- 018_store_borrowings.sql — inter-store borrowed-money tracking (#6 / roadmap 6a).
-- When a store funds asset purchases with money BORROWED from another store, the user logs
-- it here. Each borrowing is a debt: borrower_store owes lender_store `amount` as of
-- borrowed_date. Paybacks are recorded in store_borrowing_payments; outstanding per loan =
-- amount - SUM(payments). The reconciliation report nets these into who-owes-whom-how-much.
-- (Same-company funds create no debt and are not stored.)
CREATE TABLE IF NOT EXISTS commcalc.store_borrowings (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  borrower_store TEXT NOT NULL,           -- store that borrowed (owes the money)
  lender_store   TEXT NOT NULL,           -- store it borrowed from (is owed)
  market         TEXT,                    -- borrower's market, for filtering
  amount         NUMERIC NOT NULL,        -- amount borrowed
  borrowed_date  DATE NOT NULL,
  note           TEXT,
  created_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS store_borrowings_org_idx ON commcalc.store_borrowings(org_id);

CREATE TABLE IF NOT EXISTS commcalc.store_borrowing_payments (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  borrowing_id  UUID NOT NULL REFERENCES commcalc.store_borrowings(id) ON DELETE CASCADE,
  amount        NUMERIC NOT NULL,         -- amount paid back
  paid_date     DATE NOT NULL,
  note          TEXT,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS store_borrowing_payments_loan_idx ON commcalc.store_borrowing_payments(borrowing_id);

ALTER TABLE commcalc.store_borrowings         ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.store_borrowing_payments ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY open_all ON commcalc.store_borrowings         FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN OTHERS THEN NULL; END $$;
DO $$ BEGIN
  CREATE POLICY open_all ON commcalc.store_borrowing_payments FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN OTHERS THEN NULL; END $$;
GRANT ALL ON commcalc.store_borrowings         TO anon, authenticated, service_role;
GRANT ALL ON commcalc.store_borrowing_payments TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 018 complete — commcalc.store_borrowings + store_borrowing_payments ready' AS status;
