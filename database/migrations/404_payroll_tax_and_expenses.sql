-- 404_payroll_tax_and_expenses.sql — Payroll tax + operator-customizable Payroll Expenses items
--   (mod-people, band 400-499).
--
-- WHY (owner request 2026-07-15): (a) employer payroll tax (FICA SS / Medicare / FUTA / SUTA) should
-- be auto-computed on the payroll run and land in Store Expenses; (b) a separate "Payroll Expenses"
-- HR page where employer-burden items (Unemployment Insurance, Workers Comp, and fully custom fields)
-- are configured and roll into the SAME "Payroll Expenses" line on the expense page. RULE TWO: every
-- rate/wage-base/calc-method/scope here is CONFIG, never hard-coded.
--
-- This migration is ADDITIVE + IDEMPOTENT and touches no existing table. It does not change any
-- payroll payout number — every table here is a NEW, ADDITIVE cost line, never a modification of
-- wages/commission.
--
-- Consumed by GET/PUT /storeops/payroll-tax-config, GET/POST/PATCH/DELETE
-- /storeops/payroll-expense-items(/{id}), GET /storeops/payroll-expenses/{period},
-- POST /storeops/payroll-expenses/run/{period} (backend/app/modules/storeops/router.py) and the pure
-- engine in backend/app/modules/storeops/payroll_expenses.py.

-- ── storeops.payroll_tax_config ─────────────────────────────────────────────────────────────────
-- ONE row per org (not layered by role/employee like PTO — payroll tax rates are a jurisdiction fact
-- about the tenant/legal entity, not something that varies per person). A tenant operating in more
-- than one state (differing SUTA rates per state) is a known limitation — see docs/handoffs/people.md.
CREATE TABLE IF NOT EXISTS storeops.payroll_tax_config (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,
  enabled            BOOLEAN NOT NULL DEFAULT true,
  fica_ss_rate       NUMERIC NOT NULL DEFAULT 0.062,     -- employer FICA Social Security
  fica_ss_wage_base  NUMERIC NOT NULL DEFAULT 168600,    -- annual per-employee wage base (cumulative, tracked via payroll_tax_ledger)
  medicare_rate      NUMERIC NOT NULL DEFAULT 0.0145,    -- employer Medicare — no wage cap
  futa_rate          NUMERIC NOT NULL DEFAULT 0.006,     -- federal unemployment
  futa_wage_base     NUMERIC NOT NULL DEFAULT 7000,      -- federal FUTA wage base (statutory)
  suta_rate          NUMERIC NOT NULL DEFAULT 0.027,     -- STATE unemployment — generic placeholder, tenant MUST set their real state rate
  suta_wage_base     NUMERIC NOT NULL DEFAULT 9000,      -- ditto — generic placeholder
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by         TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_payroll_tax_config_org ON storeops.payroll_tax_config (org_id);

-- ── storeops.payroll_expense_item ───────────────────────────────────────────────────────────────
-- Operator-customizable employer-burden line items (Unemployment Insurance, Workers Comp, anything
-- else added via the HR "Payroll Expenses" page's "+ Add item").
CREATE TABLE IF NOT EXISTS storeops.payroll_expense_item (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,
  key                TEXT NOT NULL,           -- stable slug (component_key in the ledger); unique per org
  name               TEXT NOT NULL,           -- display label
  calc_method        TEXT NOT NULL DEFAULT 'pct_wages'
                       CHECK (calc_method IN ('pct_wages', 'per_100_wages', 'per_employee', 'fixed')),
  rate_or_amount     NUMERIC NOT NULL DEFAULT 0,
  wage_cap           NUMERIC,                 -- nullable; PER-PERIOD cap on the wages basis (NOT a cumulative annual cap — see payroll_expenses.py docstring)
  scope              TEXT NOT NULL DEFAULT 'store' CHECK (scope IN ('store', 'company')),
  enabled            BOOLEAN NOT NULL DEFAULT true,
  sort_order         INTEGER NOT NULL DEFAULT 0,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by         TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_payroll_expense_item_org_key ON storeops.payroll_expense_item (org_id, key);
CREATE INDEX IF NOT EXISTS ix_payroll_expense_item_org ON storeops.payroll_expense_item (org_id);

-- ── storeops.payroll_tax_ledger ─────────────────────────────────────────────────────────────────
-- One row per (org, period, employee) per run — the source of truth for "wages already taxed toward
-- each cap so far this calendar year" (queried the same way pto_accrual_ledger's prior_balance is:
-- sum prior periods THIS YEAR, before the period being computed). A re-run for the same (org, period)
-- DELETEs the prior rows for that period first (see router), so this always reflects the latest run.
CREATE TABLE IF NOT EXISTS storeops.payroll_tax_ledger (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   UUID NOT NULL,
  period                   TEXT NOT NULL,        -- "YYYY-MM"
  employee_id              TEXT NOT NULL,
  wages                    NUMERIC NOT NULL DEFAULT 0,
  ss_taxable_wages         NUMERIC NOT NULL DEFAULT 0,
  fica_ss_tax              NUMERIC NOT NULL DEFAULT 0,
  medicare_taxable_wages   NUMERIC NOT NULL DEFAULT 0,
  medicare_tax             NUMERIC NOT NULL DEFAULT 0,
  futa_taxable_wages       NUMERIC NOT NULL DEFAULT 0,
  futa_tax                 NUMERIC NOT NULL DEFAULT 0,
  suta_taxable_wages       NUMERIC NOT NULL DEFAULT 0,
  suta_tax                 NUMERIC NOT NULL DEFAULT 0,
  total_tax                NUMERIC NOT NULL DEFAULT 0,
  run_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
  run_by                   TEXT
);
CREATE INDEX IF NOT EXISTS ix_payroll_tax_ledger_org_period ON storeops.payroll_tax_ledger (org_id, period);
CREATE INDEX IF NOT EXISTS ix_payroll_tax_ledger_org_emp ON storeops.payroll_tax_ledger (org_id, employee_id, period);

-- ── storeops.payroll_expense_ledger ─────────────────────────────────────────────────────────────
-- One row per (org, period, store, component) per run — the itemized breakdown the HR page renders
-- (tax broken into fica_ss/medicare/futa/suta, plus one row per enabled payroll_expense_item). This
-- table is display/audit only; the rolled-up push to Store Expenses is computed independently from
-- the same in-memory result the router persists here, so the two can never drift out of sync.
CREATE TABLE IF NOT EXISTS storeops.payroll_expense_ledger (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL,
  period           TEXT NOT NULL,
  store            TEXT NOT NULL,
  component_type   TEXT NOT NULL CHECK (component_type IN ('tax', 'item')),
  component_key    TEXT NOT NULL,          -- 'fica_ss'|'medicare'|'futa'|'suta' or the item's key
  label            TEXT NOT NULL,
  amount           NUMERIC NOT NULL DEFAULT 0,
  run_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  run_by           TEXT
);
CREATE INDEX IF NOT EXISTS ix_payroll_expense_ledger_org_period ON storeops.payroll_expense_ledger (org_id, period);
CREATE INDEX IF NOT EXISTS ix_payroll_expense_ledger_org_period_store ON storeops.payroll_expense_ledger (org_id, period, store);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['storeops.payroll_tax_config', 'storeops.payroll_expense_item',
                            'storeops.payroll_tax_ledger', 'storeops.payroll_expense_ledger'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

-- ── seed: default tax config row + the 2 default expense items, for every existing tenant ────────
-- Idempotent (ON CONFLICT DO NOTHING — never overwrites a tenant's already-edited config). New
-- tenants created after this migration runs get their defaults lazily: GET falls back to the same
-- code defaults when no row exists yet (see payroll_expenses.py DEFAULT_TAX_CONFIG), so the feature
-- degrades gracefully for a not-yet-seeded org too.
--
-- Unemployment Insurance / Workers Comp are seeded ENABLED but rate_or_amount = 0 ("configured, not
-- yet priced") rather than any nonzero default: (a) Workers Comp premium rates vary by state AND risk
-- classification code — there is no universally-correct default, a wrong nonzero default would be
-- WORSE than an honest zero; (b) SUTA already has a real (if generic) nonzero default in
-- payroll_tax_config above — seeding "Unemployment Insurance" here at a nonzero rate too would
-- silently DOUBLE-COUNT unemployment cost for a tenant who doesn't notice the two separate config
-- surfaces. Flagged for Gate 2 — the owner may prefer these merged, or the item's purpose narrowed to
-- "a private/supplemental UI carrier premium, distinct from statutory SUTA."
CREATE OR REPLACE FUNCTION storeops.seed_payroll_expense_defaults(p_org uuid)
RETURNS void LANGUAGE plpgsql AS $fn$
BEGIN
  INSERT INTO storeops.payroll_tax_config (org_id)
  VALUES (p_org)
  ON CONFLICT (org_id) DO NOTHING;

  INSERT INTO storeops.payroll_expense_item (org_id, key, name, calc_method, rate_or_amount, wage_cap, scope, enabled, sort_order)
  VALUES
    (p_org, 'unemployment_insurance', 'Unemployment Insurance', 'pct_wages', 0, NULL, 'store', true, 1),
    (p_org, 'workers_comp', 'Workers Comp', 'pct_wages', 0, NULL, 'store', true, 2)
  ON CONFLICT (org_id, key) DO NOTHING;
END;
$fn$;
GRANT EXECUTE ON FUNCTION storeops.seed_payroll_expense_defaults(uuid) TO anon, authenticated, service_role;

DO $seed$
DECLARE t record;
BEGIN
  PERFORM storeops.seed_payroll_expense_defaults('00000000-0000-0000-0000-000000000001');
  BEGIN
    FOR t IN SELECT org_id FROM storeops.tenants LOOP
      PERFORM storeops.seed_payroll_expense_defaults(t.org_id);
    END LOOP;
  EXCEPTION WHEN undefined_table THEN
    NULL;  -- storeops.tenants absent in a bare env -> house seed above still applied
  END;
END $seed$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 404 complete — storeops.payroll_tax_config + payroll_expense_item + payroll_tax_ledger + payroll_expense_ledger' AS status;
