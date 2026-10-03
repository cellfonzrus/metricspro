-- 1046_store_visit_alerts.sql — STORE VISIT FOLLOW-THROUGH: the to-do alert, the separate accessory
-- notification, and the DRAFT purchase order raised from a visit's accessory list.
--
-- OWNER ASK 2026-10-03, verbatim:
--   "based on the store visits need to create an email and whats app alert to the dm and all people
--    above to send them a lit of all items which are needed to be done, also create a notification
--    list for the store visit, default will be dm and above, a list of accesories to be created as a
--    separate notification and a purchase oirder automatically created to be sent to vaccessorize"
--
-- WHAT THIS MIGRATION DOES *NOT* CREATE, because it already exists and is dereferenced:
--   · the NOTIFICATION LIST          storeops.alert_recipient (mig 089) — scope / name / email /
--                                    whatsapp / via_email / via_whatsapp / include_dm, with its own
--                                    editor on the Cash & Closing Alerts page. The two new scopes
--                                    are VALUES in that table ('store_visit_todo',
--                                    'store_visit_accessories'), not a new table. A tenant with NO
--                                    rows for a scope gets the house default, which is the owner's
--                                    ask: the DM and every manager above the DM, resolved from
--                                    storeops.org_chain. Nothing is seeded.
--   · the DEDUP trail                storeops.alert_log (mig 089), same scope values.
--   · the ONE fan-out                commcalc/manager_digest — recipients, one digest each, the
--                                    ref_key spelling, the unreachable-recipient skip.
--   · the PURCHASE ORDER             commcalc.purchase_order + _line (mig 301) and next_po_number.
--                                    A store-visit accessory list is not a different kind of PO, so
--                                    it is the same table, the same numbering and the same
--                                    draft -> submitted -> received lifecycle a supply cart uses,
--                                    distinguished only by source = 'store_visit'.
--   · the accessory reorder LINK     storeops.store_visit_config (mig 503), already per tenant.
--
-- NOT MONEY-MOVING AND NOT A SEND. Every switch below defaults to OFF/'off', so applying this
-- migration changes no behaviour at all: nothing emails, nothing WhatsApps, and no purchase order
-- is created until a tenant switches it on. The purchase order it then creates is a DRAFT, and no
-- code path in this platform transmits it to a supplier — see §: integrating a supplier's own store
-- needs credentials nobody has given us yet, and until then the draft is sent by a human.
--
-- RULE TWO: no vendor, carrier, tenant or product name appears in this file. The accessory supplier
-- is a commcalc.po_vendor row the tenant points at (store_visit_accessory_vendor_id).
--
-- REVERT:
--   ALTER TABLE storeops.tenants
--     DROP COLUMN IF EXISTS store_visit_alert_enabled,
--     DROP COLUMN IF EXISTS store_visit_alert_channels,
--     DROP COLUMN IF EXISTS store_visit_alert_lookback_days,
--     DROP COLUMN IF EXISTS store_visit_alert_min_items,
--     DROP COLUMN IF EXISTS store_visit_accessory_alert_enabled,
--     DROP COLUMN IF EXISTS store_visit_accessory_channels,
--     DROP COLUMN IF EXISTS store_visit_accessory_vendor_id,
--     DROP COLUMN IF EXISTS store_visit_accessory_po_mode;
--   ALTER TABLE commcalc.purchase_order DROP COLUMN IF EXISTS store_visit_id;
--   (Dropping them restores the house defaults, which are OFF — nothing would send.)

-- ── 1. per-tenant config (house defaults mirror visit_alerts.HOUSE_CONFIG) ──────────────────────
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS store_visit_alert_enabled            boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS store_visit_alert_channels           jsonb   NOT NULL DEFAULT '["whatsapp","email"]'::jsonb,
  ADD COLUMN IF NOT EXISTS store_visit_alert_lookback_days      integer NOT NULL DEFAULT 7,
  ADD COLUMN IF NOT EXISTS store_visit_alert_min_items          integer NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS store_visit_accessory_alert_enabled  boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS store_visit_accessory_channels       jsonb   NOT NULL DEFAULT '["whatsapp","email"]'::jsonb,
  ADD COLUMN IF NOT EXISTS store_visit_accessory_vendor_id      uuid,
  ADD COLUMN IF NOT EXISTS store_visit_accessory_po_mode        text    NOT NULL DEFAULT 'off';

COMMENT ON COLUMN storeops.tenants.store_visit_alert_lookback_days IS
  'Days. How far back a sweep looks for SUBMITTED visits whose to-do items nobody has been told '
  'about yet. It is not a repeat window: storeops.alert_log keys on (visit, item), so a given item '
  'is announced once however often the sweep runs, and a longer lookback only catches visits that '
  'were submitted while the alert was switched off or a channel was down.';

COMMENT ON COLUMN storeops.tenants.store_visit_accessory_po_mode IS
  '''off'' (default) or ''draft''. There is deliberately no ''submit'': no transport to a supplier''s '
  'own store exists in this platform, and a mode that silently did nothing would be worse than '
  'refusing the word. ''draft'' resolves back to ''off'' when store_visit_accessory_vendor_id names '
  'no vendor, and the sweep result says so rather than failing every tick.';

COMMENT ON COLUMN storeops.tenants.store_visit_accessory_vendor_id IS
  'The commcalc.po_vendor row the accessory purchase order is raised against. A vendor row, never a '
  'name in code (RULE TWO): the platform has no knowledge of who any tenant buys accessories from.';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tenants_store_visit_po_mode_chk') THEN
    ALTER TABLE storeops.tenants ADD CONSTRAINT tenants_store_visit_po_mode_chk
      CHECK (store_visit_accessory_po_mode IN ('off','draft'));
  END IF;
END $$;

-- ── 2. the PO's link back to the visit that asked for it ────────────────────────────────────────
-- This is the IDEMPOTENCY key for the draft: one visit gets one draft purchase order, however many
-- times the sweep runs. A partial unique index rather than a plain one, so the column stays NULL on
-- every PO that did not come from a visit (manual, forecast, supply cart) without colliding.
ALTER TABLE commcalc.purchase_order ADD COLUMN IF NOT EXISTS store_visit_id uuid;

COMMENT ON COLUMN commcalc.purchase_order.store_visit_id IS
  'The storeops.store_visits row whose accessory list raised this draft (source = ''store_visit''). '
  'NULL for every other kind of purchase order. Unique where present: a visit raises ONE draft.';

CREATE UNIQUE INDEX IF NOT EXISTS ux_po_org_store_visit
  ON commcalc.purchase_order (org_id, store_visit_id)
  WHERE store_visit_id IS NOT NULL;

NOTIFY pgrst, 'reload schema';

-- ── 3. pg_cron registration (run AFTER deploy, once, with the real secret) ──────────────────────
-- HOURLY, like mig 433 / 905 / 1043 / 1044. There is no per-tenant send TIME here on purpose: a
-- visit's to-do list is news when the visit is submitted, not at a fixed hour, and alert_log keys on
-- (visit, item) so an hourly tick announces each item exactly once.
--
--   SELECT cron.schedule('store-visit-alerts-run-due', '25 * * * *', $$
--     SELECT net.http_post(
--       url     := 'https://api.metricspro.tech/api/v1/storevisit/alerts/run-due',
--       headers := jsonb_build_object('Content-Type','application/json','X-Notify-Secret','<NOTIFY_RUN_SECRET>'),
--       body    := '{}'::jsonb); $$);

SELECT 'Migration 1046 complete — store-visit to-do alert, accessory notification and draft '
       'purchase order config on storeops.tenants (all OFF), plus '
       'commcalc.purchase_order.store_visit_id. Nothing sends and no PO is created until a tenant '
       'switches it on; the recipient list is the existing storeops.alert_recipient table, and a '
       'tenant with no rows gets the DM and every manager above the DM.' AS status;
