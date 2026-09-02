-- 314_pl_ma_store_attribution.sql
-- mod-commission · band 200–299 spill → 314 (follows 313). Additive + idempotent + safe to re-run.
--
-- WHAT IT IS FOR (owner spec 2026-09-02, verbatim): "it shows company wide vida commission, it
-- should show store wise commission for all M1 thru M12, also it should say Residual on Total side
-- and Mi on boost side, there is no numbers for the residual in the p&l on the luxelink side, mdf
-- should capture the market spiff of $1000/$500 per store if it is part of any of the commission
-- report on the total side, rebates and phone cost are not being captured per store, none of these
-- are hard coded, they should be as a part of the design".
--
-- ROOT CAUSE (measured, luxelink August 2026): every MA/VidaPay dollar in the P&L was booked
-- COMPANY-WIDE, and company-wide money shows ONLY in the Consolidated scope — so the
-- company:"Luxlink Wireless" and every per-store P&L read $0.00 for residual ($28,370.84),
-- merchant discount ($14,421.56), spiffs ($7,521.85) and rebates (−$71,512.83). The account→store
-- map the code said didn't exist DOES exist in the dealer's own data: raw_ma_fulfillment carries
-- both tspid (processor account) and business_address on every row — 19 tspids, zero ambiguous,
-- covering 13/13 raw_ma_daily_tx accounts and 17/18 raw_ma_commission accounts (170405 has no
-- fulfillment row and stays company-wide until pinned in the override table below).
--
-- WHAT THIS ADDS (RULE TWO — per-org config with house defaults; NOTHING is keyed on a tenant or
-- carrier name in code). Resolution: app/modules/account/ma_store_pnl.py (load_config,
-- account_store_index, ma_commission_bookings, ma_tx_bookings, apply_line_labels) read by
-- account/coa.py:build_inputs + account/device_cogs.py. The code degrades adaptively when this
-- migration has not run (missing columns/table ⇒ the defaults below ⇒ books byte-identical to
-- pre-314 for EVERY org).
--
--   commcalc.commission_org_config, five new columns:
--   • pl_ma_store_attribution   (bool, default false) — master switch: attribute MA money
--     (residual, merchant discount, spiffs, MDF, rebates, device margin/fees/financing, MA device
--     COGS) to STORES via the account→store index. false ⇒ company-wide, exactly as today.
--   • pl_ma_month_spiff_source  (text, default 'commission_sheet') — where the P&L books the
--     multi-month commission. 'commission_sheet' = today's behaviour (raw_ma_commission
--     spiff_m1..m6 columns, credited at the ACTIVATION month; the monthly re-pull back-fills
--     later months into PAST periods). 'daily_tx' = CASH basis: book the raw_ma_daily_tx
--     month-spiff rows in the month PAID (M1..M12+ parsed from product_name by THE shared
--     commission_ledger.parse_payment_month regex) and STOP booking the sheet's spiff columns so
--     one payment can never book at both the activation month and the cash month.
--   • pl_ma_spiff_order_types   (jsonb list, default ["PostPaid Additional Spiff"]) — the daily-tx
--     order_type families that ARE month spiffs (read only when source = 'daily_tx').
--   • pl_mdf_product_tokens     (jsonb list, default []) — product_name tokens whose rows book to
--     the new `mdf_income` "MDF (market spiffs)" P&L line, per store. Empty ⇒ line never
--     materialises (auto_opt).
--   • pl_line_labels            (jsonb map, default {}) — per-org DISPLAY label per P&L/BS line
--     key, e.g. {"mi_income": "Residual"}. Unknown keys ignored; empty ⇒ spec labels.
--
--   commcalc.ma_account_store_map (new table) — owner-pinned processor-account → store overrides.
--   The fulfillment-derived map is automatic; this table wins over it and covers accounts the
--   fulfillment sheet never names (luxelink: 170405).
--
-- 💰 MONEY POSTURE — the DDL alone changes NOTHING for any org (all defaults reproduce today's
-- books byte-identically; proof: backend/harness_ma_store_pnl.py). The LUXELINK SEED at the bottom
-- is the owner-approved opt-in and it MOVES STATEMENT PRESENTATION for that org (measured on
-- August 2026, next /account/compute after running this):
--   • Residual $28,370.84, Merchant discount $14,421.56, MDF $12,000.00, month spiffs, rebates
--     −$71,512.83, MA device margin $1,790.00 move from company-wide to PER-STORE (13–18 stores;
--     account 170405's $6,816.87 of rebates stays company-wide until mapped below).
--   • carrier_comm changes VALUE: −$7,521.85 (Aug sheet spiffs, activation-month accrual) and
--     +$66,574.44 (Aug daily-tx month-spiff cash rows: M1 $1,428.88 incl. 'M1 Proration' rows,
--     M2 $13,460.69, M3 $13,380.44, M4 $13,959.74, M5 $11,329.69, M6 $12,990.00, 'Spiff (other)'
--     $25.00 — luxelink is paid through M6 today; M7–M12+ book automatically when the carrier pays
--     them, nothing hardcoded). Net carrier_comm +$59,052.59; consolidated net income +$71,052.59
--     with MDF. NO payout, ledger, or ingested row is touched — statement presentation only.
--   • 'Retroactive Postpaid Spiff' rows ($2,349.47 in Aug) are NOT in the seed list — add the
--     order_type to pl_ma_spiff_order_types if the owner wants them booked as commission.
--   • pl_line_labels renames the "MI residual income" LINE to "Residual" for luxelink only; Boost
--     orgs keep their label (set {"mi_income": "MI"} on a Boost org's row if the owner wants the
--     short form there — config, never code).
--
-- REVERT:
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS pl_ma_store_attribution;
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS pl_ma_month_spiff_source;
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS pl_ma_spiff_order_types;
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS pl_mdf_product_tokens;
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS pl_line_labels;
--   DROP TABLE IF EXISTS commcalc.ma_account_store_map;
--   (The backend then falls back to the same defaults these columns carry — company-wide grain,
--    commission-sheet spiffs, no MDF line, spec labels — for every org.)

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS pl_ma_store_attribution BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS pl_ma_month_spiff_source TEXT NOT NULL DEFAULT 'commission_sheet'
  CHECK (pl_ma_month_spiff_source IN ('commission_sheet', 'daily_tx'));

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS pl_ma_spiff_order_types JSONB NOT NULL
  DEFAULT '["PostPaid Additional Spiff"]'::jsonb;

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS pl_mdf_product_tokens JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS pl_line_labels JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN commcalc.commission_org_config.pl_ma_store_attribution IS
  'Mig 314. Attribute MA/VidaPay P&L money to stores via the processor-account -> store index '
  '(raw_ma_fulfillment tspid map, overridden by ma_account_store_map). false = company-wide (pre-314).';
COMMENT ON COLUMN commcalc.commission_org_config.pl_ma_month_spiff_source IS
  'Mig 314. commission_sheet = book raw_ma_commission.spiff_m1..m6 at activation month (pre-314). '
  'daily_tx = cash basis: book raw_ma_daily_tx month-spiff rows (pl_ma_spiff_order_types) in the '
  'month paid, M-number via commission_ledger.parse_payment_month; sheet spiff columns then do NOT book.';
COMMENT ON COLUMN commcalc.commission_org_config.pl_ma_spiff_order_types IS
  'Mig 314. raw_ma_daily_tx.order_type families booked as month spiffs when '
  'pl_ma_month_spiff_source = daily_tx.';
COMMENT ON COLUMN commcalc.commission_org_config.pl_mdf_product_tokens IS
  'Mig 314. Case-insensitive product_name tokens whose raw_ma_daily_tx rows book -retail_cost to '
  'the mdf_income P&L line (per store). Empty = no MDF line.';
COMMENT ON COLUMN commcalc.commission_org_config.pl_line_labels IS
  'Mig 314. Per-org display label per P&L/BS line key, e.g. {"mi_income": "Residual"}. Unknown '
  'keys ignored; applied by ma_store_pnl.apply_line_labels via coa.build_inputs.';

-- Owner-pinned processor-account -> store overrides (wins over the fulfillment-derived map).
CREATE TABLE IF NOT EXISTS commcalc.ma_account_store_map (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  account_id    TEXT NOT NULL,          -- processor account (raw_ma_daily_tx.account_id /
                                        -- raw_ma_commission.merchant_account_id / fulfillment tspid)
  store_address TEXT NOT NULL,          -- resolved through coa.store_resolver at read time
  note          TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, account_id)
);
COMMENT ON TABLE commcalc.ma_account_store_map IS
  'Mig 314. Per-org processor-account -> store overrides for MA P&L store attribution. The '
  'automatic map is derived from raw_ma_fulfillment (tspid, business_address); rows here win over '
  'it. An account in neither books company-wide (honest, never guessed).';

-- ── LUXELINK SEED (org 854f6d7b-6590-4e4d-88ab-646f560d4f4c) — the owner-approved opt-in this
-- migration exists for. Running this file applies it; the exact August-2026 dollar movements are
-- itemised in the MONEY POSTURE block above. Idempotent (plain UPDATE of an existing config row —
-- the org's row exists since mig 201-era config; if it ever didn't, the code defaults simply keep
-- applying and this UPDATE is a no-op).
UPDATE commcalc.commission_org_config SET
  pl_ma_store_attribution  = true,
  pl_ma_month_spiff_source = 'daily_tx',
  pl_ma_spiff_order_types  = '["PostPaid Additional Spiff"]'::jsonb,
  pl_mdf_product_tokens    = '["premium store spiff"]'::jsonb,
  pl_line_labels           = '{"mi_income": "Residual"}'::jsonb
WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';

-- ── OPTIONAL SEED (SEED DATA — NOT auto-applied; mig-224 pattern). Account 170405 appears on
-- luxelink's raw_ma_commission (Aug 2026: $6,816.87 of rebates) but never on the fulfillment
-- sheet, so the automatic map cannot name its store. Uncomment + set the address to pin it:
-- INSERT INTO commcalc.ma_account_store_map (org_id, account_id, store_address, note) VALUES
--   ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '170405', '<store address here>',
--    'owner-pinned: account not present on raw_ma_fulfillment')
-- ON CONFLICT (org_id, account_id) DO UPDATE SET store_address = EXCLUDED.store_address;

-- ── OPTIONAL SEED (SEED DATA — NOT auto-applied; needs its own owner GO per ruling K3). Device
-- ("phone") cost per store for luxelink requires invoice-first device COGS to be ON. Measured Aug
-- 2026 effect: books $69,966.68 of MA device cost (243 IMEIs priced off the fulfillment sheet;
-- 97 'Product Not Available' SIM/BYOD rows and 18 unpriced SKUs excluded and counted in meta) —
-- per store for the 17 mapped accounts, $6,766.80 company-wide (acct 170405). This is a LARGE
-- net-income movement, which is why it is not auto-applied here:
-- INSERT INTO commcalc.account_config (org_id, device_cogs_mode)
--   VALUES ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', 'invoice')
-- ON CONFLICT (org_id) DO UPDATE SET device_cogs_mode = 'invoice';

SELECT 'Migration 314 complete — MA P&L store attribution config installed '
       '(house defaults byte-identical; luxelink seeded per owner spec 2026-09-02)' AS status;
