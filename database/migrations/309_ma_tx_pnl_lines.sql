-- 309_ma_tx_pnl_lines.sql
-- mod-commission · band 200–299 spill → 309 (follows 308). Additive + idempotent + safe to re-run.
--
-- WHAT IT IS FOR (owner spec 2026-09-01, "MA TX → P&L booking", Phase B): "Merchant discount for
-- each line item goes into the P&L as merchant discount, residual under residual." Two per-org
-- presentation knobs on commcalc.commission_org_config (the per-org config home since mig 201,
-- extended 233/246/256):
--   • pl_merchant_discount_own_line — raw_ma_daily_tx.merchant_discount books to its OWN
--     "Merchant discount" revenue line (coa.PL_SPEC key ma_merchant_discount) instead of being
--     folded into "ATU income".
--   • pl_ma_residual_order_types — order_type values that book as Residual IN ADDITION to the
--     existing product_name '%residual%' label family (residual_subs._MA_RESIDUAL_LABEL_MATCH).
--     UNION semantics, dedup: a row matching both criteria books ONCE.
-- Resolution / row classification: app/modules/account/residual_subs.py (load_ma_pnl_config,
-- ma_residual_row_matcher, ma_tx_pnl_bookings) read by account/coa.py:build_inputs. The code
-- degrades adaptively when this migration has not run (missing columns ⇒ the defaults below).
--
-- 💰 MONEY POSTURE — ⚠️ P&L PRESENTATION CHANGES ON DEPLOY for orgs left at default. That is the
-- DELIBERATE owner ask (2026-09-01), not an accident: net income is IDENTICAL (the same dollars move
-- between revenue lines), but any surface quoting "ATU income" for an MA/VidaPay tenant will drop by
-- the merchant-discount amount, which reappears on the new "Merchant discount" line, and rows whose
-- order_type is 'Postpaid Residual Order' but whose product_name lacks 'residual' newly book their
-- −retail_cost under "MI residual income". Boost tenants (raw_mi present for the period) never reach
-- the MA fallback and are byte-identical. No payout, ledger, or ingested row is touched — this is
-- statement presentation only. merchant_invoice (an invoice NUMBER stored as NUMERIC — see
-- residual_subs._MA_IDENTIFIER_COLUMNS) is never read as money: the only MA TX money columns the
-- P&L reads are merchant_discount and retail_cost, checked by assert_money_columns.
--
-- REVERT:
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS pl_merchant_discount_own_line;
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS pl_ma_residual_order_types;
--   (The backend then falls back to the same defaults these columns carry, so a revert changes the
--    statement only for orgs that had explicitly set pl_merchant_discount_own_line = false or a
--    custom order-type list.)

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS pl_merchant_discount_own_line BOOLEAN NOT NULL DEFAULT true;

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS pl_ma_residual_order_types TEXT[] NOT NULL DEFAULT ARRAY['Postpaid Residual Order'];

COMMENT ON COLUMN commcalc.commission_org_config.pl_merchant_discount_own_line IS
  'P&L presentation (mig 309, owner spec 2026-09-01 Phase B): TRUE (default) books '
  'raw_ma_daily_tx.merchant_discount to its own "Merchant discount" revenue line '
  '(coa.PL_SPEC ma_merchant_discount, same sign/section the sum carried under ATU income); FALSE '
  'restores the legacy fold into "ATU income" byte-identically. Trade-off: the default IS the '
  'owner''s requested presentation, so an org that wants the legacy statement must opt OUT '
  'explicitly — chosen because a default of false would have required every MA tenant to flip a '
  'switch to get the spec''d books. Net income is identical either way; only the line split moves. '
  'Read adaptively by account/residual_subs.load_ma_pnl_config (missing column/row = TRUE).';

COMMENT ON COLUMN commcalc.commission_org_config.pl_ma_residual_order_types IS
  'P&L residual widening (mig 309, owner spec 2026-09-01 Phase B): raw_ma_daily_tx.order_type values '
  '(case-insensitive, trimmed) whose rows book as Residual (−retail_cost onto coa mi_income, "MI '
  'residual income") IN ADDITION to the product_name ''%residual%'' label family of '
  'residual_subs._MA_RESIDUAL_LABEL_MATCH. UNION with dedup: a row matching both criteria books '
  'exactly once. Default ARRAY[''Postpaid Residual Order''] (VidaPay''s wording on the owner''s '
  'sample rows). Trade-offs: (a) a list, not a scalar, because a feed can carry several residual '
  'order types; (b) an EMPTY array is legal and means "label family only" — the pre-309 filter; '
  '(c) this widens what counts as residual, so an order-type row whose product_name lacks '
  '''residual'' moves onto the books the first compute after deploy (deliberate owner ask). '
  'Config, not code (RULE TWO): another processor''s wording needs no deploy.';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 309 complete — MA TX P&L booking (Phase B): pl_merchant_discount_own_line (default true) + pl_ma_residual_order_types (default {Postpaid Residual Order}) on commcalc.commission_org_config' AS status;
