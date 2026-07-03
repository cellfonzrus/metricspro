-- 087_hours_budget.sql — per-store weekly scheduling HOURS BUDGET + DM-approved overrides
--
-- WHY: each store manager gets a budget of labor hours to schedule per week. The scheduler must
-- STOP a manager from scheduling beyond the store's weekly budget and alert them; to exceed it they
-- request District Manager approval, and the DM's in-app tick (a workflow, not an email approval) is
-- recorded — the moment the DM approves, the week is unlocked. "Week" = the tenant work-week
-- (mig 085 work_week_start_dow), 7 days.
--
-- SAFE: additive + idempotent. With no budget row for a store, nothing is enforced (today's behavior).

-- The standing weekly budget per store (applies every week; a manager/admin sets it).
CREATE TABLE IF NOT EXISTS storeops.hours_budget (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  store_code   TEXT NOT NULL,
  weekly_hours NUMERIC NOT NULL DEFAULT 0,
  updated_by   TEXT,
  updated_at   TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, store_code)
);

-- A DM-approved authorization for ONE store + ONE work-week to schedule past the budget.
CREATE TABLE IF NOT EXISTS storeops.budget_override (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  store_code        TEXT NOT NULL,
  week_start        DATE NOT NULL,        -- the work-week start this override unlocks
  approved_hours    NUMERIC,              -- optional new ceiling (NULL = simply "may exceed")
  reason            TEXT,
  status            TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | denied
  requested_by      TEXT, requested_by_name TEXT, requested_at TIMESTAMPTZ DEFAULT NOW(),
  dm_employee_id    TEXT, dm_email TEXT,
  decided_by        TEXT, decided_by_name TEXT, decided_at TIMESTAMPTZ, decision_note TEXT,
  created_at        TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS budget_override_lookup ON storeops.budget_override (org_id, store_code, week_start, status);

ALTER TABLE storeops.hours_budget    ENABLE ROW LEVEL SECURITY;
ALTER TABLE storeops.budget_override ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS open_all ON storeops.hours_budget;
DROP POLICY IF EXISTS open_all ON storeops.budget_override;
CREATE POLICY open_all ON storeops.hours_budget    FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
CREATE POLICY open_all ON storeops.budget_override FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
GRANT ALL ON storeops.hours_budget, storeops.budget_override TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 087 complete — storeops.hours_budget + budget_override' AS status;
