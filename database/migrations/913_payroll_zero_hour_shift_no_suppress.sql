-- 913_payroll_zero_hour_shift_no_suppress.sql — $0-clocked-day payroll fix (2026-08-24, mod-people).
--
-- WHY: a rep who clocked in on an UNSCHEDULED day (manager clock-in override) was paid $0 for a full
-- worked day. `clock_in_override` (storeops/router.py) inserts a shift row to put the unscheduled
-- store "on record" (status='scheduled') with NO scheduled_hours and NO actual_hours — a pure
-- ZERO-HOUR SHELL. storeops.payroll_month_rows()'s no-double-count anti-join (migration 407) then
-- dropped that day's REAL closed punch because *a* shifts row existed for (employee_id, work_date) —
-- with no check that the shift carried any hours. The shell itself contributes 0 (actual_eff_sum =
-- actual==0 ? scheduled : actual = 0), so the day paid nothing while the rep's 6.5h punch was hidden.
--
-- WHAT: the anti-join now only lets a shift SUPPRESS its day's punch when the shift genuinely carries
-- hours (scheduled_hours>0 OR actual_hours>0). This is the SAME distinction the inactive-employee
-- path already draws (real_shifts = actual_hours>0), widened to include scheduled_hours>0 so a normal
-- SCHEDULED shift (sched>0, actual not yet reconciled) still suppresses its punch EXACTLY as before —
-- the schedule-vs-punch behavior on real scheduled days is unchanged; only the never-paying zero
-- shell stops hiding a punch. Never double-counts: the shell adds 0 to actual_eff_sum, so counting
-- its punch is the only hours contribution that day. Mirrors the Python legacy-path + drill-down fix
-- in storeops/router.py (_shift_contributes_hours) so the fast RPC path and the fallback agree, and
-- GET /payroll/actual-hours-detail keeps reconciling EXACTLY to the report row.
--
-- Everything else about the function is byte-identical to migration 407. Idempotent; safe to re-run.
-- Until this runs: the router falls back to the legacy Python path on RPC error, which now carries
-- the same guard — no divergence, no double count.

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
                        AND s2.shift_date = t.work_date
                        -- $0-clocked-day fix (mig 913): only a shift that actually carries hours may
                        -- suppress this punch. A zero-hour override shell (both 0) must not.
                        AND (coalesce(s2.scheduled_hours, 0)::double precision > 0
                             OR coalesce(s2.actual_hours, 0)::double precision > 0))
   GROUP BY t.employee_id, btrim(coalesce(t.store_code, ''))
$$;

GRANT EXECUTE ON FUNCTION storeops.payroll_month_rows(uuid, date, date)
  TO anon, authenticated, service_role;
