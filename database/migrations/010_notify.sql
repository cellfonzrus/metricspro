-- MIGRATION 010: NOTIFY / SUBSCRIBE (report delivery by email + WhatsApp)
-- Run this in the Supabase SQL editor. Follows the 004_module_template pattern.
-- Creates the notify.* schema: recipients, subscriptions, send_log.
-- (Scheduling via pg_cron is a SEPARATE block at the bottom — see NOTES.)

CREATE SCHEMA IF NOT EXISTS notify;

-- Saved recipients (manually entered or copied from storeops.employees).
CREATE TABLE IF NOT EXISTS notify.recipients (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  employee_id TEXT,            -- optional link to storeops.employees.employee_id
  name TEXT,
  email TEXT,
  phone TEXT,                  -- E.164 for WhatsApp
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Recurring report subscriptions (fired by /notify/run-due via pg_cron).
CREATE TABLE IF NOT EXISTS notify.subscriptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  name TEXT,
  report_key TEXT NOT NULL,
  filters JSONB DEFAULT '{}'::jsonb,
  channels TEXT[] NOT NULL DEFAULT '{email}',     -- {'email','whatsapp'}
  formats TEXT[] NOT NULL DEFAULT '{xlsx,pdf}',
  recipient_ids UUID[] DEFAULT '{}',
  ad_hoc_emails TEXT[] DEFAULT '{}',
  ad_hoc_phones TEXT[] DEFAULT '{}',
  frequency TEXT CHECK (frequency IN ('daily','weekly','monthly')),
  day_of_week INT,            -- 0=Mon .. 6=Sun (weekly)
  day_of_month INT,           -- 1..31 (monthly; clamped to month length)
  hour INT DEFAULT 8,         -- local hour in `timezone`
  timezone TEXT DEFAULT 'America/New_York',
  is_active BOOLEAN DEFAULT true,
  next_run_at TIMESTAMPTZ,
  last_run_at TIMESTAMPTZ,
  created_by TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS subs_due ON notify.subscriptions(org_id, is_active, next_run_at);

-- One row per delivery attempt (on-demand and scheduled).
CREATE TABLE IF NOT EXISTS notify.send_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  subscription_id UUID,        -- null for on-demand sends
  report_key TEXT NOT NULL,
  channel TEXT NOT NULL,       -- 'email' | 'whatsapp'
  target TEXT NOT NULL,        -- email address or phone
  status TEXT NOT NULL,        -- 'sent' | 'failed'
  provider_message_id TEXT,
  error TEXT,
  filters JSONB,
  triggered_by TEXT,           -- 'manual' | 'schedule'
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS send_log_recent ON notify.send_log(org_id, created_at DESC);

-- RLS (open_all, same as other modules)
DO $$ DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['notify.recipients','notify.subscriptions','notify.send_log'] LOOP
    BEGIN
      EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
      EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXCEPTION WHEN OTHERS THEN NULL; END;
  END LOOP;
END $$;

GRANT USAGE ON SCHEMA notify TO anon, authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA notify TO anon, authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA notify TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 010 complete — notify schema ready' AS status;

-- ─────────────────────────────────────────────────────────────────────────────
-- NOTES — SCHEDULER (run SEPARATELY, after setting NOTIFY_RUN_SECRET on Railway)
-- ─────────────────────────────────────────────────────────────────────────────
-- Enable the extensions once (Supabase: Database → Extensions, or):
--   CREATE EXTENSION IF NOT EXISTS pg_cron;
--   CREATE EXTENSION IF NOT EXISTS pg_net;
--
-- Then schedule a poll every 5 minutes that hits the backend run-due endpoint.
-- Replace <SECRET> with the SAME value as the Railway env var NOTIFY_RUN_SECRET.
--
--   SELECT cron.schedule('notify-run-due', '*/5 * * * *', $$
--     SELECT net.http_post(
--       url     := 'https://metricspro-production.up.railway.app/api/v1/notify/run-due',
--       headers := jsonb_build_object('Content-Type','application/json','x-notify-secret','<SECRET>'),
--       body    := '{}'::jsonb
--     );
--   $$);
--
-- To change/remove later:  SELECT cron.unschedule('notify-run-due');
