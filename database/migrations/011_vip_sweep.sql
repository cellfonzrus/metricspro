-- 011_vip_sweep.sql — VIP portal auto-sweep: credentials + schedule.
-- Run this in the Supabase SQL editor (Claude cannot run SQL).
--
-- SECURITY: this table holds the VIP portal password, so it is BACKEND-ONLY.
-- RLS is enabled with NO policy for anon/authenticated, and we REVOKE their grants,
-- so the public anon key (used by the browser) cannot read it. Only the service_role
-- (the FastAPI backend) can read/write it. The admin page talks to the backend API,
-- which returns the schedule + "credentials set?" but NEVER the password.

CREATE TABLE IF NOT EXISTS commcalc.vip_sweep_config (
  org_id          UUID PRIMARY KEY,
  portal_user     TEXT,                              -- VIP dealer-portal email
  portal_pass     TEXT,                              -- backend-only; never returned to the browser
  enabled         BOOLEAN     NOT NULL DEFAULT false,-- off until creds are set + turned on
  frequency       TEXT        NOT NULL DEFAULT 'weekly',  -- daily | weekly | monthly
  day_of_week     INT         NOT NULL DEFAULT 0,    -- 0=Mon..6=Sun (weekly)
  day_of_month    INT         NOT NULL DEFAULT 1,    -- monthly
  hour            INT         NOT NULL DEFAULT 6,    -- hour-of-day in `timezone`
  timezone        TEXT        NOT NULL DEFAULT 'America/New_York',
  lookback_days   INT         NOT NULL DEFAULT 14,   -- incremental invoice window per sweep
  sweep_invoices  BOOLEAN     NOT NULL DEFAULT true,
  sweep_asset     BOOLEAN     NOT NULL DEFAULT false,-- Phase 2: portal asset-lending pull
  next_run_at     TIMESTAMPTZ,
  last_run_at     TIMESTAMPTZ,
  last_status     TEXT,                              -- ok | error | running
  last_detail     TEXT,                              -- summary or error (no secrets)
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE commcalc.vip_sweep_config ENABLE ROW LEVEL SECURITY;
-- intentionally NO anon/authenticated policy → the public key gets RLS-denied.
REVOKE ALL ON commcalc.vip_sweep_config FROM anon, authenticated;
GRANT ALL ON commcalc.vip_sweep_config TO service_role;

-- Seed the org row with defaults so the admin page has something to edit (no creds yet).
INSERT INTO commcalc.vip_sweep_config (org_id)
VALUES ('00000000-0000-0000-0000-000000000001')
ON CONFLICT (org_id) DO NOTHING;


-- ── STEP 2 (run AFTER NOTIFY_RUN_SECRET + APP_PUBLIC_URL are set on Railway) ───
-- Requires pg_cron + pg_net (same as 010_notify.sql). Fires run-due every 30 min;
-- the backend only actually sweeps when an enabled config's next_run_at has passed.
-- Replace <APP_PUBLIC_URL> and <NOTIFY_RUN_SECRET> with your real values.
--
-- create extension if not exists pg_cron;
-- create extension if not exists pg_net;
-- select cron.schedule('vip-sweep-run-due', '*/30 * * * *', $$
--   select net.http_post(
--     url := '<APP_PUBLIC_URL>/api/v1/commcalc/vip/sweep/run-due',
--     headers := jsonb_build_object('Content-Type','application/json',
--                                   'X-Notify-Secret','<NOTIFY_RUN_SECRET>')
--   );
-- $$);
