-- 914_payroll_punch_driven_pay.sql — PUNCH-DRIVEN PAY (owner-approved pay-model change, 2026-08-24).
--
-- WHY: schedule-authoritative pay underpaid/overpaid the actual worked time. On a real scheduled day
-- a rep who punched 6.6h was paid the SCHEDULED 6.3h — the punch was "not counted." Owner directive:
-- when a rep has a CLOSED punch (storeops.timelog.hours NOT NULL) on a (employee_id, work_date), the
-- PUNCH hours are AUTHORITATIVE for pay that day, overriding scheduled_hours. With no closed punch,
-- pay stays schedule-driven (unchanged).
--
-- PRECEDENCE (money-critical):  manual correction  >  closed punch  >  scheduled_hours.
-- A MANUAL correction is a human-set shifts.actual_hours (> 0). The ONLY writer of shifts.actual_hours
-- is the DM edit path (PATCH /storeops/shifts), which also logs to storeops.payroll_change_log; nothing
-- auto-reconciles a punch into shifts.actual_hours (clock_out and the force-clockout sweep write ONLY
-- storeops.timelog). So actual_hours>0 is, by construction, a manual correction and MUST win over the
-- raw punch — a DM who fixed a forgotten punch must not be overwritten by a partial/again-forgotten one.
--
-- WHAT changes vs migration 913 (the $0-clocked-day fix, which this preserves):
--   (1) SHIFT leg — actual_eff_sum / hours_eff_sum: a shift now contributes
--         actual_hours                  when actual_hours>0                              (manual wins)
--         0                             when a CLOSED punch exists that (emp, day) AND no manual
--                                       correction that day                              (punch drives)
--         scheduled_hours               otherwise                                        (sched fallback)
--       (both columns already equalled actual_hours>0?actual:scheduled; the new middle case is the only
--        change — a scheduled shift on a punched day yields 0 so the punch, summed in the timelog leg,
--        is the day's only contribution: no double count.)
--   (2) TIMELOG leg — anti-join: a punch is now suppressed ONLY by a MANUAL correction that day
--       (actual_hours>0), NOT by a merely-scheduled shift. Migration 913 suppressed a punch whenever a
--       shift carried hours (scheduled_hours>0 OR actual_hours>0); a scheduled shift no longer does. The
--       PR #74 zero-hour override shell (both 0) still never suppresses (it is not a manual correction).
--
-- Mirrors the Python legacy path + drill-down in storeops/router.py (_punch_driven_day_maps,
-- _shift_actual_contribution, _punch_counts_for_pay) exactly, so the fast RPC path, the legacy
-- fallback, and GET /payroll/actual-hours-detail all reconcile. No data mutation, no backfill —
-- read-time preference, so historical months fix themselves. Idempotent; safe to re-run.

-- ORDER MATTERS: the helper _payroll_day_is_punch_driven MUST be created BEFORE payroll_month_rows,
-- which references it. A LANGUAGE sql function body is validated at CREATE time, so a forward
-- reference errors 42883 ("function ... does not exist"). Helper first, then the RPC.
CREATE OR REPLACE FUNCTION storeops._payroll_day_is_punch_driven(p_org_id uuid, p_emp text, p_day date)
RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1
                   FROM storeops.timelog t
                  WHERE t.org_id = p_org_id
                    AND t.employee_id = p_emp
                    AND t.work_date = p_day
                    AND t.clock_out IS NOT NULL
                    AND t.hours IS NOT NULL)
     AND NOT EXISTS (SELECT 1
                       FROM storeops.shifts s
                      WHERE s.org_id = p_org_id
                        AND s.is_deleted = false
                        AND s.employee_id = p_emp
                        AND s.shift_date = p_day
                        AND coalesce(s.actual_hours, 0)::double precision > 0);
$$;

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
         -- PUNCH-DRIVEN PAY: manual(actual>0) -> actual ; punch-driven day -> 0 ; else scheduled.
         sum(CASE
               WHEN coalesce(s.actual_hours, 0)::double precision > 0
                 THEN coalesce(s.actual_hours, 0)::double precision
               WHEN storeops._payroll_day_is_punch_driven(p_org_id, s.employee_id, s.shift_date)
                 THEN 0::double precision
               ELSE coalesce(s.scheduled_hours, 0)::double precision
             END)                                               AS actual_eff_sum,
         sum(CASE
               WHEN coalesce(s.actual_hours, 0)::double precision > 0
                 THEN coalesce(s.actual_hours, 0)::double precision
               WHEN storeops._payroll_day_is_punch_driven(p_org_id, s.employee_id, s.shift_date)
                 THEN 0::double precision
               ELSE coalesce(s.scheduled_hours, 0)::double precision
             END)                                               AS hours_eff_sum,
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
     -- PUNCH-DRIVEN PAY: a punch is suppressed ONLY by a MANUAL correction that day (actual_hours>0),
     -- never by a merely-scheduled shift (that is the punch-driven change). Manual wins.
     AND NOT EXISTS (SELECT 1
                       FROM storeops.shifts s2
                      WHERE s2.org_id = p_org_id
                        AND s2.is_deleted = false
                        AND s2.employee_id = t.employee_id
                        AND s2.shift_date = t.work_date
                        AND coalesce(s2.actual_hours, 0)::double precision > 0)
   GROUP BY t.employee_id, btrim(coalesce(t.store_code, ''))
$$;

GRANT EXECUTE ON FUNCTION storeops._payroll_day_is_punch_driven(uuid, text, date)
  TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION storeops.payroll_month_rows(uuid, date, date)
  TO anon, authenticated, service_role;
