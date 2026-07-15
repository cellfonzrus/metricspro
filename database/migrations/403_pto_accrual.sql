-- 403_pto_accrual.sql — Paid-Leave-Accumulated (PTO accrual) engine config + ledger (mod-people,
--   band 400-499).
--
-- WHY (owner request 2026-07-15): a "Paid Leave Accumulated" cost — hours earned per hour worked,
-- payable whether taken or not (mode-dependent) — computed on the payroll run and handed to Store
-- Expenses as a per-store cost line. RULE TWO: nothing about the accrual rate/mode/cap/basis is
-- hard-coded — it is a per-org config with optional per-role and per-employee OVERRIDE rows layered
-- on top (employee > role > org > code default), same layering shape as other storeops config
-- (roles.permissions, closing tender config).
--
-- This migration is ADDITIVE + IDEMPOTENT and touches no existing table. It does not change any
-- payroll payout number — the accrual is a new, separate cost line, never a modification of wages.
--
-- Consumed by GET/PUT /storeops/pto-accrual-config, POST /storeops/pto-accrual/run/{period},
-- GET /storeops/pto-accrual/{period} (backend/app/modules/storeops/router.py) and the pure engine
-- in backend/app/modules/storeops/pto_accrual.py.

-- ── storeops.pto_accrual_config ─────────────────────────────────────────────────────────────────
-- One row per (org) [scope='org'], optionally one row per (org, role) [scope='role'] and one row
-- per (org, employee_id) [scope='employee'] as an OVERRIDE. Override rows may leave any field NULL
-- to mean "inherit from the next layer down" — only the org-scope row is expected to be fully
-- populated (seeded below with the code defaults so there is always a base to inherit from).
CREATE TABLE IF NOT EXISTS storeops.pto_accrual_config (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                UUID NOT NULL,
  scope                 TEXT NOT NULL DEFAULT 'org' CHECK (scope IN ('org', 'role', 'employee')),
  role                  TEXT,                      -- set when scope='role' (storeops.roles.name)
  employee_id           TEXT,                      -- set when scope='employee' (storeops.employees.employee_id)
  enabled               BOOLEAN,                   -- NULL on an override row = inherit
  accrual_rate          NUMERIC,                   -- PTO hours earned per hour worked (default 0.0385 = 80hr/2080hr)
  mode                  TEXT CHECK (mode IS NULL OR mode IN ('accrue', 'on_use')),
  cost_basis            TEXT CHECK (cost_basis IS NULL OR cost_basis IN ('payscale_rate')),
  max_accrual_hours     NUMERIC,                   -- balance cap; NULL = no cap (org row: NULL means "no cap" literally, not "inherit" — there is nothing below org to inherit from)
  hours_per_pto_day     NUMERIC,                   -- calendar day -> hours conversion for taken PTO (default 8)
  counts_as_pto_types   JSONB,                     -- time_off_requests.type values that draw the bank (default ["PTO"])
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by            TEXT
);

-- Exactly one row per scope key. Partial unique indexes (NULLS are not distinct-checked by a plain
-- UNIQUE, so we scope each index to its own scope value).
CREATE UNIQUE INDEX IF NOT EXISTS uq_pto_accrual_config_org
  ON storeops.pto_accrual_config (org_id) WHERE scope = 'org';
CREATE UNIQUE INDEX IF NOT EXISTS uq_pto_accrual_config_org_role
  ON storeops.pto_accrual_config (org_id, role) WHERE scope = 'role';
CREATE UNIQUE INDEX IF NOT EXISTS uq_pto_accrual_config_org_emp
  ON storeops.pto_accrual_config (org_id, employee_id) WHERE scope = 'employee';
CREATE INDEX IF NOT EXISTS ix_pto_accrual_config_org ON storeops.pto_accrual_config (org_id);

-- ── storeops.pto_accrual_ledger ─────────────────────────────────────────────────────────────────
-- One row per (org, period, employee_id, store) produced by a payroll-run compute. A re-run for the
-- same (org_id, period) DELETEs the prior rows for that period first (see the router), so this table
-- always reflects exactly the latest run — safe to re-run, no duplicate accumulation.
-- `balance_hours` is a derived/cached snapshot (prior-period ledger sum + this period's accrued -
-- taken) kept alongside for cheap display; it is always RECOMPUTED from the ledger history on each
-- run, never hand-edited, so it can never drift out of sync with the source rows.
CREATE TABLE IF NOT EXISTS storeops.pto_accrual_ledger (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  period         TEXT NOT NULL,             -- "YYYY-MM", same convention as /storeops/payroll?month=
  employee_id    TEXT NOT NULL,
  store          TEXT,
  accrued_hours  NUMERIC NOT NULL DEFAULT 0,
  taken_hours    NUMERIC NOT NULL DEFAULT 0,
  cost           NUMERIC NOT NULL DEFAULT 0,
  balance_hours  NUMERIC,                   -- running earned - taken balance as of this period, this employee
  mode           TEXT,                      -- the mode in effect when this row was computed (audit trail)
  run_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  run_by         TEXT
);
CREATE INDEX IF NOT EXISTS ix_pto_accrual_ledger_org_period
  ON storeops.pto_accrual_ledger (org_id, period);
CREATE INDEX IF NOT EXISTS ix_pto_accrual_ledger_org_emp
  ON storeops.pto_accrual_ledger (org_id, employee_id, period);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['storeops.pto_accrual_config', 'storeops.pto_accrual_ledger'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

-- Seed the org-scope default row for every existing tenant (idempotent — ON CONFLICT DO NOTHING, so
-- it never overwrites a tenant's already-edited config). New tenants created after this migration
-- runs get their default row lazily: the GET/PUT endpoints fall back to the same code defaults when
-- no org row exists yet, so the feature degrades gracefully for a not-yet-seeded org too.
CREATE OR REPLACE FUNCTION storeops.seed_pto_accrual_config(p_org uuid)
RETURNS void LANGUAGE plpgsql AS $fn$
BEGIN
  INSERT INTO storeops.pto_accrual_config
    (org_id, scope, enabled, accrual_rate, mode, cost_basis, max_accrual_hours, hours_per_pto_day, counts_as_pto_types)
  VALUES
    (p_org, 'org', true, 0.0385, 'accrue', 'payscale_rate', NULL, 8, '["PTO"]'::jsonb)
  ON CONFLICT (org_id) WHERE scope = 'org' DO NOTHING;
END;
$fn$;
GRANT EXECUTE ON FUNCTION storeops.seed_pto_accrual_config(uuid) TO anon, authenticated, service_role;

DO $seed$
DECLARE t record;
BEGIN
  PERFORM storeops.seed_pto_accrual_config('00000000-0000-0000-0000-000000000001');
  BEGIN
    FOR t IN SELECT org_id FROM storeops.tenants LOOP
      PERFORM storeops.seed_pto_accrual_config(t.org_id);
    END LOOP;
  EXCEPTION WHEN undefined_table THEN
    NULL;  -- storeops.tenants absent in a bare env -> house seed above still applied
  END;
END $seed$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 403 complete — storeops.pto_accrual_config + storeops.pto_accrual_ledger' AS status;
