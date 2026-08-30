-- 305_store_manager_map.sql
-- mod-commission · band 200–299 spill → 305 (296–304 taken). Additive + idempotent + safe to re-run.
--
-- WHAT IT IS FOR (Chicago 3-tier commission, owner 2026-08-30). Chicago pays THREE sets of commission on
-- the same sales: the SALES REP (existing rep engine) plus the DISTRICT MANAGER and the MARKET MANAGER,
-- who are paid on the management-incentive framework (mig 852): store-performance components (e.g. an
-- override on the accessory sales their reps generate), goal attainment, and qualifier gates (cash
-- deposited on time, scheduled hours kept under target).
--
-- The management-incentive engine already computes all of that from a manager's store ROLL-UP — but it
-- had no way to know WHICH stores a given DM / market manager owns; the compute endpoint took the store
-- set from the request body. This table is that missing primitive: the per-tenant, CONFIG-DRIVEN map of
-- store -> (role -> manager). With it, computing a manager's incentive auto-resolves their store set (and
-- so their accessory-override actual, targets, and gates) instead of a human passing store codes in.
--
-- CONFIG, NEVER CODE: every carrier/market/manager difference lives in these rows, not a branch. A tenant
-- adds its own rows; nothing about "Chicago" is hard-coded.
--
-- Also extends the management_incentive_qualifier.source check to allow 'schedule_hours' — the scheduler-
-- fed gate ("scheduled hours under the target") the owner named as part of the DM/market-manager pay.
--
-- 💰 STANDALONE / ADDITIVE. No existing payout path reads this table; it only lets the management-incentive
-- compute resolve a manager's stores. Running it changes no money.

CREATE TABLE IF NOT EXISTS commcalc.store_manager (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  store_code    text NOT NULL,                 -- the store (matched case-insensitively to the sales store key)
  role          text NOT NULL,                 -- 'district_manager' | 'market_manager' | 'regional' | tenant-defined
  manager_name  text NOT NULL,                 -- the manager who owns this store for this role
  market        text,                          -- optional display / grouping
  is_active     boolean NOT NULL DEFAULT true,
  priority      integer NOT NULL DEFAULT 0,     -- tie-break when a store lists two managers for one role
  created_at    timestamptz DEFAULT now(),
  updated_at    timestamptz DEFAULT now(),
  UNIQUE (org_id, store_code, role, manager_name)
);
CREATE INDEX IF NOT EXISTS store_manager_by_manager
  ON commcalc.store_manager (org_id, role, manager_name);
CREATE INDEX IF NOT EXISTS store_manager_by_store
  ON commcalc.store_manager (org_id, store_code);

ALTER TABLE commcalc.store_manager ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                  AND tablename='store_manager' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.store_manager FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;
GRANT USAGE ON SCHEMA commcalc TO anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.store_manager TO anon, authenticated, service_role;

-- Extend the qualifier source enum to include the scheduler-fed gate. Idempotent: drop + re-add the check.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema='commcalc' AND table_name='management_incentive_qualifier') THEN
    ALTER TABLE commcalc.management_incentive_qualifier
      DROP CONSTRAINT IF EXISTS management_incentive_qualifier_source_check;
    ALTER TABLE commcalc.management_incentive_qualifier
      ADD CONSTRAINT management_incentive_qualifier_source_check
      CHECK (source IN ('kpi', 'cash_deposit', 'inventory', 'schedule_hours', 'manual'));
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
