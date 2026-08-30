-- 306_commission_sales_source.sql
-- mod-commission · band 200–299 spill → 306 (305 taken). Additive + idempotent + safe to re-run.
--
-- WHAT IT IS FOR (owner decision 2026-08-30: "the sales source which creates the sales report is
-- accurate, the same source should feed into all other related modules").
--
-- THE PROBLEM IT MAKES FIXABLE. The pay engines and the reports read DIFFERENT sales universes for the
-- same period. `commission_engine._read_sales` takes raw_sales and only falls back to the daily feed when
-- raw_sales is EMPTY, while the Sales Report / Executive MTD read the feed∪raw_sales union. Measured live
-- (org 854f6d7b, period 2026-08): raw_sales held 3,235 rows for the period, the union held 9,161. So a
-- rules-plan rep was paid on roughly a THIRD of the accessory sales their own report showed, and the Rep
-- Incentive drill could never reconcile against the report it exists to explain (12-rep sample: accessory
-- basis $6,165 under raw_sales vs $16,969 under the union — a ~2.75x difference).
--
-- WHAT THIS COLUMN DOES. Per-tenant selection of which sales universe the pay engines + the commission
-- drill read:
--   'legacy' — DEFAULT. Today's raw_sales-first read. Byte-identical to before this migration; this is
--              also what the code reads when the column is absent, so running the migration alone
--              changes NOTHING.
--   'union'  — the TRANSACTION-grain feed∪raw_sales union (`_sales_rows_union_txn`), deduped by
--              trans_id so a transaction in both tables is counted ONCE and every line item of a kept
--              transaction survives (accessory / tax ext_price sums stay correct). This is the same
--              completeness the Sales Report and Executive MTD show.
--
-- CONFIG, NEVER CODE: the choice is a per-tenant row, not a branch. Nothing about any tenant, carrier or
-- market appears in the engine.
--
-- 💰 THIS COLUMN IS A MONEY SETTING — but ONLY once it is set to 'union'.
-- Running this migration is safe and changes no payout: the column defaults to 'legacy'. Flipping a
-- tenant to 'union' is the deliberate money event, and it RAISES rules-plan commissions (~2.75x the
-- accessory basis for org 854f6d7b in 2026-08) because the engine stops paying on a partial view.
-- Flipping the value back to 'legacy' is an instant, complete revert — no recompute is required to undo
-- the setting itself, though any period already recomputed under 'union' keeps its recomputed numbers
-- until it is recomputed again.
--
-- ORDER OF OPERATIONS to adopt it for a tenant:
--   1. run this migration                                    (no change to any payout)
--   2. UPDATE ... SET sales_source = 'union'                 (see the commented statement below)
--   3. recompute the period                                  (this is when paid numbers move)
-- Reps on an exec_mtd-basis plan are unaffected at every step — they already pay from Executive MTD.

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS sales_source text NOT NULL DEFAULT 'legacy';

-- Idempotent: drop + re-add so a re-run (or a widened value set later) is safe.
ALTER TABLE commcalc.commission_org_config
  DROP CONSTRAINT IF EXISTS commission_org_config_sales_source_check;
ALTER TABLE commcalc.commission_org_config
  ADD CONSTRAINT commission_org_config_sales_source_check
  CHECK (sales_source IN ('legacy', 'union'));

COMMENT ON COLUMN commcalc.commission_org_config.sales_source IS
  'Which sales universe the pay engines + commission drill read. legacy = raw_sales-first (default, '
  'byte-identical to pre-mig-306). union = transaction-grain feed∪raw_sales, the same completeness the '
  'Sales Report / Executive MTD show. MONEY SETTING once set to union.';

-- STEP 2 — deliberately NOT executed by this migration. Uncomment and run when you are ready for the
-- payout change, then recompute the period:
--
-- INSERT INTO commcalc.commission_org_config (org_id, sales_source)
-- VALUES ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', 'union')
-- ON CONFLICT (org_id) DO UPDATE SET sales_source = EXCLUDED.sales_source;

NOTIFY pgrst, 'reload schema';
