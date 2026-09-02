-- 944_billpay_threeway_config.sql — bill-pay-on-credit column + 3-way bill-payment recon CONFIG
-- (owner directive 2026-09-02, item #2)
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- Owner, verbatim: "in the billpayment pick, add another column for bill payment on credit card,
-- and the pos bill payments are showing 0 as the pos does not store the bill payment on credit
-- card separately, two ways it will be done and a part of 3 way recon for bill payments, 1 will
-- be the total of bill payments received on credit card from the sales transactions for that day
-- from the email ingested reports and the second will be from the owners portal report for bill
-- payment in case of boost and the daily tx report for total, again nothing hardcoded and
-- everything indexed for future".
--
-- Nothing hardcoded ⇒ two per-org vocabularies become CONFIG (house defaults live in
-- backend metric_recon.py — DEFAULT_CARD_TENDERS/DEFAULT_CASH_TENDERS and
-- DEFAULT_MA_BILLPAY_ORDER_TYPES/DEFAULT_MA_BILLPAY_PRODUCT_TOKENS — so NULL columns are
-- byte-identical to the defaults; no carrier or tenant name in code, RULE TWO):
--
--   1. accessory_config.billpay_card_tenders / billpay_cash_tenders — the POS tender_type tokens
--      (lower-cased containment) that classify a bill-payment sales line as taken on CARD vs
--      CASH for the tender split (Leg B of the 3-way recon; "bill payments received on credit
--      card from the sales transactions"). NULL → card = {credit, debit}, cash = {cash} (live
--      evidence 2026-09-02, org 854f…: tender_type values are Cash / Debit Card / Credit Card /
--      multi-tender combos; a multi-tender line lands in the 'mixed' bucket, never guessed).
--      Sits beside the mig-214 billpay_products list — the SAME classification config family.
--
--   2. metric_source_of_truth.processor_order_types / processor_product_tokens (meaningful on
--      the metric='bill_payments' row) — WHICH rows of the carrier daily-TX report are bill
--      payments (Leg C, the 'daily tx report' side of the config-resolved processor choice; the
--      'owner's portal' side is the ePay daily feed, already bill-pay-only). NULL →
--      order_types = {Sales Order}, product tokens = {rtr, wallet funding}, UNION'd with the
--      org's curated accessory_config.billpay_products exact list. This is the DEFECT FIX for
--      "the pos bill payments are showing 0": the daily-TX leg previously summed EVERY row
--      (handsets/residuals/spiffs — 18,120 of 22,163 Aug 2026 rows for org 854f… were
--      non-billpay) under unmapped raw account ids, so the declared-side lookup missed every
--      real store-day and rendered honest zeros everywhere.
--
-- The Leg-C source itself stays the EXISTING mig-923 metric_source_of_truth resolution
-- (processor config / data_source auto-detect) — no new source enum needed. Account→store for
-- the daily-TX feed now falls back from storeops.store_merchant_id (mig 902) to the mig-314
-- account→store index (ma_account_store_map ∪ raw_ma_fulfillment) — existing config, reused.
--
-- MONEY: display/recon vocabularies only — no P&L or payout number books off these columns.
-- Additive + idempotent. Run in the Supabase SQL editor.

ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS billpay_card_tenders JSONB,
  ADD COLUMN IF NOT EXISTS billpay_cash_tenders JSONB;

COMMENT ON COLUMN commcalc.accessory_config.billpay_card_tenders IS
  'POS tender_type tokens (lower-cased containment) classifying a bill-payment sales line as CARD '
  'for the tender split (owner 2026-09-02 #2; Leg B of the 3-way bill-pay recon). NULL = house '
  'default {credit, debit} (metric_recon.DEFAULT_CARD_TENDERS).';
COMMENT ON COLUMN commcalc.accessory_config.billpay_cash_tenders IS
  'POS tender_type tokens classifying a bill-payment sales line as CASH for the tender split. '
  'NULL = house default {cash} (metric_recon.DEFAULT_CASH_TENDERS). A line matching both '
  'vocabularies (multi-tender receipt) is bucketed ''mixed'', never attributed to either side.';

ALTER TABLE commcalc.metric_source_of_truth
  ADD COLUMN IF NOT EXISTS processor_order_types    JSONB,
  ADD COLUMN IF NOT EXISTS processor_product_tokens JSONB;

COMMENT ON COLUMN commcalc.metric_source_of_truth.processor_order_types IS
  'For metric=bill_payments: order_type families of the carrier daily-TX report that carry bill '
  'payments (owner 2026-09-02 #2 — Leg C row filter). NULL = house default {Sales Order} '
  '(metric_recon.DEFAULT_MA_BILLPAY_ORDER_TYPES).';
COMMENT ON COLUMN commcalc.metric_source_of_truth.processor_product_tokens IS
  'For metric=bill_payments: product_name containment tokens marking a daily-TX row as a bill '
  'payment, UNION''d with the org''s accessory_config.billpay_products exact list (mig 214). '
  'NULL = house default {rtr, wallet funding} (metric_recon.DEFAULT_MA_BILLPAY_PRODUCT_TOKENS).';

-- RLS: columns on existing tables — accessory_config (mig 208) and metric_source_of_truth
-- (mig 923) policies already cover them; no policy change.

-- ── ORG SEEDS (OWNER-GATED — do not run without explicit owner approval) ───────────────────────
-- The house defaults already match the live evidence for org 854f6d7b (Total-style: daily-TX
-- 'Sales Order' RTR families; tenders Cash/Debit Card/Credit Card), so NO seed is required for
-- the fix to take effect. Kept here only as the template an owner-approved narrowing would use:
--   -- UPDATE commcalc.metric_source_of_truth
--   --    SET processor_order_types = '["Sales Order"]'::jsonb,
--   --        processor_product_tokens = '["rtr","wallet funding"]'::jsonb
--   --  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' AND metric = 'bill_payments';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 944 complete — billpay tender-split + daily-TX row-filter config (NULL = house defaults, byte-identical)' AS status;

-- REVERT:
--   ALTER TABLE commcalc.accessory_config
--     DROP COLUMN IF EXISTS billpay_card_tenders, DROP COLUMN IF EXISTS billpay_cash_tenders;
--   ALTER TABLE commcalc.metric_source_of_truth
--     DROP COLUMN IF EXISTS processor_order_types, DROP COLUMN IF EXISTS processor_product_tokens;
--   (every reader is defensive: a pre-944 schema resolves to the metric_recon house defaults —
--    the tender split and the daily-TX row filter keep working on defaults.)
