-- 905_epay_discrepancy_alerts.sql — ePay/fee reconciliation epic, P4.
--
-- Owner directive 2026-08-20: an HOURLY ePay discrepancy sweep that emails District Managers AND above
-- the same day when a store's ePay reconciliation is off beyond a tolerance. Mirrors the accountability
-- morning lateness alerts (migration 433) EXACTLY:
--   • Per-tenant on/off switch + a dollar tolerance live on storeops.tenants (the SAME place 433 put the
--     lateness config and 421 the attendance thresholds) — no new table, no RLS/grant change.
--   • Dedup uses the EXISTING storeops.alert_log (scope 'epay_discrepancy', ref_key incorporating
--     tenant + store + date + kind) so a given discrepancy escalates once per day.
--   • Fired by POST /commcalc/epay/alerts/run-due (secret-gated) on an hourly pg_cron tick.
--
-- Recompute reuses the existing recon math: FEE = commcalc.epay_fee_recon (system raw_sales vs the Boost
-- portal Daily Transaction Detail); PAYMENT = the P2 recon embedded in the DM-Verify money reconciliation
-- (declared ePay vs the portal DTD payment, epay_ingest.per_store_day / migration 903).
--
-- SAFE BY DEFAULT: `epay_alerts_enabled` defaults FALSE — nothing emails on deploy. The owner turns it on
-- per tenant from the fee-recon page's alert panel. Additive + idempotent (ADD COLUMN IF NOT EXISTS).
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS epay_alerts_enabled    boolean       NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS epay_alert_tolerance   numeric       NOT NULL DEFAULT 1.00,   -- dollars
  ADD COLUMN IF NOT EXISTS epay_alert_last_run    timestamptz,
  ADD COLUMN IF NOT EXISTS epay_alert_last_detail text;

NOTIFY pgrst, 'reload schema';

-- ── pg_cron registration (run AFTER deploy, once, in the Supabase SQL editor with the real secret) ──
-- Hourly is enough: the per-(store,date,kind) alert_log dedupe makes a discrepancy escalate exactly once
-- per day and never double-send. Mirrors 433_storeops_accountability_alerts.sql.
--
--   SELECT cron.schedule('epay-discrepancy-run-due', '0 * * * *', $$
--     SELECT net.http_post(
--       url     := 'https://metricspro-production.up.railway.app/api/v1/commcalc/epay/alerts/run-due',
--       headers := jsonb_build_object('Content-Type','application/json','X-Notify-Secret','<NOTIFY_RUN_SECRET>'),
--       body    := '{}'::jsonb); $$);

SELECT 'Migration 905 complete — storeops.tenants ePay discrepancy-alert config (enabled=false, tolerance=1.00). Register the pg_cron job with the real NOTIFY_RUN_SECRET, then enable per tenant.' AS status;
