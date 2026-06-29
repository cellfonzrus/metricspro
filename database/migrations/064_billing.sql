-- 064_billing.sql — Tenant Billing (SaaS) Phase 1: per-tenant billing plans + generated invoices.
--
-- WHY: MetricsPro is multi-tenant (storeops.tenants, mig 055). The SUPER-ADMIN needs to price each
-- tenant and bill them. A plan picks a BASIS (what the price is multiplied by) and a CYCLE:
--   basis ∈ {flat, per_store, per_entity, per_user, per_module}
--     flat       — one fixed price (quantity = 1)
--     per_store  — count of active storeops.stores for the tenant
--     per_user   — count of storeops.app_users for the tenant
--     per_entity — count of commcalc.companies (account/legal entities) for the tenant
--     per_module — count of enabled storeops.tenant_modules for the tenant
--   cycle  ∈ {monthly, annual}   (unit_price is per-cycle; no auto-proration in v1)
-- An invoice freezes basis/quantity/unit_price/amount at generation time (so later driver changes
-- don't rewrite history). PAYMENT GATEWAY is a LATER phase — payment_ref is the only seam here.
--
-- Additive + idempotent. RLS open_all (backend service role is the real guard, like the rest of storeops.*).

-- ── billing_plan: one plan per tenant (org_id) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.billing_plan (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  basis       TEXT NOT NULL DEFAULT 'flat',       -- flat | per_store | per_entity | per_user | per_module
  unit_price  NUMERIC DEFAULT 0,
  cycle       TEXT NOT NULL DEFAULT 'monthly',     -- monthly | annual
  currency    TEXT DEFAULT 'USD',
  modules     TEXT[],                              -- modules this price covers (NULL = all)
  is_active   BOOLEAN DEFAULT true,
  notes       TEXT,
  created_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE (org_id)
);
CREATE INDEX IF NOT EXISTS billing_plan_org ON storeops.billing_plan (org_id);

-- ── billing_invoice: a frozen, generated invoice (status lifecycle: draft → sent → paid / void) ──
CREATE TABLE IF NOT EXISTS storeops.billing_invoice (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  period_start DATE,
  period_end   DATE,
  basis        TEXT,
  quantity     NUMERIC,
  unit_price   NUMERIC,
  amount       NUMERIC,
  currency     TEXT DEFAULT 'USD',
  status       TEXT DEFAULT 'draft',               -- draft | sent | paid | void
  issued_at    TIMESTAMPTZ,
  due_date     DATE,
  payment_ref  TEXT,                               -- seam for a future payment gateway (Stripe etc.)
  notes        TEXT,
  created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS billing_invoice_org    ON storeops.billing_invoice (org_id);
CREATE INDEX IF NOT EXISTS billing_invoice_status ON storeops.billing_invoice (status);

-- ── RLS open_all (service role bypasses; matches the rest of storeops.*) ───────────────────────
ALTER TABLE storeops.billing_plan    ENABLE ROW LEVEL SECURITY;
ALTER TABLE storeops.billing_invoice ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='storeops' AND tablename='billing_plan' AND policyname='open_all') THEN
    CREATE POLICY open_all ON storeops.billing_plan FOR ALL TO anon, authenticated USING (true) WITH CHECK (true); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='storeops' AND tablename='billing_invoice' AND policyname='open_all') THEN
    CREATE POLICY open_all ON storeops.billing_invoice FOR ALL TO anon, authenticated USING (true) WITH CHECK (true); END IF;
END $$;
GRANT ALL ON storeops.billing_plan    TO anon, authenticated, service_role;
GRANT ALL ON storeops.billing_invoice TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 064 complete — storeops.billing_plan + storeops.billing_invoice' AS status;
