-- 201_commission_sale_installments.sql — SALE-TRIGGERED multi-month rep-pay under Commission Plans.
--
-- DOCTRINE (owner, 2026-07-14; commission-0 §7b, authoritative):
--   Rep multi-month commission is a COMMISSION-PLAN payout type triggered by the SALE LINE (M1..N
--   relative to the sale's trans_date), NOT by the carrier statement roster. The legacy raw_mi
--   installment_engine (mig 057) STAYS, demoted to dealer-revenue recon — this migration is the
--   NEW, sale-triggered path that lives beside it. rep_commissions gets a SEPARATE component column
--   (installment_comm_sale) so the two paths are never conflated during cutover.
--
-- PAID GATE (owner rev-B correction, §7b decision 2): months 1..N pay ONLY when, at calc time, the
--   sold line is ACTIVE and the dealer is receiving residual on that line (proven by raw_mi presence
--   for that line/period). "We pay as we get paid." Gate mode is per-schedule + configurable which
--   month it starts on (gate_from_month, default 1 = every month gated). A sold-but-unpaid line is
--   surfaced as TWO flags via the existing commcalc.flags machinery (sources 'commission_rebate_tracking'
--   + 'employee_miss'). Clawback is OPTIONAL and DEFAULT OFF (clawback_enabled).
--
-- USER-DEFINED MONTHS (§7b decision 3): which sales generate installments (backfill vs cutover) is
--   config on the schedule — effective_from / effective_to window and/or an explicit eligible_sale_periods
--   list. NOTHING hardcoded.
--
-- MRC SOURCE (§7b decision 1): pct_mrc lines resolve MRC from the commcalc.product_mrc CATALOG (mig 074),
--   USER-CONFIRMED, auto-prefilled by extracting the $ amount from the product-description text. This
--   migration extends product_mrc with the classification-first + confirm/prefill provenance columns
--   (reusing the existing classifier config — accessory_classification mig 092, carrier_category_map
--   mig 038 — never a new sixth classifier).
--
-- NEW-TENANT POSTURE + raw_mi visibility (§7b decisions 6 & 7): commcalc.commission_org_config holds the
--   R1 "refuse to pay when unconfigured" override (pay_disabled) and the carrier-residual visibility mode.
--
-- ADDITIVE + IDEMPOTENT + BOOST-SAFE: every table starts EMPTY, so all engines no-op and the house/Boost
--   calc is byte-identical. calculator.py's Boost path is untouched. New org_id columns are NOT NULL with
--   NO house-org default (XM-5: a missing org_id must fail loudly, never silently mis-file). RLS open_all
--   matches every commcalc table today. Run in the Supabase SQL editor.

-- ── 1. plan_installment_schedule — plan-scoped, SALE-triggered multi-month schedule ───────────────
CREATE TABLE IF NOT EXISTS commcalc.plan_installment_schedule (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL,                          -- no house default (XM-5)
  plan_id          UUID NOT NULL REFERENCES commcalc.commission_plan(id) ON DELETE CASCADE,
  name             TEXT,
  num_months       INT  NOT NULL DEFAULT 1,                -- horizon N (1..12); month_index 1..N
  -- which sold lines TRIGGER this schedule (same matcher shape as commission_rule). 'any' = every
  -- qualifying line under the plan. Lets one plan carry a base spiff AND a targeted installment.
  trigger_match_field TEXT NOT NULL DEFAULT 'any',         -- contract_type|department|category|product_desc|sku|trans_type|any
  trigger_match_op    TEXT NOT NULL DEFAULT 'equals',      -- equals | contains | in
  trigger_match_value TEXT,
  -- PAID GATE (default ON, rev-B): how a month confirms the dealer is being paid on the line.
  gate_mode        TEXT NOT NULL DEFAULT 'paid_residual',  -- 'none'|'paid_residual'|'active_status'|'nonzero_residual'
  gate_from_month  INT  NOT NULL DEFAULT 1,                -- gate applies to month_index >= this (1 = all months)
  clawback_enabled BOOLEAN NOT NULL DEFAULT false,         -- optional, default OFF ("we pay as we get paid")
  -- USER-DEFINED effective window (backfill vs cutover). Both NULL = no floor/ceiling.
  effective_from   DATE,                                   -- only sales with trans_date >= this generate
  effective_to     DATE,                                   -- only sales with trans_date <= this generate
  eligible_sale_periods TEXT[] NOT NULL DEFAULT '{}',      -- explicit sale-month labels; non-empty overrides the window
  is_active        BOOLEAN NOT NULL DEFAULT true,
  notes            TEXT,
  created_at       TIMESTAMPTZ DEFAULT NOW(),
  updated_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS plan_installment_schedule_lookup
  ON commcalc.plan_installment_schedule (org_id, plan_id, is_active);

-- ── 2. plan_installment_line — one row per installment month within a schedule ────────────────────
CREATE TABLE IF NOT EXISTS commcalc.plan_installment_line (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,                              -- no house default (XM-5)
  schedule_id  UUID NOT NULL REFERENCES commcalc.plan_installment_schedule(id) ON DELETE CASCADE,
  month_index  INT  NOT NULL,                              -- 1..N
  payout_kind  TEXT NOT NULL DEFAULT 'flat',               -- 'flat' | 'pct_mrc'
  flat_amount  NUMERIC NOT NULL DEFAULT 0,                 -- used when payout_kind='flat'
  mrc_pct      NUMERIC NOT NULL DEFAULT 0,                 -- used when payout_kind='pct_mrc' (0.05 = 5% of MRC)
  mrc_source   TEXT NOT NULL DEFAULT 'product_catalog',    -- catalog-only; carrier-agnostic, NEVER raw_mi
  UNIQUE (org_id, schedule_id, month_index)
);
CREATE INDEX IF NOT EXISTS plan_installment_line_sched
  ON commcalc.plan_installment_line (org_id, schedule_id, month_index);

-- ── 3. sale_installment_ledger — per sold-line per-month audit trail (idempotent on the line key) ─
-- The subscriber key is the SALE LINE itself (trans_id + mdn/serial) — no raw_mi dependency for
-- identity; raw_mi is consulted ONLY for the paid gate. This is the clawback join too.
CREATE TABLE IF NOT EXISTS commcalc.sale_installment_ledger (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL,
  trans_id          TEXT,                                  -- sale-line identity
  mdn               TEXT,
  serial_1          TEXT,
  plan_id           UUID,
  schedule_id       UUID,
  store             TEXT,
  epay_salesperson  TEXT,
  sale_period       TEXT,                                  -- month the line was SOLD (anchor)
  pay_period        TEXT,                                  -- month this installment is PAID in
  month_index       INT,                                   -- 1..N
  payout_kind       TEXT,
  mrc_at_pay        NUMERIC,
  mrc_source        TEXT,                                  -- 'product_catalog' | 'prefill' | 'none'
  amount            NUMERIC,
  paid_gate_met     BOOLEAN,
  gate_mode         TEXT,
  status            TEXT,                                  -- 'paid' | 'withheld_unpaid' | 'out_of_window'
  matched_mi_period TEXT,                                  -- which raw_mi period proved the gate (recon)
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, trans_id, mdn, month_index, pay_period)
);
CREATE INDEX IF NOT EXISTS sale_installment_ledger_pay
  ON commcalc.sale_installment_ledger (org_id, pay_period);
CREATE INDEX IF NOT EXISTS sale_installment_ledger_line
  ON commcalc.sale_installment_ledger (org_id, trans_id, mdn);

-- ── 4. rep_commissions component column — the sale-triggered installment total ────────────────────
-- SEPARATE from residual_installment_comm (mig 057, the raw_mi path) so cutover never conflates them.
-- Summed into total_payout only when the sale-triggered path is wired (money gate — parked).
ALTER TABLE commcalc.rep_commissions
  ADD COLUMN IF NOT EXISTS installment_comm_sale NUMERIC DEFAULT 0;

-- ── 5. product_mrc — classification-first + confirm/prefill provenance (§7b decision 1) ───────────
-- The user classifies an imported plan/line (accessory/activation/upgrade/swap/bill_payment/rebate/
-- misc_other — reusing the existing classifier config, not a new one) and confirms the $ MRC. The
-- system PREFILLS mrc by extracting the $ from the product-description text; the user confirms/overwrites.
ALTER TABLE commcalc.product_mrc
  ADD COLUMN IF NOT EXISTS classification TEXT,             -- user/auto line classification
  ADD COLUMN IF NOT EXISTS confirmed      BOOLEAN NOT NULL DEFAULT false, -- false = auto-prefilled, awaiting user confirm
  ADD COLUMN IF NOT EXISTS prefill_mrc    NUMERIC,          -- the $ auto-extracted from the description
  ADD COLUMN IF NOT EXISTS source_desc    TEXT;             -- the description text the prefill came from

-- ── 6. commission_org_config — R1 override + carrier-residual visibility (§7b decisions 6 & 7) ────
CREATE TABLE IF NOT EXISTS commcalc.commission_org_config (
  org_id              UUID PRIMARY KEY,                    -- one row per tenant; no house default (XM-5)
  -- §7b decision 7: a tenant may DELIBERATELY run no commissions — this silences the R1 loud refusal.
  pay_disabled        BOOLEAN NOT NULL DEFAULT false,
  -- §7b decision 6: carrier-residual (raw_mi-derived) visibility. 'all' = today's behavior (byte-identical);
  -- 'permissioned' = require the carrier_residual RBAC grant (admins/super-admins always allowed).
  residual_visibility TEXT NOT NULL DEFAULT 'all',
  updated_by          TEXT,
  updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ── RLS open_all (matches every commcalc table today; per-tenant RLS is the later backstop) ───────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'commcalc.plan_installment_schedule',
    'commcalc.plan_installment_line',
    'commcalc.sale_installment_ledger',
    'commcalc.commission_org_config'
  ] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                   AND tablename = split_part(t,'.',2) AND policyname='open_all') THEN
      EXECUTE format('CREATE POLICY open_all ON %s FOR ALL USING (true) WITH CHECK (true)', t);
    END IF;
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 201 complete — sale-triggered plan installments (schedule/line/ledger), '
       'rep_commissions.installment_comm_sale, product_mrc classify/prefill cols, commission_org_config' AS status;
