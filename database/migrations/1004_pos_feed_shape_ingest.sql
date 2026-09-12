-- 1004_pos_feed_shape_ingest.sql — POS line-sales + on-hand inventory ingest (owner request
-- 2026-09-12: "add fields … check if we can use the existing columns and how can we map the same …
-- for a new tenant who is onboarding for verizon they should be able to upload these reports").
--
-- DUPLICATE CHECK (build gate, CLAUDE.md). Checked docs/SYSTEM_DATA_FLOW_INDEX.md §2 (ingest routes),
-- §11 (inventory & aging) and the §16 table cross-reference before writing a line. REUSED, not rebuilt:
--   · commcalc.raw_sales (mig 002)                  — the canonical line-level sales table. The POS
--     export maps onto 13 of its EXISTING columns; only 4 genuinely-new fields are added below.
--   · commcalc.inventory_aging_device (mig 216)     — the EXISTING per-device on-hand table, already
--     upload-fed (b2b_sweep.fetch_inventory_aging is a stub, §2 "Known gaps"). EXTENDED with 4
--     columns rather than standing up a sibling inventory table.
--   · commcalc.column_mapping (mig 042)             — header→column mapping is already DATA.
--   · commcalc.report_definitions.carrier_id (mig 291) — already the mechanism that shows a report
--     only to tenants running that carrier. Mig 291's own header names "Verizon -> Vzone" as intended.
--   · commcalc.ui_label_override (mig 068 / 945 / 953) — carrier VOCABULARY presets. The resolver
--     already slugifies any carrier code, so a ':verizon' scope needs NO code change.
--   · merchant_portals.iso_date                     — the existing PURE date parser, EXTENDED to
--     accept a trailing clock time rather than adding a second parser that could disagree.
-- NOTHING new was created that an existing mechanism already served.
--
-- NO MONEY MOVES. This adds nullable columns, config rows and display terminology. It books no
-- payout, changes no rate, and rewrites no existing row: every ADD COLUMN is IF NOT EXISTS and
-- every seed is ON CONFLICT DO NOTHING, so a re-run is a no-op and existing rows read byte-identically.
--
-- RULE TWO: no carrier name appears in code. The report keys are named for the FEED SHAPE
-- (pos_product_sales / pos_inventory_listing); WHICH tenant is offered them is decided by the
-- carrier_id rows seeded here, never by a branch.
--
-- Additive + idempotent.
--
-- REVERT (paste and run to undo — drops only what this migration owns):
--   ALTER TABLE commcalc.raw_sales
--     DROP COLUMN IF EXISTS quantity, DROP COLUMN IF EXISTS total_cost,
--     DROP COLUMN IF EXISTS pricing_discounts, DROP COLUMN IF EXISTS contract_no;
--   ALTER TABLE commcalc.inventory_aging_device
--     DROP COLUMN IF EXISTS status, DROP COLUMN IF EXISTS quantity,
--     DROP COLUMN IF EXISTS total_cost, DROP COLUMN IF EXISTS category;
--   DELETE FROM commcalc.report_definitions
--    WHERE report_key IN ('pos_product_sales','pos_inventory_listing');
--   DELETE FROM commcalc.ui_label_override
--    WHERE org_id = '00000000-0000-0000-0000-000000000001' AND scope = 'report_term:verizon';
--   NOTIFY pgrst, 'reload schema';

BEGIN;

-- ── 1. raw_sales: the 4 genuinely-new fields ────────────────────────────────────────────────────
-- Everything else in the export maps to an EXISTING column (trans_id, store, salesperson,
-- user_login, trans_date, customer, sku, serial_1, product_desc, category, department, ext_price,
-- gp, voided). These four have no existing home and are load-bearing:
--   quantity          — a refund line carries -1; without it a return cannot be told from a sale by
--                       amount alone, and unit economics cannot be derived.
--   total_cost        — the cost leg of gross profit (the export carries cost per line; raw_sales
--                       previously held only gp, so COGS could not be re-derived or audited).
--   pricing_discounts — negative discount applied to the line (measured -1,930,979.06 over 20
--                       months on the first feed); folding it into price would hide it.
--   contract_no       — the carrier contract/order number, the join key back to a carrier statement.
ALTER TABLE commcalc.raw_sales
  ADD COLUMN IF NOT EXISTS quantity          NUMERIC,
  ADD COLUMN IF NOT EXISTS total_cost        NUMERIC,
  ADD COLUMN IF NOT EXISTS pricing_discounts NUMERIC,
  ADD COLUMN IF NOT EXISTS contract_no       TEXT;

COMMENT ON COLUMN commcalc.raw_sales.quantity IS
  'Line quantity. Negative on a refund/return line (POS line-sales feeds). NULL for feeds that do not carry it.';
COMMENT ON COLUMN commcalc.raw_sales.total_cost IS
  'Line cost (the COGS leg of gp). NULL for feeds that do not carry a cost column.';
COMMENT ON COLUMN commcalc.raw_sales.pricing_discounts IS
  'Discount applied to the line, as the feed spells it (negative = a reduction). Never folded into ext_price.';
COMMENT ON COLUMN commcalc.raw_sales.contract_no IS
  'Carrier contract / order number for the line — the join key back to a carrier statement. Not contract_type.';

-- ── 2. inventory_aging_device: 4 columns so an on-hand snapshot is fully representable ──────────
-- status   — 'In Stock' / 'Committed' / 'On Order' / 'On Back Order'. Load-bearing: only a
--            physically-present status can be reconciled against sold units, and an ordered-but-not-
--            received row must never be reported as a missing device.
-- quantity / total_cost — the row's on-hand count and extended cost (the table previously held only
--            unit_cost, so an inventory VALUATION could not be summed from it).
-- category — the POS category path, so on-hand value is breakable by product line.
ALTER TABLE commcalc.inventory_aging_device
  ADD COLUMN IF NOT EXISTS status     TEXT,
  ADD COLUMN IF NOT EXISTS quantity   NUMERIC,
  ADD COLUMN IF NOT EXISTS total_cost NUMERIC,
  ADD COLUMN IF NOT EXISTS category   TEXT;

COMMENT ON COLUMN commcalc.inventory_aging_device.status IS
  'On-hand status as the POS spells it (e.g. In Stock / Committed / On Order / On Back Order). '
  'Only a physically-present status is reconcilable against sold units.';
COMMENT ON COLUMN commcalc.inventory_aging_device.quantity IS 'On-hand quantity for the row.';
COMMENT ON COLUMN commcalc.inventory_aging_device.total_cost IS 'Extended on-hand cost (unit_cost x quantity as the feed spells it).';
COMMENT ON COLUMN commcalc.inventory_aging_device.category IS 'POS category path for the item.';

CREATE INDEX IF NOT EXISTS inventory_aging_device_org_status
  ON commcalc.inventory_aging_device (org_id, status);

-- ── 3. Register the two reports for every tenant that runs the carrier ──────────────────────────
-- carrier_id is resolved per-org from that org's OWN commcalc.carrier row (mig 038), so this seeds
-- only orgs that have actually chosen the carrier — an org that never picked it gets no rows and is
-- byte-identical to today. A tenant onboarding later picks the carrier and re-runs this INSERT (or
-- adds the rows from the Imports screen); the ON CONFLICT makes that safe.
INSERT INTO commcalc.report_definitions
  (org_id, report_key, label, source_name, target_table, upload_endpoint, period_mode, auto, sort_order, carrier_id, note)
SELECT c.org_id,
       v.report_key, v.label, v.source_name, v.target_table, v.upload_endpoint, v.period_mode,
       false, v.sort_order, c.id,
       'Seeded by mig 1004. Carrier-scoped: hidden from tenants not running this carrier (mig 291).'
FROM commcalc.carrier c
CROSS JOIN (VALUES
  ('pos_product_sales', 'Sales by Product (POS line detail)', 'Sales By Product Report',
   'raw_sales', '/commcalc/upload-mapped', 'data', 20),
  ('pos_inventory_listing', 'Inventory Listing (on-hand + cost)', 'Inventory Listing Report',
   'inventory_aging_device', '/commcalc/upload-mapped', 'snapshot', 21)
) AS v(report_key, label, source_name, target_table, upload_endpoint, period_mode, sort_order)
WHERE lower(coalesce(c.code, c.name)) LIKE '%verizon%'
ON CONFLICT (org_id, report_key) DO NOTHING;

-- ── 4. Carrier VOCABULARY preset (display terminology only, no money) ───────────────────────────
-- Same mechanism and posture as migs 945/953: HOUSE-org rows under scope 'report_term:<carrier>'.
-- The resolver (report_labels.normalize_carrier_code) already slugifies an unknown carrier code to
-- its own name, so 'verizon' resolves with NO code change. A tenant may override any of these.
-- Only terms this carrier actually has are seeded — an unseeded term renders the NEUTRAL noun.
INSERT INTO commcalc.ui_label_override (org_id, scope, key, label) VALUES
  ('00000000-0000-0000-0000-000000000001', 'report_term:verizon', 'pos_system',  'RQ'),
  ('00000000-0000-0000-0000-000000000001', 'report_term:verizon', 'distributor', 'Wireless Zone')
ON CONFLICT (org_id, scope, key) DO NOTHING;

COMMIT;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1004 complete — POS line-sales + on-hand inventory ingest (raw_sales +4 cols, inventory_aging_device +4 cols, carrier-scoped report definitions, carrier vocabulary preset)' AS status;
