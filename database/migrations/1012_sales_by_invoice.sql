-- 1012_sales_by_invoice.sql — SALES BY INVOICE with the TENDER TYPES: a report kind of its own, its
-- own landing (invoice header + invoice × tender × amount), the house registry row and the learned
-- header signature (owner 2026-09-21).
--
-- OWNER, verbatim: "sales by invoice report also has the tender types on the report, need to capture
-- that as well" — "tender types is in columns".
--
-- THE FILE, MEASURED (kept in the private bucket onboarding-intake, org f4f1c16e…, 1.7 MB, 10,823 rows,
-- 41 columns): ONE ROW PER INVOICE — who / where / when, the invoice's money columns (subtotal,
-- adjustments, net sales, sales, cost, gross profit, extra charges, donations, invoice total, coupons,
-- gift-card sales, non-revenue sales), then ONE AMOUNT COLUMN PER TENDER TYPE (twelve: card brands,
-- their non-integrated twins, cash, a debit PIN column, a vendor rebate applied as payment), then two
-- tax columns (the total and one named jurisdiction). Its Invoice # is the trans_id of the line-level
-- export (raw_sales, 48,875 rows for this org).
--
-- THE MIS-CARDING, stated plainly: on 2026-09-20 this file was committed as the BY-PRODUCT aggregate
-- (`pos_product_sales` → raw_sales, mig 1011's table since #264) — it is not a per-product report, it is
-- a per-invoice report. Its intake instance still reads "verified" from that commit; the rows it wrote
-- into raw_sales were removed on 2026-09-21; nothing of it is landed anywhere. The recovery is the
-- intake's own clicks (PR body), never a DB edit.
--
-- THE DECISION ON MIG 1011's TABLE — option (b): NEW tables; `raw_sales_product` is KEPT for a genuine
-- per-product export. Reasons: (1) the by-product card ("one row per invoice LINE with SKU, cost and
-- selling price") describes a real POS report and its table has the line grain that report needs;
-- (2) the invoice grain does not fit it — a header row plus a variable set of tender columns is two
-- grains, so it needs a child table; (3) repurposing would rename a table an unapplied migration
-- declares and re-point the product card at nothing. NOTHING IS DROPPED here. (Were option (a) ever
-- taken, its migration would have to prove emptiness first: `DO $$ BEGIN IF EXISTS (SELECT 1 FROM
-- commcalc.raw_sales_product) THEN RAISE EXCEPTION 'raw_sales_product has rows' … ` — recorded so the
-- precondition is on the file, not in a head.)
--
-- THE LANDING (landing identity, index §32): every row of both tables is STAMPED with the report kind
-- that wrote it (`source` = the column-mapping layout key, default 'sales_by_invoice'); the slice a
-- file replaces is store × trans_date × kind (ingest_slice.INGEST_PARTITION); the child rows carry the
-- PARENT's kind because the same landing wrote them; a landing to either table before this migration
-- runs is REFUSED naming this file, never redirected.
--
-- THE TENDER CLASSES have ONE home — closing.router.TENDER_VOCAB (the 3-way recon's axis plus the
-- finer invoice classes: debit, coupon, vendor rebate — additive; `_canon_tender` folds them to the
-- axis, so every closing leg reads as before). Which columns are tenders, and what each is, is
-- DECLARED at intake step 2.5b and REMEMBERED as commcalc.closing_tender_map rows (mig 111) with
-- report = 'invoice' — the existing raw-label → tender map, one more report leg, no new table.
--
-- DUPLICATE CHECK (build gate): searched index §2 (raw_sales, the mig-1004 shape rules, _ingest_mapped_df),
-- §12 / §23h (pos_tender_summary, _canon_tender, closing_tender_map mig 111, accessory_config
-- billpay_card_tenders mig 944), §16 (raw_sales_product mig 1011, report_kind / report_signature mig
-- 1010), §23f (tax_collected.aggregate, mig 991), §30.6–30.12, §32. REUSED: _ingest_mapped_df (the ONE
-- landing path, twice), landing_identity, ingest_slice, closing_tender_map, tender_config.make_resolver,
-- closing.router._addr_resolver + _xreport_tenders_by_store (the Stage-4 tie-out), report_links.
-- NEW: the two tables below, one house registry row, one house signature row.
--
-- 💰 MONEY: none moved, none recomputed. No row of any existing table is touched. No P&L, GP, payout or
-- tax path reads these tables (the tax aggregator's dereference is PROPOSED in index §30.13, not built).
-- The closing cash / card recons read the tender rows ONLY when a company sets its tender basis to the
-- invoice tenders (2b below; NULL = the X-report, byte-identical). Written and NOT applied.
--
-- REVERT: ALTER TABLE storeops.tenants DROP CONSTRAINT IF EXISTS tenants_closing_tender_basis_chk;
--         ALTER TABLE storeops.tenants DROP COLUMN IF EXISTS closing_tender_basis;
--         DROP TABLE IF EXISTS commcalc.raw_sales_invoice_tender;
--         DROP TABLE IF EXISTS commcalc.raw_sales_invoice;
--         DELETE FROM commcalc.report_signature WHERE org_id = '00000000-0000-0000-0000-000000000001'
--           AND report_kind_key = 'sales_by_invoice' AND house_copy;
--         DELETE FROM commcalc.report_kind WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'sales_by_invoice';
--         NOTIFY pgrst, 'reload schema';

-- ── 1. the invoice header — ONE ROW PER INVOICE (layout sales_by_invoice; column_mapping.TABLE_MAP) ──
CREATE TABLE IF NOT EXISTS commcalc.raw_sales_invoice (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL,
  period              TEXT NOT NULL, period_month INT, period_year INT,      -- each row's own month (mig-1004 rule)
  trans_id            TEXT NOT NULL,                                         -- Invoice # — the line-level export's trans_id
  trans_date          DATE,
  store               TEXT,
  invoiced_by         TEXT, salesperson TEXT, salesperson_login TEXT, tendered_by TEXT, tendered_by_login TEXT,
  customer            TEXT, channel TEXT, region TEXT, district TEXT,
  subtotal            NUMERIC, adjustments NUMERIC, net_sales NUMERIC, sales NUMERIC, total_cost NUMERIC, gp NUMERIC,
  extra_charges       NUMERIC, donations NUMERIC, invoice_total NUMERIC, coupons NUMERIC,
  gift_card_sales     NUMERIC, non_revenue_sales NUMERIC,
  tax                 NUMERIC,                                               -- the invoice's TOTAL tax
  source              TEXT,                                                  -- the report KIND that wrote the row (landing identity)
  import_batch_id     UUID,
  created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS raw_sales_invoice_period ON commcalc.raw_sales_invoice (org_id, period);
CREATE INDEX IF NOT EXISTS raw_sales_invoice_store_date ON commcalc.raw_sales_invoice (org_id, store, trans_date);
CREATE INDEX IF NOT EXISTS raw_sales_invoice_trans ON commcalc.raw_sales_invoice (org_id, trans_id);
COMMENT ON TABLE commcalc.raw_sales_invoice IS
  'Sales by invoice (layout sales_by_invoice, owner 2026-09-21): ONE ROW PER INVOICE with its totals and total tax. Its own table — never raw_sales (line grain; the mis-carded landing of 2026-09-20 deleted 48,875 line-level rows there). Slice-replaced per store x trans_date x source. trans_id = the invoice number the line-level export carries. Read by the onboarding Stage-4 verify (invoice totals, tender split vs the X-report, tax tie-out, report links); no money path yet (index §30.13).';
COMMENT ON COLUMN commcalc.raw_sales_invoice.source IS
  'The report KIND that wrote the row = its column-mapping layout key (landing_identity.KIND_STAMP). NULL reads as this table''s default kind, sales_by_invoice.';

-- ── 2. the tender split — ONE ROW PER (invoice, declared column) with an amount ≠ 0 (column_mapping.CHILD_TABLE_MAP) ──
CREATE TABLE IF NOT EXISTS commcalc.raw_sales_invoice_tender (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL,
  period              TEXT NOT NULL, period_month INT, period_year INT,
  store               TEXT,
  trans_date          DATE,
  trans_id            TEXT NOT NULL,
  salesperson         TEXT,
  role                TEXT NOT NULL DEFAULT 'tender' CHECK (role IN ('tender', 'tax')),   -- a jurisdiction tax column rides the same grain
  tender_label        TEXT NOT NULL,                                          -- the column header, verbatim
  tender_class        TEXT,                                                   -- closing.router.TENDER_VOCAB key (NULL for role = tax)
  keyed_manually      BOOLEAN NOT NULL DEFAULT false,                         -- a non-integrated twin: same class, keyed by hand
  amount              NUMERIC NOT NULL,
  source              TEXT,                                                   -- the PARENT's report kind (the same landing wrote it)
  import_batch_id     UUID,
  created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS raw_sales_invoice_tender_store_date ON commcalc.raw_sales_invoice_tender (org_id, store, trans_date);
CREATE INDEX IF NOT EXISTS raw_sales_invoice_tender_trans ON commcalc.raw_sales_invoice_tender (org_id, trans_id);
COMMENT ON TABLE commcalc.raw_sales_invoice_tender IS
  'The tender split of the sales-by-invoice export: one row per invoice and DECLARED tender column with an amount != 0 (owner 2026-09-21: "tender types is in columns"), carrying the column''s canonical tender class (closing.router.TENDER_VOCAB — one home) and whether the register keyed it by hand; a named tax column rides the same grain with role = tax. Which columns are tenders is declared at intake step 2.5b and remembered in closing_tender_map (report = invoice). Summed per store-day beside pos_tender_summary on the Stage-4 verify — a tie-out, NOT a basis for the closing cash / card recon.';

ALTER TABLE commcalc.raw_sales_invoice ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.raw_sales_invoice_tender ENABLE ROW LEVEL SECURITY;
DO $p$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='raw_sales_invoice' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.raw_sales_invoice FOR ALL USING (true) WITH CHECK (true);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='raw_sales_invoice_tender' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.raw_sales_invoice_tender FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $p$;

-- ── 2b. THE TENDER BASIS (owner 2026-09-21: "nothing on cash collected either") — what Cash Collected, the
--    cash / card recon and the deposit recon read as the tender split per store-day. Per-org CONFIG beside
--    the closing recon's other settings (mig 111 put closing_recon_mode here). NULL = the house default
--    'x_report' — byte-identical for every existing tenant. 'invoice' = derived from the invoice tender
--    rows above; 'x_report_else_invoice' = the X-report when one exists for the store-day, else the invoice.
--    ONE resolver reads it (closing.router.tender_basis → _tender_split_by_store); the intake's step 2.5b
--    sets it (closing.router.put_tender_basis); every closing page states which basis it read.
ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS closing_tender_basis TEXT;
DO $c$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tenants_closing_tender_basis_chk') THEN
    ALTER TABLE storeops.tenants ADD CONSTRAINT tenants_closing_tender_basis_chk
      CHECK (closing_tender_basis IS NULL OR closing_tender_basis IN ('x_report', 'invoice', 'x_report_else_invoice'));
  END IF;
END $c$;
COMMENT ON COLUMN storeops.tenants.closing_tender_basis IS
  'The tender split per store-day the closing recons read (closing.router.tender_basis): NULL = house default x_report (the register''s X-report, pos_tender_summary); invoice = the sales-by-invoice tender rows (raw_sales_invoice_tender); x_report_else_invoice = the X-report when one exists for the store-day, else the invoice tenders. Set by the intake''s step 2.5b (owner 2026-09-21).';

-- ── 3. THE HOUSE REGISTRY ROW (mig 1010's table; the code mirror is report_kinds.HOUSE_KINDS — byte-equal,
--    parsed back by harness_report_kinds.py §A over report_kinds.SEED_MIGRATIONS). Same VALUES block shape
--    as 1010 so the one parser reads both. Sorted between the two sales cards. ANY POS, ANY carrier.
INSERT INTO commcalc.report_kind (org_id, key, label, what_in_it, recognisable_columns, source_hint, applies_to_pos,
  applies_to_carrier, defined_by, statement_type, landing, layout, signature_fields, requires_columns, excludes_columns,
  upload_types, sort_order, custom_sheet_label) VALUES
  ('00000000-0000-0000-0000-000000000001', 'sales_by_invoice', 'Sales by invoice with tender types (how each invoice was paid)', 'One row per invoice with its totals and tax, and one amount column per tender type — cash, each card brand, debit, a vendor rebate — so you can see how each invoice was paid.', '{"Invoice #","Invoice Total","Net Sales","Tendered By","Cash","Adjustments"}', 'export from your POS', '{}', '{}', 'house', NULL, 'invoice', 'sales_by_invoice', '{"trans_id","invoice_total","net_sales","tax","tendered_by"}', '{"Invoice Total"}', '{"Product SKU","SKU","Tracking #","Product Name","IMEI","Serial"}', '{}', 25, NULL)
ON CONFLICT (org_id, key) DO NOTHING;

-- ── 4. THE LEARNED SIGNATURE of the measured file (HEADER NAMES ONLY — report_kinds.header_fingerprint over
--    its 41 headers, normalised and ordered; no cell value, filename, store, rep or customer string). The
--    house copy, so detection confirms this layout at 1.0 for every tenant from day one; without it the
--    kind's own signals rank it first but within the ask margin of the by-product card (both are the
--    same POS's sales exports and share headers) — the honest "which of these two?" question.
INSERT INTO commcalc.report_signature (org_id, fingerprint, report_kind_key, statement_type, layout, header_count, confirmations, house_copy)
VALUES ('00000000-0000-0000-0000-000000000001',
        'created on|invoiced by|invoiced at|tendered by|tendered by username|sold by|sold by username|invoice #|customer|application|invoice comments|invoice subtotal|adjustments|net sales|sales|total cost|gross profit|extra charges|total donations|invoice total|total coupons|gift card sales|non revenue sales|channel|region|district|emailed|discover|mastercard non integrated|cash|visa non integrated|debit pin|ven reb act|american express|amex non integrated|visa|mastercard|debit non integrated|discover non integrated|totaltaxpaidamount|sales tax nyc brooklyn',
        'sales_by_invoice', NULL, 'sales_by_invoice', 41, 1, true)
ON CONFLICT (org_id, fingerprint, report_kind_key) DO NOTHING;

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 1012 complete — commcalc.raw_sales_invoice + raw_sales_invoice_tender created; report_kind sales_by_invoice + its house signature seeded. No existing row touched.' AS status;
