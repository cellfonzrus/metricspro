-- 866_pos_vendor_connectors.sql — POS "Customer Special Order" (owner directive 2026-08-19), Phase 2.5:
-- PLUG-AND-PLAY vendor connectors. See docs/POS_SPECIAL_ORDER_PLAN.md.
--
-- Amazon is just one dropship vendor. Any vendor with a dropship platform can be added by registering a
-- connector row here — no code change for a 'manual' or 'inbound_api' vendor; only 'outbound_api'
-- vendors (where WE call THEIR API) need a per-vendor adapter (pos/vendor_adapters.py).
--
-- Two integration directions, per connector:
--   • outbound_api — WE call THEIR API to place/refresh the order (api_base_url + credential_ref, where
--     credential_ref names an env/secret; the raw key is NEVER stored in the DB).
--   • inbound_api  — THEY call OUR API: the vendor authenticates with a per-vendor token (only its
--     SHA-256 hash is stored, inbound_token_hash) to pull their queued orders + post status/tracking.
--   • manual       — HQ/ops fulfills from the queue (the default; Amazon at launch).
--
-- The special-order catalog links a product to a vendor by pos.special_order_vendor.vendor == vendor_key.
-- Org-scoped per the migration-728 convention. Additive + idempotent. pos.* is service-role-only.
CREATE TABLE IF NOT EXISTS pos.vendor_connector (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             uuid NOT NULL,
  vendor_key         text NOT NULL,                 -- stable key, e.g. 'amazon' (matches special_order_vendor.vendor)
  display_name       text,                          -- HQ-only label; never shown store/customer-side
  integration_mode   text NOT NULL DEFAULT 'manual'
                     CHECK (integration_mode IN ('manual','outbound_api','inbound_api')),
  api_base_url       text,                           -- outbound_api: the vendor's API base
  credential_ref     text,                           -- outbound_api: NAME of the env/secret holding the key (never the key)
  inbound_token_hash text,                           -- inbound_api: SHA-256 of the vendor's access token
  config             jsonb NOT NULL DEFAULT '{}'::jsonb,   -- mode-specific knobs
  is_active          boolean NOT NULL DEFAULT true,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  UNIQUE (org_id, vendor_key)
);
-- Fast inbound-token lookup (them → us): the token hash is globally unique in practice, so an inbound
-- request resolves to exactly one (org, vendor) connector.
CREATE INDEX IF NOT EXISTS ix_vendor_connector_inbound ON pos.vendor_connector (inbound_token_hash)
  WHERE inbound_token_hash IS NOT NULL;

-- Seed a 'manual' Amazon connector for every org that already has special-order vendor rows, so the
-- existing catalog keeps working with no HQ action. Idempotent (ON CONFLICT DO NOTHING on the unique key).
INSERT INTO pos.vendor_connector (org_id, vendor_key, display_name, integration_mode)
SELECT DISTINCT org_id, 'amazon', 'Amazon (manual)', 'manual'
  FROM pos.special_order_vendor
 WHERE (vendor IS NULL OR vendor = 'amazon')
ON CONFLICT (org_id, vendor_key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 866 complete — pos.vendor_connector (plug-and-play dropship vendors)' AS status;
