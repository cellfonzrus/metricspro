-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- RUNBOOK (owner-approved statement, NOT a migration — the column half is migration 1067, which
-- this file repeats so the whole change is one paste). Versioned here rather than left in a share,
-- because an owner statement that only exists in chat cannot be audited later.
--
-- MOVE DEVICE COST TO THE STORE THE CARRIER PAID — migration 1067 + the declaration
-- Owner decision 2026-10-10: "Move the cost".
--
-- RUN THIS AS ONE "Run" IN THE SUPABASE SQL EDITOR. Do not split it across runs: the Editor ends
-- each Run's transaction, so a BEGIN in one Run and a COMMIT in another silently discards the work.
-- Paste the whole file, press Run once. It commits itself.
--
-- WHAT IT DOES, IN ORDER
--   1. Adds the nullable column (migration 1067). Inert on its own: NULL = today's behaviour.
--   2. PREVIEWS nothing destructive — it prints what is currently declared, before and after.
--   3. DECLARES the activating basis for the HOUSE org only
--      (00000000-0000-0000-0000-000000000001). LuxeLink and Vzone are untouched and stay on the
--      distributor ledger's store.
--
-- WHAT CHANGES ON THE P&L, measured read-only 2026-10-10 (Jun-Oct 2026, house org): 468 devices
-- and $195,155.32 of device cost move from the store that STOCKED each phone to the store the
-- carrier PAID for it, across 100 store pairs. It NETS TO $0.00 company-wide — consolidated COGS
-- and consolidated gross profit do not move at all. Each STORE's gross profit restates, which is
-- the point: a store is no longer charged for a phone it did not sell.
--
-- AFTER RUNNING: press Recompute (or POST /account/compute/{period}) for each month you want
-- restated — June, July, August, September and October 2026. Until a month is recomputed its
-- stored P&L keeps the old per-store figures. Nothing moves on any month you do not recompute.
--
-- TO UNDO: UPDATE commcalc.account_config SET device_cost_store_basis = NULL
--            WHERE org_id = '00000000-0000-0000-0000-000000000001';
--          then recompute the same months. That restores the pre-1067 placement exactly.
-- ═══════════════════════════════════════════════════════════════════════════════════════════════

-- 1. THE COLUMN (migration 1067 — idempotent, additive, inert)
ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS device_cost_store_basis TEXT
    CHECK (device_cost_store_basis IS NULL
           OR device_cost_store_basis IN ('stocking', 'activating'));

COMMENT ON COLUMN commcalc.account_config.device_cost_store_basis IS
  'mig 1067 — WHICH STORE carries a handset''s cost for this org. ''stocking'' = the distributor '
  'ledger''s own store (pre-1067 behaviour). ''activating'' = the store the carrier paid, for the '
  'devices the per-device reimbursement reconciliation (index SS65) PROVED were activated at another '
  'store; everything it could not measure stays on the ledger''s store and is reported. NULL = NOT '
  'DECLARED, which resolves to ''stocking'' byte-identically. The company total never moves (a '
  'dollar leaves one store and lands on another); each store''s gross profit restates, so a '
  'recompute follows a declaration. Resolved in exactly one place: '
  'backend/app/modules/account/device_cogs.resolve_device_cost_store_basis, and applied in exactly '
  'one place: device_cogs.cost_store. Never branch on this in code anywhere else (RULE TWO; '
  'harness_device_inventory_cogs.py section H fails the build).';

-- 2. BEFORE — what every org is declared as right now
SELECT 'BEFORE' AS stage, org_id, device_cost_basis, device_cost_store_basis
  FROM commcalc.account_config
 ORDER BY org_id;

-- 3. THE DECLARATION — house org only
UPDATE commcalc.account_config
   SET device_cost_store_basis = 'activating'
 WHERE org_id = '00000000-0000-0000-0000-000000000001';

-- 4. AFTER — confirm exactly one row changed and the other tenants are untouched
SELECT 'AFTER' AS stage, org_id, device_cost_basis, device_cost_store_basis
  FROM commcalc.account_config
 ORDER BY org_id;
