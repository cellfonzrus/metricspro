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
-- GATE-1 REDO N1 (2026-07-27): the same identical bug was found, independently, in the admin Time
-- Off page (storeops/timeoff/page.tsx — its employee picker sent the numeric `employees.id` too,
-- now fixed alongside this migration) — poisoning storeops.time_off_requests.employee_id the same
-- way. This migration now backfills BOTH tables in one run, sharing the SAME alias_map (which is
-- derived purely from storeops.employees — it has no idea which table it's reconciling).
--
-- EXPECTED, HONEST EFFECT once this runs (money-adjacent, by design — this is the fix, not a
-- side-effect). Verified empirically against a real Postgres 16 instance (docs/handoffs/people.md
-- carries the runnable proof, backend/scratchpad/payroll_id_canonicalize_blast_radius.sql):
--   • HOURS: for any employee who has BOTH a Schedule-created shift AND a kiosk punch (or a
--     manual-hours row) on the SAME day, the report's displayed "Actual Hrs" ALWAYS DROPS to their
--     true worked hours (the shift's scheduled-fallback hours and the punch's real hours were being
--     summed additively before this ran — see payroll_identity.py's module docstring) — this is
--     INFLATION REMOVAL, not a pay cut; nothing here changes anyone's PAY RATE or the hours×rate
--     formula.
--   • DOLLARS ($0→real-rate correction) — this hits GET /payroll's OWN scheduled_pay/actual_pay
--     columns directly (both are hours×pay_rate, and pay_rate was silently $0 for a numeric-id-keyed
--     shift bucket before this), NOT just the Store Expenses push described next. BUT the dollar
--     SIGN is NOT uniformly positive: on the SAME affected day, the shift bucket's contribution goes
--     UP (from $0 to real-rate × scheduled hours) while the punch bucket's contribution DROPS TO
--     ZERO (it's now correctly excluded by the no-double-count rule, instead of being summed in on
--     top of the $0-priced shift). The NET dollar change on a rep's row is real-rate ×
--     (scheduled_hours − actual_punched_hours) for the affected days: if they typically punch MORE
--     than scheduled (the common case — a longer real shift than what's on the schedule), the row's
--     TOTAL DOLLARS GO DOWN even though the previously-$0 shift bucket is now priced correctly; if
--     they typically punch LESS than scheduled, dollars go UP. Read each affected row's own
--     before/after, do not assume a direction.
--   • Separately, GET /payroll-by-store's pushed Store Expenses "Employee Salaries" amount follows
--     the IDENTICAL two-sided rule per store (it sums the SAME shift + timelog RPC groups
--     get_payroll_by_store() already does) — a shift-derived contribution goes from an incorrect $0
--     to the employee's real rate, WHILE that same employee/day's punch-derived contribution (if any)
--     simultaneously drops out of that store's total. Net per store can be up OR down; verified both
--     directions empirically (see the blast-radius script above).
--   • An INACTIVE employee's leftover schedule-only PHANTOM shift (never really worked, actual_hours
--     0) still correctly contributes ZERO after this runs — that rule is untouched — but an inactive
--     employee's REAL worked hours (a genuine shift with actual_hours > 0, or a real punch) go from
--     the same $0-rate artifact to being paid at their real rate too, same as an active employee.
--   • A block-mode tenant's time-off enforcement is NOT affected by this migration itself (the
--     write-side fix already guards the conflict-check lookup — see storeops/router.py's Gate-1 REDO
--     N1 fix — regardless of whether this backfill has run yet); this migration's own effect on
--     time_off_requests is purely a display/matching-cleanliness one (existing numeric-keyed rows
--     become findable by their canonical id too, everywhere else in the app that looks them up by
--     business id).
--
-- SAFETY / GUARDS (identical rule, applied to BOTH tables):
--   • Idempotent — a row is only touched while its employee_id still literally equals some
--     employee's numeric primary key (as text) in the SAME org; once re-keyed to the business id it
--     no longer matches that join condition, so a second run updates 0 rows (verified empirically
--     against a real Postgres 16 instance, see docs/handoffs/people.md).
--   • Org-scoped PER ROW — every join includes `org_id = alias_map.org_id`, so a numeric id that
--     happens to collide across two different tenants' rosters can never cross-contaminate (RULE ONE).
--   • AMBIGUITY GUARD (hard rule, same class as payroll_identity.business_id_alias_map /
--     storeops/router.py._emp_id_variants): a numeric value is only re-keyed when it does NOT ALSO
--     exist as some OTHER employee's own REAL business employee_id in the same org — e.g. employee A
--     (numeric id 42, business "E42") and employee B (business employee_id literally "42", unrelated
--     to A). A row stored under "42" could be A's numeric-id-bug row OR B's own real identity — this
--     migration does NOT guess; every such row is left completely UNTOUCHED and counted in the
--     "skipped as ambiguous" summary for manual review. This is strictly more conservative than doing
--     nothing: an unresolved ambiguous row behaves exactly as it did before this migration (still
--     reconciled, if at all, only by the existing residual `reconcile_employee_identity`/
--     `_emp_id_variants` fallback).
--     — Gate-1 REDO N2 fix (2026-07-27, report-only, no change to what gets UPDATED): an employee
--       whose OWN business employee_id happens to equal their OWN numeric id (a legitimate, harmless
--       no-op case `business_id_alias_map` already treats as "nothing to reconcile") was being
--       counted as "AMBIGUOUS" too, purely because the ambiguity check didn't exclude the row
--       matching ITSELF. Fixed (`AND e2.id <> e.id` below) — a self-match is simply not ambiguous.
--   • Additive-only to DATA, not schema — no column added/dropped, no table created. Nothing reads
--     differently until a row's employee_id value actually changes; every existing degrade-gracefully
--     path is untouched.
--   • Not money-touching in the sense of changing a RATE or FORMULA (AGENT_CONTRACT: "a change to
--     hours/rate math is propose-first") — this re-keys an IDENTITY column only; the owner's explicit
--     approval for this exact shape ("run the payroll money fix") is recorded in the Gate-1 package.
--     (See the "EXPECTED, HONEST EFFECT" note above: the identity fix DOES change what downstream
--     hours×rate math computes on affected rows, by correcting which rate it multiplies by — the
--     formula itself is untouched.)
--
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- PREVIEW (READ-ONLY) — run this FIRST. Shows, per org, how many rows in EACH table WOULD be
-- re-keyed and how many WOULD be skipped as ambiguous, with ZERO writes. Safe to run any time,
-- including on prod, before or after this migration (0 "would_rekey" either side once already
-- applied — "would_skip_ambiguous" is a standing, informational count, not a to-do).
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
--
-- WITH alias_map AS (
--   SELECT e.org_id, e.id::text AS numeric_id, btrim(e.employee_id) AS business_id
--     FROM storeops.employees e
--    WHERE e.employee_id IS NOT NULL AND btrim(e.employee_id) <> ''
--      AND e.id::text <> btrim(e.employee_id)
--      AND NOT EXISTS (SELECT 1 FROM storeops.employees e2
--                        WHERE e2.org_id = e.org_id AND btrim(e2.employee_id) = e.id::text)
-- ),
-- ambiguous_numeric AS (
--   -- Gate-1 REDO N2: "AND e2.id <> e.id" excludes an employee whose OWN business id equals their
--   -- OWN numeric id — that is a harmless no-op, never ambiguous (see the SAFETY note above).
--   SELECT DISTINCT e.org_id, e.id::text AS numeric_id
--     FROM storeops.employees e
--    WHERE EXISTS (SELECT 1 FROM storeops.employees e2
--                    WHERE e2.org_id = e.org_id AND btrim(e2.employee_id) = e.id::text
--                      AND e2.id <> e.id)
-- )
-- SELECT 'storeops.shifts' AS table_name, s.org_id,
--        count(*) FILTER (WHERE am.business_id IS NOT NULL) AS would_rekey,
--        count(*) FILTER (WHERE an.numeric_id  IS NOT NULL) AS would_skip_ambiguous
--   FROM storeops.shifts s
--   LEFT JOIN alias_map         am ON am.org_id = s.org_id AND btrim(s.employee_id) = am.numeric_id
--   LEFT JOIN ambiguous_numeric an ON an.org_id = s.org_id AND btrim(s.employee_id) = an.numeric_id
--  WHERE am.business_id IS NOT NULL OR an.numeric_id IS NOT NULL
--  GROUP BY s.org_id
-- UNION ALL
-- SELECT 'storeops.time_off_requests', t.org_id,
--        count(*) FILTER (WHERE am.business_id IS NOT NULL),
--        count(*) FILTER (WHERE an.numeric_id  IS NOT NULL)
--   FROM storeops.time_off_requests t
--   LEFT JOIN alias_map         am ON am.org_id = t.org_id AND btrim(t.employee_id) = am.numeric_id
--   LEFT JOIN ambiguous_numeric an ON an.org_id = t.org_id AND btrim(t.employee_id) = an.numeric_id
--  WHERE am.business_id IS NOT NULL OR an.numeric_id IS NOT NULL
--  GROUP BY t.org_id
--  ORDER BY 1, 2;
--
-- ═══════════════════════════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
  r RECORD;
  total_updated BIGINT := 0;
  total_skipped BIGINT := 0;
BEGIN
  -- 1) Report-only pass — count rows that will be DELIBERATELY LEFT UNTOUCHED because the numeric id
  --    is AMBIGUOUS, computed BEFORE any write. Gate-1 REDO N2 fix: computing this AFTER the update
  --    (the original shape) could over-count — a row the update below re-keys to a DIFFERENT
  --    employee's genuine, all-digit business id (e.g. someone literally typed "103" as their
  --    employee ID) can itself look "ambiguous" post-write purely because that string also happens
  --    to equal a THIRD employee's numeric primary key. Snapshotting this pass FIRST, against the
  --    pre-update state, makes the count match the PREVIEW query above exactly, every time — the
  --    owner WILL re-run this file, so the notices must be truthful on every run, not just the first.
  FOR r IN
    WITH ambiguous_numeric AS (
      SELECT DISTINCT e.org_id, e.id::text AS numeric_id
        FROM storeops.employees e
       WHERE EXISTS (
               SELECT 1 FROM storeops.employees e2
                WHERE e2.org_id = e.org_id
                  AND btrim(e2.employee_id) = e.id::text
                  AND e2.id <> e.id   -- Gate-1 REDO N2: a self-match is NOT ambiguous
             )
    )
    SELECT 'storeops.shifts'::text AS tbl, s.org_id, count(*) AS n
      FROM storeops.shifts s
      JOIN ambiguous_numeric an ON an.org_id = s.org_id AND btrim(s.employee_id) = an.numeric_id
     GROUP BY s.org_id
    UNION ALL
    SELECT 'storeops.time_off_requests'::text, t.org_id, count(*)
      FROM storeops.time_off_requests t
      JOIN ambiguous_numeric an ON an.org_id = t.org_id AND btrim(t.employee_id) = an.numeric_id
     GROUP BY t.org_id
  LOOP
    RAISE NOTICE 'migration 415: SKIPPED % row(s) in % as AMBIGUOUS in org % — numeric id also matches another employee''s real business employee_id; left unchanged, needs manual review', r.n, r.tbl, r.org_id;
    total_skipped := total_skipped + r.n;
  END LOOP;

  -- 2) The actual backfill — storeops.shifts, re-keying every unambiguous numeric-id row to the
  --    business id (RETURNING lets us report a per-org count without a second scan).
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

  -- 3) The actual backfill — storeops.time_off_requests (Gate-1 REDO N1: same bug, found in the
  --    admin Time Off page's employee picker, now also fixed at the write side).
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
      UPDATE storeops.time_off_requests t
         SET employee_id = am.business_id
        FROM alias_map am
       WHERE t.org_id = am.org_id
         AND btrim(t.employee_id) = am.numeric_id
      RETURNING t.org_id
    )
    SELECT org_id, count(*) AS n FROM upd GROUP BY org_id
  LOOP
    RAISE NOTICE 'migration 415: re-keyed % time_off_requests row(s) to the business employee_id in org %', r.n, r.org_id;
    total_updated := total_updated + r.n;
  END LOOP;

  RAISE NOTICE 'Migration 415 complete — % row(s) re-keyed to the business employee_id across storeops.shifts + storeops.time_off_requests, % row(s) left unchanged as ambiguous (see above)', total_updated, total_skipped;
END $$;

SELECT 'Migration 415 complete — storeops.shifts.employee_id + storeops.time_off_requests.employee_id backfilled to the business employee_id (numeric-id rows, ambiguous cases skipped and reported)' AS status;
