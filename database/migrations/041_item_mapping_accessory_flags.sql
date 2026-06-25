-- 041_item_mapping_accessory_flags.sql
-- Two pieces for the "accessories sold over $X → rep chargeback" feature:
--
-- 1. commcalc.item_mapping — the master ITEM → (type, phone model) map. Seeded from the Product
--    Catalog ("SU sheet") and auto-grown: any item seen in raw_sales that isn't here yet is added
--    as an 'unclassified' stub (item_type guessed from its Department/Category) for the user to
--    correct on the Item / Model Mapping page. item_type drives whether a sales line is treated as
--    an accessory; device_model is the phone model shown on flags / chargebacks.
--      item_type: accessory | phone | other | unclassified
--    Keyed by item_key = SKU when present, else the (upper-cased, trimmed) product description.
--
-- 2. commcalc.flag_rules — a single org-level config row holding the USER-DEFINED accessory rules:
--    the price threshold (default $35) above which an accessory sale is flagged, and the default
--    chargeback amount pre-filled on each flag (overridable per row before pushing to chargebacks).
--
-- Idempotent (CREATE ... IF NOT EXISTS). Re-running is safe.

CREATE TABLE IF NOT EXISTS commcalc.item_mapping (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  item_key      TEXT NOT NULL,                 -- SKU if present, else upper(trim(product_desc))
  sku           TEXT,
  item_desc     TEXT,
  department    TEXT,                          -- last-seen raw_sales Department (for context)
  category      TEXT,                          -- last-seen raw_sales Category
  item_type     TEXT NOT NULL DEFAULT 'unclassified',  -- accessory | phone | other | unclassified
  device_model  TEXT,                          -- the phone model (from the SU sheet / catalog)
  source        TEXT DEFAULT 'auto',           -- catalog | auto | manual
  created_at    TIMESTAMPTZ DEFAULT now(),
  updated_at    TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS item_mapping_key_idx ON commcalc.item_mapping (org_id, item_key);
CREATE INDEX IF NOT EXISTS item_mapping_type_idx ON commcalc.item_mapping (org_id, item_type);

CREATE TABLE IF NOT EXISTS commcalc.flag_rules (
  id                          SMALLINT PRIMARY KEY DEFAULT 1,
  org_id                      UUID NOT NULL,
  accessory_threshold         NUMERIC DEFAULT 35,   -- flag accessory sales with ext_price ABOVE this
  accessory_chargeback_amount NUMERIC DEFAULT 0,    -- default chargeback $ pre-filled per flagged row
  updated_at                  TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT flag_rules_singleton CHECK (id = 1)
);

INSERT INTO commcalc.flag_rules (id, org_id, accessory_threshold, accessory_chargeback_amount)
VALUES (1, '00000000-0000-0000-0000-000000000001', 35, 0)
ON CONFLICT (id) DO NOTHING;
