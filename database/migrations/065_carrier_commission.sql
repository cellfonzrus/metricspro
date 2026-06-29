-- 065_carrier_commission.sql — GENERIC carrier commission STATEMENT (any carrier: Total/VidaPay, Cricket…).
--
-- WHY: commission is hard-coded to Boost's sales-driven spiff stack today. A statement-driven carrier
-- (Total Wireless via VidaPay) instead EMAILS a commission statement that already contains the per-rep,
-- per-transaction commission — including the per-MONTH installments the carrier computed (1st..6th Month
-- Spiff) plus margins/rebate/MRC. The generic path: map ANY carrier's commission file → this canonical
-- table (via the existing column_mapping engine) → aggregate per rep into rep_commissions. New carrier
-- tomorrow = map their file, no code. Custom labels + which columns count are configured per carrier.
--
-- Additive + idempotent. RLS open_all. (Boost is unaffected — it has no rows here.)

CREATE TABLE IF NOT EXISTS commcalc.carrier_commission (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  carrier_id         UUID,
  period             TEXT, period_month INT, period_year INT,
  trans_date         DATE,
  rep_name           TEXT,                 -- the rep on the statement (Total: "User Name")
  rep_user_id        TEXT,                 -- carrier user id (Total: "User Id")
  store              TEXT,                 -- store / merchant account (Total: MerchantAccountId / BAN)
  account_id         TEXT,
  carrier_name       TEXT,
  activation_type    TEXT,                 -- New ACT / Upgrade / Residual / Protect / …
  sub_type           TEXT,
  sku                TEXT,
  imei               TEXT,
  mdn                TEXT,
  order_id           TEXT,
  -- commission component amounts (any carrier maps the columns it has; the rest stay 0)
  device_margin      NUMERIC DEFAULT 0,
  consumer_margin    NUMERIC DEFAULT 0,
  rebate             NUMERIC DEFAULT 0,
  mrc_net_discount   NUMERIC DEFAULT 0,
  fees_margin        NUMERIC DEFAULT 0,
  spiff_m1           NUMERIC DEFAULT 0,
  spiff_m2           NUMERIC DEFAULT 0,
  spiff_m3           NUMERIC DEFAULT 0,
  spiff_m4           NUMERIC DEFAULT 0,
  spiff_m5           NUMERIC DEFAULT 0,
  spiff_m6           NUMERIC DEFAULT 0,
  residual           NUMERIC DEFAULT 0,
  other_amount       NUMERIC DEFAULT 0,
  total_commission   NUMERIC DEFAULT 0,    -- computed sum of the components on ingest
  raw_row            JSONB,
  created_at         TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS carrier_commission_period ON commcalc.carrier_commission (org_id, period);
CREATE INDEX IF NOT EXISTS carrier_commission_rep    ON commcalc.carrier_commission (org_id, period, rep_name);

-- the aggregated per-rep statement commission, summed into the rep payout by the live calc (additive)
ALTER TABLE commcalc.rep_commissions ADD COLUMN IF NOT EXISTS carrier_statement_comm NUMERIC DEFAULT 0;

ALTER TABLE commcalc.carrier_commission ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='carrier_commission' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.carrier_commission FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;
