-- 211_commission_custom_report.sql — the config-driven Custom Report builder (RULES TWO/THREE/FOUR/FIVE)
--
-- WHY: MetricsPro needs ONE primary, universally-designed report where a user pulls data "based on the
-- data available from all reports" — a report builder over every commcalc dataset (the unified sales
-- union, rep_commissions, MA commission / daily-tx, targets actuals, KPI metrics, store expenses,
-- chargebacks, flags). Per RULE TWO the set of datasets an org can report over is CONFIG, not code: this
-- migration adds the dataset registry so an admin can rename / reorder / disable datasets per tenant, and
-- the saved-definition store that makes a configuration a recallable "primary report".
--
-- Two tables:
--   1. commcalc.custom_report_dataset — the dataset REGISTRY. One row per (org, dataset_key). Merges OVER
--      the code-default registry in commcalc/custom_report.py (DATASETS). Resolution in
--      custom_report.resolve_registry(): code default -> HOUSE row (every tenant inherits) -> the org's own
--      row (enabled / sort_order / display_name override). Seeded with the HOUSE rows below so the registry
--      exists and is editable; the report still works with the table ABSENT (falls back to code defaults).
--   2. commcalc.custom_report_def — SAVED report definitions. org_id NOT NULL (a saved report belongs to a
--      tenant). config JSONB carries {datasets, columns, group_by, filters}. Degrades to "no saved reports"
--      when the table is absent.
--
-- SAFE: additive + idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING). Nothing existing changes. The
-- Custom Report page + endpoints degrade gracefully before this runs (code-default registry, no saved defs).
-- RLS open_all — matches every commcalc table today (tenant isolation is enforced in the app layer via the
-- org_id query param rewritten by tenant_middleware, RULE ONE).

-- ── 1. Dataset registry ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.custom_report_dataset (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  dataset_key   TEXT NOT NULL,                 -- matches a key in custom_report.DATASETS
  display_name  TEXT,                          -- NULL = use the code-default name
  enabled       BOOLEAN NOT NULL DEFAULT true, -- an org can hide a dataset from its builder
  sort_order    INT,                           -- NULL = use the code-default order
  column_catalog JSONB,                        -- optional per-org override of the column catalog (rarely used)
  notes         TEXT,
  updated_at    TIMESTAMPTZ DEFAULT NOW(),
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, dataset_key)
);
CREATE INDEX IF NOT EXISTS custom_report_dataset_org ON commcalc.custom_report_dataset (org_id);

COMMENT ON TABLE commcalc.custom_report_dataset IS
  'Per-tenant registry of datasets the Custom Report can report over. Merges over the code-default '
  'registry in commcalc/custom_report.py: code default -> HOUSE row -> org row. Resolved in '
  'custom_report.resolve_registry(). Edit at /commcalc/custom-report (admin).';

-- Seed the HOUSE registry rows (every tenant inherits them; an org may override its own). Order + name
-- mirror the code-default DATASETS so the seed and code agree. ON CONFLICT DO NOTHING keeps a re-run safe
-- and never clobbers an admin's edits.
INSERT INTO commcalc.custom_report_dataset (org_id, dataset_key, display_name, enabled, sort_order) VALUES
  ('00000000-0000-0000-0000-000000000001', 'sales_line',      'Sales — line items',            true, 10),
  ('00000000-0000-0000-0000-000000000001', 'rep_commissions', 'Commissions — rep payout',      true, 20),
  ('00000000-0000-0000-0000-000000000001', 'targets_actuals', 'Targets — achieved actuals',    true, 30),
  ('00000000-0000-0000-0000-000000000001', 'kpi_metrics',     'KPI — store metrics (DLAR)',    true, 40),
  ('00000000-0000-0000-0000-000000000001', 'store_expenses',  'Store expenses',                true, 50),
  ('00000000-0000-0000-0000-000000000001', 'chargebacks',     'Chargebacks',                   true, 60),
  ('00000000-0000-0000-0000-000000000001', 'flags',           'Flags',                         true, 70),
  ('00000000-0000-0000-0000-000000000001', 'ma_commission',   'MA — carrier commission',       true, 80),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx',     'MA — daily transactions',       true, 90)
ON CONFLICT (org_id, dataset_key) DO NOTHING;

-- ── 2. Saved report definitions ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.custom_report_def (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,                 -- a saved report belongs to a tenant (RULE ONE)
  name          TEXT NOT NULL,
  config        JSONB NOT NULL DEFAULT '{}'::jsonb, -- {datasets, columns, group_by, filters}
  created_by    TEXT,
  updated_at    TIMESTAMPTZ DEFAULT NOW(),
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, name)
);
CREATE INDEX IF NOT EXISTS custom_report_def_org ON commcalc.custom_report_def (org_id);

COMMENT ON TABLE commcalc.custom_report_def IS
  'Saved Custom Report configurations (datasets + columns + group-by + filters) per tenant. org_id NOT NULL. '
  'Loaded via the RULE THREE picker on /commcalc/custom-report.';

-- RLS: open_all (app-layer org scoping, matching every commcalc table).
ALTER TABLE commcalc.custom_report_dataset ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.custom_report_def     ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY open_all ON commcalc.custom_report_dataset FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE POLICY open_all ON commcalc.custom_report_def FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
