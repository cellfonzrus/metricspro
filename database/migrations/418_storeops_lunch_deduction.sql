-- 418_storeops_lunch_deduction.sql — mod-people band 400-499.
--
-- Owner directive 2026-07-27 (verbatim, Deliverable 3): "there should be option to deduct the lunch
-- break for all by 30 minutes by default but configurable and assigned to each user as needed, it is
-- a user based provision universally for all tenants."
--
-- RULE TWO (SAP-configurable): a tenant-level DEFAULT + a per-employee OVERRIDE, no hard-coding.
-- OWNER'S STATED DEFAULT (built exactly as instructed): every tenant starts with the feature ON,
-- 30 minutes, applied to shifts of 6+ hours — adjustable per tenant, and per employee (including
-- fully disabling it for one person).
--
--   storeops.tenants:
--     lunch_deduction_enabled          boolean  NOT NULL DEFAULT true   -- tenant-wide default
--     lunch_deduction_minutes          integer  NOT NULL DEFAULT 30     -- tenant-wide default
--     lunch_deduction_min_shift_hours  numeric  NOT NULL DEFAULT 6      -- below this, never deduct
--
--   storeops.employees (per-employee OVERRIDE — NULL means "inherit the tenant default"):
--     lunch_deduction_enabled          boolean  NULL
--     lunch_deduction_minutes          integer  NULL
--
-- Precedence (backend/app/modules/storeops/lunch_deduction.py `resolve_employee_lunch_settings`):
-- a non-NULL per-employee value wins over the tenant default for `enabled`/`minutes` independently;
-- `min_shift_hours` is tenant-only (no per-employee override was requested).
--
-- DOUBLE-DEDUCTION GUARD (see lunch_deduction.py for the executable rule + harness proof): a day is
-- only eligible when its CLOSED timelog punches form ONE continuous block (a single pair, or multiple
-- pairs separated by a gap of ≤ 1 minute — a system artifact, not a real break) whose total hours meet
-- `min_shift_hours`. Any REAL gap between punch-pairs that day (a genuine lunch re-clock-in, or a true
-- split shift — the two are indistinguishable from punch data alone, and BOTH already contain real
-- unpaid off-the-clock time, so this migration's deduction never applies on top of either) skips the
-- day entirely — no auto-deduction, hours stand as clocked. A day with any still-OPEN punch is also
-- skipped until it closes (can't yet know whether a second, gapped punch-pair is coming).
--
-- WHAT BREAKS UNTIL THIS RUNS: nothing — every reader in lunch_deduction.py wraps its
-- storeops.tenants / storeops.employees column reads in try/except and returns `available=False`
-- (zero deduction, byte-identical to today) whenever these columns don't exist yet. Merging the
-- application code that reads/writes these columns does NOT start deducting anything in prod — only
-- actually RUNNING this migration does (the owner controls exactly when the default-ON blast radius
-- takes effect; see docs/handoffs/people.md for the blast-radius SQL to run FIRST).
--
-- WHAT IT ENABLES ONCE RUN: the tenant-default + per-employee lunch settings (Time Clock page's
-- ⚙ Lunch Break Settings panel + the HR "Employees & Pay" tab's per-row override) start taking effect,
-- and GET /storeops/payroll, /payroll-by-store, /payroll/actual-hours-detail and /timeclock/list start
-- showing the explicit "− 0:30 lunch (auto)" line wherever a day qualifies.
--
-- No new table → no new GRANT/RLS needed (same posture as every other column added to these two
-- already-granted tables, e.g. migration 409's storeops.tenants.timeoff_conflict_mode).
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS lunch_deduction_enabled boolean NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS lunch_deduction_minutes integer NOT NULL DEFAULT 30,
  ADD COLUMN IF NOT EXISTS lunch_deduction_min_shift_hours numeric NOT NULL DEFAULT 6;

ALTER TABLE storeops.employees
  ADD COLUMN IF NOT EXISTS lunch_deduction_enabled boolean NULL,
  ADD COLUMN IF NOT EXISTS lunch_deduction_minutes integer NULL;

-- Idempotent sanity guards (mirrors migration 409's CHECK-constraint-if-absent pattern).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tenants_lunch_deduction_minutes_chk') THEN
    ALTER TABLE storeops.tenants
      ADD CONSTRAINT tenants_lunch_deduction_minutes_chk CHECK (lunch_deduction_minutes >= 0);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tenants_lunch_deduction_min_shift_hours_chk') THEN
    ALTER TABLE storeops.tenants
      ADD CONSTRAINT tenants_lunch_deduction_min_shift_hours_chk CHECK (lunch_deduction_min_shift_hours >= 0);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'employees_lunch_deduction_minutes_chk') THEN
    ALTER TABLE storeops.employees
      ADD CONSTRAINT employees_lunch_deduction_minutes_chk CHECK (lunch_deduction_minutes IS NULL OR lunch_deduction_minutes >= 0);
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 418 complete — storeops.tenants + storeops.employees lunch-deduction config (default ON, 30 min, 6h threshold, per-employee override)' AS status;
