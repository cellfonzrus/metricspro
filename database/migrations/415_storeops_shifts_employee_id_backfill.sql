-- 415_storeops_shifts_employee_id_backfill.sql — mod-people band 400-499.
-- Owner-approved money fix, 2026-07-27 ("run the payroll money fix"), endorsed by the Gate-1
-- reviewer of the 2026-07-27 payroll package (docs/handoffs/people.md).
--
-- ROOT CAUSE (write-side, now fixed alongside this migration — see storeops/router.py's new
-- `_canonical_shift_employee_id` + frontend/.../storeops/schedule/page.tsx:294): every NEW shift is
-- created with `employee_id` = the employee's NUMERIC `employees.id` primary key, while EVERY other
-- payroll source (a kiosk clock punch in storeops.timelog, a storeops.manual_hours row, and the
-- `employees` roster's own `employee_id` column used for the pay-rate lookup / RBAC) uses the
-- BUSINESS `employees.employee_id` (e.g. "E45"). This migration re-keys the EXISTING shift rows that
-- still carry the wrong (numeric) form so the already-correct, already-shipped reconciliation logic
-- (payroll_identity.reconcile_employee_identity, the mig-407 payroll_month_rows RPC's own
-- `employee_id` anti-join for no-double-count, and GET /payroll-by-store's `rate_map` lookup) starts
-- matching for real, for these rows too — none of that logic is changed by this migration; it was
-- already correct and simply had nothing to match against for a numeric-id shift.
--
-- EXPECTED, HONEST EFFECT once this runs (money-adjacent, by design — this is the fix, not a
-- side-effect): for any employee who has BOTH a Schedule-created shift AND a kiosk punch (or a
-- manual-hours row) on the SAME day, the report's displayed "Actual Hrs" DROPS to their true worked
-- hours (the shift's scheduled-fallback hours and the punch's real hours were being summed
-- additively before this ran — see payroll_identity.py's module docstring) — this is INFLATION
-- REMOVAL, not a pay cut; nothing here changes anyone's PAY RATE or the hours×rate formula.
-- Separately, GET /payroll-by-store's pushed Store Expenses "Employee Salaries" dollar amount for a
-- shift-derived contribution goes from an incorrect $0 (rate_map never matched a numeric id) to the
-- employee's REAL rate — a previously-unpriced real labor cost becoming visible, not new spending.
--
-- SAFETY / GUARDS:
--   • Idempotent — a row is only touched while its employee_id still literally equals some
--     employee's numeric primary key (as text) in the SAME org; once re-keyed to the business id it
--     no longer matches that join condition, so a second run updates 0 rows (verified empirically
--     against a real Postgres 16 instance, see docs/handoffs/people.md).
--   • Org-scoped PER ROW — the join always includes `s.org_id = e.org_id`, so a numeric id that
--     happens to collide across two different tenants' rosters can never cross-contaminate (RULE ONE).
--   • AMBIGUITY GUARD (hard rule, Gate-1 N1-class safety, identical to
--     payroll_identity.business_id_alias_map / storeops/router.py._emp_id_variants): a numeric value
--     is only re-keyed when it does NOT ALSO exist as some employee's own REAL business employee_id
--     in the same org — e.g. employee A (numeric id 42, business "E42") and employee B (business
--     employee_id literally "42", unrelated to A). A shift stored under "42" could be A's
--     Schedule-created shift OR B's own real identity — this migration does NOT guess; every such
--     row is left completely UNTOUCHED and counted in the "skipped as ambiguous" summary below for
--     manual review. This is strictly more conservative than doing nothing: an unresolved ambiguous
--     row behaves exactly as it did before this migration (still reconciled, if at all, only by the
--     existing residual `reconcile_employee_identity`/`_emp_id_variants` fallback).
--   • Additive-only to DATA, not schema — no column added/dropped, no table created. Nothing reads
--     differently until a row's employee_id value actually changes; every existing degrade-gracefully
--     path is untouched.
--   • Not money-touching in the sense of changing a RATE or FORMULA (AGENT_CONTRACT: "a change to
--     hours/rate math is propose-first") — this re-keys an IDENTITY column only; the owner's explicit
--     approval for this exact shape ("run the payroll money fix") is recorded in the Gate-1 package.
--
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- PREVIEW (READ-ONLY) — run this FIRST. Shows, per org, how many shift rows WOULD be re-keyed and
-- how many WOULD be skipped as ambiguous, with ZERO writes. Safe to run any time, including on prod
-- before or after this migration (0 rows either side once already applied).
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
--
-- WITH alias_map AS (
--   SELECT e.org_id,
--          e.id::text            AS numeric_id,
--          btrim(e.employee_id)  AS business_id
--     FROM storeops.employees e
--    WHERE e.employee_id IS NOT NULL
--      AND btrim(e.employee_id) <> ''
--      AND e.id::text <> btrim(e.employee_id)
--      AND NOT EXISTS (
--            SELECT 1 FROM storeops.employees e2
--             WHERE e2.org_id = e.org_id
--               AND btrim(e2.employee_id) = e.id::text
--          )
-- ),
-- ambiguous_numeric AS (
--   SELECT DISTINCT e.org_id, e.id::text AS numeric_id
--     FROM storeops.employees e
--    WHERE EXISTS (
--            SELECT 1 FROM storeops.employees e2
--             WHERE e2.org_id = e.org_id
--               AND btrim(e2.employee_id) = e.id::text
--          )
-- )
-- SELECT s.org_id,
--        count(*) FILTER (WHERE am.business_id IS NOT NULL)  AS would_rekey,
--        count(*) FILTER (WHERE an.numeric_id  IS NOT NULL)  AS would_skip_ambiguous
--   FROM storeops.shifts s
--   LEFT JOIN alias_map         am ON am.org_id = s.org_id AND btrim(s.employee_id) = am.numeric_id
--   LEFT JOIN ambiguous_numeric an ON an.org_id = s.org_id AND btrim(s.employee_id) = an.numeric_id
--  WHERE am.business_id IS NOT NULL OR an.numeric_id IS NOT NULL
--  GROUP BY s.org_id
--  ORDER BY s.org_id;
--
-- ═══════════════════════════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
  r RECORD;
  total_updated BIGINT := 0;
  total_skipped BIGINT := 0;
BEGIN
  -- 1) The actual backfill — re-key every unambiguous numeric-id shift row to the business id,
  --    one UPDATE per matching org (RETURNING lets us report a per-org count without a second scan).
  FOR r IN
    WITH alias_map AS (
      SELECT e.org_id,
             e.id::text            AS numeric_id,
             btrim(e.employee_id)  AS business_id
        FROM storeops.employees e
       WHERE e.employee_id IS NOT NULL
         AND btrim(e.employee_id) <> ''
         AND e.id::text <> btrim(e.employee_id)
         AND NOT EXISTS (
               SELECT 1 FROM storeops.employees e2
                WHERE e2.org_id = e.org_id
                  AND btrim(e2.employee_id) = e.id::text
             )
    ),
    upd AS (
      UPDATE storeops.shifts s
         SET employee_id = am.business_id
        FROM alias_map am
       WHERE s.org_id = am.org_id
         AND btrim(s.employee_id) = am.numeric_id
      RETURNING s.org_id
    )
    SELECT org_id, count(*) AS n FROM upd GROUP BY org_id
  LOOP
    RAISE NOTICE 'migration 415: re-keyed % shift row(s) to the business employee_id in org %', r.n, r.org_id;
    total_updated := total_updated + r.n;
  END LOOP;

  -- 2) Report-only pass — count rows deliberately left untouched because the numeric id is
  --    AMBIGUOUS (also somebody's real business employee_id in the same org). Never re-run the
  --    UPDATE for these; they need a human to look at the two employees involved.
  FOR r IN
    WITH ambiguous_numeric AS (
      SELECT DISTINCT e.org_id, e.id::text AS numeric_id
        FROM storeops.employees e
       WHERE EXISTS (
               SELECT 1 FROM storeops.employees e2
                WHERE e2.org_id = e.org_id
                  AND btrim(e2.employee_id) = e.id::text
             )
    )
    SELECT s.org_id, count(*) AS n
      FROM storeops.shifts s
      JOIN ambiguous_numeric an
        ON an.org_id = s.org_id AND btrim(s.employee_id) = an.numeric_id
     GROUP BY s.org_id
  LOOP
    RAISE NOTICE 'migration 415: SKIPPED % shift row(s) as AMBIGUOUS in org % — numeric id also matches another employee''s real business employee_id; left unchanged, needs manual review', r.n, r.org_id;
    total_skipped := total_skipped + r.n;
  END LOOP;

  RAISE NOTICE 'Migration 415 complete — % shift row(s) re-keyed to the business employee_id, % row(s) left unchanged as ambiguous (see above)', total_updated, total_skipped;
END $$;

SELECT 'Migration 415 complete — storeops.shifts.employee_id backfilled to the business employee_id (numeric-id rows, ambiguous cases skipped and reported)' AS status;
