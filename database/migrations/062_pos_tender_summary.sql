-- 062_pos_tender_summary.sql — POS "X report" tender summary, for reconciling against the daily closing.
--
-- WHY: the POS end-of-day "X report" lists the day's takings BY TENDER TYPE (cash / credit / debit / …)
-- per store. Auto-importing it (via the email/FTP sweep, like the other reports) gives an authoritative
-- POS-side tender total to reconcile against what employees submit on the daily closing sheet (commcalc.
-- daily_closing) — catching cash/card discrepancies. tender_class normalizes the raw label to cash|card|other.
--
-- Additive + idempotent. RLS open_all.

CREATE TABLE IF NOT EXISTS commcalc.pos_tender_summary (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  close_date    DATE,
  store         TEXT,
  tender_type   TEXT,                         -- raw label from the X report (Cash, Credit, Visa, …)
  tender_class  TEXT,                         -- normalized: 'cash' | 'card' | 'other'
  amount        NUMERIC DEFAULT 0,
  source        TEXT DEFAULT 'x_report',
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, close_date, store, tender_type)
);
CREATE INDEX IF NOT EXISTS pos_tender_summary_lookup ON commcalc.pos_tender_summary (org_id, close_date, store);

ALTER TABLE commcalc.pos_tender_summary ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='pos_tender_summary' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.pos_tender_summary FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;
