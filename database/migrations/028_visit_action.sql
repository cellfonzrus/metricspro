-- 028_visit_action.sql — DM store-visit Phase 2: action-item rollup + rep action plan + sign-off.
-- Run this in the Supabase SQL editor (Claude cannot run SQL). Builds on migration 027.
--
-- During a store visit the DM sees each store's auto-generated action items (DLAR/sales focus areas
-- from the existing /commcalc/targets/{period}/action-plan engine), checks off the ones discussed,
-- comments + (optionally) uploads proof, then agrees a rep action plan (items with due dates) that
-- both the rep and the DM sign off. The printed+signed checklist scan is uploaded to Storage.
--
-- Tables:
--   storeops.visit_action_items — the DM's discussion overlay on each rolled-up action item
--   storeops.visit_action_plan  — the agreed rep plan items (description + due date + status)
-- Plus new sign-off / signed-checklist columns on storeops.store_visits.

-- ── DM discussion overlay on rolled-up action items ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.visit_action_items (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL,
  visit_id         UUID NOT NULL,
  item_key         TEXT NOT NULL,        -- stable id: `${rep||'__store__'}::${metric}::${title}`
  rep              TEXT,                 -- null = store-level item
  severity         TEXT,                 -- critical | warning | good
  metric           TEXT,
  title            TEXT,
  detail           TEXT,                 -- snapshot of the action-item text at visit time
  discussed        BOOLEAN DEFAULT false,
  comment          TEXT,
  proof_photo_path TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS visit_action_items_visit ON storeops.visit_action_items(visit_id);

-- ── Agreed rep action plan (signed-off summary, per-item due dates) ───────────────────────
CREATE TABLE IF NOT EXISTS storeops.visit_action_plan (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  visit_id     UUID NOT NULL,
  store_code   TEXT,
  rep          TEXT,
  description  TEXT NOT NULL,
  due_date     DATE,
  status       TEXT NOT NULL DEFAULT 'open',   -- open | done
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS visit_action_plan_visit ON storeops.visit_action_plan(visit_id);

-- ── Sign-off + signed-checklist columns on the visit header ───────────────────────────────
ALTER TABLE storeops.store_visits
  ADD COLUMN IF NOT EXISTS plan_rep_signed     BOOLEAN DEFAULT false,
  ADD COLUMN IF NOT EXISTS plan_rep_signed_by  TEXT,
  ADD COLUMN IF NOT EXISTS plan_rep_signed_at  TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS plan_dm_signed      BOOLEAN DEFAULT false,
  ADD COLUMN IF NOT EXISTS plan_dm_signed_by   TEXT,
  ADD COLUMN IF NOT EXISTS plan_dm_signed_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS signed_checklist_path TEXT;

-- ── RLS: open_all (report-style; backend uses the service key regardless) ─────────────────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['visit_action_items','visit_action_plan']
  LOOP
    EXECUTE format('ALTER TABLE storeops.%I ENABLE ROW LEVEL SECURITY', t);
    BEGIN
      EXECUTE format('CREATE POLICY open_all ON storeops.%I FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXCEPTION WHEN OTHERS THEN NULL; END;
    EXECUTE format('GRANT ALL ON storeops.%I TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 028 complete — visit action-items + action plan + sign-off columns' AS status;
