-- 089_cash_management.sql — cash-management workstream: closing gate, alerts, deposit tracking
--
-- Adds: (E1) per-tenant daily-closing deadline + gate flag, and a per-store "assigned closer";
-- (E2) configurable alert recipients (auto-DM + named extras, email/WhatsApp) with a dedupe log;
-- (E3) a system "alert after N days if cash not picked up" setting; (E4) deposit-tracking columns
-- on cash_pickup (deposited vs handed to management, deposit slip + OCR + match vs declared cash).
--
-- SAFE: additive + idempotent. Nothing enforces until a tenant enables the gate / sets recipients.

-- ── E1: closing gate + cash-aging settings live on the tenant (defined at onboarding) ────────────
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS closing_deadline      TEXT,               -- "HH:MM" business-local daily cutoff
  ADD COLUMN IF NOT EXISTS closing_gate_enabled  BOOLEAN DEFAULT false, -- block the closer's clock-out until closing in
  ADD COLUMN IF NOT EXISTS cash_alert_after_days INT DEFAULT 2;       -- alert if cash unpicked this many days

-- The assigned closer/manager per store — only THEY are blocked from clocking out until the store
-- closing is submitted (the product decision). One closer per store.
CREATE TABLE IF NOT EXISTS storeops.store_closer (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  store_code    TEXT NOT NULL,
  employee_id   TEXT,
  employee_name TEXT,
  updated_at    TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, store_code)
);

-- ── E2: alert recipients — auto-DM is resolved in code; these are the NAMED EXTRAS per alert scope ─
CREATE TABLE IF NOT EXISTS storeops.alert_recipient (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  scope          TEXT NOT NULL,          -- 'closing_missing' | 'cash_unpicked' | 'deposit_mismatch' | 'all'
  name           TEXT,
  email          TEXT,
  whatsapp       TEXT,                   -- 10-digit or +country-code
  via_email      BOOLEAN DEFAULT true,
  via_whatsapp   BOOLEAN DEFAULT false,
  include_dm     BOOLEAN DEFAULT true,   -- also auto-resolve + alert the store's District Manager
  created_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS alert_recipient_scope ON storeops.alert_recipient (org_id, scope);

-- Dedupe: one alert per (scope, ref_key) e.g. 'store|date' so a cron doesn't re-alert every tick.
CREATE TABLE IF NOT EXISTS storeops.alert_log (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  scope      TEXT NOT NULL,
  ref_key    TEXT NOT NULL,
  sent_at    TIMESTAMPTZ DEFAULT NOW(),
  recipients TEXT,
  detail     JSONB,
  UNIQUE (org_id, scope, ref_key)
);

-- ── E4: deposit tracking on the existing cash_pickup row ─────────────────────────────────────────
ALTER TABLE commcalc.cash_pickup
  ADD COLUMN IF NOT EXISTS dm_employee_id    TEXT,     -- the DM who collected (for the DM filter)
  ADD COLUMN IF NOT EXISTS disposition       TEXT,     -- 'deposited' | 'handed_to_mgmt'
  ADD COLUMN IF NOT EXISTS handed_to         TEXT,     -- who it was handed to (management)
  ADD COLUMN IF NOT EXISTS deposit_slip_path TEXT,     -- storage path of the deposit-slip photo
  ADD COLUMN IF NOT EXISTS deposit_amount    NUMERIC,  -- amount read/entered from the slip
  ADD COLUMN IF NOT EXISTS declared_amount   NUMERIC,  -- system-declared cash it should match
  ADD COLUMN IF NOT EXISTS deposit_ocr       JSONB,    -- raw OCR result (audit)
  ADD COLUMN IF NOT EXISTS deposit_matched   BOOLEAN,  -- amounts reconcile within tolerance
  ADD COLUMN IF NOT EXISTS deposit_flagged   BOOLEAN DEFAULT false,  -- mismatch → review
  ADD COLUMN IF NOT EXISTS deposit_note      TEXT,
  ADD COLUMN IF NOT EXISTS deposited_at      TIMESTAMPTZ;

-- RLS open_all to match sibling tables.
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['storeops.store_closer','storeops.alert_recipient','storeops.alert_log'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 089 complete — cash-management (closing gate, alerts, deposit tracking)' AS status;
