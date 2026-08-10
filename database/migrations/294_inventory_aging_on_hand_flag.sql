-- 294 — inventory_aging_device: separate "still on hand" from "we have a cost for this device".
--
-- WHY (2026-08-10). Owner report: "inventory is really those phones which were not sold ... the device
-- which got sold did not pay rebate should not be in inventory as it got sold." Correct — and measured:
-- 1,057 of luxelink's 2,097 rows (50%) were already sold in POS, because the ingest only ever UPSERT-ed
-- (org_id,imei) and never removed a device that left the shelf.
--
-- The obvious fix — delete the rows the newest file no longer lists — is WRONG, and this migration
-- exists because of it. `inventory_aging_device.unit_cost` is ALSO source ① of the Device History
-- purchase-price chain (commcalc/router.py, "AGING + OUR PURCHASE PRICE"), the universal POS/SKU-based
-- cost that the VIP-only owed_to_vip was deliberately demoted beneath. Deleting a sold device would
-- destroy the primary cost record for exactly the devices whose cost matters most (the sold ones) and
-- silently downgrade them to the at-sale fallback — a COGS regression dressed up as an inventory fix.
--
-- So the table carries TWO facts and they are now stored as two facts:
--   • on_hand      — is this device still in stock? (the inventory COUNT + VALUE question)
--   • unit_cost    — what did we pay for it? (permanent, survives the sale)
-- The ingest flips on_hand=false instead of deleting. Nothing is ever lost; "current inventory" is
-- `on_hand = true`, and the cost lookup ignores the flag entirely.

ALTER TABLE commcalc.inventory_aging_device
  ADD COLUMN IF NOT EXISTS on_hand boolean NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS off_hand_as_of date;

COMMENT ON COLUMN commcalc.inventory_aging_device.on_hand IS
  'TRUE while the device is still listed by its store''s latest Inventory Aging export. Set FALSE by the '
  'ingest when a newer export for that store no longer lists it (sold / transferred out). The row is '
  'KEPT because unit_cost is source (1) of the Device History purchase-price chain — never delete it.';
COMMENT ON COLUMN commcalc.inventory_aging_device.off_hand_as_of IS
  'The as_of_date of the export that first no longer listed this device (i.e. when it left the shelf).';

-- Every consumer that means CURRENT inventory filters on this; make that cheap.
CREATE INDEX IF NOT EXISTS inventory_aging_device_on_hand_idx
  ON commcalc.inventory_aging_device (org_id, on_hand);

-- One-off correction of the 50% backlog: anything whose as_of_date is older than its OWN store's latest
-- export was already gone from the shelf and simply never got removed. Scoped per store, exactly like
-- the ingest's own rule, so a store whose export is stale is not wrongly emptied.
WITH latest AS (
  SELECT org_id, store, MAX(as_of_date) AS max_as_of
    FROM commcalc.inventory_aging_device
   WHERE store IS NOT NULL
   GROUP BY org_id, store
)
UPDATE commcalc.inventory_aging_device d
   SET on_hand = false,
       off_hand_as_of = COALESCE(d.off_hand_as_of, l.max_as_of)
  FROM latest l
 WHERE d.org_id = l.org_id
   AND d.store  = l.store
   AND d.as_of_date < l.max_as_of
   AND d.on_hand;
