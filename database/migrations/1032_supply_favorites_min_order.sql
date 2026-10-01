-- Migration 1032 — Supply: favourite items + the vendor's minimum order quantity (owner 2026-10-01, index §36).
--
-- Owner: "add a star to favorite an item and store them in a favorites list, to reorder with the price
-- comparison" · "it should pick what is the quantity required to place the order for each item, some items
-- have min order qty".
--
--   1. commcalc.vendor_catalog_price +min_order_qty +order_multiple — what the vendor's product page says
--      ("Min: 10", "Units: 5"), read by the ONE catalog reader and landed by the ONE lander
--      (supply/store.land_catalog). The cart's ONE quantity rule (ordering_logic.order_packs) raises a line
--      to the minimum and rounds it to the multiple. NULL = the page did not say (no rule applied).
--   2. commcalc.supply_favorite — a starred item, per company: the vendor + the item's stable identity
--      (item_key, the same key every catalog read lands under), so a favourite survives every new price read.
--      The favourites list IS the price comparison filtered to these items (GET /supply/compare?favorites=1)
--      — no second comparison path.
--   3. Supply opens to EVERY business type (owner: "i cannot see the supplier module in the other tenants, i want
--      to add accessory suppliers with their prices there"). The module was scoped to one vertical by mig 1020;
--      the scope is config (core.module_catalog.applies_to_vertical, '{}' = any), mirrored in
--      core/verticals.HOUSE_MODULE_VERTICALS — harness_tenant_vertical folds this migration into the mirror check.
--
-- Idempotent, additive. Money: none (prices and orders are unchanged; a cart line may order MORE packs to
-- meet a vendor minimum, said on the line).
--
-- REVERT:
--   (module scope back to one vertical: re-run mig 1020's module_catalog seed value for supply_ordering)
--   DROP TABLE IF EXISTS commcalc.supply_favorite;
--   ALTER TABLE commcalc.vendor_catalog_price DROP COLUMN IF EXISTS min_order_qty, DROP COLUMN IF EXISTS order_multiple;

ALTER TABLE commcalc.vendor_catalog_price ADD COLUMN IF NOT EXISTS min_order_qty INT;   -- the vendor's minimum order (packs/units as the vendor sells)
ALTER TABLE commcalc.vendor_catalog_price ADD COLUMN IF NOT EXISTS order_multiple INT;  -- order in steps of N (e.g. "Units: 5")

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'vcp_min_order_chk') THEN
    ALTER TABLE commcalc.vendor_catalog_price ADD CONSTRAINT vcp_min_order_chk
      CHECK ((min_order_qty IS NULL OR min_order_qty BETWEEN 1 AND 100000)
         AND (order_multiple IS NULL OR order_multiple BETWEEN 1 AND 100000));
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS commcalc.supply_favorite (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  vendor_id   UUID NOT NULL REFERENCES commcalc.po_vendor(id) ON DELETE CASCADE,
  item_key    TEXT NOT NULL,                 -- vendor_catalog_price.item_key (stable across reads)
  label       TEXT,                          -- the product name when starred (shown if the item drops out of a read)
  reorder_qty INT CHECK (reorder_qty IS NULL OR reorder_qty BETWEEN 1 AND 100000),
  created_by  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, vendor_id, item_key)
);
CREATE INDEX IF NOT EXISTS ix_supply_favorite_org ON commcalc.supply_favorite (org_id);

ALTER TABLE commcalc.supply_favorite ENABLE ROW LEVEL SECURITY;
GRANT ALL ON commcalc.supply_favorite TO service_role;

UPDATE core.module_catalog SET applies_to_vertical = '{}' WHERE key = 'supply_ordering';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1032 complete — vendor_catalog_price +min_order_qty/+order_multiple, commcalc.supply_favorite, Supply open to every business type' AS status;
