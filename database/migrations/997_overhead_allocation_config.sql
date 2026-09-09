-- 997_overhead_allocation_config.sql
-- OWNER DIRECTIVE 2026-09-09, verbatim: "payroll needs to be attributed to each store rather than
-- teh company as that will give the real picture of the store perfromace, the company will only
-- calculate as a result of all store combined together.  All employees who are salaried and not
-- attached to stores like the DM and market manager shoudl have thier won seaprat line and thier
-- salary will be divided amongs he store they handle , if th market manager is 78000 and he handles
-- all markets then his salary will be 78000/ the number of stores in each market, simialry thier
-- commission will also be a seaprate line item so at the end of the year we know who got paid how
-- much with clear distinction , all these will be a seaprate box in the p&l under wages"
--
-- NUMBERING NOTE: 435, 991-994 applied; 995 and 996 written-not-applied (996 is the concurrent
-- commission-agent's). This migration takes 997.
--
-- ONE additive JSONB column on commcalc.account_config. Applying this migration alone moves NO
-- money for ANY tenant, including the house org: the column defaults to NULL, and
-- storeops/overhead_allocation.resolve_config reads NULL as the house default `mode='off'`, under
-- which nothing is derived, both new P&L lines stay empty, and `auto_opt` drops them entirely.
--
--   overhead_config JSONB  NULL (default)
--     {
--       "mode":                    "off" | "derive",
--       "basis":                   "equal_stores" | "equal_market_then_store" | "weighted",
--       "span_fallback":           "org_span" | "org_unit" | "org_wide",
--       "roles":                   [],        -- extra roles treated as overhead (RULE TWO)
--       "include_inactive_stores": false,
--       "wages_label":             "Overhead salaries (allocated to stores)",
--       "commission_label":        "Overhead commission (allocated to stores)",
--       "commission_source":       "off" | "management_incentive",
--       "commission_statuses":     ["approved","paid"],
--       "manual_expense_names":    []         -- reported against, NEVER suppressed
--     }
--
-- ── THE ALLOCATION BASIS: the directive contains a genuine ambiguity, and it is CONFIG ───────────
-- "divided among the stores they handle" (one division over the covered set) and "78000 / the
-- number of stores in each market" (a per-market division) are NOT the same arithmetic whenever the
-- covered markets differ in size. They are offered as two values of `basis`, and the default is the
-- defensible reading:
--
--   equal_stores (DEFAULT)      monthly salary / |covered stores|.
--   equal_market_then_store     equal across covered MARKETS first, then equal within each market.
--   weighted                    pro-rata on a per-store weight (revenue / GP / hours) supplied by
--                               the caller. Degrades to an equal split and SAYS SO when no covered
--                               store has a usable weight — a report can never show an equal split
--                               as though it were revenue-weighted.
--
-- Measured against the live LuxeLink roster on 2026-09-09 for the single active overhead employee
-- (annual $78,000 = $6,500.00/month, no home_store, no shifts, placed at the Chicago market unit;
-- 13 active Chicago stores, 7 active NY stores, 20 in total):
--
--   equal_stores over the 13 stores that employee covers   -> $500.00 per store per month
--   equal_stores if their span were all 20 stores          -> $325.00 per store per month
--   equal_market_then_store over both markets              -> $250.00 per Chicago store,
--                                                             $464.29 per NY store (last absorbs
--                                                             the cent: $464.26 on one of them)
--
-- Same $6,500.00 in every case — only its distribution across stores moves, which is exactly the
-- store-performance figure the owner is asking to get right. Switching readings is this row, never
-- a deploy.
--
-- ── WHAT THIS FIXES, MEASURED ────────────────────────────────────────────────────────────────────
-- LuxeLink (854f6d7b-…-646f560d4f4c) runs payroll_authority_grain='store' with
-- payroll_expense_names = {'DM Salaries','Employee Salaries'}, and all twenty stores carry a
-- non-zero row of each for August 2026. Every store is therefore authoritative, so
-- coa.build_inputs suppresses the whole StoreOps hours estimate — including the company-wide cell
-- that is the ONLY place this employee's $6,500.00/month appears. It is skipped by design
-- (`_est_skipped_cw`: a company-wide dollar cannot be shown to be absent from the entered store
-- figures) and therefore appears in NO P&L line at all: $78,000.00/year of real salary, invisible.
-- With mode='derive' it books as 13 store cells on its own line instead.
--
-- ── WHAT THIS DOES NOT DO ────────────────────────────────────────────────────────────────────────
-- It never suppresses an expense row. LuxeLink already types overhead salary in by hand — twenty
-- 'DM Salaries' rows totalling $25,500.01/month and twenty 'Owner / Mgmt Salaries' rows totalling
-- $10,000.00/month for August 2026, i.e. $35,500.01 against the $6,500.00 the roster explains.
-- Listing those names in `manual_expense_names` REPORTS that $29,000.01 gap on the line; it does
-- not switch either figure off. Unlike mig 994's commission suppression — where rep_commissions is
-- demonstrably the same dollars from a more authoritative source — the derived overhead figure is
-- the SMALLER one, because the roster is missing those overhead people. Suppressing the manual rows
-- against it would delete real cost and book nothing back. The fix is roster data, not code.
--
-- MONEY: the column moves nothing. The LuxeLink seed at the bottom DOES change reported figures and
-- is therefore COMMENTED OUT behind the owner gate, exactly as migs 938/939/994 did.
--
-- Idempotent, additive, safe to re-run.

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS overhead_config JSONB;

-- A scalar or an array here would be read as "no config" by the resolver; make that true at the
-- storage layer too, so a malformed write is rejected loudly instead of silently doing nothing.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'account_config_overhead_config_obj_chk'
      AND conrelid = 'commcalc.account_config'::regclass
  ) THEN
    ALTER TABLE commcalc.account_config
      ADD CONSTRAINT account_config_overhead_config_obj_chk
      CHECK (overhead_config IS NULL OR jsonb_typeof(overhead_config) = 'object');
  END IF;
END $$;

COMMENT ON COLUMN commcalc.account_config.overhead_config IS
  'Per-org allocation of SALARIED staff who are attached to no store (DM / market manager) across '
  'the stores they cover, plus their commission, as two separate P&L lines under wages '
  '(overhead_wages / overhead_comm). Keys: mode (off|derive, default off = books nothing), basis '
  '(equal_stores|equal_market_then_store|weighted), span_fallback (org_span|org_unit|org_wide), '
  'roles[], include_inactive_stores, wages_label, commission_label, commission_source '
  '(off|management_incentive), commission_statuses[], manual_expense_names[] (reported against, '
  'never suppressed). NULL = house default = nothing derived, every statement byte-identical. '
  'Read by storeops/overhead_allocation.resolve_config via account/coa._account_config. '
  'Owner directive 2026-09-09.';

-- ── LuxeLink seed — NOT APPLIED. This MOVES MONEY. Owner GO required. ────────────────────────────
-- Effect, measured against live rows on 2026-09-09 for August 2026:
--   NEW P&L line `overhead_wages` = $6,500.00, booked as $500.00 at each of the 13 Chicago stores
--       (the covered span of the one active unattached salaried employee, resolved from their
--       org unit — storeops.org_managers is EMPTY for this tenant, so the canonical
--       org_span_for_manager RPC returns nothing and the 'org_unit' fallback is what answers).
--   NEW P&L line `overhead_comm`  = $0.00 -> the line does not render. commcalc
--       .management_incentive_payout has NO rows for this org, so there is no manager commission to
--       place. That is a REPORTED gap (run Management Incentive), not a number to invent.
--   `wages`, `rep_comm`, `store_opex` and every other line: BYTE-IDENTICAL. Nothing is moved off
--       an existing line; $6,500.00 of previously-unbooked salary is added, so August opex rises by
--       $6,500.00 and net income falls by the same. That is the point of the directive.
--   The line's note reports the $29,000.01 gap against the hand-entered 'DM Salaries' +
--       'Owner / Mgmt Salaries' rows. Neither is suppressed.
--
-- UPDATE commcalc.account_config
--    SET overhead_config = jsonb_build_object(
--          'mode', 'derive',
--          'basis', 'equal_stores',
--          'span_fallback', 'org_unit',
--          'commission_source', 'management_incentive',
--          'manual_expense_names', jsonb_build_array('DM Salaries', 'Owner / Mgmt Salaries'))
--  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';

-- ── STORE-CODE BINDING — the DATA half, HANDED OVER, NOT RUN ─────────────────────────────────────
-- The owner asked for "3248 lawrence need to be mapped lawrence or similar". MEASURED 2026-09-09
-- by replaying the SHIPPED resolution chain (account/coa.store_resolver ->
-- commcalc.router._store_code_resolver -> storeops/salary_expense.build_store_folder) over ALL 64
-- distinct raw `store_code` values in storeops.shifts + storeops.timelog for this org (3,674 rows):
--
--   63 of 64 values (3,672 of 3,674 rows) ALREADY BIND to a canonical store code.
--    1 of 64 values (2 rows) binds to nothing: 'T-7812'.
--
-- So there is NO resolver fix to make, and deliberately none was made. Case ('CERMARK'), a trailing
-- city name ('3966 GRAND CHICAGO'), a bare street number ('3560', '104-08') and even the misspelling
-- '3248 LAWARANCE' all already resolve — the last three via coa.store_resolver's leading-street-
-- number step, which is guarded to UNAMBIGUOUS numbers only. Loosening the resolver further would
-- buy 2 rows and risk over-matching '3352 26th' onto '3735 26th' (one digit apart, four live raw
-- spellings between them), which would silently move one store's entire payroll onto another —
-- strictly worse than not matching. That guard is pinned in harness_overhead_allocation.py §6.
--
-- 'T-7812' needs a HUMAN decision (is the 'T-' prefix the Bergenline store, or a different site?),
-- so it is an alias row for the owner to run, not a code change:
--
-- INSERT INTO commcalc.store_aliases (org_id, alias, store_code)
-- SELECT '854f6d7b-6590-4e4d-88ab-646f560d4f4c', 'T-7812', '7812'
--  WHERE NOT EXISTS (
--        SELECT 1 FROM commcalc.store_aliases
--         WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' AND lower(alias) = lower('T-7812'));
--
-- REPORTED, NOT FIXED HERE (data defects; writing code around them would hide them):
--   • storeops.org_managers is EMPTY for this org, so storeops.org_span_for_manager returns nothing
--     for every employee. Coverage resolves via the org-unit fallback instead. Writing the manager
--     rows would make the canonical rung answer.
--   • Six storeops.employees rows carry pay_rate = 0 (three of them with August hours), and one
--     employee_id present on 12 August shift rows has NO storeops.employees row at all — together
--     351.00 August hours valued at $0.00 in every hours x rate figure.
--   • commcalc.store_mapping carries BOTH a canonical code and a second 'LUX-…' code for the same
--     19 addresses. Nothing breaks today (the alias table resolves both onto canonical codes), but
--     store_mapping's address->code map is last-write-wins across the pair, so a future reader that
--     trusts it alone can get either code back. Worth de-duplicating.

-- REVERT:
--   ALTER TABLE commcalc.account_config DROP CONSTRAINT IF EXISTS
--     account_config_overhead_config_obj_chk;
--   ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS overhead_config;
--   (Dropping the column restores pre-997 behaviour exactly: the reader falls back to NULL, which
--    resolves to mode='off' — what it already does on a database where this migration never ran.)
