-- 057_multi_month_payout.sql — configurable multi-month commission payout (generic, per-carrier).
--
-- WHY: pay structures differ per company/carrier. Some (e.g. Total Wireless) pay a rep's commission for
-- one activation SPREAD OVER up to N months, where each month's installment is flat OR a % of that
-- month's MRC, and months 2..N only pay if the subscriber's bill was PAID + residual received that month
-- (proven by the carrier statement = raw_mi: the subscriber still present + Active + non-zero residual).
--
-- This is ADDITIVE and BACKWARD-COMPATIBLE: with NO payout_schedule row, num_months defaults to 1 and the
-- engine is a no-op, so existing single-month payouts (Boost) are byte-identical. A schedule is opt-in per
-- (company, carrier, activation_type). The installment engine is read-only / preview until explicitly wired
-- into the live calc (see HANDOFF) — running this migration alone changes nothing.
--
-- Idempotent.

-- ── header: one schedule per company × carrier × activation type ─────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.payout_schedule (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  company_id      UUID,                       -- commcalc.companies.id; NULL = all companies (fallback)
  carrier_id      UUID,                       -- commcalc.carrier.id;   NULL = any carrier (fallback)
  activation_type TEXT NOT NULL DEFAULT '*',  -- 'premium' | 'byod' | 'upgrade' | '*' (all)
  num_months      INT  NOT NULL DEFAULT 1,    -- N (1..3). 1 = today's behavior.
  gate_signal     TEXT NOT NULL DEFAULT 'paid_residual',  -- how months 2..N confirm "bill paid":
                                              -- 'paid_residual' = Active + non-zero residual that month
                                              -- 'active_status' = subscriber_status Active that month
                                              -- 'nonzero_residual' = residual > 0 that month
                                              -- 'paid_flag' = a dedicated mapped paid column
  bypass_tier     BOOLEAN NOT NULL DEFAULT true,  -- deferred installments not re-scaled by the KPI tier
  is_active       BOOLEAN NOT NULL DEFAULT true,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, company_id, carrier_id, activation_type)
);

-- ── lines: one row per installment month within a schedule ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.payout_schedule_line (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  schedule_id   UUID NOT NULL REFERENCES commcalc.payout_schedule(id) ON DELETE CASCADE,
  month_index   INT  NOT NULL,                 -- 1..N
  payout_kind   TEXT NOT NULL DEFAULT 'flat',  -- 'flat' | 'pct_mrc'
  flat_amount   NUMERIC DEFAULT 0,             -- used when payout_kind='flat'
  mrc_pct       NUMERIC DEFAULT 0,             -- used when payout_kind='pct_mrc' (0.05 = 5% of MRC)
  mrc_basis     TEXT NOT NULL DEFAULT 'commissionable_mrc',  -- 'base_mrc' | 'commissionable_mrc'
  requires_paid BOOLEAN NOT NULL DEFAULT false,-- month 1 = false; months 2..N = true (the contingency)
  UNIQUE (org_id, schedule_id, month_index)
);

-- ── ledger: per-subscriber per-installment, the "reflected in the statement" audit trail ─────────
CREATE TABLE IF NOT EXISTS commcalc.subscriber_installments (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  subscriber_id     TEXT NOT NULL,             -- raw_mi.subscriber_id — the cross-month key
  carrier_id        UUID,
  company_id        UUID,
  schedule_id       UUID,
  store             TEXT,
  rep_username      TEXT,
  epay_salesperson  TEXT,
  activation_type   TEXT,
  activation_period TEXT,                       -- the month-1 period (anchor), e.g. 'April 2026'
  pay_period        TEXT,                       -- the month this installment is PAID in
  month_index       INT,                        -- 1..N
  payout_kind       TEXT,
  mrc_at_pay        NUMERIC,                     -- the MRC used for this installment
  amount            NUMERIC,
  paid_gate_met     BOOLEAN,
  status            TEXT,                        -- 'paid' | 'withheld_unpaid' | 'pending'
  source_mi_period  TEXT,                        -- which raw_mi period proved the gate
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, subscriber_id, activation_type, month_index)
);

-- ── the installment payout component on rep_commissions (summed into total_payout when wired) ────
ALTER TABLE commcalc.rep_commissions ADD COLUMN IF NOT EXISTS residual_installment_comm NUMERIC DEFAULT 0;

-- indexes
CREATE INDEX IF NOT EXISTS payout_schedule_lookup
  ON commcalc.payout_schedule (org_id, company_id, carrier_id, activation_type);
CREATE INDEX IF NOT EXISTS subscriber_installments_pay
  ON commcalc.subscriber_installments (org_id, pay_period);
CREATE INDEX IF NOT EXISTS subscriber_installments_sub
  ON commcalc.subscriber_installments (org_id, subscriber_id);

-- RLS open_all (matches every other commcalc table today; per-tenant RLS is the later backstop)
ALTER TABLE commcalc.payout_schedule        ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.payout_schedule_line   ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.subscriber_installments ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='payout_schedule' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.payout_schedule FOR ALL USING (true) WITH CHECK (true); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='payout_schedule_line' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.payout_schedule_line FOR ALL USING (true) WITH CHECK (true); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='subscriber_installments' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.subscriber_installments FOR ALL USING (true) WITH CHECK (true); END IF;
END $$;
