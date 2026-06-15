-- 020_epay_sweep.sql — epay Owner Portal MI + ATU auto-sweep: credentials + schedule (#5b).
-- Run this in the Supabase SQL editor (Claude cannot run SQL).
--
-- The epay portal (ownerportal.epayworldwide.com) is a WAF-protected JavaScriptMVC SPA
-- ("CarrierPortal" / steal.js) whose reports are SSRS downloads — so the sweep drives a
-- headless Chromium (Playwright) rather than a plain form POST (unlike the VIP/DLAR sweeps).
-- It logs in, pulls the MI report (-> commcalc.raw_mi) and the ATU report on a schedule.
--
-- SECURITY: this table holds the epay portal password, so it is BACKEND-ONLY — RLS on
-- with NO anon/authenticated policy, grants revoked, service_role only (same as the VIP/DLAR
-- sweeps). The admin page talks to the backend API, which never returns the password.

CREATE TABLE IF NOT EXISTS commcalc.epay_sweep_config (
  org_id          UUID PRIMARY KEY,
  portal_url      TEXT,                              -- ownerportal.epayworldwide.com
  portal_user     TEXT,                              -- epay owner-portal username
  portal_pass     TEXT,                              -- backend-only; never returned to the browser
  enabled         BOOLEAN     NOT NULL DEFAULT false,-- off until creds are set + turned on
  frequency       TEXT        NOT NULL DEFAULT 'daily',   -- daily | weekly | monthly
  day_of_week     INT         NOT NULL DEFAULT 0,    -- 0=Mon..6=Sun (weekly)
  day_of_month    INT         NOT NULL DEFAULT 1,    -- monthly
  hour            INT         NOT NULL DEFAULT 6,    -- hour-of-day in `timezone`
  timezone        TEXT        NOT NULL DEFAULT 'America/New_York',
  next_run_at     TIMESTAMPTZ,
  last_run_at     TIMESTAMPTZ,
  last_status     TEXT,                              -- ok | error | running
  last_detail     TEXT,                              -- summary or error (no secrets)
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE commcalc.epay_sweep_config ENABLE ROW LEVEL SECURITY;
-- intentionally NO anon/authenticated policy → the public key gets RLS-denied.
REVOKE ALL ON commcalc.epay_sweep_config FROM anon, authenticated;
GRANT ALL ON commcalc.epay_sweep_config TO service_role;

INSERT INTO commcalc.epay_sweep_config (org_id)
VALUES ('00000000-0000-0000-0000-000000000001')
ON CONFLICT (org_id) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 020 complete — epay MI/ATU auto-sweep config ready' as status;


-- ── STEP 2 (run AFTER NOTIFY_RUN_SECRET + APP_PUBLIC_URL are set on Railway) ───
-- Requires pg_cron + pg_net + the Playwright Chromium install in the backend image.
-- create extension if not exists pg_cron;
-- create extension if not exists pg_net;
-- select cron.schedule('epay-sweep-run-due', '*/30 * * * *', $$
--   select net.http_post(
--     url := '<APP_PUBLIC_URL>/api/v1/commcalc/epay/sweep/run-due',
--     headers := jsonb_build_object('Content-Type','application/json',
--                                   'X-Notify-Secret','<NOTIFY_RUN_SECRET>')
--   );
-- $$);
