-- 1033_pl_mi_store_attribution.sql
-- THE RESIDUAL'S STORE GRAIN on the raw_mi (Boost/ePay) feed.
--
-- Owner report 2026-10-01, verbatim:
--   "there is no dta for september residual for boost for individual stores since the begininig it
--    only shows the consilidated mi and atu residual, need to assign the residual at the store level
--    in the p&l and all reports"
--
-- WHAT WAS WRONG (diagnosed, not guessed — see the PR for the evidence):
--   The data was never missing. commcalc.raw_mi holds 46,047 HOUSE rows for 2026-09 and EVERY row
--   carries its dealer door in `salesforce_id` (0 blanks in a 60,000-row sample, 26 distinct doors).
--   `account/coa.build_inputs` selected only the two money columns and booked them with store=None,
--   so every residual dollar landed in the line's `company_wide` bucket. `engine._scoped` adds
--   `company_wide` for the CONSOLIDATED scope ONLY (engine.compute_and_store builds every other
--   scope with include_company_wide=False), so the consolidated P&L showed the residual and every
--   company / store / market / profit-center view read $0 — by construction, since the first
--   statement. The MA/VidaPay half of the SAME two P&L lines was given its store grain by mig 314;
--   this half was not.
--
-- WHAT THIS MIGRATION DOES: adds the per-org switch that turns the grain on. RULE TWO — no carrier,
-- tenant or store is named in code; the behaviour is this config row. The reader is
-- `account/ma_store_pnl.load_config` (ADAPTIVE: a DB without this column reads false and REPORTS the
-- column as missing), and the booking is `account/residual_subs.mi_pnl_bookings` (pure; proof
-- `backend/harness_mi_residual_store_grain.py`).
--
-- MONEY IMPACT, measured and bounded (this is why the file is surfaced and not applied):
--   • CONSOLIDATED P&L / Balance Sheet: UNCHANGED. `_scoped` sums by_store ∪ company_wide there, so
--     moving a dollar from company_wide into a store bucket cannot move that total. Pinned by
--     harness CHECK B (total preservation, to the cent, including the float-accumulation control).
--   • COMPANY / STORE / MARKET / PROFIT-CENTER views: these GAIN the residual they were reporting as
--     $0. They do not "change" a figure that was ever right — they stop understating. Net income and
--     gross profit rise for every scope that owns residual, which is the point of the request.
--   • A door the org's store_mapping cannot place, or a row with a blank salesforce_id, still books
--     COMPANY-WIDE. Nothing is dropped, nothing is guessed onto a plausible store.
--   • Statements are per-period SNAPSHOTS (commcalc.account_statements). Applying this changes no
--     stored row by itself; a period's snapshots only move when that period is RECOMPUTED. Closed
--     months therefore move only when someone recomputes them, deliberately.
--
-- Default TRUE: the grain is a FACT about the feed (the door is on every row), not a presentation
-- preference, and the correct grain is what every org should get. An org that wants the old
-- company-wide roll-up sets its own row to false — tenant row overrides, exactly like mig 314.
--
-- Idempotent, additive, no data rewrite. Coexists with migs 1013/1015 in any order (different
-- columns on the same table).

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS pl_mi_store_attribution boolean DEFAULT true;

-- Existing rows created before this column: give them the same answer a new row gets, so an org's
-- books do not depend on when its config row happened to be inserted.
UPDATE commcalc.commission_org_config
   SET pl_mi_store_attribution = true
 WHERE pl_mi_store_attribution IS NULL;

COMMENT ON COLUMN commcalc.commission_org_config.pl_mi_store_attribution IS
  'mig 1033 (owner 2026-10-01). TRUE: each raw_mi residual row books mi_income/atu_income to the '
  'store its salesforce_id resolves to (account.residual_subs.canonical_salesforce_store_index = '
  'commcalc.store_mapping.salesforce_id composed with coa.store_resolver); an unplaceable or blank '
  'door still books company-wide. FALSE: every residual dollar books company-wide (the pre-1033 '
  'grain). The CONSOLIDATED statement is identical either way; only the per-store / per-company / '
  'market-filtered views differ. Twin of pl_ma_store_attribution (mig 314) for the other residual '
  'feed shape. Read by account/ma_store_pnl.load_config.';

-- REVERT:
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS pl_mi_store_attribution;
-- Dropping the column returns every org to the company-wide grain, because load_config reads the
-- code default (false) when the column is absent. No other object depends on it, and no money row
-- is rewritten by applying OR reverting this file — only how a FUTURE recompute attributes it.
-- To revert WITHOUT dropping the column (keeping the switch available):
--   UPDATE commcalc.commission_org_config SET pl_mi_store_attribution = false;
-- In both cases a period already recomputed under the store grain keeps its snapshots until that
-- period is recomputed again.
