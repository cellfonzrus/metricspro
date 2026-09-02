-- 937_closing_entry_quality.sql — closing entry-quality coaching config + idempotency log
-- (owner directive 2026-09-02, item 3).
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- Owner, verbatim: "Need a training walkthru for an employee if their data is not entered
-- correctly for a second day in a row to tell them that they are not entering the data correctly
-- or clearly whatever the case is and guiding them how to correct it."
--
-- Detection + messaging are per-org CONFIG with house defaults (RULE TWO), resolved by
-- closing/entry_quality.resolve_config:
--   enabled           true            (in-app banner + management report on by default — guidance,
--                                      not money; notify channels stay off until configured)
--   threshold_days    2               ("a second day in a row")
--   signals           ['dm_corrected','sent_to_review']
--                                     dm_corrected = the store-day the employee submitted on was
--                                     DM-verified WITH a correction; sent_to_review = the row hit
--                                     auto_accepted (3 tries, mismatched) or mgmt_flag
--   notify_channel    'none'          ('email' | 'whatsapp' | 'both' opt-in per org)
--   message_template  house wording   ({name}/{days}/{reasons} placeholders)
--   tour_slug         'closing-submit' (the EXISTING Training Center walk-through — a tour is
--                                      DATA (mig 720); a tenant can point at its own tour slug)
--
-- closing_entry_coaching is the run-due sweep's idempotency log: one row per (employee,
-- streak-end day) actually notified, so a nightly cron never re-sends the same nudge.
--
-- NOT money-moving: coaching/guidance only. No org seeds needed — house defaults apply to every
-- org with no row. Additive + idempotent. Run in the Supabase SQL editor.

CREATE TABLE IF NOT EXISTS commcalc.closing_entry_quality_config (
  org_id           UUID PRIMARY KEY,
  enabled          BOOLEAN,                  -- NULL = house default (true)
  threshold_days   INT,                      -- NULL = house default (2)
  signals          JSONB,                    -- NULL = house default (both signals)
  notify_channel   TEXT CHECK (notify_channel IS NULL OR notify_channel IN ('none','email','whatsapp','both')),
  message_template TEXT,
  tour_slug        TEXT,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS commcalc.closing_entry_coaching (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  employee_name  TEXT NOT NULL,
  streak_end     DATE NOT NULL,             -- the last incorrect day of the notified streak
  streak_days    INT NOT NULL,
  reasons        JSONB,                     -- {day: [signal, ...]}
  notified_via   TEXT,                      -- 'email' | 'whatsapp' | 'both' | 'inapp_only'
  message        TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, employee_name, streak_end)
);
CREATE INDEX IF NOT EXISTS closing_entry_coaching_day
  ON commcalc.closing_entry_coaching (org_id, streak_end);

-- RLS open_all to match the sibling closing tables (029/504/936 precedent).
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['closing_entry_quality_config','closing_entry_coaching']
  LOOP
    EXECUTE format('ALTER TABLE commcalc.%I ENABLE ROW LEVEL SECURITY', t);
    BEGIN
      EXECUTE format('CREATE POLICY open_all ON commcalc.%I FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXCEPTION WHEN OTHERS THEN NULL; END;
    EXECUTE format('GRANT ALL ON commcalc.%I TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 937 complete — closing entry-quality coaching (config + idempotency log)' AS status;

-- REVERT:
--   DROP TABLE IF EXISTS commcalc.closing_entry_coaching;
--   DROP TABLE IF EXISTS commcalc.closing_entry_quality_config;
--   (All reads are try/except-guarded: pre-937 the endpoints run on house defaults and the
--    run-due sweep records/sends nothing.)
