-- MIGRATION 1018: POS INVENTORY INTEGRITY — flags, the adjustment ledger, and the `adjusted_out` status
-- (owner 2026-09-24; docs/SYSTEM_DATA_FLOW_INDEX.md §11b). Idempotent, additive. OWNER-RUN — not applied by the agent.
--
-- Owner, verbatim: "it is not possible that 383 imei have not ben sold but appering in the invnetory so they have
-- to be crashed agains the sales report by invoice and the commission received reports to, it is possoble that the
-- receipt was not made and it sold, 2 things it shoudl apprear as a flag and also highlight which customer it was
-- sold to from commisison report and have the ability to assign it tot hte customer with one click if the imei was
-- sold and commission revceived to move it out of the inventory, the flag still stays there till verified by the
-- management that the imei was actually sold, need to do this for all tenants as platform wide".
-- Earlier: "duplicate entires of imei have to checked before they are received and should give a pop up …" and
-- "we need to make a mechanish of manually adjusting the inventory in or out of the system".
--
-- WHAT THIS ADDS
--   1. pos.inventory_serial.status may also be 'adjusted_out' — a unit taken out of stock by a manual adjustment
--      that is not a sale / loss / theft / RMA (a duplicate record of one device, a write-off, a count
--      correction). The CHECK is WIDENED only: every existing row stays valid, nothing is rewritten.
--   2. UNIQUE (org_id, id) on pos.inventory_serial — the target the tenant-scoped foreign key below needs (the
--      mig-728 pattern). `id` is the primary key, so this cannot fail on existing data.
--   3. pos.inventory_flags — one row per integrity finding a scan persisted (kind, the device key, the evidence
--      as JSON, the customer the commission report names, and its life: open → assigned → verified, or
--      dismissed with a note). ONE LIVE flag per (org, imei_key, kind) — a partial unique index; a verified /
--      dismissed row is kept as history and a scan re-opens only with a NEW row when the evidence changed.
--   4. pos.inventory_adjustments — the adjustment LEDGER: one row per unit status change the application makes
--      (who, why, from → to, the flag it resolved, the customer it went to). Append-only (UPDATE refused). No
--      foreign keys on purpose: a ledger keeps the ids it recorded even if a unit or flag is later deleted.
--
-- NOT ADDED (checked): a non-unique (org_id, serial_number) index for the duplicate lookup — mig 725 already
-- has pos_inv_serial_serial (org_id, serial_number) and pos_inv_serial_imei (org_id, imei). NO unique index on
-- serial / IMEI: duplicates may exist in live data, and the design REPORTS them (flag duplicate_on_hand) rather
-- than failing a migration on them.
--
-- MONEY: none. No P&L / GP / payout / tax path reads a serial unit's status or these tables; nothing here writes
-- a sale. RULE TWO: no carrier / tenant / POS / distributor name.
--
-- REVERT (paste and run to undo — drops only what this migration owns; run the UPDATE first, the narrowed
-- CHECK cannot be re-added while a unit is 'adjusted_out'):
--   DROP TABLE IF EXISTS pos.inventory_adjustments;
--   DROP FUNCTION IF EXISTS pos.inventory_adjustments_append_only();
--   DROP TABLE IF EXISTS pos.inventory_flags;
--   ALTER TABLE pos.inventory_serial DROP CONSTRAINT IF EXISTS inventory_serial_org_id_uniq;
--   -- UPDATE pos.inventory_serial SET status = 'lost' WHERE status = 'adjusted_out';   -- decide per row first
--   ALTER TABLE pos.inventory_serial DROP CONSTRAINT IF EXISTS inventory_serial_status_check;
--   ALTER TABLE pos.inventory_serial ADD CONSTRAINT inventory_serial_status_check
--     CHECK (status IN ('in_stock','in_transit','sold','returned','transferred','rma','lost','stolen'));
--   NOTIFY pgrst, 'reload schema';

BEGIN;

-- ── 1. widen the unit status domain: + 'adjusted_out' ───────────────────────────────────────────────────
-- Drop whichever CHECK on inventory_serial.status does NOT yet allow 'adjusted_out' (mig 725's inline CHECK is
-- named inventory_serial_status_check by Postgres), then add the widened one if no CHECK allows it yet.
DO $$
DECLARE c RECORD;
BEGIN
  FOR c IN
    SELECT conname FROM pg_constraint
     WHERE conrelid = 'pos.inventory_serial'::regclass AND contype = 'c'
       AND pg_get_constraintdef(oid) ILIKE '%status%in_stock%'
       AND pg_get_constraintdef(oid) NOT ILIKE '%adjusted_out%'
  LOOP
    EXECUTE format('ALTER TABLE pos.inventory_serial DROP CONSTRAINT %I', c.conname);
  END LOOP;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conrelid = 'pos.inventory_serial'::regclass AND contype = 'c'
                    AND pg_get_constraintdef(oid) ILIKE '%adjusted_out%') THEN
    ALTER TABLE pos.inventory_serial ADD CONSTRAINT inventory_serial_status_check
      CHECK (status IN ('in_stock','in_transit','sold','returned','transferred','rma','lost','stolen','adjusted_out'));
  END IF;
END $$;

-- ── 2. the composite key a tenant-scoped FK needs (mig 728 pattern) ─────────────────────────────────────
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'inventory_serial_org_id_uniq'
                  AND conrelid = 'pos.inventory_serial'::regclass) THEN
    ALTER TABLE pos.inventory_serial ADD CONSTRAINT inventory_serial_org_id_uniq UNIQUE (org_id, id);
  END IF;
END $$;

-- ── 3. the flags ────────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pos.inventory_flags (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                UUID NOT NULL,
  unit_id               UUID,                 -- the unit the finding is about (the one assign moved, once assigned)
  imei_key              TEXT NOT NULL,        -- device_cost_recon.device_key of the unit (THE cross-source key)
  kind                  TEXT NOT NULL CHECK (kind IN ('sold_no_receipt','sold_still_on_hand','returned_commission_kept',
                                                      'received_after_sold','duplicate_on_hand')),
  evidence              JSONB,                -- the engine's finding: sale lines by invoice, commission net / kept,
                                              -- the units, the customer — enough to act after the unit left stock
  evidence_hash         TEXT,                 -- fingerprint of the deciding facts: a closed flag re-opens only when it changes
  customer_name         TEXT,                 -- who it was sold to (the commission report first, else the sale line)
  invoice_no            TEXT,
  sold_on               DATE,
  status                TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','assigned','verified','dismissed')),
  assigned_customer_id  UUID,                 -- the pos.customers row the one-click found or created
  assigned_by           TEXT,                 -- storeops employee_id (else the login id) — never a body field
  assigned_at           TIMESTAMPTZ,
  verified_by           TEXT,
  verified_at           TIMESTAMPTZ,
  dismissed_by          TEXT,
  dismissed_at          TIMESTAMPTZ,
  note                  TEXT,                 -- required to dismiss / reject; a re-open says why
  last_seen_at          TIMESTAMPTZ,          -- the last scan that still found it
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE pos.inventory_flags IS
  'Inventory integrity flags (mig 1018, index §11b): a unit on hand that the sales lines or the commission report say '
  'was sold, a duplicate IMEI, a unit received after it was sold. open -> assigned (one click: sold to the customer) '
  '-> verified by management; or dismissed with a note. One live flag per (org, imei_key, kind).';

-- ONE LIVE flag per (org, device, kind): the scan refreshes it in place; history rows (verified / dismissed) are free.
CREATE UNIQUE INDEX IF NOT EXISTS pos_inventory_flags_live
  ON pos.inventory_flags (org_id, imei_key, kind) WHERE status IN ('open','assigned');
CREATE INDEX IF NOT EXISTS pos_inventory_flags_org_status ON pos.inventory_flags (org_id, status);
CREATE INDEX IF NOT EXISTS pos_inventory_flags_unit ON pos.inventory_flags (org_id, unit_id);

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'inventory_flags_org_id_uniq'
                  AND conrelid = 'pos.inventory_flags'::regclass) THEN
    ALTER TABLE pos.inventory_flags ADD CONSTRAINT inventory_flags_org_id_uniq UNIQUE (org_id, id);
  END IF;
  -- tenant-scoped FKs (mig 728 pattern, PostgreSQL 15+ column-list SET NULL): a flag can never point at another
  -- tenant's unit or customer.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'inventory_flags_unit_fk') THEN
    ALTER TABLE pos.inventory_flags ADD CONSTRAINT inventory_flags_unit_fk
      FOREIGN KEY (org_id, unit_id) REFERENCES pos.inventory_serial (org_id, id) ON DELETE SET NULL (unit_id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'inventory_flags_customer_fk')
     AND EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'customers_org_id_uniq'
                  AND conrelid = 'pos.customers'::regclass) THEN
    ALTER TABLE pos.inventory_flags ADD CONSTRAINT inventory_flags_customer_fk
      FOREIGN KEY (org_id, assigned_customer_id) REFERENCES pos.customers (org_id, id) ON DELETE SET NULL (assigned_customer_id);
  END IF;
END $$;

-- ── 4. the adjustment ledger ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pos.inventory_adjustments (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  unit_id      UUID,                          -- pos.inventory_serial.id (no FK: the ledger outlives the unit)
  imei_key     TEXT,
  store_code   TEXT,                          -- the unit's store at the time (store-scoped reads filter on it)
  direction    TEXT NOT NULL CHECK (direction IN ('in','out','status')),   -- 'status' = neither enters nor leaves stock
  reason       TEXT NOT NULL,                 -- manual: sold / lost / stolen / rma / duplicate_record / damaged_write_off /
                                              -- count_correction / found / customer_return / rma_returned / reversal;
                                              -- system: received / received_duplicate_confirmed / import / assign_sold /
                                              -- assign_duplicate / reject_assign / manual_edit
  from_status  TEXT,                          -- NULL for a landing (the unit did not exist)
  to_status    TEXT,
  flag_id      UUID,                          -- the integrity flag this resolved (no FK — history)
  customer_id  UUID,                          -- who it went to (the one-click assign)
  note         TEXT,
  created_by   TEXT,                          -- storeops employee_id (else the login id)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE pos.inventory_adjustments IS
  'Inventory adjustment ledger (mig 1018, index §11b): one row per serial-unit status change the application makes '
  '(inventory_integrity_router.apply_status_change is the one writer) and per unit landed. Append-only.';
CREATE INDEX IF NOT EXISTS pos_inventory_adjustments_org_created ON pos.inventory_adjustments (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS pos_inventory_adjustments_unit ON pos.inventory_adjustments (org_id, unit_id, created_at DESC);
CREATE INDEX IF NOT EXISTS pos_inventory_adjustments_flag ON pos.inventory_adjustments (org_id, flag_id);

-- APPEND-ONLY: a ledger row is never edited (a correction is a NEW row). DELETE stays possible for a tenant purge.
CREATE OR REPLACE FUNCTION pos.inventory_adjustments_append_only()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pos, pg_temp
AS $$
BEGIN
  RAISE EXCEPTION 'pos.inventory_adjustments is append-only — write a new adjustment instead of editing one';
END;
$$;
DROP TRIGGER IF EXISTS inventory_adjustments_no_update ON pos.inventory_adjustments;
CREATE TRIGGER inventory_adjustments_no_update BEFORE UPDATE ON pos.inventory_adjustments
  FOR EACH ROW EXECUTE FUNCTION pos.inventory_adjustments_append_only();

-- ── 5. security: the pos-schema posture (migs 724/725) applied to THESE objects only ─────────────────────
-- Backend-only: RLS on, nothing for anon / authenticated, service_role (the API) reads and writes. Deliberately
-- NOT the schema-wide "GRANT ALL ON ALL FUNCTIONS" block of mig 725: re-running it would re-grant the PII
-- functions mig 909 revoked from service_role.
ALTER TABLE pos.inventory_flags ENABLE ROW LEVEL SECURITY;
ALTER TABLE pos.inventory_adjustments ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON pos.inventory_flags, pos.inventory_adjustments FROM anon, authenticated;
GRANT ALL ON pos.inventory_flags, pos.inventory_adjustments TO service_role;
REVOKE ALL ON FUNCTION pos.inventory_adjustments_append_only() FROM PUBLIC, anon, authenticated;

COMMIT;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1018 complete — pos.inventory_flags, pos.inventory_adjustments (append-only), status adjusted_out.' AS status;
