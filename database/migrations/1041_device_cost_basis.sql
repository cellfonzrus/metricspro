-- 1041_device_cost_basis.sql — THE ONE DEVICE-COST BASIS (owner report 2026-10-03, index §42.5)
--
-- Owner, verbatim: "these are not the right numbers, these numbers come from teh asset landing -
-- first check how other stores are getting thier numbers and then fix platform wide - no bandaid"
--
-- WHAT WAS WRONG, MEASURED (house org 00000000-0000-0000-0000-000000000001, 2026-10-03)
-- ------------------------------------------------------------------------------------
-- "How are other stores getting their numbers" has one answer: IDENTICALLY, and identically wrong.
-- `account_config.device_cogs_mode` is 'off' for the house org, so `coa.build_inputs` books
-- `device_cost` from the POINT OF SALE as `ext − gp` for every one of the 28 stores. There is no
-- per-store variation to chase — it is one org-wide switch, which is why the per-store figures read
-- ~$1k–$6k/store/month instead of the distributor's per-unit charge.
--
-- The distributor's charge IS already in the platform, on the asset-landing ledger
-- (`commcalc.asset_ledger`), and `account/device_cogs.resolve(..., 'auto')` already recognises it
-- correctly: run against production it reports source `asset_ledger (consignment, billed=COGS)`,
-- 28 stores attributed, company_wide $0.00, zero dedup drops. Nothing needed building to source it.
--
-- WHY THIS IS A COLUMN AND NOT A ONE-ROW FLIP OF device_cogs_mode
-- ---------------------------------------------------------------
-- Switching `device_cogs_mode` alone would DOUBLE-EXPENSE every handset. `coa` books
-- `vip_device_pay` ("Distributor device payments (PayGo, paid)") from `vip_paygo_payments` approved
-- batches UNCONDITIONALLY, with no gate on the device basis at all. Measured, house consolidated:
--
--   period    POS device_cost   vip_device_pay     TOTAL now    asset landing     net change
--   2026-05        87,936.50       533,505.50      621,442.00      433,927.88    -187,514.12
--   2026-06        38,706.04       430,541.91      469,247.95      486,656.87     +17,408.92
--   2026-07        68,625.70       647,777.49      716,403.19      521,815.94    -194,587.25
--   2026-08        75,172.69       398,355.01      473,527.70      424,668.36     -48,859.34
--   2026-09        80,413.50       362,067.01      442,480.51      274,482.69    -167,997.82
--
-- So the change is NOT "+$350k of COGS": it moves device money OFF a company-wide CASH line and ONTO
-- per-store ACCRUAL cells, and consolidated COGS mostly FALLS. The cash is not lost — the liability
-- it settles is already carried on `owed_vip` from the same ledger, so cash paid against a booked
-- payable is a settlement, not an expense.
--
-- ONE DECLARATION, THREE INSEPARABLE CONSEQUENCES. The basis is resolved in ONE home
-- (`backend/app/modules/account/device_cogs.resolve_device_cost_basis`) which returns all three
-- together, so no org can be left half-switched:
--   accrual COGS from the landing   ·   the PayGo cash line LEAVES COGS   ·   the BS inventory line
--   comes from the SAME ledger (as-of unsold), overriding `inventory_basis`
-- `harness_device_inventory_cogs.py` §G fails the build if a caller stops dereferencing that home,
-- if a second copy of the decision appears, or if the cash line can be booked alongside the accrual
-- line. RULE TWO: no tenant or carrier name anywhere — this is a per-org config row.
--
-- VALUES
--   device_cost_basis   'pos'          sale-time point-of-sale cost (`ext − gp`). Today's behaviour.
--                       'asset_ledger' the ACCRUAL basis: the distributor's per-unit charge off the
--                                      asset landing, recognised in the month the unit SOLD, with
--                                      the unsold units carried as a BS asset from the same ledger.
--                       NULL           NOT DECLARED (the default this migration installs for every
--                                      org): resolves to the carrier preset, and failing that to the
--                                      legacy `device_cogs_mode` behaviour BYTE FOR BYTE. Nothing on
--                                      any tenant's books moves until a basis is declared.
--
-- INERT BY CONSTRUCTION. This migration adds a nullable column and NOTHING else. It books no money,
-- restates no period and sets no org's basis. Declaring a basis is a SEPARATE, owner-approved
-- statement (the preview + UPDATE are in the PR body), and every period computed after it RESTATES.
--
-- MONEY-TOUCHING (the column is inert; the declaration is not). Surfaced for owner approval, never
-- applied by an agent.
--
-- Idempotent · additive · numbered.
-- REVERT:
--   ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS device_cost_basis;
--   -- Dropping the column returns every org to the pre-1041 resolution (undeclared ⇒ legacy
--   -- device_cogs_mode), because `device_cogs.load_basis` reads the column defensively and treats
--   -- its absence as "not declared". No other table, row or line is touched by this migration.

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS device_cost_basis TEXT
    CHECK (device_cost_basis IS NULL
           OR device_cost_basis IN ('pos', 'asset_ledger'));

COMMENT ON COLUMN commcalc.account_config.device_cost_basis IS
  'mig 1041 — THE ONE device-cost basis for this org. ''pos'' = sale-time point-of-sale cost '
  '(ext - gp). ''asset_ledger'' = the distributor''s per-unit charge off the asset-landing ledger, '
  'recognised in the month the unit SOLD, which ALSO suppresses the vip_device_pay cash line from '
  'COGS and forces the BS inventory line to the same ledger''s as-of unsold value. NULL = NOT '
  'DECLARED: resolve from the carrier preset, then from the legacy device_cogs_mode (pre-1041 '
  'behaviour, byte-identical). Resolved in exactly one place: '
  'backend/app/modules/account/device_cogs.resolve_device_cost_basis. Never branch on this in code '
  'anywhere else (RULE TWO; harness_device_inventory_cogs.py section G fails the build).';
