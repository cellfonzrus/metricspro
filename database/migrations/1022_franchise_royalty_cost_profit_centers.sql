-- 1022_franchise_royalty_cost_profit_centers.sql — FRANCHISE ROYALTY REPORT, COST CENTERS, PROFIT CENTERS, and the
-- sale line no classifier claims. Band 1000+ (platform). Additive + idempotent (safe to re-run). Index §37.
--
-- ⚠ MONEY-TOUCHING — WRITTEN, NOT APPLIED. Surface for owner approval before applying. What it can move, and when:
--   · NOTHING moves on apply. Every new table starts empty for every tenant; the P&L heads it adds (coa.PL_SPEC:
--     service_sales / shipping_sales / merchandise_sales / commission_income / royalty_fee / marketing_fee /
--     ad_fund_fee) are `auto_opt` — absent until they carry a dollar. Proved byte-identical for the house org path
--     on the real engine._assemble (backend/harness_royalty.py §H).
--   · Dollars reach a P&L only when a TENANT imports a royalty report (its lines book through the line vocabulary
--     seeded below) or saves a pl_sales_line_map row. The vocabulary seed is scoped to the vertical the `royalty`
--     module applies to (mig 1020), by sub-select — no other tenant reads it.
--
-- OWNER (2026-09-25), verbatim (abridged): "We need to appoint a financial agent to create and manage the finance
-- data like p&l and create cost centers and capture royalty report etc, there are detailed cost centers assigned by
-- ups and dedicated profit centers, all sales will be captured via the royalty report and reconciled against the
-- daily report uploaded by the tenant … Royalty report is uploaded for your reference to create the same in the
-- finance module and only show up if a ups store is selected while onboarding."
--
-- DUPLICATE CHECK (build gate — searched, and what was reused):
--   · cost / profit center: NOTHING existed (index §4, §13, §16 — no center, cost_center or profit_center table,
--     column or config key). The nearest mechanisms are REUSED, not copied: a profit center is a scope of the ONE
--     statement engine (statement_engine._scopes gains a scope family beside company:/store:, statement_filter's
--     scope predicate gains the prefix, fail-closed); stores resolve through coa.store_resolver; a cost center is a
--     regrouping of the assembled statement (centers.cost_center_view), never a second P&L.
--   · royalty report: no table, parser or report kind carried it. The report-kind registry (mig 1010) gains ONE row
--     and ONE axis (applies_to_vertical — '{}' = any, every existing kind unchanged); the P&L booking goes through
--     coa.build_inputs (the one place a line is booked) via a per-line pl_line_key (the mig-1009 commission_bucket
--     shape); the daily side of the recon READS the existing POS landings (raw_sales_product / raw_sales,
--     pos_tender_summary) — no new daily ingest path.
--   · the unclaimed sale line: coa.build_inputs' classifier chain is EXTENDED by one config-driven fallback
--     (pl_sales_line_map) evaluated only after every existing classifier passed the line over.
--
-- REVERT (in this order):
--   DELETE FROM commcalc.report_kind WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'royalty_report';
--   ALTER TABLE commcalc.report_kind DROP COLUMN IF EXISTS applies_to_vertical;
--   DROP TABLE IF EXISTS commcalc.royalty_report_line; DROP TABLE IF EXISTS commcalc.royalty_report;
--   DROP TABLE IF EXISTS commcalc.royalty_line_def; DROP TABLE IF EXISTS commcalc.royalty_config;
--   DROP TABLE IF EXISTS commcalc.pl_line_cost_center; DROP TABLE IF EXISTS commcalc.profit_center_store;
--   DROP TABLE IF EXISTS commcalc.finance_center; DROP TABLE IF EXISTS commcalc.pl_sales_line_map;
--   NOTIFY pgrst, 'reload schema';

-- ── 1. COST CENTERS + PROFIT CENTERS (one dimension table, two maps) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.finance_center (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  center_type   TEXT NOT NULL CHECK (center_type IN ('cost','profit')),
  code          TEXT NOT NULL,                 -- the tenant's / franchisor-assigned code
  name          TEXT NOT NULL,
  parent_code   TEXT,                          -- a center of the same type (a tree; problems reported, never re-parented)
  external_ref  TEXT,                          -- e.g. the franchisor's center number printed on its reports
  is_active     BOOLEAN NOT NULL DEFAULT true,
  notes         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, center_type, code)
);
CREATE INDEX IF NOT EXISTS finance_center_org ON commcalc.finance_center (org_id, center_type);
COMMENT ON TABLE commcalc.finance_center IS
  'Cost centers and profit centers per org (mig 1022, index §37.1). A profit center is a SET OF STORES (profit_center_store) and is a scope of the statement engine (profit_center:<code>); a cost center is a SET OF P&L LINES (pl_line_cost_center) and its view regroups the assembled statement.';

CREATE TABLE IF NOT EXISTS commcalc.profit_center_store (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL,
  store_ref           TEXT NOT NULL,           -- a store code or address; resolved through coa.store_resolver at read time
  profit_center_code  TEXT NOT NULL,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, store_ref)
);

CREATE TABLE IF NOT EXISTS commcalc.pl_line_cost_center (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL,
  pl_line_key       TEXT NOT NULL,             -- a coa.PL_SPEC line key
  detail_label      TEXT NOT NULL DEFAULT '',  -- '' = the whole line; else one drill-down detail label of it
  cost_center_code  TEXT NOT NULL,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, pl_line_key, detail_label)
);

-- ── 2. THE ROYALTY REPORT: config, line vocabulary, header, lines ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.royalty_config (
  org_id             UUID PRIMARY KEY,
  fee_basis          TEXT CHECK (fee_basis IS NULL OR fee_basis IN ('adjusted_str','str')),
  tolerance          NUMERIC,
  daily_source       TEXT CHECK (daily_source IS NULL OR daily_source IN ('raw_sales_product','raw_sales')),
  daily_match_field  TEXT CHECK (daily_match_field IS NULL OR daily_match_field IN ('category','department','product_desc')),
  book_pl            BOOLEAN,
  center_pattern     TEXT,
  period_pattern     TEXT,
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE commcalc.royalty_config IS
  'Per-org royalty-report knobs (mig 1022). A NULL column reads the house default in account/royalty.CONFIG_DEFAULT.';

CREATE TABLE IF NOT EXISTS commcalc.royalty_line_def (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  line_key             TEXT NOT NULL,
  label                TEXT NOT NULL,
  section              TEXT NOT NULL CHECK (section IN ('sales','exclusion','commission','str','fee')),
  role                 TEXT NOT NULL DEFAULT 'line' CHECK (role IN ('header','line','total','echo','str','str_adjusted')),
  aliases              TEXT[] NOT NULL DEFAULT '{}',
  pl_line_key          TEXT,                   -- the P&L line it books to (mig-1009 shape); NULL = books nothing
  pl_note              TEXT,                   -- WHY it books nothing (NULL with no pl_line_key = unmapped, reported)
  rate                 NUMERIC(9,6),           -- fee lines: the rate of the STR
  absorbs_remainder    BOOLEAN NOT NULL DEFAULT false,   -- exactly one fee: total − the other rounded fees
  daily_categories     TEXT[] NOT NULL DEFAULT '{}',     -- sales lines: the daily-report categories it sums
  sort_order           INT NOT NULL DEFAULT 100,
  applies_to_vertical  TEXT[] NOT NULL DEFAULT '{}',     -- house rows: the verticals that read them; '{}' = any
  is_active            BOOLEAN NOT NULL DEFAULT true,
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, line_key)
);
COMMENT ON TABLE commcalc.royalty_line_def IS
  'THE royalty-report line vocabulary (mig 1022, index §37.2): label + aliases per section, the P&L line each books to (or why not), fee rates + which fee absorbs the rounding remainder, the daily categories each sales line reconciles against. House rows = default (vertical-scoped); a tenant row overrides per line_key. Mirrored byte-equal in account/royalty.HOUSE_ROYALTY_LINES.';

CREATE TABLE IF NOT EXISTS commcalc.royalty_report (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                      UUID NOT NULL,
  center_code                 TEXT NOT NULL,           -- as printed on the report
  store_ref                   TEXT,                    -- the store it books to (profit center map / chosen); NULL = company-wide
  period                      TEXT NOT NULL,           -- canonical month spelling ('June 2026', _period.canonical_period)
  source                      TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('pdf','html','text','manual')),
  file_name                   TEXT,
  total_gross_sales           NUMERIC,
  total_exclusions            NUMERIC,
  total_exclusions_adjusted   NUMERIC,
  total_commissions           NUMERIC,
  total_commissions_adjusted  NUMERIC,
  total_str                   NUMERIC,
  total_adjusted_str          NUMERIC,
  total_due                   NUMERIC,
  status                      TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','flagged')),
  validation                  JSONB,                   -- {flags[], computed{}} — every cent the rule disagrees with
  import_batch_id             UUID,
  created_by                  TEXT,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, center_code, period)
);
CREATE INDEX IF NOT EXISTS royalty_report_org_period ON commcalc.royalty_report (org_id, period);
COMMENT ON TABLE commcalc.royalty_report IS
  'One franchise royalty report per org × center × month (mig 1022). The REPORT''s own figures are stored; validation holds every difference from the configured rule (flagged, never corrected). Books the P&L through royalty_line_def.pl_line_key.';

CREATE TABLE IF NOT EXISTS commcalc.royalty_report_line (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL,
  report_id        UUID NOT NULL REFERENCES commcalc.royalty_report(id) ON DELETE CASCADE,
  section          TEXT NOT NULL,
  line_key         TEXT NOT NULL,
  label            TEXT,                        -- as printed
  role             TEXT NOT NULL DEFAULT 'line',
  amount           NUMERIC NOT NULL DEFAULT 0,
  adjustment       NUMERIC,
  adjusted_amount  NUMERIC,
  reason           TEXT,
  printed_rate     NUMERIC,
  sort_order       INT NOT NULL DEFAULT 0,
  UNIQUE (report_id, section, line_key)
);
CREATE INDEX IF NOT EXISTS royalty_report_line_org ON commcalc.royalty_report_line (org_id, report_id);

-- ── 3. THE SALE LINE NO CLASSIFIER CLAIMS (every tenant, index §37.4) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.pl_sales_line_map (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  match_field  TEXT NOT NULL CHECK (match_field IN ('product','category','department')),
  match_value  TEXT NOT NULL,
  pl_line_key  TEXT NOT NULL,                  -- a REVENUE line of coa.PL_SPEC (anything else is rejected + reported)
  is_active    BOOLEAN NOT NULL DEFAULT true,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, match_field, match_value)
);
COMMENT ON TABLE commcalc.pl_sales_line_map IS
  'Routes a POS sale line NO existing P&L classifier claims to a revenue line (mig 1022, index §37.4). Evaluated only after every classifier passed the line over, so no row = byte-identical books; what still books nothing is reported in statement meta unbooked_sales.';

DO $p$ DECLARE t TEXT; BEGIN
  FOREACH t IN ARRAY ARRAY['finance_center','profit_center_store','pl_line_cost_center','royalty_config','royalty_line_def',
                           'royalty_report','royalty_report_line','pl_sales_line_map'] LOOP
    EXECUTE format('ALTER TABLE commcalc.%I ENABLE ROW LEVEL SECURITY', t);
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename=t AND policyname='open_all') THEN
      EXECUTE format('CREATE POLICY open_all ON commcalc.%I FOR ALL USING (true) WITH CHECK (true)', t);
    END IF;
  END LOOP;
END $p$;

-- ── 4. THE HOUSE LINE VOCABULARY — generated from account/royalty.HOUSE_ROYALTY_LINES (harness_royalty.py §A parses
--    this block back and requires it EQUAL to the mirror). Scoped by sub-select to wherever the `royalty` module
--    applies (mig 1020) — no vertical literal here or in code.
INSERT INTO commcalc.royalty_line_def (org_id, line_key, label, section, role, aliases, pl_line_key, pl_note, rate,
  absorbs_remainder, daily_categories, sort_order) VALUES
  ('00000000-0000-0000-0000-000000000001', 'hdr_sales', 'Products / Services', 'sales', 'header', '{}', NULL, NULL, NULL, false, '{}', 1),
  ('00000000-0000-0000-0000-000000000001', 'hdr_exclusion', 'Exclusions', 'exclusion', 'header', '{}', NULL, NULL, NULL, false, '{}', 2),
  ('00000000-0000-0000-0000-000000000001', 'hdr_commission', 'Commissions', 'commission', 'header', '{}', NULL, NULL, NULL, false, '{}', 3),
  ('00000000-0000-0000-0000-000000000001', 'hdr_str', 'Subject to Royalty', 'str', 'header', '{}', NULL, NULL, NULL, false, '{}', 4),
  ('00000000-0000-0000-0000-000000000001', 'hdr_fee', 'Royalty Fees', 'fee', 'header', '{}', NULL, NULL, NULL, false, '{}', 5),
  ('00000000-0000-0000-0000-000000000001', 'mailbox_service', 'Mailbox Service', 'sales', 'line', '{}', 'service_sales', NULL, NULL, false, '{}', 10),
  ('00000000-0000-0000-0000-000000000001', 'copies', 'Copies', 'sales', 'line', '{}', 'service_sales', NULL, NULL, false, '{}', 11),
  ('00000000-0000-0000-0000-000000000001', 'color_copies', 'Color Copies', 'sales', 'line', '{"Colour Copies"}', 'service_sales', NULL, NULL, false, '{}', 12),
  ('00000000-0000-0000-0000-000000000001', 'laminating_binding', 'Laminating/Binding', 'sales', 'line', '{}', 'service_sales', NULL, NULL, false, '{}', 13),
  ('00000000-0000-0000-0000-000000000001', 'facsimile', 'Facsimile', 'sales', 'line', '{"Fax"}', 'service_sales', NULL, NULL, false, '{}', 14),
  ('00000000-0000-0000-0000-000000000001', 'stamp_sales', 'Stamp Sales', 'sales', 'line', '{}', 'merchandise_sales', NULL, NULL, false, '{}', 15),
  ('00000000-0000-0000-0000-000000000001', 'metered_mail', 'Metered Mail', 'sales', 'line', '{}', 'shipping_sales', NULL, NULL, false, '{}', 16),
  ('00000000-0000-0000-0000-000000000001', 'shipping_charge', 'Shipping Charge (UPS)', 'sales', 'line', '{"Shipping Charge"}', 'shipping_sales', NULL, NULL, false, '{}', 17),
  ('00000000-0000-0000-0000-000000000001', 'no_limit_shipping', 'No Limit Shipping', 'sales', 'line', '{}', 'shipping_sales', NULL, NULL, false, '{}', 18),
  ('00000000-0000-0000-0000-000000000001', 'retail_shipping_supply', 'Retail Shipping Supply', 'sales', 'line', '{}', 'merchandise_sales', NULL, NULL, false, '{}', 19),
  ('00000000-0000-0000-0000-000000000001', 'packaging_materials', 'Packaging Materials', 'sales', 'line', '{}', 'merchandise_sales', NULL, NULL, false, '{}', 20),
  ('00000000-0000-0000-0000-000000000001', 'packaging_service_fee', 'Packaging Service Fee', 'sales', 'line', '{}', 'service_sales', NULL, NULL, false, '{}', 21),
  ('00000000-0000-0000-0000-000000000001', 'office_supplies', 'Office Supplies', 'sales', 'line', '{}', 'merchandise_sales', NULL, NULL, false, '{}', 22),
  ('00000000-0000-0000-0000-000000000001', 'rubber_stamps', 'Rubber Stamps', 'sales', 'line', '{}', 'merchandise_sales', NULL, NULL, false, '{}', 23),
  ('00000000-0000-0000-0000-000000000001', 'greeting_cards', 'Greeting Cards', 'sales', 'line', '{}', 'merchandise_sales', NULL, NULL, false, '{}', 24),
  ('00000000-0000-0000-0000-000000000001', 'printing', 'Printing', 'sales', 'line', '{}', 'service_sales', NULL, NULL, false, '{}', 25),
  ('00000000-0000-0000-0000-000000000001', 'desktop_word_processing', 'Desktop/Word Processing', 'sales', 'line', '{}', 'service_sales', NULL, NULL, false, '{}', 26),
  ('00000000-0000-0000-0000-000000000001', 'computer_timeshare', 'Computer Timeshare', 'sales', 'line', '{}', 'service_sales', NULL, NULL, false, '{}', 27),
  ('00000000-0000-0000-0000-000000000001', 'message_services', 'Message Services', 'sales', 'line', '{}', 'service_sales', NULL, NULL, false, '{}', 28),
  ('00000000-0000-0000-0000-000000000001', 'pagers', 'Pagers', 'sales', 'line', '{}', 'merchandise_sales', NULL, NULL, false, '{}', 29),
  ('00000000-0000-0000-0000-000000000001', 'money_transfer', 'Money Transfer', 'sales', 'line', '{}', NULL, 'pass-through: the face value is collected for a third party, not earned; the commission on it books under Commissions', NULL, false, '{}', 30),
  ('00000000-0000-0000-0000-000000000001', 'money_orders', 'Money Orders', 'sales', 'line', '{}', NULL, 'pass-through: the face value is collected for a third party, not earned; the commission on it books under Commissions', NULL, false, '{}', 31),
  ('00000000-0000-0000-0000-000000000001', 'notary', 'Notary', 'sales', 'line', '{}', 'service_sales', NULL, NULL, false, '{}', 32),
  ('00000000-0000-0000-0000-000000000001', 'passport_photos', 'Passport Photos', 'sales', 'line', '{}', 'service_sales', NULL, NULL, false, '{}', 33),
  ('00000000-0000-0000-0000-000000000001', 'public_service_payments', 'Public Service Payments', 'sales', 'line', '{}', NULL, 'pass-through: the face value is collected for a third party, not earned; the commission on it books under Commissions', NULL, false, '{}', 34),
  ('00000000-0000-0000-0000-000000000001', 'rapid_air', 'Rapid Air', 'sales', 'line', '{}', 'shipping_sales', NULL, NULL, false, '{}', 35),
  ('00000000-0000-0000-0000-000000000001', 'misc_taxable', 'Miscellaneous Taxable', 'sales', 'line', '{}', 'merchandise_sales', NULL, NULL, false, '{}', 36),
  ('00000000-0000-0000-0000-000000000001', 'misc_non_taxable', 'Miscellaneous Non Taxable', 'sales', 'line', '{"Miscellaneous Non-Taxable"}', 'merchandise_sales', NULL, NULL, false, '{}', 37),
  ('00000000-0000-0000-0000-000000000001', 'deposits', 'Deposits', 'sales', 'line', '{}', NULL, 'a customer deposit is a liability until it is earned, not revenue', NULL, false, '{}', 38),
  ('00000000-0000-0000-0000-000000000001', 'sales_tax', 'Sales Tax', 'sales', 'line', '{}', NULL, 'sales tax collected is owed to the taxing authority (a liability), not revenue', NULL, false, '{}', 39),
  ('00000000-0000-0000-0000-000000000001', 'gross_sales_total', 'Total Gross Sales', 'sales', 'total', '{}', NULL, NULL, NULL, false, '{}', 49),
  ('00000000-0000-0000-0000-000000000001', 'excl_stamp_cost', 'Stamp Cost', 'exclusion', 'line', '{}', NULL, 'an exclusion reduces the royalty base only; the underlying cost books through expenses, not from this report', NULL, false, '{}', 50),
  ('00000000-0000-0000-0000-000000000001', 'excl_metered_mail_cost', 'Metered Mail Cost', 'exclusion', 'line', '{}', NULL, 'an exclusion reduces the royalty base only; the underlying cost books through expenses, not from this report', NULL, false, '{}', 51),
  ('00000000-0000-0000-0000-000000000001', 'excl_money_transfer', 'Money Transfer', 'exclusion', 'line', '{}', NULL, 'an exclusion reduces the royalty base only; the underlying cost books through expenses, not from this report', NULL, false, '{}', 52),
  ('00000000-0000-0000-0000-000000000001', 'excl_money_order_cost', 'Money Order Cost', 'exclusion', 'line', '{}', NULL, 'an exclusion reduces the royalty base only; the underlying cost books through expenses, not from this report', NULL, false, '{}', 53),
  ('00000000-0000-0000-0000-000000000001', 'excl_public_service_payment_cost', 'Public Service Payment Cost', 'exclusion', 'line', '{}', NULL, 'an exclusion reduces the royalty base only; the underlying cost books through expenses, not from this report', NULL, false, '{}', 54),
  ('00000000-0000-0000-0000-000000000001', 'excl_sales_tax', 'Sales Tax', 'exclusion', 'line', '{}', NULL, 'an exclusion reduces the royalty base only; the underlying cost books through expenses, not from this report', NULL, false, '{}', 55),
  ('00000000-0000-0000-0000-000000000001', 'excl_deposits', 'Deposits', 'exclusion', 'line', '{}', NULL, 'an exclusion reduces the royalty base only; the underlying cost books through expenses, not from this report', NULL, false, '{}', 56),
  ('00000000-0000-0000-0000-000000000001', 'excl_other_1', 'Other 1', 'exclusion', 'line', '{}', NULL, 'an exclusion reduces the royalty base only; the underlying cost books through expenses, not from this report', NULL, false, '{}', 57),
  ('00000000-0000-0000-0000-000000000001', 'excl_iship_proc_fee', 'iShip Proc Fee', 'exclusion', 'line', '{"iShip Processing Fee"}', NULL, 'an exclusion reduces the royalty base only; the underlying cost books through expenses, not from this report', NULL, false, '{}', 58),
  ('00000000-0000-0000-0000-000000000001', 'exclusions_total', 'Total Exclusions', 'exclusion', 'total', '{}', NULL, NULL, NULL, false, '{}', 69),
  ('00000000-0000-0000-0000-000000000001', 'comm_money_transfer', 'Money Transfer', 'commission', 'line', '{}', 'commission_income', NULL, NULL, false, '{}', 70),
  ('00000000-0000-0000-0000-000000000001', 'comm_other_1', 'Other 1', 'commission', 'line', '{}', 'commission_income', NULL, NULL, false, '{}', 71),
  ('00000000-0000-0000-0000-000000000001', 'comm_other_2', 'Other 2', 'commission', 'line', '{}', 'commission_income', NULL, NULL, false, '{}', 72),
  ('00000000-0000-0000-0000-000000000001', 'commissions_total', 'Total Commissions', 'commission', 'total', '{}', NULL, NULL, NULL, false, '{}', 79),
  ('00000000-0000-0000-0000-000000000001', 'str_gross_sales', 'Total Gross Sales', 'str', 'echo', '{}', NULL, NULL, NULL, false, '{}', 80),
  ('00000000-0000-0000-0000-000000000001', 'str_exclusions', 'Total Exclusions', 'str', 'echo', '{}', NULL, NULL, NULL, false, '{}', 81),
  ('00000000-0000-0000-0000-000000000001', 'str_commissions', 'Total Commissions', 'str', 'echo', '{}', NULL, NULL, NULL, false, '{}', 82),
  ('00000000-0000-0000-0000-000000000001', 'str_total', 'Total STR', 'str', 'str', '{}', NULL, NULL, NULL, false, '{}', 83),
  ('00000000-0000-0000-0000-000000000001', 'str_adjusted', 'Total Adjusted STR', 'str', 'str_adjusted', '{}', NULL, NULL, NULL, false, '{}', 84),
  ('00000000-0000-0000-0000-000000000001', 'fee_royalty', 'Royalty Due', 'fee', 'line', '{}', 'royalty_fee', NULL, 0.05, false, '{}', 90),
  ('00000000-0000-0000-0000-000000000001', 'fee_marketing', 'Marketing Due', 'fee', 'line', '{}', 'marketing_fee', NULL, 0.01, true, '{}', 91),
  ('00000000-0000-0000-0000-000000000001', 'fee_naf', 'NAF Due', 'fee', 'line', '{"National Advertising Fund Due"}', 'ad_fund_fee', NULL, 0.025, false, '{}', 92),
  ('00000000-0000-0000-0000-000000000001', 'fee_total', 'Total Due', 'fee', 'total', '{}', NULL, NULL, NULL, false, '{}', 99)
ON CONFLICT (org_id, line_key) DO NOTHING;
UPDATE commcalc.royalty_line_def
   SET applies_to_vertical = COALESCE((SELECT applies_to_vertical FROM core.module_catalog WHERE key = 'royalty'), '{}')
 WHERE org_id = '00000000-0000-0000-0000-000000000001' AND applies_to_vertical = '{}';

-- ── 5. THE REPORT-KIND REGISTRY: the vertical axis + the royalty report kind ─────────────────────────────────
ALTER TABLE commcalc.report_kind ADD COLUMN IF NOT EXISTS applies_to_vertical TEXT[] NOT NULL DEFAULT '{}';
COMMENT ON COLUMN commcalc.report_kind.applies_to_vertical IS
  'Tenant verticals (core.tenant_vertical keys, mig 1020) this kind applies to; {} = any (every kind before 1022). Read by report_kinds.applies / carrier-scope.kindApplies through the tenant declaration''s vertical (report_kinds.tenant_declaration).';
-- generated from report_kinds.HOUSE_KINDS (harness_report_kinds.py §A parses it back with 1010 + 1012)
INSERT INTO commcalc.report_kind (org_id, key, label, what_in_it, recognisable_columns, source_hint, applies_to_pos,
  applies_to_carrier, defined_by, statement_type, landing, layout, signature_fields, requires_columns, excludes_columns,
  upload_types, sort_order, custom_sheet_label) VALUES
  ('00000000-0000-0000-0000-000000000001', 'royalty_report', 'Franchise royalty report (monthly)', 'The franchisor''s monthly royalty statement for one center: product and service sales, exclusions, commissions, the sales subject to royalty and the royalty, marketing and advertising-fund fees due.', '{"Products / Services","Total Gross Sales","Exclusions","Commissions","Total STR","Total Due"}', 'from the franchisor''s center-management portal (save the page as PDF or HTML, or paste it)', '{}', '{}', 'house', NULL, 'module', NULL, '{}', '{}', '{}', '{}', 400, NULL)
ON CONFLICT (org_id, key) DO NOTHING;
UPDATE commcalc.report_kind
   SET applies_to_vertical = COALESCE((SELECT applies_to_vertical FROM core.module_catalog WHERE key = 'royalty'), '{}')
 WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'royalty_report';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 1022 complete — finance_center / profit_center_store / pl_line_cost_center, royalty_config / royalty_line_def (house vocabulary, vertical-scoped) / royalty_report / royalty_report_line, pl_sales_line_map, report_kind.applies_to_vertical + the royalty_report kind. No existing row touched.' AS status;
