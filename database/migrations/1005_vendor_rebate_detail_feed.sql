-- 1005_vendor_rebate_detail_feed.sql — per-line vendor rebate/commission HISTORY landing table
-- (owner 2026-09-12: a new tenant onboarding on a carrier whose POS exports a "Vendor Rebate
-- History Report" must be able to upload it, map it, land it and query it).
--
-- ┌────────────────────────────────────────────────────────────────────────────────────────────┐
-- │ THIS MIGRATION MOVES NO MONEY, AND THAT IS THE POINT.                                      │
-- │ The feed it lands is EARNED, NOT COLLECTED: on the first real file `Collected` is $0.00 on │
-- │ all 47,252 data rows and `Balance` carries the entire $6,748,358.09. Whether an earned-but- │
-- │ uncollected rebate books as a receivable or waits for a payment file is an OPEN OWNER       │
-- │ DECISION. Until it is made this table is a LANDING ZONE ONLY: nothing reads it into the     │
-- │ P&L, the Balance Sheet, GP, commission payout or any accrual. `earned_amount`,              │
-- │ `collected_amount` and `balance_amount` are carried as THREE DISTINCT columns so that       │
-- │ decision is later a config flip, not a re-ingest.                                          │
-- └────────────────────────────────────────────────────────────────────────────────────────────┘
--
-- DUPLICATE CHECK (build gate, CLAUDE.md). Searched docs/SYSTEM_DATA_FLOW_INDEX.md §2 (ingest routes
-- + every raw_* table), §16 (by TABLE), §17 (by ENDPOINT), §25 (the POS feed shapes) and §26 (the
-- implementation spine), plus data_lineage_registry.INGEST_TABLES_BY_MODULE. REUSED, not rebuilt:
--
--   · POST /commcalc/upload-mapped + _ingest_mapped_df  — the any-carrier mapped ingest. NO new
--     ingest route, NO new parser. It already brings column pre-validation, snapshot/restore,
--     upload_log + upload_trace, footer-row drop and per-row period derivation.
--   · commcalc.column_mapping (mig 042)                 — header→column mapping is already DATA.
--   · commcalc.feed_shape (mig 1004, §25.3)             — is_footer_row / period_fields, unchanged.
--     The first real file carries a grand-total FOOTER row, so this is load-bearing again: see below.
--   · commcalc.report_definitions.carrier_id (mig 291)  — already decides WHICH tenant is offered a
--     report. The seed below is the only thing that scopes this feed to a carrier.
--   · commcalc.ingest_slice.INGEST_PARTITION            — the store∩date slice replace, so a
--     re-upload is idempotent and one store's file never deletes another's rows.
--   · POST /commcalc/column-mapping/detect              — the confidence + sample-value proposal
--     (PR #230). No second detector.
--
-- WHY A NEW TABLE, HAVING CHECKED THE THREE CANDIDATES THAT ALREADY EXIST:
--
--   1. `commcalc.raw_ma_commission` (mig 083) is the per-activation carrier commission detail and is
--      the closest shape — but it is READ BY MONEY. `account/device_cogs._ma_sold_cost` selects
--      `imei,sku` from it and prices every distinct IMEI into MA device COGS; `account/residual_subs
--      ._aggregate_ma` counts ONE ROW = ONE ACTIVATED LINE as the residual-per-subscriber
--      denominator; `account/coa.py` books its component columns to the P&L. This feed's grain is
--      ~7 rows per device (one per rebate COMPONENT: 258 distinct `Product Name` values over 6,449
--      IMEIs), so landing it there would move MA device COGS and divide residual income by a
--      denominator 7x too large. Gating all three consumers on a discriminator column would put a
--      new filter in seven money modules, where one missed filter silently moves money.
--   2. `commcalc.activation_rebate_ledger` (mig 867) was built for THIS report — and it is the
--      P&L BOOKING (commission_amount → carrier_comm revenue, device_rebate_amount → device_rebate
--      contra-COGS, read by account/coa.py). Landing here books, which the money rule forbids. It is
--      also an AGGREGATE (one row per store/period/source), so it cannot answer a per-line question.
--   3. `commcalc.raw_custom_import` is the generic JSONB capture a Custom Import falls back to
--      today. That IS the dead end being fixed: nothing counts those rows.
--
--   So the per-line detail has no existing home that does not either book money or corrupt an
--   existing money path. This table is the landing zone; it is deliberately read by NOTHING.
--
-- RULE TWO: no carrier, POS or vendor name appears in code or in this table. The report key
-- `vendor_rebate_history` names the FEED SHAPE (as mig 1004 named `pos_product_sales`); WHICH tenant
-- is offered it is decided entirely by the report_definitions.carrier_id rows seeded below. The
-- file's own `Vendor Account Name` is carried as DATA in `vendor_account` — on the first real file it
-- holds FIVE different values, so the feed is not single-vendor and must never be assumed to be.
--
-- Additive + idempotent: CREATE/ADD ... IF NOT EXISTS, every seed ON CONFLICT DO NOTHING. Re-running
-- is a no-op and no existing row is read or rewritten.
--
-- REVERT (paste and run to undo — drops only what this migration owns):
--   DROP TABLE IF EXISTS commcalc.raw_vendor_rebate;
--   DELETE FROM commcalc.report_definitions WHERE report_key = 'vendor_rebate_history';
--   DELETE FROM commcalc.data_lineage WHERE affected_key = 'raw_vendor_rebate';
--   NOTIFY pgrst, 'reload schema';
--
-- The lineage EDGE for this feed is seeded by 925_data_lineage_seed.sql (seq 137), not here: that
-- file is a wholesale reseed (it DELETEs every row first), so an edge inserted here would be erased
-- the next time it runs. Re-run 925 after this migration.

BEGIN;

-- ── 1. The landing table ────────────────────────────────────────────────────────────────────────
-- GRAIN: one row per REBATE COMPONENT per line per invoice. Measured on the first real file:
-- 47,252 data rows · 6,094 invoices · 6,449 device IMEIs · 258 distinct component names — i.e. a
-- single activated device carries ~7 rows (a device-payment rebate, a financing fee, a rate-plan
-- rebate, a protection line, …). Nothing here is one-row-per-activation; do not treat it as such.
--
-- NO UNIQUE CONSTRAINT ON PURPOSE. The file contains 50 byte-identical duplicate rows and 164
-- (invoice, line, sku) groups with up to 4 rows, so there is no natural key that is unique in the
-- SOURCE. Idempotence therefore comes from the SLICE REPLACE (ingest_slice.INGEST_PARTITION:
-- store ∩ sold_on), never from an upsert key that would silently collapse real rows.
CREATE TABLE IF NOT EXISTS commcalc.raw_vendor_rebate (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  carrier_id          UUID,          -- stamped by the mapped ingest when the caller names a carrier
  source_id           UUID,          -- NULL = manual/email upload (keeps the portal-pull coexistence
                                     -- contract the raw_ma_* family uses)
  period              TEXT, period_month INT, period_year INT,   -- derived PER ROW from sold_on

  -- identity
  invoice_no          TEXT,          -- 'Invoice Number' — 100% filled; blank ⇒ footer/totals row
  mdn                 TEXT,          -- 'Tracking Number' — the ACTIVATED LINE's 10-digit number
                                     -- (measured: 47,078/47,252 are exactly 10 digits). It is NOT a
                                     -- shipment tracking id; it is the join key to raw_sales.mdn /
                                     -- raw_payment_detail.mdn.
  original_invoice_no TEXT,          -- 'Original Invoice Number' (1.8% — set on adjustment rows)

  -- what was rebated
  rebate_sku          TEXT,          -- 'Product SKU'   (259 distinct)
  rebate_name         TEXT,          -- 'Product Name'  — WHICH rebate/commission component this is
  quantity            NUMERIC,       -- 'Quantity' — LOAD BEARING: -1 on a reversal/chargeback line
                                     -- (6,283 of 47,252). earned_amount = unit_amount x quantity on
                                     -- 47,252/47,252 data rows, so dropping the sign overstates.
  unit_amount         NUMERIC,       -- 'Unit Rebate' — UNSIGNED per-unit rate. Never sum this
                                     -- column alone: it ignores the reversals (measured overstatement
                                     -- on the first file: +$630,615.08 against earned_amount).
  earned_amount       NUMERIC,       -- 'Total Rebate' — the SIGNED net the carrier owes for this line
  collected_amount    NUMERIC,       -- 'Collected' — what the carrier has actually PAID
  balance_amount      NUMERIC,       -- 'Balance'   — what is still OWED
  tax_amount          NUMERIC,       -- 'Tax Amount'

  -- the device the rebate is against (repeated on EVERY component row for that device)
  device_sku          TEXT,          -- 'Related Product SKU'
  device_name         TEXT,          -- 'Related Product Name'
  imei                TEXT,          -- 'Related Tracking Number' — the 15-digit IMEI. The header
                                     -- lies; only the VALUE distinguishes it from 'Tracking Number'.
  device_cost         NUMERIC,       -- 'Related Unit Cost'    ⚠ SEE THE WARNING BELOW
  device_price        NUMERIC,       -- 'Related Selling Price' ⚠ SEE THE WARNING BELOW

  -- the sale
  rate_plan           TEXT,
  term_code           TEXT,
  sold_on             DATE,          -- 'Sold On' — the real datetime; the period source
  customer_name       TEXT,
  customer_ref        TEXT,          -- 'Customer Identifier'
  postal_code         TEXT,
  port_number         TEXT,          -- 'Port Number' — the ported-in number (22.4% filled)
  contract_no         TEXT,
  soc_code            TEXT,
  salesperson         TEXT,
  salesperson_id      TEXT,

  -- where / whose. The STORE arrives ONLY in the data (see the note on store resolution below).
  store               TEXT,          -- 'Invoiced At' — holds the STORE NAME + CODE, not a timestamp
  invoiced_by         TEXT,          -- 'Invoiced By' — the same store string on the first file
  vendor_account      TEXT,          -- 'Vendor Account Name' — the rebate PROGRAM (5 distinct values
                                     -- on the first file; NOT a single carrier)
  channel             TEXT,
  district            TEXT,
  region_label        TEXT,          -- 'Region' — holds a PERSON'S NAME on the first file, not a
                                     -- geography. Named `region_label` so it can never be mistaken
                                     -- for the org hierarchy's region (§13).

  -- statement state as the feed spells it
  charge_back         TEXT,
  adjusted            TEXT,
  reconciled          TEXT,
  flagged             TEXT,

  created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ⚠ device_cost / device_price ARE REPEATED PER COMPONENT ROW, NOT PER DEVICE. Summing them across
--   rows multiplies the device's cost by the number of rebate components on it. MEASURED on the first
--   file: the naive per-row sum of `Related Unit Cost` is $41,040,251.81; summed ONCE per distinct
--   IMEI it is $5,393,764.59 — a 7.6x overstatement. Any future consumer must dedupe by IMEI first.
--   This is exactly why this feed is not allowed near device COGS today.
COMMENT ON TABLE commcalc.raw_vendor_rebate IS
  'Per-line vendor rebate/commission history (LANDING ZONE, mig 1005). One row per rebate COMPONENT '
  'per line per invoice — NOT one row per activation. EARNED, NOT COLLECTED: earned_amount is what the '
  'carrier owes, collected_amount what it has paid, balance_amount what is outstanding. Read by NOTHING '
  'that books money; whether an earned-but-uncollected rebate is a receivable is an open owner decision.';
COMMENT ON COLUMN commcalc.raw_vendor_rebate.earned_amount IS
  'Signed net rebate the carrier OWES for this line (= unit_amount x quantity). Not proof of payment.';
COMMENT ON COLUMN commcalc.raw_vendor_rebate.collected_amount IS
  'What the carrier has actually PAID against this line. $0.00 on every row of the first real file.';
COMMENT ON COLUMN commcalc.raw_vendor_rebate.balance_amount IS
  'Still OWED against this line (earned - collected as the feed spells it).';
COMMENT ON COLUMN commcalc.raw_vendor_rebate.device_cost IS
  'Device cost REPEATED on every component row for that device. Dedupe by imei before summing — a '
  'per-row sum overstated the first file 7.6x ($41.0M vs a true $5.4M).';
COMMENT ON COLUMN commcalc.raw_vendor_rebate.quantity IS
  'Line quantity; -1 on a reversal/chargeback line. earned_amount already carries the sign.';
COMMENT ON COLUMN commcalc.raw_vendor_rebate.mdn IS
  'The ACTIVATED LINE''s number (source header "Tracking Number" — not a shipment id).';
COMMENT ON COLUMN commcalc.raw_vendor_rebate.imei IS
  'Device IMEI (source header "Related Tracking Number" — the header lies; the value is 15 digits).';
COMMENT ON COLUMN commcalc.raw_vendor_rebate.store IS
  'Store as the feed spells it, from the "Invoiced At" column (name + code, e.g. "<Store Name> WZ1321"). '
  'The slice-replace partition: a re-upload replaces only this store''s own date range.';
COMMENT ON COLUMN commcalc.raw_vendor_rebate.region_label IS
  'The feed''s own "Region" cell. NOT the org hierarchy region — on the first file it holds a person''s name.';

CREATE INDEX IF NOT EXISTS raw_vendor_rebate_org_period ON commcalc.raw_vendor_rebate (org_id, period);
CREATE INDEX IF NOT EXISTS raw_vendor_rebate_org_sold   ON commcalc.raw_vendor_rebate (org_id, sold_on);
CREATE INDEX IF NOT EXISTS raw_vendor_rebate_org_store  ON commcalc.raw_vendor_rebate (org_id, store);
CREATE INDEX IF NOT EXISTS raw_vendor_rebate_org_imei   ON commcalc.raw_vendor_rebate (org_id, imei);

-- ── 2. Offer the report to every tenant that runs the carrier ───────────────────────────────────
-- Identical mechanism and posture to mig 1004 §3: carrier_id is resolved per-org from that org's OWN
-- commcalc.carrier row (mig 038), so ONLY orgs that have actually chosen the carrier get a row. An
-- org that never picked it gets nothing and is byte-identical to today. A tenant onboarding later
-- picks the carrier and re-runs this INSERT; the ON CONFLICT makes that safe.
--
-- STORE RESOLUTION — from the DATA, not from the upload form. The store is present on 100% of rows
-- (the 'Invoiced At' / 'Invoiced By' columns both carry "<Store Name> <Code>") and is also embedded in
-- the invoice prefix, whereas a store PICKED AT UPLOAD is one operator mistake away from attributing
-- a whole file to the wrong store — and because `store` is the slice-replace partition, a wrong pick
-- would also DELETE the wrong store's rows on re-upload. Reading it from the data makes the file
-- self-describing and the import idempotent per store. A multi-store tenant exporting per store
-- therefore needs no extra step, and a file that ever carries two stores lands correctly without one.
INSERT INTO commcalc.report_definitions
  (org_id, report_key, label, source_name, target_table, upload_endpoint, period_mode, auto, sort_order, carrier_id, note)
SELECT c.org_id,
       'vendor_rebate_history',
       'Vendor Rebate History (per-line, earned)',
       'Vendor Rebate History Report',
       'raw_vendor_rebate', '/commcalc/upload-mapped', 'data',
       false, 22, c.id,
       'Seeded by mig 1005. Carrier-scoped (mig 291). LANDING ONLY — earned, not collected; books nothing.'
FROM commcalc.carrier c
WHERE lower(coalesce(c.code, c.name)) LIKE '%verizon%'
ON CONFLICT (org_id, report_key) DO NOTHING;

COMMIT;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1005 complete — per-line vendor rebate landing table (books nothing), carrier-scoped report definition. Re-run 925_data_lineage_seed.sql for the lineage edge.' AS status;
