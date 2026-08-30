-- 299_mtd_commission_payout.sql
-- mod-commission · band 200–299 · additive + idempotent + safe to re-run.
--
-- WHAT IT IS FOR (owner 2026-08-30): the "Calculate from Executive MTD" panel computes each rep's commission
-- from the Exec MTD numbers (per-category activation rate + Acc. Sales × %). The owner wants to SAVE that
-- result — the recorded commission derived from Executive MTD for a plan + period — WITHOUT changing the
-- rules engine or the /calculate path. This table is that record: the owner presses Save on the panel and
-- the per-rep amounts are upserted here.
--
-- 💰 STANDALONE RECORD. Nothing in the /calculate rep_commissions path reads or writes this table, so saving
-- here changes no existing payout and a recalculation never touches it. It is the saved Exec-MTD commission
-- basis, kept alongside everything else exactly as it was.

CREATE TABLE IF NOT EXISTS commcalc.mtd_commission_payout (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  period         text NOT NULL,
  plan_id        uuid NOT NULL,
  rep            text NOT NULL,
  activations    integer NOT NULL DEFAULT 0,
  acc_sales      numeric NOT NULL DEFAULT 0,
  activation_pay numeric NOT NULL DEFAULT 0,
  accessory_pay  numeric NOT NULL DEFAULT 0,
  commission     numeric NOT NULL DEFAULT 0,
  by_category    jsonb,
  rate_map       jsonb,
  accessory_pct  numeric,
  saved_at       timestamptz DEFAULT now(),
  saved_by       text,
  UNIQUE (org_id, period, plan_id, rep)
);
CREATE INDEX IF NOT EXISTS mtd_commission_payout_lookup
  ON commcalc.mtd_commission_payout (org_id, period, plan_id);

ALTER TABLE commcalc.mtd_commission_payout ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                  AND tablename='mtd_commission_payout' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.mtd_commission_payout FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

GRANT USAGE ON SCHEMA commcalc TO anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.mtd_commission_payout TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
