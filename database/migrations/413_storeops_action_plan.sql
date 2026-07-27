-- 413_storeops_action_plan.sql — generic Action-Plan engine (Phase 1: google_reviews area only).
-- mod-people, band 400-499. Owner directive 2026-07-27: "the action plan areas will be extended to
-- other areas of KPI achievement... this will evolve as we go along" — so the AREA is a config-table
-- registry (RULE TWO), not a hard-coded enum, even though only 'google_reviews' is wired in Phase 1.
--
-- storeops.action_plan_area — the registry of area keys a tenant has turned on (per-org, so a
-- tenant can disable an area without a code change once more areas exist).
CREATE TABLE IF NOT EXISTS storeops.action_plan_area (
  id          BIGSERIAL PRIMARY KEY,
  org_id      UUID NOT NULL,
  area_key    TEXT NOT NULL,
  label       TEXT NOT NULL,
  enabled     BOOLEAN NOT NULL DEFAULT true,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_action_plan_area_org_key
  ON storeops.action_plan_area (org_id, area_key);

-- storeops.action_plan — one row per (employee, store, area) IMPROVEMENT CYCLE. State machine:
--   required     -> system-detected trigger (e.g. store rating below target) — no plan yet.
--   submitted    -> employee submitted plan_text; awaiting DM review.
--   pushed_back  -> DM reviewed and sent it back with dm_comments + a due_date.
--   in_progress  -> employee has started (set automatically once pushed_back is acknowledged, or
--                   directly by a DM approving the plan as-is); employee_marked_done_at is set when
--                   the employee says the work is done, but status STAYS in_progress until —
--   completed    -> the DM confirms (dm-confirm-complete). Terminal.
-- Only ONE cycle may be OPEN (status <> 'completed') at a time per (employee, store, area) — see the
-- partial unique index below — so a fresh trigger after a prior cycle completed starts a new row,
-- never collides with history.
CREATE TABLE IF NOT EXISTS storeops.action_plan (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   UUID NOT NULL,
  employee_id              TEXT NOT NULL,
  employee_name            TEXT,
  store_code               TEXT NOT NULL,
  area_key                 TEXT NOT NULL DEFAULT 'google_reviews',
  status                   TEXT NOT NULL DEFAULT 'required'
                             CHECK (status IN ('required', 'submitted', 'pushed_back', 'in_progress', 'completed')),
  trigger_detail           TEXT,        -- audit context, e.g. "Store rating 4.3 vs target 4.7 on 2026-07-27"
  plan_text                TEXT,
  dm_comments              TEXT,
  due_date                 DATE,
  submitted_at             TIMESTAMPTZ,
  reviewed_at              TIMESTAMPTZ,
  reviewed_by              TEXT,
  employee_marked_done_at  TIMESTAMPTZ,
  completed_at             TIMESTAMPTZ,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_action_plan_open_cycle
  ON storeops.action_plan (org_id, employee_id, store_code, area_key) WHERE status <> 'completed';
CREATE INDEX IF NOT EXISTS ix_action_plan_org_status ON storeops.action_plan (org_id, status);
CREATE INDEX IF NOT EXISTS ix_action_plan_org_store  ON storeops.action_plan (org_id, store_code);
CREATE INDEX IF NOT EXISTS ix_action_plan_org_emp     ON storeops.action_plan (org_id, employee_id);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['storeops.action_plan_area', 'storeops.action_plan'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

-- Seed the 'google_reviews' area, enabled, for every existing tenant (idempotent).
CREATE OR REPLACE FUNCTION storeops.seed_action_plan_area(p_org uuid)
RETURNS void LANGUAGE plpgsql AS $fn$
BEGIN
  INSERT INTO storeops.action_plan_area (org_id, area_key, label, enabled)
  VALUES (p_org, 'google_reviews', 'Google Reviews', true)
  ON CONFLICT (org_id, area_key) DO NOTHING;
END;
$fn$;
GRANT EXECUTE ON FUNCTION storeops.seed_action_plan_area(uuid) TO anon, authenticated, service_role;

DO $seed$
DECLARE t record;
BEGIN
  PERFORM storeops.seed_action_plan_area('00000000-0000-0000-0000-000000000001');
  BEGIN
    FOR t IN SELECT org_id FROM storeops.tenants LOOP
      PERFORM storeops.seed_action_plan_area(t.org_id);
    END LOOP;
  EXCEPTION WHEN undefined_table THEN
    NULL;
  END;
END $seed$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 413 complete — storeops.action_plan_area + storeops.action_plan' AS status;
