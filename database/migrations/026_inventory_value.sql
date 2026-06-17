-- 026_inventory_value.sql — real-time inventory VALUE for the Balance Sheet, EDITABLE,
-- sourced from the b2bsoft "Inventory Aging" report (wsreports.b2bsoft.com).
-- Run this in the Supabase SQL editor (Claude cannot run SQL).
--
-- Two tables:
--   commcalc.inventory_value   — one row per store: the swept $ value + an optional manual
--                                override. The Balance Sheet inventory line uses
--                                COALESCE(manual_value, swept_value) per store, falling back to
--                                the asset_ledger on-hand value for stores with no row.
--   commcalc.b2b_sweep_config  — b2bsoft portal credentials + schedule for the auto-fetch
--                                sweep. BACKEND-ONLY (holds the password) — same locked-down
--                                pattern as commcalc.dlar_sweep_config / vip_sweep_config.

-- ── per-store inventory value (editable) ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.inventory_value (
  org_id        UUID        NOT NULL,
  store         TEXT        NOT NULL,            -- store key as reported; canonicalized on read
  swept_value   NUMERIC,                          -- $ value from the b2bsoft Inventory Aging sweep
  manual_value  NUMERIC,                          -- user override; when set, wins on the Balance Sheet
  as_of_date    DATE,                             -- date of the swept snapshot
  source        TEXT        DEFAULT 'b2bsoft',    -- 'b2bsoft' (swept) | 'manual' (entered by hand)
  note          TEXT,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, store)
);

ALTER TABLE commcalc.inventory_value ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY open_all ON commcalc.inventory_value FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN OTHERS THEN NULL; END $$;
GRANT ALL ON commcalc.inventory_value TO anon, authenticated, service_role;

-- ── b2bsoft auto-fetch config (backend-only — holds the portal password) ────────────────
CREATE TABLE IF NOT EXISTS commcalc.b2b_sweep_config (
  org_id          UUID PRIMARY KEY,
  portal_user     TEXT,                                  -- b2bsoft / wsreports login
  portal_pass     TEXT,                                  -- backend-only; never returned to the browser
  enabled         BOOLEAN     NOT NULL DEFAULT false,
  frequency       TEXT        NOT NULL DEFAULT 'daily',   -- daily | weekly | monthly
  day_of_week     INT         NOT NULL DEFAULT 0,         -- 0=Mon..6=Sun (weekly)
  day_of_month    INT         NOT NULL DEFAULT 1,         -- monthly
  hour            INT         NOT NULL DEFAULT 6,         -- hour-of-day in `timezone`
  timezone        TEXT        NOT NULL DEFAULT 'America/New_York',
  next_run_at     TIMESTAMPTZ,
  last_run_at     TIMESTAMPTZ,
  last_status     TEXT,                                  -- ok | error | running
  last_detail     TEXT,                                  -- summary or error (no secrets)
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE commcalc.b2b_sweep_config ENABLE ROW LEVEL SECURITY;
-- intentionally NO anon/authenticated policy → the public key gets RLS-denied.
REVOKE ALL ON commcalc.b2b_sweep_config FROM anon, authenticated;
GRANT ALL ON commcalc.b2b_sweep_config TO service_role;

INSERT INTO commcalc.b2b_sweep_config (org_id)
VALUES ('00000000-0000-0000-0000-000000000001')
ON CONFLICT (org_id) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 026 complete — commcalc.inventory_value + commcalc.b2b_sweep_config ready' AS status;

-- ── STEP 2 (run AFTER NOTIFY_RUN_SECRET + APP_PUBLIC_URL are set on Railway) ─────────────
-- Schedules the b2bsoft inventory sweep the same way as the DLAR/VIP sweeps. Requires pg_cron
-- + pg_net (same as 010/011/012). Replace the two placeholders and uncomment to run once:
--
-- create extension if not exists pg_cron;
-- create extension if not exists pg_net;
-- select cron.schedule('b2b-sweep-run-due', '*/30 * * * *', $$
--   select net.http_post(
--     url := '<APP_PUBLIC_URL>/api/v1/commcalc/b2b/sweep/run-due',
--     headers := jsonb_build_object('Content-Type','application/json',
--                                   'X-Notify-Secret','<NOTIFY_RUN_SECRET>')
--   );
-- $$);
