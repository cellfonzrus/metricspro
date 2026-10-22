-- 1091_device_aging_policy.sql
-- OWNER REPORT 2026-10-22: "under Luxelink it is using Boost - it should not , also it should not say
-- sell before 60 days as that is boost requirement , total is sell before 12 days" + "the due date in
-- case of boost is as per the asset landing and total is the handset ordering report".
--
-- Three carrier TERMS were literals in the code, shown to every tenant including the ones that never
-- signed them: the selling window (`< 45 / 45-60 / > 60`, written out twice — asset/router.get_aging
-- and commcalc/device_history.AGING_BUCKETS), the net payment terms (reachable ONLY by linking a
-- distributor row, which is why Luxelink's Total map computed no due date at all), and where a
-- device's COST comes from (hard-wired to an `owed_field` on the source table, which the Total
-- activation feed does not have — its prices are in the handset ordering report).
--
-- All three become DECLARED columns on the existing per-carrier config spine
-- (commcalc.payable_source_map, mig 095). RULE TWO: no carrier name or number in code; behaviour is a
-- per-org config row with house defaults. NO new table, NO second registry — the duplicate check for
-- this change is in docs/SYSTEM_DATA_FLOW_INDEX.md §COMMCALC-26.
--
-- Additive + idempotent. Reads nothing, backfills nothing, moves no money: every column is NULL after
-- this runs, and NULL means "the house default answered" — which is byte-identical to today's
-- behaviour (sell_within_days NULL -> 60, warn_fraction NULL -> 0.75 -> the 45/60 ladder exactly).
-- The Luxelink/Total values are a SEPARATE, owner-approved data step
-- (database/runbooks/payables_carrier_terms_2026-10-22.sql).
--
-- REVERT: ALTER TABLE commcalc.payable_source_map
--           DROP COLUMN IF EXISTS sell_within_days,
--           DROP COLUMN IF EXISTS warn_fraction,
--           DROP COLUMN IF EXISTS net_terms_days,
--           DROP COLUMN IF EXISTS owed_mode,
--           DROP COLUMN IF EXISTS owed_price_table,
--           DROP COLUMN IF EXISTS owed_price_model_field,
--           DROP COLUMN IF EXISTS unsold_source;
--         Dropping them restores the house defaults, so the ladder returns to 45/60 everywhere and
--         the Total side goes back to carrying no price and no due date.

-- ── 1. The carrier's SELLING window, and the warn edge derived from it ─────────────────────────────
ALTER TABLE commcalc.payable_source_map
  -- "sell it within N days or the carrier bills you" — Boost 60, Total 12 (owner 2026-10-22).
  -- NULL = the house default (60), so an unfilled row behaves exactly as it does today.
  ADD COLUMN IF NOT EXISTS sell_within_days       INT,
  -- How far through that window the "aging" warning starts, as a FRACTION rather than a second
  -- number, so a window change cannot leave a stale edge behind. NULL = 0.75, which reproduces the
  -- historical 45-of-60 edge to the day.
  ADD COLUMN IF NOT EXISTS warn_fraction          NUMERIC,
  -- ── 2. The carrier's PAYING terms, declared where the map already lives ───────────────────────
  -- A separate fact from the selling window: on Boost they are not the same number. Until now the
  -- only route to net terms was payable_source_map.distributor_id -> distributors.terms_days, and
  -- Luxelink's Total map has no distributor_id, so `due_date_mode='net_terms'` produced NULL on all
  -- 4,288 of its ledger rows (measured 2026-10-22). This column is read FIRST, with the distributor
  -- link kept as the fallback so nothing that works today changes.
  ADD COLUMN IF NOT EXISTS net_terms_days         INT,
  -- ── 3. WHERE A DEVICE'S COST COMES FROM ───────────────────────────────────────────────────────
  -- 'field'          — a price column on the source row itself (Boost: asset_ledger.owed_to_vip).
  --                    The default, and what every existing row does.
  -- 'unit_price_map' — the distributor's HANDSET ORDERING sheet, priced per MODEL and joined on the
  --                    model name, because that sheet carries no IMEI (owner 2026-10-22: "total is
  --                    the handset ordering report"). Resolved through the platform's existing one
  --                    home for that price list (account/device_cogs.ma_unit_price_from_rows), which
  --                    the P&L already costs Total handsets with — never a second price derivation.
  -- 'none'           — no cost source; `owed` stays NULL and the surface says "unpriced", which is
  --                    already how the payables screen reads it.
  ADD COLUMN IF NOT EXISTS owed_mode              TEXT,
  ADD COLUMN IF NOT EXISTS owed_price_table       TEXT,
  ADD COLUMN IF NOT EXISTS owed_price_model_field TEXT,
  -- ── 4. WHICH FEED ANSWERS "WHAT IS STILL UNSOLD" ──────────────────────────────────────────────
  -- 'source_table' — the carrier's own on-inventory snapshot (Boost: asset_ledger's unsold
  --                  On-Inventory rows). The default.
  -- 'payable_ledger' — commcalc.device_payable_ledger, the carrier-agnostic per-IMEI materialization
  --                  this module already builds, used where the carrier has no on-inventory snapshot
  --                  of its own (every Total/MA tenant: 0 asset_ledger rows, 4,288 ledger rows).
  --                  Deliberately NOT a third source — the ledger already exists and already carries
  --                  IMEI, store, model, invoice date, due date and sold flag.
  ADD COLUMN IF NOT EXISTS unsold_source          TEXT;

COMMENT ON COLUMN commcalc.payable_source_map.sell_within_days IS
  'Carrier selling window in days (Boost 60, Total 12). NULL = house default 60. Owner 2026-10-22.';
COMMENT ON COLUMN commcalc.payable_source_map.warn_fraction IS
  'Fraction of the window at which "aging" starts. NULL = 0.75 (reproduces the historical 45/60).';
COMMENT ON COLUMN commcalc.payable_source_map.net_terms_days IS
  'Net payment terms in days, read before distributors.terms_days. NULL = fall back to the link.';
COMMENT ON COLUMN commcalc.payable_source_map.owed_mode IS
  'field | unit_price_map | none. Where a device cost comes from. NULL = field (today''s behaviour).';
COMMENT ON COLUMN commcalc.payable_source_map.unsold_source IS
  'source_table | payable_ledger. Which feed answers "still unsold". NULL = source_table.';

-- A migration grants what it creates. This one creates no relation — it adds columns to a table
-- commcalc.payable_source_map that mig 095 already granted — so the grant below is a RE-STATEMENT for
-- the guard and for anyone reading this file alone, never a widening (service_role only; the browser
-- never reads this table).
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.payable_source_map TO service_role;
