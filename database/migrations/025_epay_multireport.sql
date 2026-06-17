-- 025_epay_multireport.sql — let the epay sweep also pull Commission Payment Detail (report
-- #50273 → raw_payment_detail) and Comprehensive Compensation (report #100614 → raw_comp_report),
-- alongside the MI/ATU report it already sweeps. Three opt-in toggles on the sweep config; the
-- sweep logs into the portal once and downloads each enabled report for the current month.
-- Idempotent. No new cron — rides the existing epay sweep schedule.

ALTER TABLE commcalc.epay_sweep_config
  ADD COLUMN IF NOT EXISTS sweep_mi      BOOLEAN NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS sweep_comp    BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS sweep_payment BOOLEAN NOT NULL DEFAULT false;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 025 complete — epay sweep can now pull MI/ATU + Comp + Payment Detail' AS status;
