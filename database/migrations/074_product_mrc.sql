-- 074_product_mrc.sql — per-product MRC catalog for %-of-MRC installment payouts.
--
-- WHY: the multi-month payout engine (migration 057) pays month 2..N installments as a % of that month's
-- MRC, reading the MRC from a raw_mi column (commissionable_mrc / base_mrc). That works for carriers whose
-- statement carries the MRC (ePay / Boost MI file). But some carriers — e.g. Total Wireless — DON'T report
-- a per-subscriber MRC on their statement, so that column is 0 and the installment computes to $0.
--
-- This catalog maps a subscriber's PLAN (raw_mi.customer_plan) → its monthly recurring charge, per tenant
-- (optionally per carrier). The installment engine uses it as a FALLBACK when the mapped MRC column is <= 0
-- (or as the primary source when a schedule line's mrc_basis = 'product_catalog'). Fully ADDITIVE: with no
-- rows here, MRC resolution is byte-identical to today, so Boost (real commissionable_mrc) is untouched.
--
-- Idempotent.

CREATE TABLE IF NOT EXISTS commcalc.product_mrc (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  carrier_id   UUID,                            -- commcalc.carrier.id; NULL = any carrier (fallback)
  plan_pattern TEXT NOT NULL,                   -- matched against raw_mi.customer_plan
  match_op     TEXT NOT NULL DEFAULT 'equals',  -- 'equals' (exact, case-insensitive) | 'contains'
  mrc          NUMERIC NOT NULL DEFAULT 0,      -- the plan's monthly recurring charge ($)
  priority     INT  NOT NULL DEFAULT 100,       -- lower wins on ties (put specific plans before catch-alls)
  is_active    BOOLEAN NOT NULL DEFAULT true,
  note         TEXT,
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- one MRC per (tenant, carrier, plan, op). COALESCE so a NULL carrier de-dupes like a real value
-- (a plain UNIQUE treats NULLs as distinct and would allow accidental duplicate any-carrier rows).
CREATE UNIQUE INDEX IF NOT EXISTS product_mrc_uq
  ON commcalc.product_mrc (
    org_id,
    COALESCE(carrier_id, '00000000-0000-0000-0000-000000000000'::uuid),
    lower(plan_pattern),
    match_op
  );

CREATE INDEX IF NOT EXISTS product_mrc_lookup
  ON commcalc.product_mrc (org_id, is_active, priority);

-- RLS open_all (matches every other commcalc table today; per-tenant RLS is the later backstop)
ALTER TABLE commcalc.product_mrc ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='product_mrc' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.product_mrc FOR ALL USING (true) WITH CHECK (true); END IF;
END $$;
