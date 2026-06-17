-- 022_creditmemo_autosweep.sql — auto-schedule the VIP credit-memo sweep.
--
-- #10 (Account Module reconciliation) compares VIP "Weekly Incentive Credit" memos against
-- MI + ATU earned. The memos were already scrapeable on-demand (POST /account/credit-memos/sweep),
-- but had no scheduled refresh. Rather than a new cron/table, we ride the existing VIP sweep
-- schedule (commcalc.vip_sweep_config → pg_cron → /commcalc/vip/sweep/run-due), exactly like the
-- Phase-2 asset-lending (PayGo) toggle. This adds one boolean toggle column so the sweep can,
-- on its normal cadence, also refresh commcalc.vip_credit_memos.
--
-- Idempotent: safe to re-run. No new env var, no new cron job.

ALTER TABLE commcalc.vip_sweep_config
  ADD COLUMN IF NOT EXISTS sweep_creditmemo BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN commcalc.vip_sweep_config.sweep_creditmemo IS
  'When true, each scheduled VIP sweep also pulls credit memos (Weekly Incentive Credit) into '
  'commcalc.vip_credit_memos for the #10 Account Module reconciliation. Migration 022.';
