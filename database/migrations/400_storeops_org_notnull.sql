-- ════════════════════════════════════════════════════════════════════
-- 400_storeops_org_notnull.sql — make the "vanishing rows" write-side gap
-- (fixed once already in 9668179 / 471c65e) STRUCTURALLY IMPOSSIBLE.
--
-- storeops.stores / employees / shifts / time_off_requests were created (003_storeops.sql)
-- with `org_id UUID` — nullable, no default. Every current INSERT into these 4 tables has
-- been audited (2026-07-14, mod-people) and already stamps org_id — see the insert-site
-- inventory in docs/handoffs/people.md. This migration closes the class of bug at the
-- SCHEMA level so a future insert that forgets to stamp org_id gets a loud 500 at write
-- time instead of a silently-vanishing row: org_id becomes NOT NULL (mirroring
-- storeops.timelog's pattern from 045_timeclock.sql), backed by an org_id-first index on
-- each table for the hot filters the routers actually use.
--
-- Deliberately NOT doing: a column DEFAULT on org_id. Per XM-5, a missing org_id must fail
-- loudly at insert time (an explicit 500), not silently default into the house org — a
-- default would hide exactly the bug class this migration exists to prevent.
--
-- Additive + idempotent. Safe to re-run: the backfill only touches remaining NULLs (no-op
-- once none are left), CREATE INDEX IF NOT EXISTS is a no-op on re-run, and the NOT NULL
-- ALTER is a no-op once already applied (Postgres allows re-issuing SET NOT NULL when the
-- column is already NOT NULL).
--
-- Run in the Supabase SQL editor (mod-people cannot run SQL — operator-run per contract §5).
--
-- ── PRE-RUN CHECK (paste this first — see docs/handoffs/people.md "mig 400" for the
--    expected-safe reading of this query before you run the migration below) ──────────
--   select 'storeops.stores' t, count(*) from storeops.stores where org_id is null
--   union all select 'storeops.employees', count(*) from storeops.employees where org_id is null
--   union all select 'storeops.shifts', count(*) from storeops.shifts where org_id is null
--   union all select 'storeops.time_off_requests', count(*) from storeops.time_off_requests where org_id is null;
-- ════════════════════════════════════════════════════════════════════


-- ── STEP 1 — guarded backfill ──────────────────────────────────────────────────────────
-- These are pre-multi-tenant rows: created before org_id existed / before every insert path
-- stamped it. '00000000-0000-0000-0000-000000000001' is the HOUSE org (see CLAUDE.md /
-- AGENT_CONTRACT.md — every row in this codebase predating multi-tenancy belongs to the
-- house org by construction). This is the ONE place a house-org literal is correct: it is
-- not a runtime default, not config, not a fallback a handler reaches for — it is a one-time
-- backfill of historical data that has nowhere else to belong. Do not copy this pattern into
-- application code (that is exactly the "hard-coded org" anti-pattern the contract forbids).
UPDATE storeops.stores             SET org_id = '00000000-0000-0000-0000-000000000001' WHERE org_id IS NULL;
UPDATE storeops.employees          SET org_id = '00000000-0000-0000-0000-000000000001' WHERE org_id IS NULL;
UPDATE storeops.shifts             SET org_id = '00000000-0000-0000-0000-000000000001' WHERE org_id IS NULL;
UPDATE storeops.time_off_requests  SET org_id = '00000000-0000-0000-0000-000000000001' WHERE org_id IS NULL;


-- ── STEP 2 — NOT NULL, with a loud/specific failure ────────────────────────────────────
-- Each table gets its own DO block: re-checks for NULLs immediately before the ALTER (in
-- case something inserted an unstamped row between step 1 and here) and RAISEs a message
-- naming the exact table + blocking row count, instead of letting a bare
-- "ALTER TABLE ... SET NOT NULL" fail with Postgres's generic constraint-violation error
-- that doesn't say which table it was validating.

DO $$
DECLARE bad_count BIGINT;
BEGIN
  SELECT count(*) INTO bad_count FROM storeops.stores WHERE org_id IS NULL;
  IF bad_count > 0 THEN
    RAISE EXCEPTION 'storeops.stores: % row(s) still have org_id IS NULL after backfill — NOT NULL blocked. Investigate those rows (which insert path skipped org_id?) before re-running 400_storeops_org_notnull.sql.', bad_count;
  END IF;
  ALTER TABLE storeops.stores ALTER COLUMN org_id SET NOT NULL;
END $$;

DO $$
DECLARE bad_count BIGINT;
BEGIN
  SELECT count(*) INTO bad_count FROM storeops.employees WHERE org_id IS NULL;
  IF bad_count > 0 THEN
    RAISE EXCEPTION 'storeops.employees: % row(s) still have org_id IS NULL after backfill — NOT NULL blocked. Investigate those rows (which insert path skipped org_id?) before re-running 400_storeops_org_notnull.sql.', bad_count;
  END IF;
  ALTER TABLE storeops.employees ALTER COLUMN org_id SET NOT NULL;
END $$;

DO $$
DECLARE bad_count BIGINT;
BEGIN
  SELECT count(*) INTO bad_count FROM storeops.shifts WHERE org_id IS NULL;
  IF bad_count > 0 THEN
    RAISE EXCEPTION 'storeops.shifts: % row(s) still have org_id IS NULL after backfill — NOT NULL blocked. Investigate those rows (which insert path skipped org_id?) before re-running 400_storeops_org_notnull.sql.', bad_count;
  END IF;
  ALTER TABLE storeops.shifts ALTER COLUMN org_id SET NOT NULL;
END $$;

DO $$
DECLARE bad_count BIGINT;
BEGIN
  SELECT count(*) INTO bad_count FROM storeops.time_off_requests WHERE org_id IS NULL;
  IF bad_count > 0 THEN
    RAISE EXCEPTION 'storeops.time_off_requests: % row(s) still have org_id IS NULL after backfill — NOT NULL blocked. Investigate those rows (which insert path skipped org_id?) before re-running 400_storeops_org_notnull.sql.', bad_count;
  END IF;
  ALTER TABLE storeops.time_off_requests ALTER COLUMN org_id SET NOT NULL;
END $$;


-- ── STEP 3 — org_id-first indexes on the hot filters the routers actually use ──────────
-- (mirrors storeops.timelog's (org_id, employee_id, work_date) / (org_id, employee_id)
-- pattern from 045_timeclock.sql). Each wrapped so a table/column that doesn't exist yet
-- in some environment is skipped instead of aborting the migration (030_perf_indexes.sql
-- pattern).

-- stores: GET /stores, GET /timeclock/stores select by org_id alone (order by address);
-- bulk-create + create/update dedupe-check and PATCH-by-code paths filter org_id+store_code.
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_storeops_stores_org        ON storeops.stores (org_id);              EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_storeops_stores_org_code   ON storeops.stores (org_id, store_code);  EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

-- employees: GET /employees, payroll, roster joins select by org_id alone (+ is_active);
-- shift-swap/name-map/onboarding lookups filter org_id+employee_id (the business key
-- shifts/time-off/payroll all join on).
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_storeops_employees_org         ON storeops.employees (org_id);                EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_storeops_employees_org_empid  ON storeops.employees (org_id, employee_id);   EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

-- shifts: GET /shifts, /payroll, template save/apply all filter org_id + is_deleted, usually
-- with a shift_date range and/or store_code; clock-in override + reassign/merge filter
-- org_id + employee_id. (030_perf_indexes.sql already added non-org (shift_date),
-- (store_code), (employee_name) indexes — these add the org_id-qualified versions so a
-- multi-tenant scan doesn't fall back to a full-table filter.)
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_storeops_shifts_org_date   ON storeops.shifts (org_id, shift_date) WHERE is_deleted = false; EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_storeops_shifts_org_store  ON storeops.shifts (org_id, store_code)  WHERE is_deleted = false; EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_storeops_shifts_org_emp    ON storeops.shifts (org_id, employee_id);                          EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

-- time_off_requests: GET /time-off filters org_id (+ optional employee_id); the
-- create-shift / apply-template conflict-check (org_id, employee_id, status=approved,
-- date range) runs on every shift write, so it's the hottest path on this table.
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_storeops_timeoff_org           ON storeops.time_off_requests (org_id);                     EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;
DO $$ BEGIN CREATE INDEX IF NOT EXISTS ix_storeops_timeoff_org_emp_stat  ON storeops.time_off_requests (org_id, employee_id, status); EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$;

ANALYZE storeops.stores;
ANALYZE storeops.employees;
ANALYZE storeops.shifts;
ANALYZE storeops.time_off_requests;
NOTIFY pgrst, 'reload schema';
SELECT 'Migration 400 complete — storeops.{stores,employees,shifts,time_off_requests}.org_id is NOT NULL + org_id-first indexes added' AS status;
