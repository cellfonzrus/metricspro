-- 864_pos_special_order_catalog.sql — POS "Customer Special Order" (owner directive 2026-08-19),
-- Phase 1: the HIDDEN vendor catalog.
--
-- Lets HQ curate items the stores DON'T stock but can special-order for a customer, sourced from a
-- back-end vendor (Amazon) whose identity is NEVER exposed to store staff or the customer. The full
-- design is in docs/POS_SPECIAL_ORDER_PLAN.md.
--
-- Two additive pieces, both org-scoped per the migration-728 tenant-scoped-FK convention
-- (org_id NOT NULL + composite (org_id, fk) FK into a parent's UNIQUE (org_id, id)):
--   1) pos.products.is_special_order — flags a catalog product as special-order-only (offered through
--      the POS "Customer special order" flow, not normal in-store stock).
--   2) pos.special_order_vendor — the HQ-ONLY vendor linkage (Amazon ASIN / URL / cost). This is the
--      ONLY place the vendor is named. It is NEVER returned by the store/customer-facing catalog API;
--      only the HQ admin API (gated by the pos_special_order_admin permission) reads it. Source-hiding
--      is enforced at the API boundary, not just in the UI.
--
-- Additive + idempotent. No RLS/grant change — pos.* is service-role-only behind the FastAPI backend.
ALTER TABLE pos.products
  ADD COLUMN IF NOT EXISTS is_special_order boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS pos.special_order_vendor (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL,
  product_id     uuid NOT NULL,
  vendor         text NOT NULL DEFAULT 'amazon',   -- kept generic on purpose; not shown store-side
  vendor_sku     text,           -- ASIN / vendor SKU (HQ-only)
  vendor_url     text,           -- product URL at the vendor (HQ-only)
  vendor_cost    numeric,        -- unit cost at the vendor (HQ-only) — the COGS basis for the sale
  lead_time_days integer,        -- typical ship-to-store lead time, for the customer promise
  notes          text,
  is_active      boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  UNIQUE (org_id, product_id),
  CONSTRAINT special_order_vendor_product_fk
    FOREIGN KEY (org_id, product_id) REFERENCES pos.products (org_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_special_order_vendor_org ON pos.special_order_vendor (org_id, product_id);
CREATE INDEX IF NOT EXISTS ix_pos_products_special_order ON pos.products (org_id, is_special_order) WHERE is_special_order;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 864 complete — pos.products.is_special_order + pos.special_order_vendor (HQ-only vendor linkage)' AS status;
