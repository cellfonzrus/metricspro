-- 433_storeops_accountability_alerts.sql — mod-people band 400-499.
--
-- Owner directive 2026-08-18 (verbatim): "the report should be auto triggered to the management for
-- the employees who are getting late to their respective managers above the dm, so all manager should
-- get an email every morning by at 1030 for the lateness and the immediate manager should get the
-- corrective actions plan email ... stating the average of how many times the employee has been late".
-- Clarified: email window = CURRENT PAY PERIOD; the "how many times late" figure = COUNT this pay
-- period; the CAP email fires for EVERY employee late that day.
--
-- Backs the accountability morning lateness alerts:
--   • MORNING SUMMARY  → every manager ABOVE the DM (storeops.router `_managers_above_dm` climbs the
--     org tree above the district node), one digest per manager, each late employee with the DATES +
--     clock-in TIME and their pay-period late count.
--   • CAP email        → the immediate DM, per employee late that day, with a plan to communicate.
-- Fired by POST /storeops/accountability/alerts/run-due (secret-gated), on an hourly pg_cron tick; the
-- job compares tenant-local HH:MM to `lateness_alert_time` (default 10:30) and dedupes via
-- storeops.alert_log (scope 'lateness_am' / 'lateness_cap', ref_key = business date) so it sends once.
--
-- RULE TWO (SAP-configurable): the on/off switch + send time are per-tenant settings, added to
-- storeops.tenants (the SAME place migration 421 put the attendance thresholds and 851 the timezone) —
-- no new table, no RLS/grant change (storeops.tenants keeps its service-role-only posture).
--
-- SAFE BY DEFAULT: `lateness_alerts_enabled` defaults FALSE — nothing emails on deploy. The owner turns
-- it on deliberately from the Accountability page's "Morning lateness alerts" panel (an operator
-- go-live, same posture as every other alert sweep). Additive + idempotent (ADD COLUMN IF NOT EXISTS).
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS lateness_alerts_enabled   boolean     NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS lateness_alert_time       text        NOT NULL DEFAULT '10:30',  -- HH:MM, tenant-local
  ADD COLUMN IF NOT EXISTS lateness_alert_last_run   timestamptz,
  ADD COLUMN IF NOT EXISTS lateness_alert_last_detail text;

NOTIFY pgrst, 'reload schema';

-- ── pg_cron registration (run AFTER deploy, once, in the Supabase SQL editor with the real secret) ──
-- Hourly is enough: the HH:MM compare + alert_log dedupe make it fire exactly once at/after the
-- configured time (e.g. 10:30) tenant-local and never double-send. Mirrors 033_closing_sweep.sql.
--
--   SELECT cron.schedule('accountability-lateness-run-due', '0 * * * *', $$
--     SELECT net.http_post(
--       url     := 'https://metricspro-production.up.railway.app/api/v1/storeops/accountability/alerts/run-due',
--       headers := jsonb_build_object('Content-Type','application/json','X-Notify-Secret','<NOTIFY_RUN_SECRET>'),
--       body    := '{}'::jsonb); $$);

SELECT 'Migration 433 complete — storeops.tenants lateness-alert config (enabled=false, time=10:30). Register the pg_cron job with the real NOTIFY_RUN_SECRET, then enable per tenant on the Accountability page.' AS status;
