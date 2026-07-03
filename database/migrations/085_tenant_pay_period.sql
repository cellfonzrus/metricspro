-- 085_tenant_pay_period.sql — per-tenant pay period / work-week definition (captured at onboarding)
--
-- WHY: payroll + scheduling currently assume a Monday-start week (mondayOf() in the frontend,
-- .weekday() Mon=0 in the backend) and payroll runs over ad-hoc start/end params with no stored
-- definition. Different tenants run different cycles — Luxelink's work week starts THURSDAY, ends
-- WEDNESDAY, and pays the FOLLOWING FRIDAY. This stores that definition per tenant so the schedule
-- week, the hours-budget week (mig 086), and payroll all derive from ONE source of truth.
--
-- DOW convention: 0=Monday .. 6=Sunday (matches Python date.weekday(); the UI uses named-day
-- dropdowns so operators never see the number). Luxelink = work_week_start_dow 3 (Thu),
-- payday_dow 4 (Fri), weekly, payday_weeks_after 1 (the Friday on/after the period end).
--
-- ONBOARDING GATE: setup_complete flags whether the tenant has defined its mandatory setup. Per the
-- product decision this is a BANNER ONLY — nothing is blocked; the admin decides when to proceed.
--
-- SAFE: additive + idempotent. Existing tenants default to a Monday week (today's behavior), so no
-- payroll/schedule math changes until a tenant sets its own values.

ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS work_week_start_dow INT     DEFAULT 0,      -- 0=Mon..6=Sun; Luxelink Thu=3
  ADD COLUMN IF NOT EXISTS pay_period_type     TEXT    DEFAULT 'weekly', -- 'weekly' | 'biweekly'
  ADD COLUMN IF NOT EXISTS payday_dow          INT     DEFAULT 4,      -- 0=Mon..6=Sun; Friday=4
  ADD COLUMN IF NOT EXISTS payday_weeks_after  INT     DEFAULT 1,      -- weeks: 1 = payday_dow on/after period end
  ADD COLUMN IF NOT EXISTS biweekly_anchor     DATE,                   -- a known period-START date (phases biweekly)
  ADD COLUMN IF NOT EXISTS timezone            TEXT,                   -- optional per-tenant tz (else BUSINESS_TZ)
  ADD COLUMN IF NOT EXISTS setup_complete      BOOLEAN DEFAULT false,  -- mandatory onboarding items defined
  ADD COLUMN IF NOT EXISTS setup_completed_at  TIMESTAMPTZ;

COMMENT ON COLUMN storeops.tenants.work_week_start_dow IS
  'First day of the tenant work-week / pay-period. 0=Mon..6=Sun. Drives schedule week, hours-budget week, and payroll period. Luxelink=3 (Thursday).';
COMMENT ON COLUMN storeops.tenants.payday_weeks_after IS
  'How the payday is placed after a period: payday = the first payday_dow on/after the period end, advanced by (payday_weeks_after-1) weeks. Luxelink weekly Thu-Wed + payday_dow Fri + 1 = the Friday following the Wed end.';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 085 complete — storeops.tenants pay-period columns' AS status;
