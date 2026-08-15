-- 853 — KPI actuals store + per-metric source mode
-- Zulu / TWP / Address Checks (and any carrier_kpi_metric) have no source table today. This adds a
-- key/value actuals store so arbitrary KPI values can be captured — by MANUAL entry now, and by an
-- EMAIL import later — WITHOUT a migration per metric. The `source` discriminator lets a manual row and
-- an email row coexist for the same (entity, period, metric); the per-metric `source_mode` on
-- carrier_kpi_metric decides which one the read path returns, so flipping modes never loses data
-- (mirrors the swept_value/manual_value pattern in commcalc.inventory_value, mig 026).

CREATE TABLE IF NOT EXISTS commcalc.kpi_actual (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  scope       TEXT NOT NULL DEFAULT 'store',      -- 'store' | 'rep'
  entity      TEXT NOT NULL,                      -- store_code (scope='store') or rep key (scope='rep')
  period      TEXT NOT NULL,                      -- same period spelling used across commcalc
  metric_key  TEXT NOT NULL,                      -- 'zulu' | 'twp' | 'address_checks' | any carrier_kpi_metric key
  value       NUMERIC,
  source      TEXT NOT NULL DEFAULT 'manual',     -- 'manual' | 'email'
  updated_by  TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (org_id, scope, entity, period, metric_key, source)
);
CREATE INDEX IF NOT EXISTS kpi_actual_lookup ON commcalc.kpi_actual (org_id, period, metric_key);

ALTER TABLE commcalc.kpi_actual ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='kpi_actual' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.kpi_actual FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

-- Per-metric source mode: 'manual' (hand-entered values win) or 'email' (imported values win).
ALTER TABLE commcalc.carrier_kpi_metric ADD COLUMN IF NOT EXISTS source_mode TEXT NOT NULL DEFAULT 'manual';
