-- 1086_pl_carrier_comm_leg_split.sql
-- THE MONTH-OF-LIFE SPLIT OF THE COMMISSION LINE — the switch, per org.
--
-- Owner ask 2026-10-16, verbatim:
--   "the first month and m2-m12 commission should sow the m2-m12 as a part of the p&l , if it sia
--    part of teh p&l already then solit that category into two parts one whatever they ave righ
--    tonw and the m2-m12 as a seaprate line"
-- and, on the same day: "this stays a platofrm wide chaneg / report not a patch for any one
-- carrier".
--
-- WHY THE SPLIT EXISTS. One commission line carries BOTH the money this month's own activations
-- earned and the trailing legs of cohorts sold months ago. Those answer two different questions: a
-- store whose first-month commission is falling is shrinking NOW, while a store whose first month
-- holds and whose M4-M6 fall lost its activation cohort months ago. Index §COMMCALC-12 measured the
-- difference: 96% of a 41% fall on a takeover prospect, and -17.9% of the house estate's own -19.4%
-- May→Sep 2026, were trailing legs. While the two families share a P&L line, that difference is
-- invisible on the statement.
--
-- WHAT THIS MIGRATION DOES: adds the per-org switch. Nothing else. RULE TWO — no carrier, tenant or
-- product is named in code; the behaviour is this config row, with the house answer as the default.
-- The reader is `account/ma_store_pnl.load_config` (ADAPTIVE: a database without this column reads
-- the code default and REPORTS the column as missing, so the feature works before this file is
-- applied and this file only makes it switchable).
--
-- MONEY IMPACT: NONE, and that is proven rather than asserted.
--   • The split RE-POINTS trailing-rung dollars from one REVENUE line to another REVENUE line of the
--     same section (`account/ma_store_pnl.split_leg_bookings`). Revenue, cost of goods, gross
--     profit, expenses and net income are therefore identical TO THE CENT with it on or off, as is
--     every section subtotal. `backend/harness_prospect_eval.py` §V2 is named `no_total_moved` and
--     asserts exactly that; §V11 carries the per-feed identity moved + kept == source total.
--   • A dollar with no month-of-life — a rebate, a fee margin, a residual, airtime margin, a cash
--     spiff whose label states no month — does not move at all (§V6, §V8). The owner's "whatever
--     they have right now" is everything that is not a trailing rung.
--   • The Gross Profit report does not move either: the new line is registered in
--     `GP_INCOME_COLUMNS` and `COMMISSION_RECEIVED_LINES` as the same commission in the same column
--     as the line it splits (§V12).
--   • The new chart line (`coa.PL_SPEC`) is `auto_opt`, so it materialises ONLY on a statement that
--     actually books a trailing dollar. Today the only caller that asks for the split is the Store
--     Takeover Evaluation (§COMMCALC-03), so every live P&L is line-for-line what it was.
--   • Statements are per-period SNAPSHOTS (commcalc.account_statements). Applying this file changes
--     no stored row; a period's snapshot only changes when that period is RECOMPUTED, deliberately.
--
-- Default TRUE: showing the two families as two lines is the platform's own answer to the question,
-- and a tenant that wants a single commission line sets its own row to false — tenant row overrides
-- the house default, exactly like migs 314 / 934 / 1033 on this table.
--
-- Idempotent, additive, no data rewrite. Coexists with every other pl_* column on this table in any
-- order (different columns, same table).

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS pl_carrier_comm_leg_split boolean DEFAULT true;

-- Existing rows created before this column: give them the same answer a new row gets, so an org's
-- statement does not depend on when its config row happened to be inserted.
UPDATE commcalc.commission_org_config
   SET pl_carrier_comm_leg_split = true
 WHERE pl_carrier_comm_leg_split IS NULL;

COMMENT ON COLUMN commcalc.commission_org_config.pl_carrier_comm_leg_split IS
  'mig 1086 (owner ask 2026-10-16, index §COMMCALC-15). TRUE: the commission line is shown in two '
  'parts — the money this month''s own activations earned, and the M2-M12 trailing legs of cohorts '
  'activated in an earlier month, on their own auto_opt revenue line '
  '(account.ma_store_pnl.CARRIER_COMM_TRAILING_LINE). FALSE: one line carrying both, exactly as '
  'before. The rung of each dollar is the booking''s own, from '
  'commcalc.commission_legs.bucket_for_leg; a dollar with no month-of-life never moves. Every P&L '
  'total and every section subtotal is identical either way — the dollars move between two revenue '
  'lines of one section. Read by account/ma_store_pnl.load_config.';

-- REVERT:
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS pl_carrier_comm_leg_split;
-- Dropping the column returns every org to the CODE default, which is TRUE (the split) — the column
-- exists to let a tenant turn it OFF, so dropping it does not restore the single line. To revert the
-- presentation itself, keep the column and set it:
--   UPDATE commcalc.commission_org_config SET pl_carrier_comm_leg_split = false;
-- No money row is rewritten by applying or reverting this file, and no period's stored statement
-- changes until that period is recomputed.
