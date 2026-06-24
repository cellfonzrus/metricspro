-- 033_closing_sweep.sql
-- Auto-import the daily-closing Google "Envelopes Data (Responses)" sheet via a Google
-- SERVICE ACCOUNT (read-only). The service-account JSON key is NOT stored here — it lives in
-- the Railway env var GOOGLE_SERVICE_ACCOUNT_JSON (secret, never returned by the API). This
-- table only holds the sheet id + tab + schedule, mirroring the other *_sweep_config tables.
--
-- Setup (user): (1) create a Google Cloud service account, enable the Sheets API, download its
-- JSON key; (2) set GOOGLE_SERVICE_ACCOUNT_JSON = <that JSON> on Railway; (3) SHARE the responses
-- sheet (Viewer) with the service account's client_email; (4) on /closing/imports paste the sheet
-- id, pick the schedule, enable. pg_cron POSTs /closing/sweep/run-due (X-Notify-Secret) like the
-- other sweeps.

CREATE TABLE IF NOT EXISTS commcalc.closing_sweep_config (
  org_id        UUID PRIMARY KEY,
  sheet_id      TEXT,                 -- the spreadsheet id from its URL (.../d/<THIS>/edit)
  tab           TEXT,                 -- responses tab name (blank = first sheet, e.g. 'Form Responses 1')
  enabled       BOOLEAN     DEFAULT false,
  frequency     TEXT        DEFAULT 'daily',          -- daily | weekly | monthly
  day_of_week   INT         DEFAULT 1,                -- 0=Mon .. 6=Sun (weekly)
  day_of_month  INT         DEFAULT 1,                -- 1..31 (monthly)
  hour          INT         DEFAULT 22,               -- local hour to run
  timezone      TEXT        DEFAULT 'America/New_York',
  next_run_at   TIMESTAMPTZ,
  last_run_at   TIMESTAMPTZ,
  last_status   TEXT,
  last_detail   TEXT,
  updated_at    TIMESTAMPTZ DEFAULT now()
);

-- RLS: blanket open_all to match the sibling commcalc.*_sweep_config tables. (No secret here.)
ALTER TABLE commcalc.closing_sweep_config ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS open_all ON commcalc.closing_sweep_config;
CREATE POLICY open_all ON commcalc.closing_sweep_config FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
GRANT ALL ON commcalc.closing_sweep_config TO anon, authenticated, service_role;

-- ── STEP 2 (run after the app is deployed): schedule the run-due poller every 30 min. ──
-- Requires pg_cron + pg_net. Replace <APP_PUBLIC_URL> + <NOTIFY_RUN_SECRET> if not already set.
-- SELECT cron.schedule('closing-sweep-run-due', '*/30 * * * *', $$
--   SELECT net.http_post(
--     url    := 'https://metricspro-production.up.railway.app/api/v1/closing/sweep/run-due',
--     headers:= jsonb_build_object('Content-Type','application/json','X-Notify-Secret','<NOTIFY_RUN_SECRET>'),
--     body   := '{}'::jsonb);
-- $$);
