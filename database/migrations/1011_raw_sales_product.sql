-- 1011_raw_sales_product.sql — the POS BY-PRODUCT aggregate gets its OWN landing table, and a landed
-- row says which report KIND wrote it (landing identity — owner 2026-09-20).
--
-- OWNER, verbatim: "the data is not flowing into the exec mtd from wherever it is uploaded — need to
-- know where the data is uploaded and it should be mentioned on the upload page where this upload
-- will be reflected, with a link. If the user does not know and uploads the data it does no good."
--
-- THE INSTANCE (org f4f1c16e…, measured from upload_trace). 2026-09-13: the line-level sales export
-- landed 48,875 rows in commcalc.raw_sales. 2026-09-20 04:07: the onboarding intake committed the
-- by-product aggregate ("Sales report with cost and selling price", layout pos_product_sales — 10,823
-- rows, no IMEI, department / category / product name blank) into the SAME table, and its slice replace
-- (store × dates only) deleted every one of the 48,875 line-level rows. raw_sales.source (mig 727) and
-- import_batch_id (mig 732) exist and were NULL on every intake row: nothing recorded which kind wrote
-- what, so nothing could scope the replace to it.
--
-- THE CLASS, and what this migration does about it:
--   1. TWO REPORT KINDS THAT ANSWER DIFFERENT QUESTIONS SHARED ONE TABLE. Summed beside line-level rows
--      the by-product aggregate double-counts; landed in their slice it replaces them. Owner decision:
--      it lands in its OWN table. This file creates commcalc.raw_sales_product — the same shape as
--      raw_sales (every reader of the product-level fields sku / quantity / total_cost / pricing_discounts
--      / contract_no finds them here) plus the two provenance columns raw_sales carries.
--   2. A LANDED ROW RECORDS THE KIND THAT WROTE IT. The `source` column's vocabulary widens from writer
--      provenance (NULL | 'pos_builtin', mig 727) to ALSO carry the column-mapping layout key of the
--      report kind ('sales', 'pos_product_sales'). NULL, 'pos_builtin' and any value that is not a layout
--      key of the table read as the table's DEFAULT kind (raw_sales → 'sales'; raw_sales_product →
--      'pos_product_sales'), so every existing row keeps exactly its meaning. The code home is
--      backend/app/modules/commcalc/landing_identity.py (KIND_STAMP); every writer stamps it, the slice
--      replace is store × dates × kind, and a landing that would delete rows of a DIFFERENT kind in its
--      slice is refused naming the loss unless confirmed.
--   3. The mig-1004 report_definitions rows that pointed the pos_product_sales layout at raw_sales are
--      re-pointed (a config row — column_mapping.TABLE_MAP is the code home; this keeps the DB copy honest).
--
-- DUPLICATE CHECK (build gate): searched index §2 (raw_sales, mig 002 / 1004 columns; the replace-slice
-- rules of ingest_slice), §16 raw_sales / daily_sales_feed (source, mig 727), core.import_batches (mig
-- 732), §25 (the second POS shape), §30.6 (the intake landing), §30.9 (the report-kind registry).
-- REUSED: raw_sales.source (no new column on raw_sales), ingest_slice.INGEST_PARTITION (one more row),
-- column_mapping.TABLE_MAP (one entry re-pointed), _ingest_mapped_df (the ONE landing path — no new
-- ingest route). NEW: this one table.
--
-- 💰 MONEY: none moved, none recomputed. No row of raw_sales is touched, deleted or restamped. The
-- 10,823 misplaced product-level rows of org f4f1c16e… STAY where they are (NULL source = 'sales' kind)
-- until the owner re-uploads the line-level export, whose store × date slice replaces them (see the PR's
-- recovery plan). Written and NOT applied: until it runs, a landing to raw_sales_product is REFUSED
-- naming this file — never redirected into raw_sales.
--
-- REVERT: DROP TABLE IF EXISTS commcalc.raw_sales_product;
--         UPDATE commcalc.report_definitions SET target_table = 'raw_sales'
--           WHERE report_key = 'pos_product_sales' AND target_table = 'raw_sales_product';
--         COMMENT ON COLUMN commcalc.raw_sales.source IS
--           'Writer provenance. NULL = pre-existing/external feed (never touched by POS promotion). ''pos_builtin'' = written by commcalc.pos_promote_period.';
--         NOTIFY pgrst, 'reload schema';

CREATE TABLE IF NOT EXISTS commcalc.raw_sales_product (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL,
  period            TEXT NOT NULL, period_month INT, period_year INT,
  store             TEXT, salesperson TEXT, user_login TEXT,
  department        TEXT, category TEXT, product_desc TEXT,
  product_id        NUMERIC, gp NUMERIC, ext_price NUMERIC,
  trans_id          TEXT, trans_date DATE, contract_type TEXT,
  mdn               TEXT, serial_1 TEXT, register TEXT, tender_type TEXT,
  voided            TEXT, trans_type TEXT, sku TEXT,
  customer          TEXT, email TEXT, customer_no TEXT,
  tax               NUMERIC,
  quantity          NUMERIC, total_cost NUMERIC, pricing_discounts NUMERIC, contract_no TEXT,
  -- the report KIND that wrote the row (landing identity). NULL reads as this table's default kind,
  -- 'pos_product_sales'. Same column name and meaning as raw_sales.source.
  source            TEXT,
  import_batch_id   UUID,
  created_at        TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS raw_sales_product_period ON commcalc.raw_sales_product (org_id, period);
CREATE INDEX IF NOT EXISTS raw_sales_product_store_date ON commcalc.raw_sales_product (org_id, store, trans_date);
CREATE INDEX IF NOT EXISTS raw_sales_product_org_period_source_idx ON commcalc.raw_sales_product (org_id, period, source);
COMMENT ON TABLE commcalc.raw_sales_product IS
  'The POS BY-PRODUCT sales aggregate (layout pos_product_sales): one row per invoice line / product with SKU, cost and selling price. Its OWN table (owner 2026-09-20) — never summed beside raw_sales (double count) and never landed in it (its slice replace deleted 48,875 line-level rows on 2026-09-20). Slice-replaced per store × trans_date × source (the report kind). Read by the onboarding Stage-4 verify and the report links; no money report sums it.';
COMMENT ON COLUMN commcalc.raw_sales_product.source IS
  'The report KIND that wrote the row = its column-mapping layout key (landing_identity.KIND_STAMP). NULL reads as this table''s default kind, pos_product_sales.';

-- the same column on raw_sales carries one more vocabulary — documented on the column, no data change
COMMENT ON COLUMN commcalc.raw_sales.source IS
  'Writer provenance AND report kind (landing identity, 2026-09-20). NULL = pre-existing / external feed; ''pos_builtin'' = written by commcalc.pos_promote_period; a column-mapping layout key (''sales'') = the report kind that landed the row. NULL, ''pos_builtin'' and any non-layout value read as the table''s default kind, ''sales'' (line-level).';

-- the mig-1004 config copy of "where the layout lands" follows the code home (column_mapping.TABLE_MAP)
UPDATE commcalc.report_definitions
   SET target_table = 'raw_sales_product',
       note = COALESCE(note, '') || ' Re-pointed by mig 1011: the by-product aggregate lands in its own table (landing identity, 2026-09-20).'
 WHERE report_key = 'pos_product_sales' AND target_table = 'raw_sales';

-- PostgREST schema cache
NOTIFY pgrst, 'reload schema';

SELECT 'Migration 1011 complete — commcalc.raw_sales_product created; raw_sales.source documented as the report-kind stamp; pos_product_sales report definitions re-pointed. No row of raw_sales touched.' AS status;
