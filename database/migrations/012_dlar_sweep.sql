-- 012_dlar_sweep.sql — Boost Elevate GO DLAR auto-sweep: credentials + schedule.
-- Run this in the Supabase SQL editor (Claude cannot run SQL).
--
-- Replaces the manual monthly DLAR upload: the backend logs into boostelevatego.com on a
-- schedule, pulls the store (DLAR) + rep (Advocate) reports, and wipes+inserts the
-- period's raw_dlar_store / raw_dlar_rep rows.
--
-- SECURITY: this table holds the Boost portal password, so it is BACKEND-ONLY — RLS on
-- with NO anon/authenticated policy, grants revoked, service_role only (same as the VIP
-- sweep). The admin page talks to the backend API, which never returns the password.

CREATE TABLE IF NOT EXISTS commcalc.dlar_sweep_config (
  org_id          UUID PRIMARY KEY,
  portal_user     TEXT,                              -- Boost Elevate GO email
  portal_pass     TEXT,                              -- backend-only; never returned to the browser
  enabled         BOOLEAN     NOT NULL DEFAULT false,-- off until creds are set + turned on
  frequency       TEXT        NOT NULL DEFAULT 'daily',   -- daily | weekly | monthly
  day_of_week     INT         NOT NULL DEFAULT 0,    -- 0=Mon..6=Sun (weekly)
  day_of_month    INT         NOT NULL DEFAULT 1,    -- monthly
  hour            INT         NOT NULL DEFAULT 7,    -- hour-of-day in `timezone`
  timezone        TEXT        NOT NULL DEFAULT 'America/New_York',
  next_run_at     TIMESTAMPTZ,
  last_run_at     TIMESTAMPTZ,
  last_status     TEXT,                              -- ok | error | running
  last_detail     TEXT,                              -- summary or error (no secrets)
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE commcalc.dlar_sweep_config ENABLE ROW LEVEL SECURITY;
-- intentionally NO anon/authenticated policy → the public key gets RLS-denied.
REVOKE ALL ON commcalc.dlar_sweep_config FROM anon, authenticated;
GRANT ALL ON commcalc.dlar_sweep_config TO service_role;

-- Seed the org row with defaults so the admin page has something to edit (no creds yet).
INSERT INTO commcalc.dlar_sweep_config (org_id)
VALUES ('00000000-0000-0000-0000-000000000001')
ON CONFLICT (org_id) DO NOTHING;


-- ── Ensure the raw_dlar_* tables carry every column the sweep writes ───────────
-- (Migration 002 created a minimal subset; the manual upload added the rest in prod.
--  ADD COLUMN IF NOT EXISTS is a safe no-op where they already exist, and makes this
--  migration self-contained for a fresh database.)
ALTER TABLE commcalc.raw_dlar_rep
  ADD COLUMN IF NOT EXISTS salesforce_id TEXT,
  ADD COLUMN IF NOT EXISTS door_name TEXT,
  ADD COLUMN IF NOT EXISTS door_address TEXT,
  ADD COLUMN IF NOT EXISTS door_city TEXT,
  ADD COLUMN IF NOT EXISTS door_state TEXT,
  ADD COLUMN IF NOT EXISTS door_zip TEXT,
  ADD COLUMN IF NOT EXISTS advocate_name TEXT,
  ADD COLUMN IF NOT EXISTS gross_adds NUMERIC,
  ADD COLUMN IF NOT EXISTS ga_postpaid NUMERIC,
  ADD COLUMN IF NOT EXISTS upgrades NUMERIC,
  ADD COLUMN IF NOT EXISTS atu NUMERIC,
  ADD COLUMN IF NOT EXISTS device_insurance_total NUMERIC,
  ADD COLUMN IF NOT EXISTS device_insurance_ga NUMERIC,
  ADD COLUMN IF NOT EXISTS device_insurance_upgrades NUMERIC,
  ADD COLUMN IF NOT EXISTS device_insurance_pct NUMERIC,
  ADD COLUMN IF NOT EXISTS platinum_pts NUMERIC,
  ADD COLUMN IF NOT EXISTS avg_platinum_pts NUMERIC,
  ADD COLUMN IF NOT EXISTS platinum_pts_5plus NUMERIC,
  ADD COLUMN IF NOT EXISTS boost_ready_bounty NUMERIC,
  ADD COLUMN IF NOT EXISTS tablet_ga NUMERIC,
  ADD COLUMN IF NOT EXISTS boost_app_pct NUMERIC;

ALTER TABLE commcalc.raw_dlar_store
  ADD COLUMN IF NOT EXISTS salesforce_id TEXT,
  ADD COLUMN IF NOT EXISTS location TEXT,
  ADD COLUMN IF NOT EXISTS gross_adds NUMERIC,
  ADD COLUMN IF NOT EXISTS pay_now_acts NUMERIC,
  ADD COLUMN IF NOT EXISTS pay_later_acts NUMERIC,
  ADD COLUMN IF NOT EXISTS total_upgrades NUMERIC,
  ADD COLUMN IF NOT EXISTS family_plan_pct NUMERIC,
  ADD COLUMN IF NOT EXISTS tmr3 NUMERIC,
  ADD COLUMN IF NOT EXISTS aal_conversion NUMERIC,
  ADD COLUMN IF NOT EXISTS protect_pct NUMERIC,
  ADD COLUMN IF NOT EXISTS atu NUMERIC,
  ADD COLUMN IF NOT EXISTS byod_pct NUMERIC,
  ADD COLUMN IF NOT EXISTS conversion_rate NUMERIC,
  ADD COLUMN IF NOT EXISTS acc_attach_rate NUMERIC,
  ADD COLUMN IF NOT EXISTS avg_first_mrc NUMERIC,
  ADD COLUMN IF NOT EXISTS sales_target NUMERIC,
  ADD COLUMN IF NOT EXISTS zero_selling_days NUMERIC,
  ADD COLUMN IF NOT EXISTS shopper_trak_conversion NUMERIC;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 012 complete — DLAR auto-sweep ready' as status;


-- ── STEP 2 (run AFTER NOTIFY_RUN_SECRET + APP_PUBLIC_URL are set on Railway) ───
-- Requires pg_cron + pg_net (same as 010_notify.sql / 011_vip_sweep.sql). Fires run-due
-- every 30 min; the backend only sweeps when an enabled config's next_run_at has passed.
-- Replace <APP_PUBLIC_URL> and <NOTIFY_RUN_SECRET> with your real values.
--
-- create extension if not exists pg_cron;
-- create extension if not exists pg_net;
-- select cron.schedule('dlar-sweep-run-due', '*/30 * * * *', $$
--   select net.http_post(
--     url := '<APP_PUBLIC_URL>/api/v1/commcalc/dlar/sweep/run-due',
--     headers := jsonb_build_object('Content-Type','application/json',
--                                   'X-Notify-Secret','<NOTIFY_RUN_SECRET>')
--   );
-- $$);
