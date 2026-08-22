-- 908_pricing_and_trial.sql — PUBLIC price list + the 30-day free trial.
--
-- WHY: MetricsPro is sold as SaaS, but until now there was no price the OPERATOR could set and no
-- trial a new company could start on. Billing (mig 064) prices ONE tenant at a time, privately, after
-- a deal is already closed — it is an invoicing tool, not a storefront. This migration adds the two
-- things a SaaS front door needs:
--
--   1. storeops.pricing_package   — the published price list the marketing site (/welcome) reads.
--                                   Prices are DATA, set from the back office (/admin/pricing), never
--                                   hardcoded in the website.
--   2. storeops.pricing_settings  — one row of platform-wide knobs; chiefly trial_days (default 30).
--
-- ...plus the trial stamp on each tenant (trial_started_at / trial_ends_at / plan_status).
--
-- NOTHING IS PUBLISHED BY DEFAULT. The three seeded packages are placeholders with price 0 and
-- is_public = false: the operator sets real prices and ticks "publish" before anything reaches the
-- public page. A price the operator did not choose must never appear on the internet.
--
-- NO EXISTING TENANT IS PUT ON A TRIAL. Every tenant that exists when this runs is backfilled to
-- plan_status = 'active' — they are live customers, not trials. Only tenants provisioned AFTER this
-- migration get a trial stamp, and only from the signup path.
--
-- Additive + idempotent. RLS open_all (backend service role is the real guard, like the rest of storeops.*).

-- ── pricing_package: one row per publicly-listed package ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.pricing_package (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  key          TEXT NOT NULL,                    -- stable slug (starter | growth | enterprise | …)
  name         TEXT NOT NULL,                    -- display name on the card
  tagline      TEXT,                             -- one line under the name
  price        NUMERIC DEFAULT 0,                -- 0 + price_note = "Custom" / "Talk to us"
  cycle        TEXT NOT NULL DEFAULT 'monthly',  -- monthly | annual
  currency     TEXT DEFAULT 'USD',
  unit_label   TEXT,                             -- e.g. 'per store / month' — what the price BUYS
  price_note   TEXT,                             -- e.g. 'billed annually', 'from'
  features     TEXT[],                           -- short bullets; the site shows them as-is
  cta_label    TEXT,                             -- overrides the default button text
  is_featured  BOOLEAN DEFAULT false,            -- one card gets the highlighted treatment
  is_public    BOOLEAN DEFAULT false,            -- ⚠️ DEFAULT OFF — nothing goes live unpublished
  sort_order   INT DEFAULT 0,
  notes        TEXT,                             -- internal only; never served publicly
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (key)
);
CREATE INDEX IF NOT EXISTS pricing_package_public ON storeops.pricing_package (is_public, sort_order);

-- ── pricing_settings: SINGLETON (id is pinned to 1 by the CHECK) ──────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.pricing_settings (
  id                INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  trial_enabled     BOOLEAN NOT NULL DEFAULT true,
  trial_days        INT NOT NULL DEFAULT 30,     -- the free-trial length, editable from /admin/pricing
  currency          TEXT DEFAULT 'USD',
  show_pricing      BOOLEAN NOT NULL DEFAULT true,   -- master switch for the public pricing section
  pricing_headline  TEXT,
  pricing_subhead   TEXT,
  trial_note        TEXT,                            -- e.g. 'No card required.'
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO storeops.pricing_settings (id, trial_days, pricing_headline, pricing_subhead, trial_note)
  VALUES (1, 30, 'Start free for 30 days.',
          'Every module, every store, for the length of the trial. Set your own pace after that.',
          'No card required.')
  ON CONFLICT (id) DO NOTHING;

-- ── placeholder packages (UNPUBLISHED — price 0 until the operator sets the real ones) ────────
INSERT INTO storeops.pricing_package (key, name, tagline, unit_label, features, sort_order, is_featured)
VALUES
  ('starter', 'Starter', 'For a single store finding its feet.', 'per store / month',
   ARRAY['Commission intelligence', 'Point of sale', 'Daily closing'], 10, false),
  ('growth', 'Growth', 'For a multi-store operator running a market.', 'per store / month',
   ARRAY['Everything in Starter', 'Workforce, payroll & HR', 'Inventory, finance & reporting'], 20, true),
  ('enterprise', 'Enterprise', 'For dealers running several companies at once.', NULL,
   ARRAY['Everything in Growth', 'Multi-company & multi-market', 'Onboarding and priority support'], 30, false)
ON CONFLICT (key) DO NOTHING;

-- ── trial stamp on the tenant ─────────────────────────────────────────────────────────────────
-- plan_status: trialing | active | trial_expired | cancelled. Intentionally NO column default —
-- the trial is stamped by _provision_tenant() at signup, so a row created by any other path is not
-- silently declared a trial.
ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS trial_started_at TIMESTAMPTZ;
ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS trial_ends_at    TIMESTAMPTZ;
ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS plan_status      TEXT;
ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS package_key      TEXT;

-- Backfill: everyone who already exists is a live customer, NOT a trial.
UPDATE storeops.tenants SET plan_status = 'active' WHERE plan_status IS NULL;

-- ── RLS open_all (service role bypasses; matches the rest of storeops.*) ──────────────────────
ALTER TABLE storeops.pricing_package  ENABLE ROW LEVEL SECURITY;
ALTER TABLE storeops.pricing_settings ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='storeops' AND tablename='pricing_package' AND policyname='open_all') THEN
    CREATE POLICY open_all ON storeops.pricing_package FOR ALL TO anon, authenticated USING (true) WITH CHECK (true); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='storeops' AND tablename='pricing_settings' AND policyname='open_all') THEN
    CREATE POLICY open_all ON storeops.pricing_settings FOR ALL TO anon, authenticated USING (true) WITH CHECK (true); END IF;
END $$;
GRANT ALL ON storeops.pricing_package  TO anon, authenticated, service_role;
GRANT ALL ON storeops.pricing_settings TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 908 complete — storeops.pricing_package + storeops.pricing_settings + tenant trial columns' AS status;
