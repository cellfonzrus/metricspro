-- 088_cross_tenant_identity.sql — let one person work in multiple tenants with a distinct login each
--
-- WHY: a person (e.g. Yaseer) can work for more than one tenant, but each tenant must be a SEPARATE
-- login and separate employee record (isolated clock-in/pay). Two things blocked that:
--   1. employees.employee_id was GLOBALLY unique (mig 003) → the same business id couldn't exist in
--      two orgs. Make it unique PER ORG instead.
--   2. app_users.auth_id is UNIQUE (one login = one tenant), so the second tenant needs its own login
--      under a DISTINCT email — the backend now auto-mints a tenant-aliased login (local+slug@domain)
--      when the person already has a login elsewhere (see core.create_login). No schema change needed
--      for that; this migration just frees employee_id.
--
-- SAFE: additive/idempotent. Per-org uniqueness is WEAKER than global, so no existing row violates it.

-- 1) Drop the global unique on employee_id (auto-named constraint from `employee_id TEXT UNIQUE`).
ALTER TABLE storeops.employees DROP CONSTRAINT IF EXISTS employees_employee_id_key;
-- Belt-and-suspenders: drop any other unique index solely on employee_id, whatever it's named.
DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN
    SELECT c.conname FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'storeops' AND t.relname = 'employees' AND c.contype = 'u'
      AND (SELECT array_agg(a.attname) FROM unnest(c.conkey) k JOIN pg_attribute a
             ON a.attrelid = c.conrelid AND a.attnum = k) = ARRAY['employee_id']
  LOOP
    EXECUTE format('ALTER TABLE storeops.employees DROP CONSTRAINT %I', r.conname);
  END LOOP;
END $$;

-- 2) Add per-(org, employee_id) uniqueness (NULL employee_ids are allowed and don't collide).
CREATE UNIQUE INDEX IF NOT EXISTS employees_org_empid_uidx
  ON storeops.employees (org_id, employee_id) WHERE employee_id IS NOT NULL;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 088 complete — employees.employee_id is now unique PER ORG' AS status;
