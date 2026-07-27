-- ═══════════════════════════════════════════════════════════════════════════════════════
-- READ-ONLY BLAST-RADIUS PREVIEW for migration 415 (storeops.shifts.employee_id backfill).
-- 100% SAFE — wrapped in BEGIN/ROLLBACK. It actually RUNS the backfill UPDATE and computes
-- real before/after numbers using the SAME storeops.payroll_month_rows RPC that /payroll and
-- /payroll-by-store already use in production, then ROLLS BACK so NOTHING is changed. Safe to
-- run against prod as many times as you like, at any time (before or after migration 415 is
-- permanently applied — after it's applied for real, every delta below will just show 0).
--
-- EDIT the two rows marked "last pay cycle (EDIT ME)" to your ACTUAL configured pay-period
-- window per org before running (house/Boost and luxelink may be on different cadences —
-- check storeops.tenants / Pay Period & Work-Week settings). July-to-date is pre-filled as
-- 2026-07-01..2026-07-27 (today).
--
-- HONEST FRAMING (see docs/handoffs/people.md for the full writeup): this fix hits /payroll's
-- OWN scheduled_pay/actual_pay columns directly, not just the Store Expenses push (RESULT 2)
-- — GET /payroll sums each raw bucket's ALREADY-COMPUTED dollar figure (hours x that bucket's
-- OWN rate lookup), and a numeric-id-keyed shift bucket's rate lookup misses (rate=$0) today,
-- so its scheduled_pay/actual_pay contribution is $0 regardless of hours. RESULT 1 below shows
-- this directly: actual_pay_before is the TRUE $0-blended figure /payroll currently displays
-- (NOT hours x the correct rate), actual_pay_after is the correct hours x real-rate figure.
-- Inactive employees see the SAME class of fix: a phantom (never-worked) shift still correctly
-- contributes zero either way, but a genuinely-worked shift/punch goes from the $0-rate
-- artifact to being priced at the real rate, same as an active employee.
-- ═══════════════════════════════════════════════════════════════════════════════════════
BEGIN;

CREATE TEMP TABLE date_ranges (org_id uuid, period_label text, lo date, hi date) ON COMMIT DROP;
INSERT INTO date_ranges VALUES
  ('00000000-0000-0000-0000-000000000001', 'last pay cycle (EDIT ME)', '2026-07-01', '2026-07-15'),
  ('00000000-0000-0000-0000-000000000001', 'July-to-date',             '2026-07-01', '2026-07-27'),
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', 'last pay cycle (EDIT ME)', '2026-07-01', '2026-07-15'),
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', 'July-to-date',             '2026-07-01', '2026-07-27');

-- alias_map: the SAME canonicalization /payroll's own reconcile_employee_identity() already
-- applies for the DISPLAY GROUPING (row label) today, even pre-backfill — so the snapshots
-- below group a numeric-id shift bucket together with its business-id timelog bucket UNDER ONE
-- rep row, exactly matching what the Payroll Report page already shows (one row per rep).
CREATE TEMP TABLE alias_map ON COMMIT DROP AS
SELECT e.org_id, e.id::text AS numeric_id, btrim(e.employee_id) AS business_id
  FROM storeops.employees e
 WHERE e.employee_id IS NOT NULL AND btrim(e.employee_id) <> ''
   AND e.id::text <> btrim(e.employee_id)
   AND NOT EXISTS (SELECT 1 FROM storeops.employees e2
                     WHERE e2.org_id = e.org_id AND btrim(e2.employee_id) = e.id::text);

-- ── BEFORE snapshot: hours are grouped canonically (matches the report's row grouping today);
-- $ figures are SUMMED PER-BUCKET at each bucket's OWN raw-id rate lookup (a numeric-id bucket
-- misses and prices at $0) — this is the exact, un-simplified math GET /payroll runs today
-- (reconcile_employee_identity sums already-computed dollars, it never recomputes them from a
-- corrected/display rate — see payroll_identity.py's own module docstring).
CREATE TEMP TABLE before_emp ON COMMIT DROP AS
SELECT dr.org_id, dr.period_label,
       coalesce(am.business_id, g.employee_id) AS employee_id,
       sum(g.scheduled_sum) AS scheduled_hours,
       sum(CASE WHEN g.kind = 'shift' THEN g.actual_eff_sum ELSE g.timelog_hours_sum END) AS actual_hours,
       sum(g.scheduled_sum * coalesce(rb.pay_rate, 0)) AS scheduled_pay,
       sum((CASE WHEN g.kind = 'shift' THEN g.actual_eff_sum ELSE g.timelog_hours_sum END) * coalesce(rb.pay_rate, 0)) AS actual_pay
FROM date_ranges dr
CROSS JOIN LATERAL storeops.payroll_month_rows(dr.org_id, dr.lo, dr.hi + 1) g
LEFT JOIN alias_map am ON am.org_id = dr.org_id AND am.numeric_id = g.employee_id
LEFT JOIN storeops.employees rb ON rb.org_id = dr.org_id AND rb.employee_id = g.employee_id  -- RAW (un-canonicalized) rate lookup — mirrors emp_map's own per-bucket miss
GROUP BY dr.org_id, dr.period_label, coalesce(am.business_id, g.employee_id);

CREATE TEMP TABLE before_store ON COMMIT DROP AS
-- Gate-1-caught bug (fixed before shipping): /payroll-by-store sums BOTH shift-kind
-- (hours_eff_sum) AND timelog-kind (timelog_hours_sum) RPC groups into the store total — a
-- version of this query that only looked at kind='shift' undercounts "before" (misses the
-- punch's real dollars still landing at this store pre-fix) and therefore overstates the
-- delta. This is the exact math get_payroll_by_store() runs (its own two `for g in ... groups`
-- loops, both adding into the same `d["amount"]`).
SELECT dr.org_id, dr.period_label, g.store_code,
       sum(CASE WHEN g.kind = 'shift' THEN g.hours_eff_sum ELSE g.timelog_hours_sum END) AS hours,
       sum((CASE WHEN g.kind = 'shift' THEN g.hours_eff_sum ELSE g.timelog_hours_sum END) * coalesce(e.pay_rate, 0)) AS amount
FROM date_ranges dr
CROSS JOIN LATERAL storeops.payroll_month_rows(dr.org_id, dr.lo, dr.hi + 1) g
LEFT JOIN storeops.employees e ON e.org_id = dr.org_id AND e.employee_id = g.employee_id
WHERE g.store_code <> ''
GROUP BY dr.org_id, dr.period_label, g.store_code;

-- ── Apply migration 415's shifts backfill logic INSIDE this transaction (identical to the
-- checked-in migration file's DO block, condensed here without its narrative comments/RAISE
-- NOTICE — the checked-in file also backfills time_off_requests, not relevant to $ blast radius).
WITH alias_map2 AS (
  SELECT e.org_id, e.id::text AS numeric_id, btrim(e.employee_id) AS business_id
    FROM storeops.employees e
   WHERE e.employee_id IS NOT NULL AND btrim(e.employee_id) <> ''
     AND e.id::text <> btrim(e.employee_id)
     AND NOT EXISTS (SELECT 1 FROM storeops.employees e2
                       WHERE e2.org_id = e.org_id AND btrim(e2.employee_id) = e.id::text)
)
UPDATE storeops.shifts s
   SET employee_id = am.business_id
  FROM alias_map2 am
 WHERE s.org_id = am.org_id AND btrim(s.employee_id) = am.numeric_id;

-- ── AFTER snapshot (post-backfill, still inside the transaction). Post-backfill the raw
-- employee_id is ALREADY canonical, so the RAW rate lookup (rb) now hits correctly too.
CREATE TEMP TABLE after_emp ON COMMIT DROP AS
SELECT dr.org_id, dr.period_label,
       coalesce(am.business_id, g.employee_id) AS employee_id,
       sum(g.scheduled_sum) AS scheduled_hours,
       sum(CASE WHEN g.kind = 'shift' THEN g.actual_eff_sum ELSE g.timelog_hours_sum END) AS actual_hours,
       sum(g.scheduled_sum * coalesce(rb.pay_rate, 0)) AS scheduled_pay,
       sum((CASE WHEN g.kind = 'shift' THEN g.actual_eff_sum ELSE g.timelog_hours_sum END) * coalesce(rb.pay_rate, 0)) AS actual_pay
FROM date_ranges dr
CROSS JOIN LATERAL storeops.payroll_month_rows(dr.org_id, dr.lo, dr.hi + 1) g
LEFT JOIN alias_map am ON am.org_id = dr.org_id AND am.numeric_id = g.employee_id
LEFT JOIN storeops.employees rb ON rb.org_id = dr.org_id AND rb.employee_id = g.employee_id
GROUP BY dr.org_id, dr.period_label, coalesce(am.business_id, g.employee_id);

CREATE TEMP TABLE after_store ON COMMIT DROP AS
SELECT dr.org_id, dr.period_label, g.store_code,
       sum(CASE WHEN g.kind = 'shift' THEN g.hours_eff_sum ELSE g.timelog_hours_sum END) AS hours,
       sum((CASE WHEN g.kind = 'shift' THEN g.hours_eff_sum ELSE g.timelog_hours_sum END) * coalesce(e.pay_rate, 0)) AS amount
FROM date_ranges dr
CROSS JOIN LATERAL storeops.payroll_month_rows(dr.org_id, dr.lo, dr.hi + 1) g
LEFT JOIN storeops.employees e ON e.org_id = dr.org_id AND e.employee_id = g.employee_id
WHERE g.store_code <> ''
GROUP BY dr.org_id, dr.period_label, g.store_code;

-- ══ RESULT 1: per-rep hours + /payroll's OWN scheduled_pay/actual_pay delta ══════════════════
-- A NEGATIVE hours_delta = inflation being removed (a real double-count going away).
-- A POSITIVE pay_delta on an UNCHANGED (or even negative) hours_delta = the $0-rate artifact
-- being corrected on /payroll's own columns (not just the Store Expenses push in RESULT 2).
-- A row only appears here if something actually changed for that rep/period.
SELECT coalesce(b.org_id, a.org_id)         AS org_id,
       coalesce(b.period_label, a.period_label) AS period,
       coalesce(b.employee_id, a.employee_id)   AS employee_id,
       e.name, e.pay_rate,
       coalesce(b.actual_hours, 0)  AS actual_hours_before,
       coalesce(a.actual_hours, 0)  AS actual_hours_after,
       coalesce(a.actual_hours, 0) - coalesce(b.actual_hours, 0) AS hours_delta,
       round(coalesce(b.scheduled_pay, 0)::numeric, 2) AS scheduled_pay_before,
       round(coalesce(a.scheduled_pay, 0)::numeric, 2) AS scheduled_pay_after,
       round(coalesce(b.actual_pay, 0)::numeric, 2)    AS actual_pay_before,
       round(coalesce(a.actual_pay, 0)::numeric, 2)    AS actual_pay_after,
       round((coalesce(a.actual_pay, 0) - coalesce(b.actual_pay, 0))::numeric, 2) AS actual_pay_delta
FROM before_emp b
FULL OUTER JOIN after_emp a USING (org_id, period_label, employee_id)
LEFT JOIN storeops.employees e ON e.org_id = coalesce(b.org_id, a.org_id) AND e.employee_id = coalesce(b.employee_id, a.employee_id)
WHERE coalesce(b.actual_hours, 0)  IS DISTINCT FROM coalesce(a.actual_hours, 0)
   OR coalesce(b.actual_pay, 0)    IS DISTINCT FROM coalesce(a.actual_pay, 0)
   OR coalesce(b.scheduled_pay, 0) IS DISTINCT FROM coalesce(a.scheduled_pay, 0)
ORDER BY org_id, period, hours_delta;

-- ══ RESULT 2: per-store Store Expenses "Employee Salaries" delta (via /payroll-by-store basis,
-- INCLUDING both the shift AND timelog RPC groups, matching get_payroll_by_store()'s own two
-- summation loops exactly) ════════════════════════════════════════════════════════════════════
-- The sign is NOT always positive: for an employee/day with BOTH a shift and a punch, the shift's
-- contribution goes UP (from $0 to real-rate) but the punch's contribution DROPS TO ZERO (excluded
-- by the now-working no-double-count rule) — net effect depends on whether their real punched
-- hours were MORE or LESS than their scheduled hours that day. Read the actual sign per row; do
-- not assume every store goes up.
SELECT coalesce(b.org_id, a.org_id) AS org_id,
       coalesce(b.period_label, a.period_label) AS period,
       coalesce(b.store_code, a.store_code) AS store_code,
       coalesce(b.hours, 0) AS hours_before, coalesce(a.hours, 0) AS hours_after,
       coalesce(a.hours, 0) - coalesce(b.hours, 0) AS hours_delta,
       round(coalesce(b.amount, 0)::numeric, 2) AS amount_before,
       round(coalesce(a.amount, 0)::numeric, 2) AS amount_after,
       round((coalesce(a.amount, 0) - coalesce(b.amount, 0))::numeric, 2) AS amount_delta
FROM before_store b
FULL OUTER JOIN after_store a USING (org_id, period_label, store_code)
WHERE coalesce(b.amount, 0) IS DISTINCT FROM coalesce(a.amount, 0)
   OR coalesce(b.hours, 0)  IS DISTINCT FROM coalesce(a.hours, 0)
ORDER BY org_id, period, amount_delta DESC;

ROLLBACK;  -- NOTHING was changed. To actually apply the fix, run the real, checked-in
           -- database/migrations/415_storeops_shifts_employee_id_backfill.sql instead (it has
           -- the full narrative + RAISE NOTICE summary and is the canonical, tracked migration).
