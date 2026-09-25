-- 1021_supply_ordering.sql — SUPPLY ORDERING: vendor price compare, cart optimizer, assisted order +
-- confirmation capture (index §36; module key `supply_ordering`, catalogued + vertical-scoped by mig 1020).
-- Band 1000+ (platform). Additive + idempotent (safe to re-run). Requires 301 (Purchase Orders) and 083
-- (commcalc.data_source).
--
-- MONEY-ADJACENT, SURFACED FOR OWNER APPROVAL BEFORE APPLYING: it adds columns to commcalc.purchase_order
-- (the vendor's order number / total / shipping estimate / confirmation evidence) and a vendor price
-- snapshot table. It moves no money, books nothing to the P&L and changes no existing row: every new column
-- is NULL / '{}' / false on existing rows, and no existing reader selects them.
--
-- OWNER (2026-09-24/25), verbatim (abridged): "Create a dashboard for UPS Store operations and add the
-- comparison prices module there, the user should be able to add the items to the cart in the cheapest
-- vendors cart from this platform and place the order, the order confirmation should be captured back in
-- and updated in the system. Each vendor had the free shipping threshold which needs to be defined by the
-- tenant. When setting up the vendor module the tenant needs to define this and the approx delivery time
-- for the vendor." · vendors "not to be hard coded".
--
-- DUPLICATE CHECK (build gate — searched, and what is reused instead of a sibling):
--   · commcalc.po_vendor (301)         — THE vendor roster. EXTENDED here (portal, catalog links, config,
--                                        free-shipping threshold, shipping fee, delivery days, login link,
--                                        is_price_source). No second vendor table.
--   · commcalc.purchase_order(+_line) (301) + next_po_number + the draft→submitted→… lifecycle
--                                      — THE order. A supply cart becomes ONE purchase_order per vendor
--                                        (source='supply_cart'); EXTENDED here with the vendor's confirmation.
--   · commcalc.data_source (083) + router._SOURCE_SECRETS — THE login + credential store. po_vendor only
--                                        points at the row (data_source_id). No new credential column.
--   · commcalc/live_login + /data-sources/sweep/run-due — THE browser path (assisted order, catalog read).
--   · pos.vendors / pos.purchase_orders (726) — the POS module's own receiving tables for a POS catalog;
--                                        not the ordering roster the Purchase Orders module already owns.
--   · commcalc.distributors (058), vendor rebate history (§27) — wireless supply terms / rebates; no
--                                        catalog price. Nothing stored "what does vendor X charge for item Y
--                                        today", so vendor_catalog_price is NEW (the only new table).
--
-- REVERT (in order):
--   DROP TABLE IF EXISTS commcalc.vendor_catalog_price;
--   ALTER TABLE commcalc.purchase_order DROP COLUMN IF EXISTS vendor_order_ref, DROP COLUMN IF EXISTS vendor_order_total,
--     DROP COLUMN IF EXISTS shipping_estimate, DROP COLUMN IF EXISTS supply_cart_ref, DROP COLUMN IF EXISTS supply_meta,
--     DROP COLUMN IF EXISTS cart_evidence, DROP COLUMN IF EXISTS confirmation, DROP COLUMN IF EXISTS submitted_by,
--     DROP COLUMN IF EXISTS submitted_at;
--   ALTER TABLE commcalc.po_vendor DROP CONSTRAINT IF EXISTS po_vendor_portal_terms_chk,
--     DROP CONSTRAINT IF EXISTS po_vendor_delivery_days_chk, DROP CONSTRAINT IF EXISTS po_vendor_money_chk,
--     DROP CONSTRAINT IF EXISTS po_vendor_data_source_fk;
--   ALTER TABLE commcalc.po_vendor DROP COLUMN IF EXISTS portal_url, DROP COLUMN IF EXISTS catalog_urls,
--     DROP COLUMN IF EXISTS portal_config, DROP COLUMN IF EXISTS free_shipping_threshold,
--     DROP COLUMN IF EXISTS shipping_fee_below_threshold, DROP COLUMN IF EXISTS delivery_days_min,
--     DROP COLUMN IF EXISTS delivery_days_max, DROP COLUMN IF EXISTS data_source_id, DROP COLUMN IF EXISTS is_price_source;

-- ── 1. THE VENDOR (commcalc.po_vendor, extended) ─────────────────────────────────────────────────────────
ALTER TABLE commcalc.po_vendor ADD COLUMN IF NOT EXISTS portal_url TEXT;                         -- the vendor's login page
ALTER TABLE commcalc.po_vendor ADD COLUMN IF NOT EXISTS catalog_urls TEXT[] NOT NULL DEFAULT '{}'; -- where the catalog walk starts
ALTER TABLE commcalc.po_vendor ADD COLUMN IF NOT EXISTS portal_config JSONB NOT NULL DEFAULT '{}'::jsonb; -- a vendors.json block + optional `ordering` recipe (never a credential)
ALTER TABLE commcalc.po_vendor ADD COLUMN IF NOT EXISTS free_shipping_threshold NUMERIC(12,2);     -- tenant-defined: order $ at/above which shipping is free
ALTER TABLE commcalc.po_vendor ADD COLUMN IF NOT EXISTS shipping_fee_below_threshold NUMERIC(12,2); -- optional estimate charged below it
ALTER TABLE commcalc.po_vendor ADD COLUMN IF NOT EXISTS delivery_days_min INT;                   -- tenant-defined approx delivery time
ALTER TABLE commcalc.po_vendor ADD COLUMN IF NOT EXISTS delivery_days_max INT;
ALTER TABLE commcalc.po_vendor ADD COLUMN IF NOT EXISTS data_source_id UUID;                     -- the portal login row (commcalc.data_source)
ALTER TABLE commcalc.po_vendor ADD COLUMN IF NOT EXISTS is_price_source BOOLEAN NOT NULL DEFAULT false; -- a portal vendor we read prices from / order through

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'po_vendor_data_source_fk') THEN
    ALTER TABLE commcalc.po_vendor ADD CONSTRAINT po_vendor_data_source_fk
      FOREIGN KEY (data_source_id) REFERENCES commcalc.data_source(id) ON DELETE SET NULL;
  END IF;
  -- A PORTAL vendor must carry the tenant-defined free-shipping threshold and delivery time (the owner's rule).
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'po_vendor_portal_terms_chk') THEN
    ALTER TABLE commcalc.po_vendor ADD CONSTRAINT po_vendor_portal_terms_chk
      CHECK (NOT is_price_source OR (free_shipping_threshold IS NOT NULL AND delivery_days_max IS NOT NULL));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'po_vendor_delivery_days_chk') THEN
    ALTER TABLE commcalc.po_vendor ADD CONSTRAINT po_vendor_delivery_days_chk
      CHECK ((delivery_days_min IS NULL OR delivery_days_min BETWEEN 0 AND 365)
         AND (delivery_days_max IS NULL OR delivery_days_max BETWEEN 0 AND 365)
         AND (delivery_days_min IS NULL OR delivery_days_max IS NULL OR delivery_days_min <= delivery_days_max));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'po_vendor_money_chk') THEN
    ALTER TABLE commcalc.po_vendor ADD CONSTRAINT po_vendor_money_chk
      CHECK ((free_shipping_threshold IS NULL OR free_shipping_threshold >= 0)
         AND (shipping_fee_below_threshold IS NULL OR shipping_fee_below_threshold >= 0));
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS ix_po_vendor_org_source ON commcalc.po_vendor (org_id, data_source_id);

-- ── 2. THE ORDER (commcalc.purchase_order, extended with the vendor's confirmation) ─────────────────────
ALTER TABLE commcalc.purchase_order ADD COLUMN IF NOT EXISTS vendor_order_ref TEXT;             -- the vendor's order / confirmation number
ALTER TABLE commcalc.purchase_order ADD COLUMN IF NOT EXISTS vendor_order_total NUMERIC(12,2);  -- the vendor's confirmed total (when captured)
ALTER TABLE commcalc.purchase_order ADD COLUMN IF NOT EXISTS shipping_estimate NUMERIC(12,2);   -- the optimizer's shipping for this vendor
ALTER TABLE commcalc.purchase_order ADD COLUMN IF NOT EXISTS supply_cart_ref TEXT;              -- the cart this PO was split from (one PO per vendor)
ALTER TABLE commcalc.purchase_order ADD COLUMN IF NOT EXISTS supply_meta JSONB NOT NULL DEFAULT '{}'::jsonb; -- line links, threshold, delivery window, plan saving
ALTER TABLE commcalc.purchase_order ADD COLUMN IF NOT EXISTS cart_evidence JSONB;              -- the vendor cart as captured (total, lines added, screenshot)
ALTER TABLE commcalc.purchase_order ADD COLUMN IF NOT EXISTS confirmation JSONB;               -- how the confirmation was captured (live / manual), page, screenshot
ALTER TABLE commcalc.purchase_order ADD COLUMN IF NOT EXISTS submitted_by TEXT;
ALTER TABLE commcalc.purchase_order ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS ix_po_org_source ON commcalc.purchase_order (org_id, source);
CREATE INDEX IF NOT EXISTS ix_po_org_cart ON commcalc.purchase_order (org_id, supply_cart_ref) WHERE supply_cart_ref IS NOT NULL;

-- ── 3. THE PRICES (ONE snapshot table) ───────────────────────────────────────────────────────────────────
-- One row per product per vendor per catalog read (run_id). Readers take each vendor's NEWEST run; older
-- runs are history. Landed ONLY by supply/store.land_catalog (kit upload + portal read).
CREATE TABLE IF NOT EXISTS commcalc.vendor_catalog_price (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  vendor_id     UUID NOT NULL REFERENCES commcalc.po_vendor(id) ON DELETE CASCADE,
  run_id        UUID NOT NULL,
  source        TEXT NOT NULL DEFAULT 'upload',          -- 'upload' (the kit's products file) | 'portal' (live / scheduled read)
  item_key      TEXT NOT NULL,                           -- 's:<item number>' or 'u:<hash of link + name>'
  sku           TEXT,
  name          TEXT NOT NULL,
  price         NUMERIC(12,4) NOT NULL CHECK (price > 0),
  list_price    NUMERIC(12,4),
  pack_qty      INT,
  availability  TEXT NOT NULL DEFAULT 'unknown' CHECK (availability IN ('in_stock','backorder','out_of_stock','unknown')),
  stock_qty     INT,
  stock_text    TEXT,
  url           TEXT,
  description   TEXT,
  seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_vcp_org ON commcalc.vendor_catalog_price (org_id);
CREATE INDEX IF NOT EXISTS ix_vcp_org_vendor_seen ON commcalc.vendor_catalog_price (org_id, vendor_id, seen_at DESC);
CREATE INDEX IF NOT EXISTS ix_vcp_org_run ON commcalc.vendor_catalog_price (org_id, run_id);
CREATE INDEX IF NOT EXISTS ix_vcp_org_vendor_item ON commcalc.vendor_catalog_price (org_id, vendor_id, item_key);

-- RLS on, no anon/authenticated policy: only the backend (service role, every query org-scoped — CI's
-- org-scope guard scans backend/app/modules/supply) reads or writes it. Same posture as mig 1018.
ALTER TABLE commcalc.vendor_catalog_price ENABLE ROW LEVEL SECURITY;
GRANT ALL ON commcalc.vendor_catalog_price TO service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1021 complete — po_vendor +portal/threshold/delivery/login, purchase_order +vendor confirmation, commcalc.vendor_catalog_price' AS status;
