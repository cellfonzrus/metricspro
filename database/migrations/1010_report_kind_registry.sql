-- 1010_report_kind_registry.sql — THE REPORT-KIND REGISTRY + the learned header signatures.
--
-- OWNER DIRECTIVES 2026-09-20 (verbatim): "for the layman we need to be more clear of what report
-- we are trying to get them uploaded — sales reports with imei and phone number; sales report with
-- cost and selling price; commission report … residual report; inventory at hand report; inventory
-- aging report; Bill Payment transactions report; X report; any other report which they feel is
-- needed and the system to check against what report it matches using intelligence gained by using
-- all the reports." And: "it is very important that we don't have extra file upload paths for a new
-- tenant who does not need those based on the carrier they pick. Our system should be smart enough to
-- only show those which are carrier-specific once they have been defined by a previous tenant or us
-- on the back end … no patchwork, it should work as a design. Currently in the Verizon tenant we have
-- all the table uploads for B2B when it has been declared that the POS is not B2B, it is RQ."
--
-- WHAT THIS IS (design of record: docs/ONBOARDING_FLOW_DESIGN.md §7). ONE table of every report kind
-- the platform can take — its layman label and one-sentence "what's in it", the columns a person
-- recognises it by, where it usually comes from, what it APPLIES TO (POS codes / carrier codes; an
-- empty array = any), who DEFINED it (the house seed, or a tenant whose completed intake confirmed
-- it), where it LANDS (the intake's source kind), the column-mapping LAYOUT it maps through, and the
-- canonical SIGNATURE FIELDS (names from column_mapping.TARGET_FIELDS — no second alias list) that
-- identify it. The house org seeds the layman cards + every report the Upload page / wizard already
-- offered by hand; a tenant's row overrides the house row PER KEY (the mig-207 report_pull_map shape).
--
-- WHAT A TENANT SEES = rows whose applies-to intersects the tenant's declaration (the pos_system
-- term of mig 953 / its pos_profile rows + its commcalc.carrier rows) AND that are defined — computed
-- by ONE function (backend report_kinds.visible_kinds / frontend carrier-scope.reportKindsVisible)
-- on EVERY upload surface, never listed in a page. A super-admin widens through the EXISTING
-- ui_label_override scope 'cap' (key 'kind:<key>' = show|hide) — config, recorded, not code.
--
-- report_signature: the header fingerprint CONFIRMED at an intake (org row + house copy), so the
-- platform recognises a layout it has seen before ("seen before as …, confirmed N times") for the
-- next tenant. HEADER NAMES ONLY — never a cell value, a filename, a store, a rep or a customer
-- string (pinned by backend/harness_report_kinds.py). A fingerprint later confirmed under a different
-- kind keeps both rows and detection ASKS.
--
-- DUPLICATE CHECK (build gate): searched index §16 (report_pull_map 207, pos_profile 200,
-- report_definitions 039/291, ui_label_override 068/945/953, column_mapping 042, onboarding_state 927,
-- commission_bucket 1009), §17 (/upload-registry, /connectors, /report-labels, /nav-config,
-- /pos-profiles/*), §26, §30.8 OPEN (b)/(d). REUSED: pos_profile.filename_rules (the per-POS filename
-- standard — this table does NOT copy patterns), report_definitions.carrier_id (the connector
-- registry's own scope — still ANDed), ui_label_override 'cap' (the override), TARGET_FIELDS (the
-- aliases), the pos_system term (the declaration). NEW: only the two tables below — no existing table
-- answered "which report kinds exist, what do they apply to, who defined them".
--
-- Additive, idempotent, display/config only — NOTHING recomputed, no money row touched. Written and
-- NOT applied: until it runs, the code renders from report_kinds.HOUSE_KINDS (the byte-equal mirror
-- of this seed) and says registry_ready:false; signatures are not learned until the table exists.
--
-- REVERT: DROP TABLE IF EXISTS commcalc.report_signature;
--         DROP TABLE IF EXISTS commcalc.report_kind;
--         NOTIFY pgrst, 'reload schema';

CREATE TABLE IF NOT EXISTS commcalc.report_kind (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  key                  TEXT NOT NULL,                       -- [a-z0-9_]
  label                TEXT NOT NULL,                       -- the layman name on every card / tile
  what_in_it           TEXT,                                -- one sentence
  recognisable_columns TEXT[] NOT NULL DEFAULT '{}',        -- header names a person recognises it by
  source_hint          TEXT,                                -- "export from your POS" / "from the carrier portal"
  applies_to_pos       TEXT[] NOT NULL DEFAULT '{}',        -- POS codes (pos_system term / pos_profile.pos_key); {} = any
  applies_to_carrier   TEXT[] NOT NULL DEFAULT '{}',        -- carrier codes (commcalc.carrier.code); {} = any
  defined_by           TEXT NOT NULL DEFAULT 'house' CHECK (defined_by IN ('house','tenant')),
  defined_by_org       UUID,                                -- the tenant whose intake defined it (defined_by='tenant')
  statement_type       TEXT CHECK (statement_type IS NULL OR statement_type IN ('commission','residual')),
  landing              TEXT NOT NULL,                       -- onboarding_intake.SOURCE_KINDS + custom_import | carrier_report | module
  layout               TEXT,                                -- the column_mapping report_key it maps through
  signature_fields     TEXT[] NOT NULL DEFAULT '{}',        -- canonical TARGET_FIELDS names that identify it
  requires_columns     TEXT[] NOT NULL DEFAULT '{}',        -- any-of headers that MUST be present (aging = a received / age column)
  excludes_columns     TEXT[] NOT NULL DEFAULT '{}',        -- headers whose presence rules the kind OUT (on-hand has no received column)
  upload_types         TEXT[] NOT NULL DEFAULT '{}',        -- the legacy /upload/<file_type> route keys / tile ids it is uploaded through
  custom_sheet_label   TEXT,                                -- a self-serve custom-import sheet (mig 099) it is captured as
  sort_order           INT NOT NULL DEFAULT 100,
  is_active            BOOLEAN NOT NULL DEFAULT true,
  updated_at           TIMESTAMPTZ DEFAULT NOW(),
  created_at           TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, key)
);
CREATE INDEX IF NOT EXISTS report_kind_org ON commcalc.report_kind (org_id);
COMMENT ON TABLE commcalc.report_kind IS
  'THE REPORT-KIND REGISTRY (design §7): every report kind the platform takes, what it applies to (POS / carrier codes; {} = any), who defined it, its layman copy and signature fields. House org = defaults; a tenant row overrides per key. Visible to a tenant = applies-to ∩ declaration ≠ ∅ AND defined — computed by report_kinds.visible_kinds / carrier-scope.reportKindsVisible on every upload surface.';

CREATE TABLE IF NOT EXISTS commcalc.report_signature (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,
  fingerprint        TEXT NOT NULL,                         -- report_kinds.header_fingerprint: normalised ORDERED header names, '|'-joined
  report_kind_key    TEXT NOT NULL,
  statement_type     TEXT,
  layout             TEXT,
  header_count       INT,
  confirmations      INT NOT NULL DEFAULT 1,
  first_confirmed_at TIMESTAMPTZ DEFAULT NOW(),
  last_confirmed_at  TIMESTAMPTZ DEFAULT NOW(),
  house_copy         BOOLEAN NOT NULL DEFAULT false,        -- true on the house org's copy of a tenant's confirmation
  UNIQUE (org_id, fingerprint, report_kind_key)
);
CREATE INDEX IF NOT EXISTS report_signature_org_fp ON commcalc.report_signature (org_id, fingerprint);
COMMENT ON TABLE commcalc.report_signature IS
  'Header fingerprints CONFIRMED at an onboarding intake — header NAMES only, never a cell value, filename, store, rep or customer string. Org row + house copy; the same fingerprint under two kinds is ambiguous and detection asks.';

ALTER TABLE commcalc.report_kind ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.report_signature ENABLE ROW LEVEL SECURITY;
DO $p$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='report_kind' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.report_kind FOR ALL USING (true) WITH CHECK (true);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='report_signature' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.report_signature FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $p$;

-- ── THE HOUSE SEED — generated from backend/app/modules/commcalc/report_kinds.HOUSE_KINDS (the code
--    mirror); harness_report_kinds.py §A parses this block back and requires it EQUAL to the mirror.
--    POS / carrier CODES appear here as DATA only (RULE TWO): 'b2bsoft' / 'boost' / 'total' are the
--    codes of the house's own pos_system term and carrier rows.
INSERT INTO commcalc.report_kind (org_id, key, label, what_in_it, recognisable_columns, source_hint, applies_to_pos,
  applies_to_carrier, defined_by, statement_type, landing, layout, signature_fields, requires_columns, excludes_columns,
  upload_types, sort_order, custom_sheet_label) VALUES
  ('00000000-0000-0000-0000-000000000001', 'sales_imei_phone', 'Sales report with IMEI and phone number', 'Every sale line your POS rang up, with the device IMEI / serial and the customer''s mobile number on the line.', '{"IMEI","Serial","Mobile Number","Trans ID","Ext Price","Salesperson"}', 'export from your POS', '{}', '{}', 'house', NULL, 'sales', 'sales', '{"trans_id","mdn","serial_1","ext_price"}', '{}', '{}', '{"daily_sales","sales"}', 10, NULL),
  ('00000000-0000-0000-0000-000000000001', 'sales_cost_price', 'Sales report with cost and selling price', 'One row per invoice line with the product SKU, what it cost you and what it sold for (gross profit).', '{"Invoice #","Product SKU","Total Price","Total Cost","Gross Profit","Sold By"}', 'export from your POS', '{}', '{}', 'house', NULL, 'pos', 'pos_product_sales', '{"trans_id","sku","ext_price","total_cost","gp"}', '{}', '{}', '{}', 20, NULL),
  ('00000000-0000-0000-0000-000000000001', 'commission_statement', 'Commission report from the carrier', 'The carrier''s statement of what it paid you for activations, upgrades and spiffs — one line per payment, with its label.', '{"Gross","Report Section","Report SubSection","AgentSSOID","Master Service Date","Commission"}', 'from the carrier portal', '{}', '{}', 'house', 'commission', 'commission', 'commission_ledger', '{"raw_amount","product_name","trans_date"}', '{}', '{"Residual"}', '{}', 30, NULL),
  ('00000000-0000-0000-0000-000000000001', 'residual_statement', 'Residual report from the carrier', 'The carrier''s monthly residual statement — recurring pay per line or account for the service month.', '{"Residual","Service Month","MDN","Plan","Account"}', 'from the carrier portal', '{}', '{}', 'house', 'residual', 'commission', 'commission_ledger__residual', '{"raw_amount","account_id","product_name","trans_date"}', '{"Residual"}', '{}', '{}', 40, NULL),
  ('00000000-0000-0000-0000-000000000001', 'inventory_on_hand', 'Inventory on hand', 'What is in stock right now at each store: one row per unit with SKU, IMEI / serial and cost.', '{"Product SKU","Tracking #","Location","Unit Cost","Quantity","Status"}', 'export from your POS', '{}', '{}', 'house', NULL, 'inventory', 'pos_inventory_listing', '{"sku","imei","store","unit_cost"}', '{}', '{"Received","Days in Stock","Age"}', '{}', 50, NULL),
  ('00000000-0000-0000-0000-000000000001', 'inventory_aging', 'Inventory aging', 'The on-hand listing with a received date or days-in-stock per unit, so old stock shows its age.', '{"Received","Days in Stock","Age","SKU","Cost","Location"}', 'export from your POS', '{}', '{}', 'house', NULL, 'inventory', 'pos_inventory_listing', '{"sku","unit_cost"}', '{"Received","Days in Stock","Age"}', '{}', '{"inventory_aging"}', 60, NULL),
  ('00000000-0000-0000-0000-000000000001', 'bill_payments_pos', 'Bill payment transactions (from your POS)', 'Every bill payment your stores took over the counter, as your POS reports it.', '{"Bill Payment","Payment Amount","Carrier","Phone Number","Store"}', 'export from your POS', '{"b2bsoft"}', '{}', 'house', NULL, 'custom_import', NULL, '{}', '{}', '{}', '{}', 70, 'Bill Payments'),
  ('00000000-0000-0000-0000-000000000001', 'bill_payments_carrier', 'Bill payment report from the carrier''s processor', 'The processor''s list of bill payments taken on your account — the feed the bill-pay coverage check reads.', '{"Order Type","Retail Cost","Account ID","Product Name","Order Number"}', 'from the carrier portal', '{}', '{}', 'house', NULL, 'bill_payments', 'ma_daily_tx', '{"account_id","order_type","retail_cost"}', '{}', '{}', '{}', 80, NULL),
  ('00000000-0000-0000-0000-000000000001', 'x_report', 'Cash register / X-report', 'Your POS''s end-of-day tender summary per store: cash, card and other takings for the day.', '{"Cash","Credit","Tender","Register","Total"}', 'export from your POS', '{}', '{}', 'house', NULL, 'x_report', NULL, '{}', '{}', '{}', '{"x_report"}', 90, NULL),
  ('00000000-0000-0000-0000-000000000001', 'merchant_settlement', 'Credit-card (merchant) report', 'Your card processor''s settlement export: per merchant, per business day, per card brand, with fees.', '{"Merchant","Settlement","Card Type","Fees","Net Amount","Batch"}', 'from the card processor''s portal', '{}', '{}', 'house', NULL, 'merchant_payments', NULL, '{}', '{}', '{}', '{}', 100, NULL),
  ('00000000-0000-0000-0000-000000000001', 'something_else', 'Something else', 'Any other report you have. It is recorded as received with its columns and row count, and the file is kept.', '{}', 'wherever it comes from', '{}', '{}', 'house', NULL, 'other', NULL, '{}', '{}', '{}', '{}', 110, NULL),
  ('00000000-0000-0000-0000-000000000001', 'payment_detail', 'Commission payment detail (processor)', 'The payment processor''s commission payment detail for the period.', '{}', 'from the processor portal', '{}', '{"boost"}', 'house', NULL, 'carrier_report', NULL, '{}', '{}', '{}', '{"payment_detail"}', 200, NULL),
  ('00000000-0000-0000-0000-000000000001', 'mi_report', 'MI & ATU report', 'Monthly incentive and ATU subscriber details.', '{}', 'from the processor portal', '{}', '{"boost"}', 'house', NULL, 'carrier_report', NULL, '{}', '{}', '{}', '{"mi_report"}', 210, NULL),
  ('00000000-0000-0000-0000-000000000001', 'comp_report', 'Comprehensive comp report', 'Carrier store-level rebates and MDF for the period.', '{}', 'from the processor portal', '{}', '{"boost"}', 'house', NULL, 'carrier_report', NULL, '{}', '{}', '{}', '{"comp_report"}', 220, NULL),
  ('00000000-0000-0000-0000-000000000001', 'dlar_rep', 'Rep KPI report (carrier portal)', 'Per-rep KPI figures from the carrier''s KPI portal.', '{}', 'from the carrier portal', '{}', '{}', 'house', NULL, 'carrier_report', NULL, '{}', '{}', '{}', '{"dlar_rep"}', 230, NULL),
  ('00000000-0000-0000-0000-000000000001', 'dlar_store', 'Store KPI report (carrier portal)', 'Store-level KPI figures from the carrier''s KPI portal.', '{}', 'from the carrier portal', '{}', '{}', 'house', NULL, 'carrier_report', NULL, '{}', '{}', '{}', '{"dlar_store"}', 240, NULL),
  ('00000000-0000-0000-0000-000000000001', 'catalog', 'Product catalog', 'Product catalog with cost and category.', '{}', 'export from your POS', '{}', '{}', 'house', NULL, 'carrier_report', NULL, '{}', '{}', '{}', '{"catalog"}', 250, NULL),
  ('00000000-0000-0000-0000-000000000001', 'master_cats', 'Payment categories', 'Payment type to category mapping.', '{}', 'export from your POS', '{}', '{}', 'house', NULL, 'carrier_report', NULL, '{}', '{}', '{}', '{"master_cats"}', 260, NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_commission', 'Marketplace commission details', 'Per-activation commission detail from the carrier marketplace feed.', '{}', 'from the carrier portal', '{}', '{"total"}', 'house', NULL, 'carrier_report', NULL, '{}', '{}', '{}', '{"ma_commission"}', 270, NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Marketplace daily transactions', 'Daily airtime / top-up transactions from the carrier marketplace feed.', '{}', 'from the carrier portal', '{}', '{"total"}', 'house', NULL, 'carrier_report', NULL, '{}', '{}', '{}', '{"ma_daily_tx"}', 280, NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_fulfillment', 'Marketplace handset fulfillment', 'Handset fulfillment orders from the carrier marketplace feed.', '{}', 'from the carrier portal', '{}', '{"total"}', 'house', NULL, 'carrier_report', NULL, '{}', '{}', '{}', '{"ma_fulfillment"}', 290, NULL),
  ('00000000-0000-0000-0000-000000000001', 'hotsheet', 'Pricing hotsheet', 'Carrier promo pricing by device, with its effective date.', '{}', 'from the carrier portal', '{}', '{}', 'house', NULL, 'module', NULL, '{}', '{}', '{}', '{"hotsheet"}', 300, NULL),
  ('00000000-0000-0000-0000-000000000001', 'vip_workbook', 'Distributor workbook', 'The distributor scraper workbook (invoices, lines, devices).', '{}', 'from the distributor portal', '{}', '{"boost"}', 'house', NULL, 'module', NULL, '{}', '{}', '{}', '{"vip_workbook"}', 310, NULL),
  ('00000000-0000-0000-0000-000000000001', 'asset_ledger', 'Asset ledger', 'The distributor''s asset-lending ledger.', '{}', 'from the distributor portal', '{}', '{"boost"}', 'house', NULL, 'module', NULL, '{}', '{}', '{}', '{"asset_ledger"}', 320, NULL),
  ('00000000-0000-0000-0000-000000000001', 'daily_closing', 'Daily closing sheet', 'The envelopes export — one row per rep per day.', '{}', 'from your closing form', '{}', '{}', 'house', NULL, 'module', NULL, '{}', '{}', '{}', '{"daily_closing"}', 330, NULL),
  ('00000000-0000-0000-0000-000000000001', 'pos_activation_details', 'Activation details (from your POS)', 'One row per activation, as your POS reports it — drives the store activation counts.', '{}', 'export from your POS', '{"b2bsoft"}', '{}', 'house', NULL, 'custom_import', NULL, '{}', '{}', '{}', '{}', 340, 'Activation Details'),
  ('00000000-0000-0000-0000-000000000001', 'pos_sales_by_product', 'Sales by product (from your POS)', 'Accessory sales by department, as your POS reports it.', '{}', 'export from your POS', '{"b2bsoft"}', '{}', 'house', NULL, 'custom_import', NULL, '{}', '{}', '{}', '{}', 350, 'Sales by Product'),
  ('00000000-0000-0000-0000-000000000001', 'pos_inventory_recon', 'Inventory recon (structured entry)', 'On-hand inventory by store and category — structured entry and recon on its own page.', '{}', 'export from your POS', '{"b2bsoft"}', '{"boost"}', 'house', NULL, 'module', NULL, '{}', '{}', '{}', '{"b2b_inventory"}', 360, NULL)
ON CONFLICT (org_id, key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
