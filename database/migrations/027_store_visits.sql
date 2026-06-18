-- 027_store_visits.sql — DM (District Manager) store-visit + inspection checklist (Phase 1).
-- Run this in the Supabase SQL editor (Claude cannot run SQL).
--
-- A DM = the existing Market Manager role (scope: market). On a store visit they check in
-- (GPS + time), confirm the scheduled vs actual rep, run a management-configurable inspection
-- checklist, list accessories to order (vAccessorize), capture a "clean store" photo, and submit.
--
-- Tables (all in the storeops.* schema — PostgREST-exposed; the DM workflow is a StoreOps module):
--   storeops.checklist_items         — the CONFIGURABLE checklist template (management-editable)
--   storeops.store_visits            — one row per visit (header: check-in/out, GPS, reps, status)
--   storeops.store_visit_responses   — per-item answers for a visit (checkbox + note + photo path)
--   storeops.store_visit_accessories — accessories-to-order list captured during a visit
--
-- Photos live in the Supabase Storage bucket `store-visits` (the backend creates it on first
-- upload); only the storage PATH is stored here, served to the UI as a short-lived signed URL.

-- ── Checklist template (management-configurable; seeded with the spec defaults) ──────────
CREATE TABLE IF NOT EXISTS storeops.checklist_items (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  item_key    TEXT NOT NULL,                       -- stable key (e.g. 'uniform')
  label       TEXT NOT NULL,                       -- display text
  category    TEXT NOT NULL DEFAULT 'general',     -- appearance|facilities|security|supplies|accessories|general
  input_type  TEXT NOT NULL DEFAULT 'check',       -- check | text | photo
  sort_order  INT  NOT NULL DEFAULT 100,
  is_active   BOOLEAN NOT NULL DEFAULT true,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS checklist_items_org_key ON storeops.checklist_items(org_id, item_key);

-- ── Visit header ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.store_visits (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  store_code    TEXT,
  store_address TEXT,
  market        TEXT,
  dm_email      TEXT,
  dm_name       TEXT,
  check_in_at   TIMESTAMPTZ,
  check_out_at  TIMESTAMPTZ,
  check_in_lat  NUMERIC,
  check_in_lng  NUMERIC,
  check_in_accuracy NUMERIC,
  scheduled_rep TEXT,                              -- auto-loaded from storeops.shifts
  actual_rep    TEXT,                              -- rep actually present
  rep_discrepancy_reason TEXT,                     -- why the scheduled rep is not there
  clean_store_photo_path TEXT,                     -- storage path to the "clean store" photo
  extra_notes   TEXT,                              -- free text: any items to add
  status        TEXT NOT NULL DEFAULT 'in_progress', -- in_progress | submitted
  submitted_at  TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS store_visits_market   ON storeops.store_visits(org_id, market, check_in_at DESC);
CREATE INDEX IF NOT EXISTS store_visits_store    ON storeops.store_visits(org_id, store_code, check_in_at DESC);

-- ── Per-item answers ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.store_visit_responses (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  visit_id       UUID NOT NULL,
  item_key       TEXT,                             -- references checklist_items.item_key (or ad-hoc)
  label_snapshot TEXT,                             -- label at the time of the visit (template may change later)
  category_snapshot TEXT,                          -- category at the time of the visit (for grouping on the detail page)
  checked        BOOLEAN,
  note           TEXT,
  photo_path     TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS store_visit_responses_visit ON storeops.store_visit_responses(visit_id);

-- ── Accessories-to-order list ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.store_visit_accessories (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  visit_id       UUID NOT NULL,
  accessory_name TEXT NOT NULL,
  qty            INT  NOT NULL DEFAULT 1,
  note           TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS store_visit_accessories_visit ON storeops.store_visit_accessories(visit_id);

-- ── RLS: open_all (report-style tables; the backend uses the service key regardless) ──────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['checklist_items','store_visits','store_visit_responses','store_visit_accessories']
  LOOP
    EXECUTE format('ALTER TABLE storeops.%I ENABLE ROW LEVEL SECURITY', t);
    BEGIN
      EXECUTE format('CREATE POLICY open_all ON storeops.%I FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXCEPTION WHEN OTHERS THEN NULL; END;
    EXECUTE format('GRANT ALL ON storeops.%I TO anon, authenticated, service_role', t);
  END LOOP;
END $$;
GRANT USAGE ON SCHEMA storeops TO anon, authenticated;

-- ── Seed the default checklist items (idempotent) ────────────────────────────────────────
INSERT INTO storeops.checklist_items (org_id, item_key, label, category, input_type, sort_order) VALUES
 ('00000000-0000-0000-0000-000000000001','uniform','Uniform','appearance','check',10),
 ('00000000-0000-0000-0000-000000000001','lanyard','Lanyard / name card','appearance','check',20),
 ('00000000-0000-0000-0000-000000000001','broken_tiles','No broken tiles','facilities','check',30),
 ('00000000-0000-0000-0000-000000000001','hvac','HVAC working','facilities','check',40),
 ('00000000-0000-0000-0000-000000000001','counter_clean','Counter clean','facilities','check',50),
 ('00000000-0000-0000-0000-000000000001','floor_clean','Floor clean','facilities','check',60),
 ('00000000-0000-0000-0000-000000000001','windows_clean','Windows clean','facilities','check',70),
 ('00000000-0000-0000-0000-000000000001','alarm','Alarm working','security','check',80),
 ('00000000-0000-0000-0000-000000000001','cameras','Cameras working','security','check',90),
 ('00000000-0000-0000-0000-000000000001','safe','Safe','security','check',100),
 ('00000000-0000-0000-0000-000000000001','camera_on_safe','Camera pointed at safe','security','check',110),
 ('00000000-0000-0000-0000-000000000001','cc_machine','Credit card machine','facilities','check',120),
 ('00000000-0000-0000-0000-000000000001','water','Water in store','supplies','check',130),
 ('00000000-0000-0000-0000-000000000001','pens','Pens in store','supplies','check',140),
 ('00000000-0000-0000-0000-000000000001','currency_pen','Currency-checking pen in store','supplies','check',150),
 ('00000000-0000-0000-0000-000000000001','accessories_stocked','All accessories in store (list what is needed below)','accessories','check',160)
ON CONFLICT (org_id, item_key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 027 complete — storeops store-visit tables + default checklist seeded' AS status;
