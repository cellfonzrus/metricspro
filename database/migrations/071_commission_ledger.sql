-- 071_commission_ledger.sql — SAP-style CANONICAL commission/payout ledger + its classification map.
--
-- WHY: every carrier's commission file uses its own type labels (Total/MA calls them "PostPaid Additional
-- Spiff", "Postpaid Residual Order", product "TBV MONTH 2 New Activation Commission", "Subsidy", "Trac
-- Autopay Residual", …). Boost had its own columns. Instead of a per-carrier column set, this normalises
-- ANY commission file into FIVE canonical buckets — Commission, Spiff, Equipment rebate, Residual/monthly
-- incentives, Auto Pay residual — via a per-tenant RULE MAP. A payout paid over 6 months stays one category
-- (e.g. Commission) but each installment keeps its payment_month, so it's "classified once, displayed as
-- it's paid". On the MA Daily Tx, anything with a NEGATIVE amount is a payout; positives are dealer charges
-- (stored, is_payout=false, kept out of the five buckets).
--
-- ADDITIVE + IDEMPOTENT + BOOST-SAFE: two NEW tables only. The live Boost/Total calc, rep_commissions,
-- carrier_commission and every legacy upload branch are untouched. The classifier falls back to hard-coded
-- DEFAULT_RULES (mirrored in commission_ledger.py) when the map table is empty/un-migrated, so it works the
-- moment the code deploys; this SQL just makes the rules editable + persists the ledger.

-- ── 1) the canonical ledger: one row per source line, amount booked into its category column ───────────
CREATE TABLE IF NOT EXISTS commcalc.commission_ledger (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  period           TEXT,                          -- reporting period the file was uploaded for
  source_report    TEXT NOT NULL DEFAULT 'ma_daily_tx',
  account_id       TEXT,
  account_name     TEXT,
  store            TEXT,
  rep_user         TEXT,                          -- the "User" / rep on the line
  order_number     TEXT,
  order_type       TEXT,                          -- raw source classifier label
  product_name     TEXT,                          -- raw source description (drives classification)
  trans_date       TEXT,
  due_date         TEXT,
  payment_month    INT,                           -- parsed M1..N from the product name (NULL if none)
  category         TEXT,                          -- canonical bucket OR 'charge'/'other'
  -- the five canonical payout buckets — the line's payout magnitude lands in exactly one
  commission       NUMERIC DEFAULT 0,
  spiff            NUMERIC DEFAULT 0,
  equipment_rebate NUMERIC DEFAULT 0,
  residual_monthly NUMERIC DEFAULT 0,
  autopay_residual NUMERIC DEFAULT 0,
  payout_total     NUMERIC DEFAULT 0,             -- = the magnitude booked (sum of the five)
  raw_amount       NUMERIC DEFAULT 0,             -- original SIGNED amount (negative = payout)
  is_payout        BOOLEAN DEFAULT false,         -- raw_amount < 0
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS commission_ledger_period ON commcalc.commission_ledger (org_id, source_report, period);
CREATE INDEX IF NOT EXISTS commission_ledger_cat    ON commcalc.commission_ledger (org_id, category);
CREATE INDEX IF NOT EXISTS commission_ledger_rep    ON commcalc.commission_ledger (org_id, rep_user);

ALTER TABLE commcalc.commission_ledger ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='commission_ledger' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.commission_ledger FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

-- ── 2) the SAP-style classification rules: (match_field, op, pattern) -> canonical category ────────────
CREATE TABLE IF NOT EXISTS commcalc.commission_category_map (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  source_report TEXT NOT NULL DEFAULT 'ma_daily_tx',  -- which report's labels this maps ('*' = any)
  match_field   TEXT NOT NULL DEFAULT 'product_name', -- product_name | order_type
  match_op      TEXT NOT NULL DEFAULT 'contains',     -- contains | equals
  pattern       TEXT NOT NULL,                        -- case-insensitive needle
  category      TEXT NOT NULL,                        -- commission|spiff|equipment_rebate|residual_monthly|autopay_residual|charge|exclude
  sign_rule     TEXT NOT NULL DEFAULT 'negative_only',-- negative_only (payout) | any
  priority      INT NOT NULL DEFAULT 100,             -- lower wins; first match by ascending priority
  is_seeded     BOOLEAN DEFAULT false,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, source_report, match_field, match_op, pattern)
);
CREATE INDEX IF NOT EXISTS commission_category_map_lookup
  ON commcalc.commission_category_map (org_id, source_report, priority);

ALTER TABLE commcalc.commission_category_map ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='commission_category_map' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.commission_category_map FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

-- ── 3) seed the MA Daily Tx defaults (house org). First match by ascending priority wins. ─────────────
-- Derived from the real file: months 1-3 products say "…Commission", months 4-6 say "…SPF"; Autopay vs
-- plain Residual; "Subsidy" / Promo orders are device rebates. These mirror commission_ledger.DEFAULT_RULES.
INSERT INTO commcalc.commission_category_map
  (org_id, source_report, match_field, match_op, pattern, category, sign_rule, priority, is_seeded)
VALUES
  ('00000000-0000-0000-0000-000000000001','ma_daily_tx','product_name','contains','Commission','commission','negative_only',10,true),
  ('00000000-0000-0000-0000-000000000001','ma_daily_tx','product_name','contains','SPF','spiff','negative_only',20,true),
  ('00000000-0000-0000-0000-000000000001','ma_daily_tx','product_name','contains','Spiff','spiff','negative_only',21,true),
  ('00000000-0000-0000-0000-000000000001','ma_daily_tx','product_name','contains','Autopay Residual','autopay_residual','negative_only',30,true),
  ('00000000-0000-0000-0000-000000000001','ma_daily_tx','product_name','contains','Residual','residual_monthly','negative_only',40,true),
  ('00000000-0000-0000-0000-000000000001','ma_daily_tx','product_name','contains','Subsidy','equipment_rebate','negative_only',50,true),
  ('00000000-0000-0000-0000-000000000001','ma_daily_tx','order_type','contains','Promo','equipment_rebate','negative_only',51,true)
ON CONFLICT (org_id, source_report, match_field, match_op, pattern) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 071 complete — commission_ledger + commission_category_map ('
       || (SELECT count(*) FROM commcalc.commission_category_map WHERE is_seeded) || ' seeded rules)' AS status;
