-- 408_hr_letters.sql — HR Letter / Template-Library system (owner directive 2026-07-26).
--
-- WHY: automatic + manual disciplinary/shortage/performance letters (late clock-in strikes,
-- cash/inventory/accessory shortage, KPI/commission communication), each editable as a per-org
-- template with merge fields, delivery mode (auto-send vs queue-for-approval), and a full sent-log
-- audit trail visible on the employee's HR record. Every table is additive/new — no existing table
-- is touched except one additive JSONB config column on storeops.tenants (same idiom as
-- twofa_policy / notify_policy / money_guard_config already there).
--
-- SAFE: additive + idempotent (CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS). Nothing
-- breaks until this runs — every new endpoint in backend/app/modules/hr/letters.py try/excepts the
-- table lookups and returns a clear "run migration 408" 400/empty-list rather than a 500, and the
-- app has ZERO other code path that reads/writes these tables (a brand new feature, not a refactor
-- of anything live).

-- ── Template library (per-org, editable; a category can have >1 tier, e.g. late_clockin 1/3/5) ──
CREATE TABLE IF NOT EXISTS storeops.letter_template (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  template_key    TEXT NOT NULL,          -- stable key, e.g. 'late_clockin_tier1', 'cash_shortage'
  category        TEXT NOT NULL,          -- late_clockin | cash_shortage | inventory_shortage |
                                          -- accessory_shortfall | kpi_miss | commission_statement |
                                          -- metrics_miss_2consec (extensible — no CHECK constraint)
  escalation_tier INT,                    -- 1 | 3 | 5 for late_clockin; NULL otherwise
  label           TEXT,                   -- display name for the admin UI / picker
  subject         TEXT NOT NULL,
  body            TEXT NOT NULL,          -- merge-field template; tokens like {{employee_name}}
  delivery_mode   TEXT NOT NULL DEFAULT 'approval',  -- 'auto' | 'approval'
  active          BOOLEAN NOT NULL DEFAULT true,
  is_default      BOOLEAN NOT NULL DEFAULT true,      -- false once an org edits subject/body/mode
                                          -- (a future re-seed of the DEFAULT text never clobbers
                                          -- an org's own edit — only ever inserts what's missing)
  updated_by      TEXT,
  updated_at      TIMESTAMPTZ DEFAULT now(),
  created_at      TIMESTAMPTZ DEFAULT now(),
  UNIQUE (org_id, template_key)
);
CREATE INDEX IF NOT EXISTS letter_template_org_cat ON storeops.letter_template (org_id, category);

-- ── Sent-letters audit log (every auto OR approved send, plus the approval queue itself) ─────────
CREATE TABLE IF NOT EXISTS storeops.sent_letter (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL,
  employee_id      TEXT,
  employee_name    TEXT,
  employee_email   TEXT,
  template_key     TEXT,
  category         TEXT,
  escalation_tier  INT,
  subject          TEXT,
  body             TEXT,                 -- rendered (merge fields already substituted)
  merge_data       JSONB DEFAULT '{}'::jsonb,   -- snapshot of every merge value used (audit)
  incident_date    DATE,
  period           TEXT,                 -- for kpi_miss / commission_statement / metrics_miss_2consec
  delivery_mode    TEXT,                 -- snapshot of the template's mode at send/queue time
  status           TEXT NOT NULL DEFAULT 'sent',  -- sent | queued_approval | approved_sent | rejected | failed
  trigger          TEXT NOT NULL DEFAULT 'manual', -- manual | auto
  sender           TEXT,                 -- HR/admin email, or 'system' for an auto-send
  approved_by      TEXT,
  approved_at      TIMESTAMPTZ,
  rejected_reason  TEXT,
  send_error       TEXT,
  dedupe_key       TEXT,                 -- automation idempotency key; NULL for manual sends
  created_at       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sent_letter_org_emp   ON storeops.sent_letter (org_id, employee_id);
CREATE INDEX IF NOT EXISTS sent_letter_org_recent ON storeops.sent_letter (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS sent_letter_org_status ON storeops.sent_letter (org_id, status);
-- One automated letter per idempotency key (re-running a sweep for the same day/period is a no-op).
CREATE UNIQUE INDEX IF NOT EXISTS sent_letter_dedupe_uq
  ON storeops.sent_letter (org_id, dedupe_key) WHERE dedupe_key IS NOT NULL;

-- ── Late clock-in strike ledger (one row per employee per scheduled work_date they were late) ────
CREATE TABLE IF NOT EXISTS storeops.late_clockin_strike (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  employee_id     TEXT NOT NULL,
  employee_name   TEXT,
  store_code      TEXT,
  work_date       DATE NOT NULL,
  scheduled_start TEXT,                  -- "HH:MM" business-local, from the scheduled shift
  grace_minutes   INT,
  first_punch_at  TIMESTAMPTZ,           -- earliest punch that day (multi-session safe)
  minutes_late    INT,
  strike_number   INT,                   -- cumulative count within the rolling window AS OF this day
  tier            INT,                   -- 1 | 3 | 5 — the template tier chosen for this occurrence
  sent_letter_id  UUID REFERENCES storeops.sent_letter(id),
  created_at      TIMESTAMPTZ DEFAULT now(),
  UNIQUE (org_id, employee_id, work_date)
);
CREATE INDEX IF NOT EXISTS late_strike_org_date ON storeops.late_clockin_strike (org_id, work_date);
CREATE INDEX IF NOT EXISTS late_strike_org_emp  ON storeops.late_clockin_strike (org_id, employee_id, work_date DESC);

-- ── Per-tenant automation config (JSONB knob-bag — same idiom as twofa_policy/notify_policy) ─────
-- Keys used (all optional, code defaults apply when absent/un-run):
--   late_clockin: {enabled: bool (default false), grace_minutes: int (default 5, clamp 0-60),
--                  strike_window_days: int (default 90, clamp 1-365)}
--   metrics_miss: {enabled: bool (default false)}   -- monthly 2-consecutive-month KPI-miss check
ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS hr_letters_config JSONB DEFAULT '{}'::jsonb;

-- RLS open_all (service-role backend is the real guard, matching every sibling table).
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['storeops.letter_template','storeops.sent_letter','storeops.late_clockin_strike'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 408 complete — HR letter template library + sent-letters log + late-clockin strikes' AS status;
