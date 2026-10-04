-- 1053_vendor_order_transport.sql — a vendor's ORDER ROUTE, declared once, and where a pushed
-- draft landed. Owner 2026-10-03/04: the store-visit accessory list should reach the vendor's own
-- store, which is a Shopify store the owner runs.
--
-- Additive and idempotent. Creates no vendor, switches nothing on, moves no money and sends
-- nothing: every new column defaults to the behaviour that exists today. Migration 1047 said in a
-- comment that "no transport to a supplier's own store exists in this platform"; this is that
-- transport, and the comment is corrected below rather than left to mislead.
--
-- RULE TWO: no vendor, store or brand name appears here. The route is a config block on the vendor
-- row and the API dialect is a VALUE inside it.
--
-- CREDENTIALS: nothing in this migration holds a token. The vendor's admin API token goes in that
-- vendor's commcalc.data_source login row, in a column of commcalc/router._SOURCE_SECRETS, which
-- never leaves the backend. A token in portal_config is REFUSED by
-- supply/order_transport.validate_transport.
--
-- REVERT:
--   ALTER TABLE commcalc.purchase_order
--     DROP COLUMN IF EXISTS external_ref,
--     DROP COLUMN IF EXISTS external_url,
--     DROP COLUMN IF EXISTS external_pushed_at,
--     DROP COLUMN IF EXISTS external_error;
--   DROP INDEX IF EXISTS commcalc.ux_po_org_external_ref;
--   ALTER TABLE storeops.tenants DROP CONSTRAINT IF EXISTS tenants_store_visit_po_mode_chk;
--   ALTER TABLE storeops.tenants ADD CONSTRAINT tenants_store_visit_po_mode_chk
--     CHECK (store_visit_accessory_po_mode IN ('off','draft'));
--   (Reverting the CHECK requires no row already holding 'draft_push'.)

-- ── 1. where a pushed draft landed on the vendor's side ─────────────────────────────────────────
-- THE IDEMPOTENCY KEY IS OURS. The vendor's API has no idempotency key, so a retry would create a
-- second basket. external_ref is what stops that: a PO that carries one is never pushed again.
ALTER TABLE commcalc.purchase_order
  ADD COLUMN IF NOT EXISTS external_ref        text,
  ADD COLUMN IF NOT EXISTS external_url        text,
  ADD COLUMN IF NOT EXISTS external_pushed_at  timestamptz,
  ADD COLUMN IF NOT EXISTS external_error      text;

COMMENT ON COLUMN commcalc.purchase_order.external_ref IS
  'The id the VENDOR gave the draft it created on its own system, when this PO was pushed over a '
  'declared order route (commcalc.po_vendor.portal_config->order_transport). It is the idempotency '
  'key: supply/order_transport''s caller never pushes a PO that already carries one, because the '
  'vendor API has no idempotency key of its own. NULL on every PO that was never pushed.';

COMMENT ON COLUMN commcalc.purchase_order.external_error IS
  'Why the last push did not land. Kept beside external_ref rather than only logged, so a draft '
  'that never reached the vendor is VISIBLE on the PO instead of being silently absent — the same '
  'ruling as a sweep whose status is not a record of what it did (index §19.41).';

-- A vendor id is unique per org; a partial unique index so the column stays NULL on every PO that
-- was never pushed without colliding.
CREATE UNIQUE INDEX IF NOT EXISTS ux_po_org_external_ref
  ON commcalc.purchase_order (org_id, external_ref)
  WHERE external_ref IS NOT NULL;

-- ── 2. the tenant may now ask for the draft to be PUSHED ────────────────────────────────────────
-- 'draft_push' raises the draft here AND creates a DRAFT on the vendor's own system. It still
-- places no order and emails nobody: the dialect never calls the vendor's invoice-send endpoint,
-- and order_transport refuses any route declaring places_order. A route that would PLACE the order
-- is never run by the sweep, however it is configured — a person drives that.
DO $$
BEGIN
  ALTER TABLE storeops.tenants DROP CONSTRAINT IF EXISTS tenants_store_visit_po_mode_chk;
  ALTER TABLE storeops.tenants ADD CONSTRAINT tenants_store_visit_po_mode_chk
    CHECK (store_visit_accessory_po_mode IN ('off','draft','draft_push'));
END $$;

COMMENT ON COLUMN storeops.tenants.store_visit_accessory_po_mode IS
  '''off'' (default), ''draft'', or ''draft_push''. ''draft'' raises a draft purchase order in '
  'MetricsPro and nothing leaves the building. ''draft_push'' additionally creates a DRAFT on the '
  'vendor''s own system over the route declared on that vendor '
  '(commcalc.po_vendor.portal_config->order_transport) — a basket the merchant reviews, not an '
  'order: nothing is charged and nobody is emailed. There is still deliberately no ''submit'': a '
  'route that PLACES an order is refused for an unattended sweep by '
  'supply/order_transport.push_enabled, whatever the config says. Any mode resolves back to '
  '''off'' when store_visit_accessory_vendor_id names no vendor, and ''draft_push'' resolves back '
  'to ''draft'' when that vendor declares no usable route; the sweep result says which and why '
  'rather than failing every tick.';

-- ── 3. tell PostgREST the shapes changed ────────────────────────────────────────────────────────
NOTIFY pgrst, 'reload schema';
