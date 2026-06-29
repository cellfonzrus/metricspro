-- 060_carrier_kpi_metrics.sql — per-carrier KPI metric definitions (user-defined, not system defaults).
--
-- WHY: the KPI metrics that drive the commission tier (ATU / Protect / "Boost App" / Family Plan / BYOD /
-- TMR3 / AAL) and their target %s were HARD-CODED in the system (ACTION_KPI_DEFS + calculator.py KPI dict),
-- with Boost-specific names baked in. A different carrier (Verizon, Metro, …) has DIFFERENT KPIs and
-- targets that the tenant should DEFINE at onboarding — not inherit Boost's. This table holds those
-- definitions per carrier; the nil-UUID carrier_id '0000…0000' is the org-wide DEFAULT set.
--
-- (carrier_id is NOT NULL with a nil-UUID sentinel rather than nullable, so UNIQUE + ON CONFLICT/upsert
-- behave — a NULL carrier_id would defeat the unique constraint since NULL != NULL in Postgres.)
--
-- Additive + idempotent. Seeds the existing 7 metrics as the org default with neutral labels (so the live
-- action-plan/coaching are unchanged), each mapped to its existing payout_config target column. RLS open_all.

CREATE TABLE IF NOT EXISTS commcalc.carrier_kpi_metric (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  carrier_id         UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000', -- nil = org default
  metric_key         TEXT NOT NULL,                 -- stable key (maps to the calc + payout_config col)
  label              TEXT NOT NULL,                 -- display name the user chooses (carrier-specific)
  target_default     NUMERIC DEFAULT 0,             -- default target % when payout_config has none
  payout_config_col  TEXT,                          -- which payout_config column holds the per-period target
  sort               INT NOT NULL DEFAULT 0,
  is_active          BOOLEAN NOT NULL DEFAULT true,
  created_at         TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, carrier_id, metric_key)
);
CREATE INDEX IF NOT EXISTS carrier_kpi_metric_lookup ON commcalc.carrier_kpi_metric (org_id, carrier_id, sort);

-- Seed the current 7 as the org-wide default (nil carrier), neutral labels (de-Boosted), mapped to their
-- existing payout_config columns + current default targets — so nothing changes until edited.
INSERT INTO commcalc.carrier_kpi_metric (org_id, carrier_id, metric_key, label, target_default, payout_config_col, sort)
VALUES
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 'atu',        'ATU',               55, 'kpi_atu_target',        1),
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 'protect',    'Protection %',      80, 'kpi_protect_target',    2),
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 'boostapp',   'App Attach %',      65, 'kpi_boostapp_target',   3),
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 'familyplan', 'Family Plan',       45, 'kpi_familyplan_target', 4),
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 'byod',       'BYOD',              35, 'kpi_byod_target',       5),
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 'tmr3',       '3-Month Retention', 70, 'kpi_tmr3_target',       6),
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 'aal',        'AAL',                5, 'kpi_aal_target',        7)
ON CONFLICT (org_id, carrier_id, metric_key) DO NOTHING;

ALTER TABLE commcalc.carrier_kpi_metric ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='carrier_kpi_metric' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.carrier_kpi_metric FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;
