-- 043_device_model_registry.sql
-- Canonical phone-model registry for the Item / Model Mapping "catalogue".
--
-- device_model on commcalc.item_mapping is free text per item. To make phone model (a) selectable
-- from the models already in the system and (b) addable on its own (a new model that isn't on any
-- item yet), this table holds the canonical list. The model combobox unions this registry with the
-- distinct device_model values already used on item_mapping, so nothing existing is lost.
--
-- Idempotent (CREATE ... IF NOT EXISTS). Re-running is safe.

CREATE TABLE IF NOT EXISTS commcalc.device_model (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL,
  model      TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS device_model_uq ON commcalc.device_model (org_id, model);

-- RLS + grants (service_role doesn't get privileges on new commcalc tables automatically).
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['commcalc.device_model'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

-- Backfill the registry from models already classified on item_mapping, so the combobox is
-- populated immediately for existing data.
INSERT INTO commcalc.device_model (org_id, model)
SELECT DISTINCT org_id, device_model
FROM commcalc.item_mapping
WHERE device_model IS NOT NULL AND btrim(device_model) <> ''
ON CONFLICT (org_id, model) DO NOTHING;
