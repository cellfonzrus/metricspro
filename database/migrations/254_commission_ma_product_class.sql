-- 254_commission_ma_product_class.sql
-- PRODUCT-NAME CLASSIFICATION for the MA Daily Tx file (commcalc.raw_ma_daily_tx.product_name).
--
-- WHY (owner directive, in chat 2026-07-31): that one column mixes many payment types — a commission
-- installment, a spiff installment, a residual, a customer PLAN PURCHASE, a DEVICE SALE, a dealer FEE,
-- a credit memo — all side by side. Booking the column as one thing ("commission") is wrong for most
-- of its rows. This adds a per-tenant, per-product-name classification with an owner CONFIRMATION step.
--
-- THIS MIGRATION MOVES $0. It creates three NEW tables and seeds the house org. Nothing that decides a
-- payout reads them: not calculator.py, not commission_engine.py, not rep_commissions, not
-- commission_ledger / commission_category_map, not ledger_ma_sync, not whatif's carrier income. Wiring
-- a class into a money number is a SEPARATE, owner-gated change (OPEN item in docs/handoffs/commission.md).
--
-- WHY NOT commcalc.commission_category_map (mig 071): that is a RULE table (pattern/priority,
-- first-match-wins) whose rows ARE read by commission_ledger.load_rules() and booked into the five
-- canonical payout buckets that feed What-If carrier income. load_rules selects
-- source_report IN (<template>, '*'), so a row filed under the wrong namespace silently reclassifies
-- live money; build_row() books row[category] only for the five buckets, so a row carrying
-- 'device_sale' would set payout_total yet vanish from every roll-up; and list_templates() would offer
-- this taxonomy as a ledger TEMPLATE. The shape needed here — ONE ROW PER OBSERVED VALUE of a raw
-- column, exact key, own vocabulary — is the one commcalc.gp_category_map (mig 069) already
-- established in this module. See backend/app/modules/commcalc/ma_product_class.py header.
--
-- MATCHING IS EXACT: byte equality after trim(), case-sensitive. No contains/prefix/regex — 'TW EDGE
-- SPF Month 1' is the TW FINANCING TENDER, not a Motorola Edge handset, and 'Total ALL ACCESS Plan $65'
-- vs 'Total ALL ACCESS Plan $65 New Activation Commission' differ only by suffix. The one normalization
-- is trim(), which exists because the export ships 'Trac Autopay Residual ' with a TRAILING SPACE.
--
-- ADDITIVE + IDEMPOTENT + RLS-ZERO-POLICY: safe to re-run; RLS on, NO policies, NO anon/authenticated
-- grants (contract §5 — all access is via the backend service role). Degrades gracefully: until this
-- runs, the code falls back to its built-in vocabulary + built-in proposals (read-only).

-- ── 1) the class vocabulary (owner-editable) ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.ma_product_class (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  class_key    TEXT NOT NULL,
  label        TEXT NOT NULL,
  description  TEXT,
  sort_order   INT  NOT NULL DEFAULT 500,
  is_reserved  BOOLEAN NOT NULL DEFAULT false,   -- 'unmapped' — shown, never assignable
  is_active    BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, class_key)
);
CREATE INDEX IF NOT EXISTS ma_product_class_org ON commcalc.ma_product_class (org_id, sort_order);

-- ── 2) the map: (tenant, source, EXACT product name) -> class, with a confirmation lifecycle ────────
CREATE TABLE IF NOT EXISTS commcalc.ma_product_class_map (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  source_report TEXT NOT NULL DEFAULT 'ma_daily_tx',
  product_name  TEXT NOT NULL,                   -- stored TRIMMED; matched byte-exact after trim()
  product_class TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'proposed',-- proposed | confirmed  (owner confirms in the UI)
  note          TEXT,
  confirmed_by  TEXT,
  confirmed_at  TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, source_report, product_name)
);
CREATE INDEX IF NOT EXISTS ma_product_class_map_lookup ON commcalc.ma_product_class_map (org_id, source_report);
CREATE INDEX IF NOT EXISTS ma_product_class_map_class  ON commcalc.ma_product_class_map (org_id, product_class);
CREATE INDEX IF NOT EXISTS ma_product_class_map_status ON commcalc.ma_product_class_map (org_id, status);

-- ── 3) the source registry: which raw table/column a source_report classifies (RULE TWO) ───────────
-- amount_column is the SIGNED money column the read-only preview sums. merchant_invoice is NOT
-- offerable: mig 083 typed it NUMERIC but it is the Merchant Invoice NUMBER (ma_upload.FIELD_LABELS
-- role='key'); the code refuses it whatever is stored here.
CREATE TABLE IF NOT EXISTS commcalc.ma_product_class_source (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  source_report TEXT NOT NULL DEFAULT 'ma_daily_tx',
  source_table  TEXT,
  name_column   TEXT,
  amount_column TEXT,
  date_column   TEXT,
  period_column TEXT,
  store_column  TEXT,
  rep_column    TEXT,
  label         TEXT,
  is_active     BOOLEAN NOT NULL DEFAULT true,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, source_report)
);
CREATE INDEX IF NOT EXISTS ma_product_class_source_org ON commcalc.ma_product_class_source (org_id);

-- RLS ON, ZERO POLICIES, ZERO GRANTS (contract §5).
ALTER TABLE commcalc.ma_product_class        ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.ma_product_class_map    ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.ma_product_class_source ENABLE ROW LEVEL SECURITY;

-- ── 4) seed the class vocabulary for the house org ─────────────────────────────────────────────────
INSERT INTO commcalc.ma_product_class (org_id, class_key, label, description, sort_order, is_reserved)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'commission', 'Commission', 'Activation / upgrade commission earned by the dealer.', 10, false),
  ('00000000-0000-0000-0000-000000000001', 'spiff', 'Spiff', 'Promotional or behaviour bonus (SPF), often paid across M1..M6.', 20, false),
  ('00000000-0000-0000-0000-000000000001', 'residual', 'Residual', 'Recurring monthly / autopay residual on an active subscriber.', 30, false),
  ('00000000-0000-0000-0000-000000000001', 'billpayment', 'Bill payment / airtime', 'Customer plan purchase or RTR airtime top-up sold at the counter — retail revenue, not a carrier payout to the dealer.', 40, false),
  ('00000000-0000-0000-0000-000000000001', 'device_sale', 'Device sale', 'Handset / tablet / router sold to the customer.', 50, false),
  ('00000000-0000-0000-0000-000000000001', 'protection', 'Protection / insurance', 'Device-protection or insurance plan line.', 60, false),
  ('00000000-0000-0000-0000-000000000001', 'financing', 'Financing', 'Financing credit or financing-tender line (e.g. the TW EDGE tender).', 70, false),
  ('00000000-0000-0000-0000-000000000001', 'subsidy', 'Subsidy', 'Carrier device subsidy / equipment rebate.', 80, false),
  ('00000000-0000-0000-0000-000000000001', 'fee', 'Fee', 'Fee charged to the dealer (invoice, processing).', 90, false),
  ('00000000-0000-0000-0000-000000000001', 'wallet', 'Wallet funding', 'Funding of the dealer''s RTR wallet.', 100, false),
  ('00000000-0000-0000-0000-000000000001', 'sim_kit', 'SIM kit', 'SIM card / SIM-kit line.', 110, false),
  ('00000000-0000-0000-0000-000000000001', 'adjustment_memo', 'Adjustment / memo', 'Credit or debit memo — a correction, not an earning.', 120, false),
  ('00000000-0000-0000-0000-000000000001', 'unmapped', 'Unmapped', 'RESERVED — no confirmed class yet. Never assignable; always surfaced with its own line count and dollar total.', 999, true)
ON CONFLICT (org_id, class_key) DO NOTHING;

-- ── 5) seed the owner's own sample of real product names as PROPOSALS ──────────────────────────────
-- status='proposed' on every row: these are suggestions, not decisions. The owner confirms each one on
-- /commcalc/ma-product-class (or bulk-confirms), which is the money decision staying with the owner.
-- Generated from ma_product_class.DEFAULT_PROPOSALS — the proof asserts SQL and code are identical.
INSERT INTO commcalc.ma_product_class_map (org_id, source_report, product_name, product_class, status, note)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'TBV MONTH 2 New Activation Commission', 'commission', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'TBV MONTH 3 New Activation Commission', 'commission', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'New Activation Commission - M1 Proration', 'commission', 'proposed', 'Partial-month M1 commission.'),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total MAX 5G BYO Plan $30 New Activation Commission', 'commission', 'proposed', 'Suffix-only difference from the plan-purchase line ''Total MAX 5G BYO Plan $30'' — the reason matching is exact.'),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total STARTER Plan $40 New Activation Commission', 'commission', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total MAX 5G Plan $55 New Activation Commission', 'commission', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total ALL ACCESS 2 Month Plan $130 New Activation Commission', 'commission', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total ALL ACCESS Plan $65 New Activation Commission', 'commission', 'proposed', 'Sampled at $0.00 — a commission line that happened to pay nothing, NOT a plan purchase.'),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless Base Unlimited Tablet 3-Month Plan $30 New Activation Commission', 'commission', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'TBV MONTH 4 New Activation SPF', 'spiff', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'TBV MONTH 5 New Activation SPF', 'spiff', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'TBV MONTH 6 New Activation SPF', 'spiff', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'BYO Activation SPF Month 1', 'spiff', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'BYO Activation SPF Month 2', 'spiff', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'TW EDGE SPF Month 1', 'spiff', 'proposed', 'EDGE here is the Total Wireless FINANCING TENDER, not a Motorola Edge handset — a ''contains edge'' rule would misclassify phones. Exact match only.'),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Residual', 'residual', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Trac Autopay Residual', 'residual', 'proposed', 'The export ships this with a TRAILING SPACE (''Trac Autopay Residual ''); it matches after trim().'),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total MAX 5G BYO Plan $30', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total MAX 5G BYO Plan $30 RTR', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total MAX 5G Plan $55', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total MAX 5G Plan $55 RTR', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total STARTER Plan $40', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total STARTER Plan $40 RTR', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total ALL ACCESS Plan $65', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total ALL ACCESS Plan $65 RTR', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total ALL ACCESS 2 Month Plan $130', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless 5G Unlimited RTR $55', 'billpayment', 'proposed', 'Sampled at $30.00 against a $55 label — a partial top-up, not a mismatch.'),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless 5G+ Unlimited RTR $65', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless Base 5G Unlimited RTR $40', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless Base Unlimited Tablet Plan $50', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless Base Unlimited Tablet Plan RTR $50', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless Base Unlimited Tablet 3-Month Plan $30', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless Base Unlimited Tablet 6-Month Plan $60', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless 5G Unlimited Tablet Plan RTR $60', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless $50 Data Plan 100GB', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless Home Internet', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless Home Internet RTR', 'billpayment', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Verizon Postpaid Payment', 'billpayment', 'proposed', 'Bill payment taken at the counter for another brand.'),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Simple Mobile RTR $60', 'billpayment', 'proposed', 'Other-brand airtime sold at the counter.'),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless Device Upgrade', 'billpayment', 'proposed', 'AMBIGUOUS — sampled at $0.00 with no '' TO'' device suffix and no price. Proposed as the upgrade TRANSACTION line rather than a device sale; please verify before confirming.'),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Apple iPhone 16e 128GB Black TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Apple iPhone 17e 256GB Black TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Samsung Galaxy A16 5G TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Samsung Galaxy A17 5G TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Samsung Galaxy A26 5G TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Samsung Galaxy A36 TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Samsung Galaxy A37 5G TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Samsung Galaxy S25 FE TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Samsung Galaxy Tab A11+ 5G TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Motorola Moto G 5G 2026 TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Motorola Moto G Power 5G 2026 TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Motorola Moto G Stylus 5G 2025 TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Motorola Razr 2025 Blue TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Motorola Razr 2025 FIFA TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Motorola Razr 2025 Teal TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Google Pixel 10a TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'TCL Tab 8 NXTPAPER 5G TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'TCL Tab 10 NXTPAPER 5G TO', 'device_sale', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Home Internet Router TO', 'device_sale', 'proposed', 'Sampled at $0.00 — a bundled router still ships as a device line.'),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless Protection', 'protection', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless Protection RTR', 'protection', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless Protect+', 'protection', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless Protect+ RTR', 'protection', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Financing Credit', 'financing', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Subsidy', 'subsidy', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Invoice Fee', 'fee', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total Wireless RTR Wallet', 'wallet', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Total by Verizon SIM Kit', 'sim_kit', 'proposed', NULL),
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'Credit Debit Memo', 'adjustment_memo', 'proposed', 'AMBIGUOUS in direction — a memo can be a credit or a debit; the sign on the line decides, and this module never touches signs.')
ON CONFLICT (org_id, source_report, product_name) DO NOTHING;

-- ── 6) seed the source registry row for the house org ──────────────────────────────────────────────
INSERT INTO commcalc.ma_product_class_source
  (org_id, source_report, source_table, name_column, amount_column, date_column, period_column,
   store_column, rep_column, label)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'ma_daily_tx', 'raw_ma_daily_tx', 'product_name', 'retail_cost', 'tx_date', 'period', 'account_name', 'user_name', 'MA Daily Tx (VidaPay / Total) — Product Name')
ON CONFLICT (org_id, source_report) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 254 complete — product_class ('
       || (SELECT count(*) FROM commcalc.ma_product_class WHERE org_id = '00000000-0000-0000-0000-000000000001') || ' classes), '
       || 'product_class_map ('
       || (SELECT count(*) FROM commcalc.ma_product_class_map WHERE org_id = '00000000-0000-0000-0000-000000000001') || ' proposals), '
       || 'product_class_source ('
       || (SELECT count(*) FROM commcalc.ma_product_class_source WHERE org_id = '00000000-0000-0000-0000-000000000001') || ' sources)' AS status;
