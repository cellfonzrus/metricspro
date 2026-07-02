-- 083_total_processor_sources.sql — Total Wireless payment-processor reports + multi-source registry
--
-- WHY: Boost's MI/ATU come from ePay, but Total's come from a DIFFERENT payment processor
-- (VidaPay / Total Access "Master Agent" portal). This adds (1) raw tables for the three MA
-- reports the dealer receives (Commission Details = the MI-equivalent per-activation detail incl.
-- 1st–6th month spiffs + MRC Net Discount; Daily Tx = the ATU-equivalent airtime margin;
-- Marketplace Handset Fulfillment Orders), and (2) commcalc.data_source — the registry modelling
-- the real-world shape: one company → N distributors → N payment processors per distributor →
-- N LOGINS per processor (one row per login). All report rows land in the SAME tables stamped
-- with org/carrier/source, so multiple sources combine into one database.
--
-- SAFE: additive + idempotent. Nothing existing changes; Boost paths untouched.

-- ── 1. MA - Commission Details (per-activation commission detail; negative = paid to dealer) ─────
CREATE TABLE IF NOT EXISTS commcalc.raw_ma_commission (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  carrier_id          UUID,              -- resolved from the file's Carrier Name when possible
  source_id           UUID,              -- commcalc.data_source that pulled it (NULL = manual/email upload)
  period              TEXT, period_month INT, period_year INT,   -- derived per row from Date
  tx_date             DATE,
  tx_time             TEXT,
  carrier_name        TEXT,              -- e.g. 'Total by Verizon'
  activation_order    TEXT,
  merchant_account_id TEXT,              -- the store's account on the processor
  imei                TEXT,
  sim                 TEXT,
  sku                 TEXT,
  activation_type     TEXT,              -- 'New' | 'Add'
  activation_type2    TEXT,              -- 'branded' | 'byop'
  sub_type            TEXT,              -- e.g. 'TWP'
  device_margin       NUMERIC,
  consumer_margin     NUMERIC,
  consumer_financing  NUMERIC,
  rebate              NUMERIC,
  perfect_sale        TEXT,
  wallet_funding      NUMERIC,
  mrc_net_discount    NUMERIC,           -- the subscriber's plan MRC — feeds per-product MRC resolution
  fees                NUMERIC,
  fees_margin         NUMERIC,
  spiff_m1            NUMERIC, spiff_m2 NUMERIC, spiff_m3 NUMERIC,
  spiff_m4            NUMERIC, spiff_m5 NUMERIC, spiff_m6 NUMERIC,
  port_status         TEXT,
  id_verification     TEXT,
  is_financed         TEXT,
  user_id             TEXT,
  user_name           TEXT,              -- the rep on the processor
  ban                 TEXT,
  bin                 TEXT,
  pos_invoice         TEXT,
  line_status         TEXT,
  status_change_date  TEXT,
  suspension_reason   TEXT,
  consumer_value      NUMERIC,
  platform            TEXT,              -- 'Total Access' | 'Vidapay' — the processor platform
  platform_tx_id      TEXT,
  external_ref        TEXT,
  created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS raw_ma_commission_org_period ON commcalc.raw_ma_commission (org_id, period);
CREATE INDEX IF NOT EXISTS raw_ma_commission_org_date   ON commcalc.raw_ma_commission (org_id, tx_date);

-- ── 2. MA Daily Tx (SubMA) — airtime/top-up transactions (the ATU-equivalent) ────────────────────
CREATE TABLE IF NOT EXISTS commcalc.raw_ma_daily_tx (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  carrier_id        UUID,
  source_id         UUID,
  period            TEXT, period_month INT, period_year INT,
  account_id        TEXT,               -- store account on the processor
  account_name      TEXT,
  direct_ma_id      TEXT, direct_ma_name TEXT,
  top_ma_id         TEXT, top_ma_name   TEXT,
  order_number      TEXT,
  tx_date           DATE,               -- Date of Transaction
  due_date          DATE,
  user_name         TEXT,
  order_type        TEXT,
  product_name      TEXT,
  retail_cost       NUMERIC,
  merchant_discount NUMERIC,            -- the dealer's airtime margin
  merchant_invoice  NUMERIC,
  created_at        TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS raw_ma_daily_tx_org_period ON commcalc.raw_ma_daily_tx (org_id, period);
CREATE INDEX IF NOT EXISTS raw_ma_daily_tx_org_date   ON commcalc.raw_ma_daily_tx (org_id, tx_date);

-- ── 3. MA - Marketplace Handset Fulfillment Orders ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.raw_ma_fulfillment (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  carrier_id       UUID,
  source_id        UUID,
  date_ordered     DATE,
  date_filled      DATE,
  order_number     TEXT,
  order_status     TEXT,
  order_type       TEXT,
  tspid            TEXT,
  business_name    TEXT,
  business_address TEXT,
  city             TEXT, state TEXT, zip TEXT,
  product_name     TEXT,
  number_ordered   NUMERIC,
  price            NUMERIC,
  tracking_number  TEXT,
  date_shipped     DATE,
  created_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS raw_ma_fulfillment_org_date ON commcalc.raw_ma_fulfillment (org_id, date_ordered);

-- ── 4. Data-source registry: distributor → processor → login (one row per LOGIN) ─────────────────
CREATE TABLE IF NOT EXISTS commcalc.data_source (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  distributor_id UUID,                   -- commcalc.distributors.id (a company can have several)
  carrier_id     UUID,                   -- which carrier's data this login carries
  processor      TEXT NOT NULL,          -- 'vidapay' | 'total_access' | 'epay' | 'other' (free text)
  label          TEXT,                   -- e.g. 'VidaPay — login 1 (NY stores)'
  portal_url     TEXT,
  username       TEXT,
  password       TEXT,                   -- same credential posture as the rest of the app (UI config)
  enabled        BOOLEAN NOT NULL DEFAULT false,
  frequency      TEXT DEFAULT 'daily',   -- for the future scraper schedule
  hour           INT  DEFAULT 6,
  next_run_at    TIMESTAMPTZ,
  last_run_at    TIMESTAMPTZ,
  last_status    TEXT,
  notes          TEXT,
  created_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS data_source_org ON commcalc.data_source (org_id);

COMMENT ON TABLE commcalc.data_source IS
  'One row per PORTAL LOGIN a tenant pulls carrier/processor data from. Models: company -> N distributors -> N payment processors per distributor -> N logins per processor. All pulled rows land in the shared raw tables stamped with source_id, combining every source into one database.';
