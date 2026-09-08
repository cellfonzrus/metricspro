-- 994_labour_coverage_config.sql
-- OWNER DIRECTIVE 2026-09-08: "we need to pull the exact salaries paid as per the schedule and update
-- the same in the expenses as those are not getting updated for a lot of stores; if we have the actual
-- hours then the salary is based on actual hours if not then the salary is based on scheduled hours
-- for that month to go in the gross profit for that month."
--
-- This migration is the READ side of that sentence. It adds TWO config columns to
-- commcalc.account_config. Both defaults reproduce today's behaviour EXACTLY, so applying this
-- migration alone moves NO money for ANY tenant, including the house org.
--
--   payroll_authority_grain           TEXT   'org' (default) | 'store'
--       'org'   — the behaviour that shipped with ruling K2 (mig 621): ONE authoritative payroll row
--                 anywhere in the org suppresses the StoreOps hours estimate for EVERY store.
--       'store' — a store's own entered payroll figure suppresses only ITS OWN estimate. A store with
--                 no figure falls back to its own hours for the month (actual where clocked,
--                 otherwise scheduled — exactly the owner's rule, at the grain the owner stated it).
--
--       WHY: under 'org', a store with no payroll row books $0.00 wages and renders identically to a
--       store that genuinely paid nobody. Measured on LuxeLink July 2026 (org 854f6d7b-…-646f560d4f4c,
--       2026-09-08): 3 of 20 stores — '3352 26th', '3735 26th', 'Chicago heights' — have no
--       'Employee Salaries' expense row at all. Their labour cost is absent from the P&L and from
--       gross profit, in both directions, silently.
--
--   labour_commission_expense_names   TEXT[]  '{}' (default)
--       Expense names that carry commission ALREADY booked from commcalc.rep_commissions. The GP
--       report deducts `rep_pay` SEPARATELY from `exp_total`, and the P&L books `rep_comm` separately
--       from `store_opex`, so such a row is the same labour dollars twice.
--
--       ⚠️ OWNER DECISION 2026-09-08, verbatim: "Rep commision should go in p&l". That names
--       commcalc.rep_commissions → the `rep_comm` P&L line as the AUTHORITATIVE route (unchanged,
--       untouched), and the manual expense row as the duplicate. So listing a name here no longer
--       only REPORTS the collision — it SUPPRESSES the expense-side booking, on BOTH readers at
--       once (the P&L `account/coa.build_inputs` and the GP report `commcalc/gp_report`, whose
--       `exp_total` carries the same rows), from ONE shared decision in
--       commcalc/labour_coverage.suppression_plan. Suppressing on one surface only would leave the
--       two reports disagreeing about the same month.
--
--       ⚠️ THIS COLUMN NOW MOVES MONEY FOR THE ORG THAT SETS IT. The column itself, and this
--       migration, still move nothing: the default stays '{}' and every org's books are
--       byte-identical until an owner names a value. The UPDATE in section (b) is the money change
--       and stays commented out behind the owner gate.
--
--       IT NEVER SILENTLY DROPS A COST. Suppression is decided per STORE-MONTH and only where
--       rep_commissions actually books something for that store to replace it. A store carrying the
--       expense row with NO rep commission that month KEEPS its row (removing it would delete a real
--       cost and book nothing back) and is reported by name and amount instead. Three states:
--       replaced / no_replacement / not_applicable.
--
--       MEASURED, LuxeLink August 2026 (2026-09-08, re-verified before this change): twenty $500.00
--       'Employee Commission' rows = $10,000.00 in commcalc.store_expenses, against $11,118.78 of
--       commcalc.rep_commissions for the same month ($10,771.87 of it attributable to those twenty
--       stores; $346.91 carries a blank store and books company-wide). Overlap booked on BOTH routes
--       across the 20 stores: $7,626.14.
--
-- MONEY: the columns themselves move nothing. The LuxeLink seeds at the bottom DO change reported
-- figures and are therefore COMMENTED OUT behind the owner gate, exactly as migs 938/939 did.
--
-- Idempotent, additive, safe to re-run.

-- ── the columns ───────────────────────────────────────────────────────────────────────────────────
ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS payroll_authority_grain TEXT NOT NULL DEFAULT 'org';

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS labour_commission_expense_names TEXT[] NOT NULL DEFAULT '{}';

-- A typo must not silently invent a third grain: the reader already ignores an unknown value and
-- falls back to 'org', and this constraint makes the same rule true at the storage layer.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'account_config_payroll_authority_grain_chk'
      AND conrelid = 'commcalc.account_config'::regclass
  ) THEN
    ALTER TABLE commcalc.account_config
      ADD CONSTRAINT account_config_payroll_authority_grain_chk
      CHECK (payroll_authority_grain IN ('org', 'store'));
  END IF;
END $$;

COMMENT ON COLUMN commcalc.account_config.payroll_authority_grain IS
  'Grain at which an entered payroll figure suppresses the StoreOps hours estimate on the P&L wages '
  'line. ''org'' (default) = pre-994 behaviour: one row anywhere suppresses every store. ''store'' = '
  'per-store, so a store with no entered figure still books its own actual-else-scheduled hours '
  'instead of a silent $0.00. Read by account/coa.build_inputs.';

COMMENT ON COLUMN commcalc.account_config.labour_commission_expense_names IS
  'store_expenses.expense_name values that carry commission ALREADY booked from '
  'commcalc.rep_commissions (which the GP report deducts as rep_pay and the P&L as rep_comm). '
  'Owner decision 2026-09-08 "Rep commision should go in p&l": rep_commissions is the authoritative '
  'route, so listing a name here SUPPRESSES the expense-side booking on BOTH the P&L (store_opex) '
  'and the GP report (exp_total) from one shared decision, and surfaces the swap per store. '
  'Suppression is per store-month and only where rep_commissions has something to book in its '
  'place; a store with no rep commission that month keeps its row and is reported instead. '
  'Read by commcalc/labour_coverage.suppression_plan (and commission_collisions, which reports what '
  'is STILL double-booked afterwards). Empty = no claim, byte-identical books (default).';

-- ── LuxeLink seeds — NOT APPLIED. These MOVE MONEY. Owner GO required. ────────────────────────────
-- (a) payroll_authority_grain = 'store'
--     Effect measured against live rows on 2026-09-08: for July and August 2026 the three/zero
--     unentered stores derive $0.00 from their own hours anyway (every LuxeLink shift in both months
--     carries 0.0 actual hours, and the three July gap stores carry 0.0 scheduled hours as well), so
--     the STORE cells do not move today. What changes is that the moment those stores' hours are
--     entered, their wages appear instead of staying at $0.00.
--     The company-wide salaried cell ($6,500.00/month — one active annual-$78,000 employee with no
--     home_store and no shifts) is deliberately still NOT booked under 'store' grain; it cannot be
--     shown to be absent from the entered store figures. Setting that employee's home store is the
--     right fix and is a data change, not a code one.
--
-- UPDATE commcalc.account_config
--    SET payroll_authority_grain = 'store'
--  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';
--
-- (b) labour_commission_expense_names = '{Employee Commission}'   ⚠️ THIS ONE MOVES MONEY
--     This is the statement that ENACTS the owner's 2026-09-08 decision for LuxeLink. Measured
--     against live rows on 2026-09-08 by running both readers with and without it:
--
--       AUGUST 2026 — twenty $500.00 'Employee Commission' rows stop booking.
--         P&L  store_opex   $295,610.15 → $285,610.15   (−$10,000.00, −$500.00 at each of 20 stores)
--         P&L  rep_comm     $11,118.78  → $11,118.78    (UNCHANGED — the authoritative route)
--         GP   −Expenses    $295,610.15 → $285,610.15   (the two readers move identically)
--         GP   −Rep Pay     $10,771.87  → $10,771.87    (UNCHANGED)
--         GP   net_profit  −$290,908.47 → −$280,908.47  (+$10,000.00); P&L net income +$10,000.00
--         Every other P&L line and every other GP total is BYTE-IDENTICAL.
--         The swap is not a wash: $10,000.00 of flat expense leaves, and the $10,771.87 that
--         rep_comm books for those same stores was ALREADY on the books — $7,626.14 of it was the
--         genuine double-count, the remaining $2,373.86 was expense the real payout never covered.
--
--       SEPTEMBER 2026 — the dangerous case, and it is HANDLED, not suppressed. September has no
--         store_expenses rows of its own, so it carries August's twenty rows, while
--         commcalc.rep_commissions has NO September rows at all. Nothing would replace the cost, so
--         NOTHING is suppressed: all twenty rows keep booking and the month reports
--         "$10,000.00 at 20 store(s) … is STILL booked as an expense". Post September's rep
--         commissions and the swap happens on its own; until then the cost stays where it is.
--
--       JULY 2026 — no 'Employee Commission' rows exist. Unaffected, byte-identical.
--
-- UPDATE commcalc.account_config
--    SET labour_commission_expense_names = ARRAY['Employee Commission']
--  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';

-- REVERT:
--   ALTER TABLE commcalc.account_config DROP CONSTRAINT IF EXISTS
--     account_config_payroll_authority_grain_chk;
--   ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS payroll_authority_grain;
--   ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS labour_commission_expense_names;
--   (Dropping the columns restores pre-994 behaviour exactly: the readers fall back to 'org' and an
--    empty name list, which is what they already do on a database where this migration never ran.)
