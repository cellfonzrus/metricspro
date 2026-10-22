-- payables_carrier_terms_2026-10-22.sql — CONFIG ROWS, for owner approval. NOT APPLIED.
--
-- Owner report 2026-10-22: "under Luxelink it is using Boost - it should not , also it should not say
-- sell before 60 days as that is boost requirement , total is sell before 12 days".
--
-- Migration 1091 added the columns. This runbook DECLARES the values. It touches only
-- commcalc.payable_source_map config rows: it moves no money, rebuilds nothing, and every statement
-- is a single UPDATE on one row. Run it as ONE Run in the Supabase SQL Editor (the Editor ends each
-- Run's transaction, so a BEGIN here and a COMMIT in a later Run would silently discard the work).
--
-- AFTER RUNNING: press "Rebuild ledger" on /commcalc/payables once. The due dates and prices below
-- are written into commcalc.device_payable_ledger by that rebuild, not by this file.
--
-- ── WHAT EACH VALUE IS, AND WHY IT IS NOT THE OBVIOUS ONE ─────────────────────────────────────────
--
-- sell_within_days — the CARRIER'S SELLING DEADLINE. Boost 60 (what the platform has always shown),
--   Total 12 (the owner's number). Declaring Boost's 60 explicitly changes nothing: the house default
--   is already 60 and 0.75 x 60 = 45, so its ladder stays < 45 / 45-60 / > 60 to the day. Total's 12
--   yields < 9 / 9-12 / > 12 instead of inheriting Boost's bands.
--
-- net_terms_days — DELIBERATELY LEFT UNSET FOR TOTAL, and this is the part worth reading. It is
--   tempting to set it to 12 so that every Total device gets a due date. That would be an invented
--   number. MEASURED 2026-10-22 on Luxelink: the processor feed's own due dates run tx_date + 1 day
--   (60,368 rows), +2 (11,576) and +3 (12,924) — they are the ACTIVATION charge's terms, not a
--   handset invoice's, and nothing in any feed states a payment deadline for the handset itself. The
--   12 days is a SELLING deadline, so it is declared as one and the out-of-pocket verdict falls back
--   to the window, which is what `out_of_pocket.basis = 'window'` says on every row. If the owner
--   confirms the real payment terms, set them here and the verdict switches to `basis = 'due_date'`
--   with no code change.
--
-- owed_mode = 'unit_price_map' — the device COST comes from the handset ordering sheet, per MODEL,
--   because that sheet carries no IMEI. MEASURED the same day, which is why it is not done the two
--   easier ways:
--     * the existing `owed_link` (mig 620) joins a related report by a shared KEY. `activation_order`
--       appears as a fulfillment `order_number` in 0 of 4,322 rows. No key, no join.
--     * `activation_order` DOES appear in the processor feed's `order_number` (3,535 of 4,322), and
--       that feed has a price column — but its `retail_cost` equals the handset's fulfillment price
--       in only 45 of 2,753 comparable rows (fulfillment $529.00 vs feed $195.00 on the same iPhone
--       13). It is an activation charge, not the device cost. Pricing devices from it would have
--       understated the payable by roughly two thirds while looking perfectly plausible.
--   The model-name join binds 3,370 of 4,322 (78%); of the 952 that do not, 903 carry the
--   distributor's own "Product Not Available" sku and stay NULL, reported as unpriced rather than $0.
--
-- unsold_source = 'payable_ledger' — Luxelink has 0 commcalc.asset_ledger rows (that is the Boost
--   consignment snapshot) and 4,288 rows in the carrier-agnostic per-IMEI ledger. Inventory Aging
--   reads whichever this names. Boost's row is left on the default ('source_table'), so its report
--   keeps reading asset_ledger exactly as it does today.
--
-- REVERT: set every column named below back to NULL on the same two rows. The house defaults then
--   apply and all three screens behave exactly as they did before this runbook.

-- ── 1. Luxelink Wireless LLC — Total / MA ─────────────────────────────────────────────────────────
UPDATE commcalc.payable_source_map
   SET sell_within_days       = 12,          -- owner 2026-10-22: "total is sell before 12 days"
       warn_fraction          = NULL,        -- house 0.75 -> warn at day 9
       net_terms_days         = NULL,        -- see the note above: NOT 12, and not guessed
       owed_mode              = 'unit_price_map',
       owed_price_table       = 'raw_ma_fulfillment',
       owed_price_model_field = 'sku',       -- the field on the SOURCE row that carries the model
       unsold_source          = 'payable_ledger'
 WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
   AND label  = 'Total / MA';

-- ── 2. Cellfonz R Us — Total / MA (same arrangement, same carrier) ───────────────────────────────
-- This org has NO raw_ma_fulfillment rows today (measured 2026-10-22), so the price map resolves
-- empty and its devices stay unpriced exactly as they are now. Declaring the window is still right:
-- the moment a handset ordering sheet is uploaded for this org, the terms are already correct.
UPDATE commcalc.payable_source_map
   SET sell_within_days       = 12,
       owed_mode              = 'unit_price_map',
       owed_price_table       = 'raw_ma_fulfillment',
       owed_price_model_field = 'sku',
       unsold_source          = 'payable_ledger'
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND label  = 'Total / MA';

-- ── 3. Cellfonz R Us — Boost / VIP: the window stated, nothing else changed ───────────────────────
-- 60 is already the house default, so this is a no-op the platform can now SHOW as declared rather
-- than assumed (`aging_policy.by_carrier[].declared = true`). unsold_source and owed_mode are left
-- alone on purpose: this carrier's own snapshot and its own owed_to_vip column keep answering.
UPDATE commcalc.payable_source_map
   SET sell_within_days = 60
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND label  = 'Boost / VIP';

-- ── 4. Read back what was declared, so the result is visible in the same Run ─────────────────────
SELECT org_id, label, source_table, sell_within_days, warn_fraction, net_terms_days,
       owed_mode, owed_price_table, owed_price_model_field, unsold_source
  FROM commcalc.payable_source_map
 ORDER BY org_id, label;
