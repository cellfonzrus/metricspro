-- 216_commission_inventory_aging_device.sql — per-DEVICE inventory-aging cost (device-history v2)
-- Run this in the Supabase SQL editor (Claude cannot run SQL).
--
-- WHY: owner directive 2026-07-17 — "the inventory aging should give the correct purchase price."
-- The b2bsoft/POS "Inventory Aging" report carries a per-DEVICE cost (imei/serial + unit cost +
-- received/aging date). The store-level roll-up (commcalc.inventory_value, mig 026) discards it. This
-- adds the per-device table the Device History lookup reads for its UNIVERSAL, POS/SKU-based purchase
-- price (owed_to_vip is demoted to last resort). The commcalc ingest (upload_file, file_type=
-- 'inventory_aging') now also upserts one row per device here, keyed on (org_id, imei).
--
-- SAFE: additive + idempotent (IF NOT EXISTS everywhere). Nothing existing changes; until this runs the
-- ingest catches the missing table (try/except → per-store inventory_value ingest is unaffected) and
-- the device-history lookup simply shows the honest "no cost on file" line for POS-cost sources.

CREATE TABLE IF NOT EXISTS commcalc.inventory_aging_device (
  id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  imei           TEXT,                                   -- canonical device key (imei, else serial)
  serial         TEXT,
  sku            TEXT,
  item           TEXT,                                   -- product / description
  store          TEXT,
  unit_cost      NUMERIC,                                -- POS on-hand cost per device (SKU-based)
  received_date  DATE,                                   -- when it entered inventory (aging basis)
  days_in_stock  INT,                                    -- report's aging days (when given directly)
  as_of_date     DATE,                                   -- snapshot date of the file
  source         TEXT        DEFAULT 'inventory_aging',
  raw_row        JSONB,                                  -- original row for honest re-derivation
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- org_id-scoped lookups (device-history matches on imei; SKU lookups for future recon).
CREATE INDEX IF NOT EXISTS inventory_aging_device_org_imei ON commcalc.inventory_aging_device (org_id, imei);
CREATE INDEX IF NOT EXISTS inventory_aging_device_org_sku  ON commcalc.inventory_aging_device (org_id, sku);
-- conflict target for the ingest upsert (hourly re-pull refreshes rather than duplicates). NULL imei
-- rows are never inserted (a device with no imei/serial is skipped), and NULLs are distinct in a
-- unique index anyway, so this is safe.
CREATE UNIQUE INDEX IF NOT EXISTS inventory_aging_device_org_imei_uq ON commcalc.inventory_aging_device (org_id, imei);

ALTER TABLE commcalc.inventory_aging_device ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY open_all ON commcalc.inventory_aging_device FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN OTHERS THEN NULL; END $$;
GRANT ALL ON commcalc.inventory_aging_device TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 216 complete — commcalc.inventory_aging_device ready (per-device inventory-aging cost)' AS status;
