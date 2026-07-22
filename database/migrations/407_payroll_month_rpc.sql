-- 407_payroll_month_rpc.sql — P0 payroll performance (mod-people band, 2026-07-22).
--
-- WHY: GET /storeops/payroll and GET /storeops/payroll-by-store pulled EVERY month shift row
-- (select *) plus up to 20,000 timelog rows over PostgREST into Python and aggregated there —
-- seconds of transfer for kiosk-heavy tenants (luxelink: reps clock in without formal schedules,
-- so the timelog-fallback path carries most of their hours). Project pattern: aggregate in
-- Postgres via RPC (sub-second), not in Python.
--
-- WHAT:
--   1. ix_timelog_org_workdate    — storeops.timelog had NO (org_id, work_date) index (only
--                                   (org_id, employee_id, work_date) + a partial open-punch
--                                   index, mig 045), so the month-range scan had no efficient path.
--   2. ix_shifts_org_date_live    — shifts month scan by (org_id, shift_date) had no index either
--                                   (mig 003 only has (store_code, shift_date)); also serves the
--                                   RPC's no-double-count anti-join probe.
--   3. storeops.payroll_month_rows(p_org_id, p_lo, p_hi)
--        One row per (kind, employee_id, trimmed store_code), kind ∈ ('shift','timelog'),
--        replicating the handlers' EXACT row-level semantics so Python merges a handful of
--        group rows instead of thousands of raw rows:
--          • shifts filter: org + is_deleted=false + shift_date in [p_lo, p_hi)
--          • actual_eff_sum  = Σ per-row (actual==0 ? scheduled : actual)   ← /payroll basis
--          • hours_eff_sum   = Σ per-row (actual>0  ? actual   : scheduled) ← /payroll-by-store basis
--          • timelog rows: only CLOSED punches (clock_out + hours NOT NULL), non-blank
--            employee_id, work_date in range, AND no live shifts row for the same
--            (employee_id, work_date) in the org — the no-double-count rule. A shift row with a
--            blank store_code still blocks that day (matches the Python shift_days set); a
--            soft-DELETED shift does not.
--          • employee_name  = the group's first row's name (shifts: min id — bigserial insert
--            order; timelog: min created_at, id is a uuid) so Python reproduces the legacy
--            "first-seen row wins" name resolution.
--          • first_ord      = min(id) for shifts / epoch(min(created_at)) for timelog, so Python
--            can rebuild the legacy first-seen insertion order (dominant-store tie-breaks +
--            stable same-name sort). shift vs timelog ordinals are never compared to each other.
--        Org-scoped by parameter (RULE ONE). SECURITY INVOKER (backend service role).
--
-- NOTE (documented, intentional): the legacy Python path silently truncated timelog at 20,000
-- rows per month with no defined order. The RPC has no such cap — it is exact where the legacy
-- path would have been silently wrong. No current tenant is near 20k punches/month.
--
-- Until this runs: NOTHING breaks — router falls back to the legacy Python aggregation on any
-- RPC error (AGENT_CONTRACT §5 degrade-gracefully). Idempotent; safe to re-run.

CREATE INDEX IF NOT EXISTS ix_timelog_org_workdate
  ON storeops.timelog (org_id, work_date);

CREATE INDEX IF NOT EXISTS ix_shifts_org_date_live
  ON storeops.shifts (org_id, shift_date) WHERE is_deleted = false;

CREATE OR REPLACE FUNCTION storeops.payroll_month_rows(p_org_id uuid, p_lo date, p_hi date)
RETURNS TABLE (
  kind              text,
  employee_id       text,
  store_code        text,
  employee_name     text,
  first_ord         double precision,
  scheduled_sum     double precision,
  actual_eff_sum    double precision,
  hours_eff_sum     double precision,
  shift_count       integer,
  timelog_hours_sum double precision
)
LANGUAGE sql STABLE AS $$
  SELECT 'shift'::text                                          AS kind,
         s.employee_id                                          AS employee_id,
         btrim(coalesce(s.store_code, ''))                      AS store_code,
         (array_agg(s.employee_name ORDER BY s.id))[1]          AS employee_name,
         min(s.id)::double precision                            AS first_ord,
         sum(coalesce(s.scheduled_hours, 0)::double precision)  AS scheduled_sum,
         sum(CASE WHEN coalesce(s.actual_hours, 0)::double precision = 0
                  THEN coalesce(s.scheduled_hours, 0)::double precision
                  ELSE coalesce(s.actual_hours, 0)::double precision END) AS actual_eff_sum,
         sum(CASE WHEN coalesce(s.actual_hours, 0)::double precision > 0
                  THEN coalesce(s.actual_hours, 0)::double precision
                  ELSE coalesce(s.scheduled_hours, 0)::double precision END) AS hours_eff_sum,
         count(*)::integer                                      AS shift_count,
         0::double precision                                    AS timelog_hours_sum
    FROM storeops.shifts s
   WHERE s.org_id = p_org_id
     AND s.is_deleted = false
     AND s.shift_date >= p_lo
     AND s.shift_date <  p_hi
   GROUP BY s.employee_id, btrim(coalesce(s.store_code, ''))

  UNION ALL

  SELECT 'timelog'::text,
         t.employee_id,
         btrim(coalesce(t.store_code, '')),
         (array_agg(t.employee_name ORDER BY t.created_at NULLS LAST, t.id))[1],
         extract(epoch FROM min(t.created_at))::double precision,
         0::double precision,
         0::double precision,
         0::double precision,
         0::integer,
         sum(coalesce(t.hours, 0)::double precision)
    FROM storeops.timelog t
   WHERE t.org_id = p_org_id
     AND t.work_date >= p_lo
     AND t.work_date <  p_hi
     AND t.clock_out IS NOT NULL
     AND t.hours IS NOT NULL
     AND t.employee_id IS NOT NULL
     AND t.employee_id <> ''
     AND NOT EXISTS (SELECT 1
                       FROM storeops.shifts s2
                      WHERE s2.org_id = p_org_id
                        AND s2.is_deleted = false
                        AND s2.employee_id = t.employee_id
                        AND s2.shift_date = t.work_date)
   GROUP BY t.employee_id, btrim(coalesce(t.store_code, ''))
$$;

GRANT EXECUTE ON FUNCTION storeops.payroll_month_rows(uuid, date, date)
  TO anon, authenticated, service_role;
