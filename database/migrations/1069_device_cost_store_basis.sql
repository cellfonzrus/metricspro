-- 1069_device_cost_store_basis.sql — WHICH STORE CARRIES A HANDSET'S COST (owner 2026-10-10, §69)
--
-- Owner, on the decision card: "Move the cost" — move device cost to the store the carrier paid,
-- for the devices the per-device reconciliation PROVED were activated elsewhere.
--
-- THE CLASS, NAMED BEFORE THE FIX
-- -------------------------------
-- "Which store does this handset's cost belong to?" had TWO authorities and nothing reconciled them
-- per device. The distributor ledger (`commcalc.asset_ledger`) books the unit's cost to the STOCKING
-- store, on the row's own `store`. The carrier statement pays the ACTIVATING store, on its per-line
-- feed (`commcalc.raw_payment_detail`). A phone stocked at one door and sold at another therefore
-- charged one store a cost whose reimbursement a DIFFERENT store received — and that is what the
-- owner was looking at when he reported store 652's equipment rebate running ~$5,000 below its
-- equipment reimbursement (15 of its 47 September devices, $5,004.98, paid to four other stores).
--
-- MEASURED LIVE (house org 00000000-0000-0000-0000-000000000001, read-only, 2026-10-10)
-- ------------------------------------------------------------------------------------
--   month      devices on the ledger   proven activated elsewhere   cost that would move
--   2026-06                    1,233                           86             40,039.14
--   2026-07                    1,474                          118             47,148.82
--   2026-08                    1,222                          117             46,968.83
--   2026-09                    1,029                          135             56,958.65
--   2026-10 (partial)            195                           12              4,039.88
--                                                             ---            ----------
--                                                             468            195,155.32
--
-- Across 100 store pairs, and it NETS TO $0.00 company-wide: every dollar leaves one store and
-- lands on another. Consolidated COGS and consolidated gross profit do not move at all. What moves
-- is each STORE's gross profit, which is the point — a store is no longer charged for a phone it
-- did not sell.
--
-- ONE FACT, ONE HOME, DEREFERENCED
-- --------------------------------
-- "Where was this device activated" is NOT answered by the cost side. It is the per-device verdict
-- of `commcalc/device_reimb_recon.device_lines` (index §65) — the same reconciliation the owner
-- reads on the Equipment Reimbursement by Device report — read through its own
-- `activation_store_moves`, and turned into a store in exactly one place,
-- `backend/app/modules/account/device_cogs.cost_store`. A second opinion on the cost side would let
-- the report and the books disagree about where one phone was sold, which is the duplicate defect
-- CLAUDE.md forbids. `harness_device_inventory_cogs.py` section H fails the build if any caller
-- places a cost without that home, if a second reader of the verdict appears, or if the switch stops
-- defaulting to today's behaviour.
--
-- PROVEN ONLY, AND IT FAILS IN THE SAFE DIRECTION. A cost moves only on LINE_PAID_OTHER_STORE:
-- money that ARRIVED, at a named store, for an identified device. Every row the report could not
-- measure — no device identifier on the claim, a month whose carrier feed arrived short, two
-- addresses that may be one physical store — moves NOTHING, stays on the distributor ledger's store,
-- and is REPORTED on the line's meta (`unmeasured_stayed_put`). An unreadable reconciliation moves
-- nothing either. A cost is never left unplaced and never moved on a verdict nobody proved.
--
-- VALUES
--   device_cost_store_basis  'stocking'    the distributor ledger's own store. TODAY'S BEHAVIOUR.
--                            'activating'  the store the carrier paid, for the proven devices.
--                            NULL          NOT DECLARED (the default this migration installs for
--                                          every org): resolves to 'stocking', which is
--                                          byte-identical to every figure produced before §69.
--
-- INERT BY CONSTRUCTION. This migration adds a nullable column and NOTHING else. It books no money,
-- restates no period and sets no org's basis. Declaring 'activating' is a SEPARATE, owner-approved
-- statement (the preview and the UPDATE are in the PR body), and every period computed after it
-- RESTATES per store, so a recompute follows.
--
-- MONEY-TOUCHING (the column is inert; the declaration is not). Surfaced for owner approval, never
-- applied by an agent.
--
-- Idempotent · additive · numbered.
-- REVERT:
--   ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS device_cost_store_basis;
--   -- Dropping the column returns every org to the pre-1069 placement (the distributor ledger's
--   -- store), because `device_cogs.load_device_cost_store_basis` reads the column defensively and
--   -- treats its absence as "not declared". No other table, row or line is touched here.

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS device_cost_store_basis TEXT
    CHECK (device_cost_store_basis IS NULL
           OR device_cost_store_basis IN ('stocking', 'activating'));

COMMENT ON COLUMN commcalc.account_config.device_cost_store_basis IS
  'mig 1069 — WHICH STORE carries a handset''s cost for this org. ''stocking'' = the distributor '
  'ledger''s own store (pre-1069 behaviour). ''activating'' = the store the carrier paid, for the '
  'devices the per-device reimbursement reconciliation (index SS65) PROVED were activated at another '
  'store; everything it could not measure stays on the ledger''s store and is reported. NULL = NOT '
  'DECLARED, which resolves to ''stocking'' byte-identically. The company total never moves (a '
  'dollar leaves one store and lands on another); each store''s gross profit restates, so a '
  'recompute follows a declaration. Resolved in exactly one place: '
  'backend/app/modules/account/device_cogs.resolve_device_cost_store_basis, and applied in exactly '
  'one place: device_cogs.cost_store. Never branch on this in code anywhere else (RULE TWO; '
  'harness_device_inventory_cogs.py section H fails the build).';
