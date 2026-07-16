-- 406_storeops_store_code_per_org.sql — let a 2nd tenant reuse a store_code another tenant already used
--
-- WHY (luxelink-parity audit, 2026-07-16): storeops.stores.store_code was declared GLOBALLY UNIQUE
-- (003_storeops.sql:11) — the exact same bug mig 088 fixed for employees.employee_id, but stores was
-- never given the equivalent fix (flagged OPEN in docs/handoffs/people.md but not yet actioned). A
-- second tenant (e.g. luxelink) creating a store whose code collides with ANY other tenant's store
-- code — including something as generic as the bulk-upload template's own example "STORE01" — gets a
-- 500 on POST /storeops/stores or /storeops/stores/bulk. Every OTHER per-tenant identifier in this
-- schema (employees.employee_id, mig 088) is already scoped per-org; store_code was the one gap left.
--
-- SAFE: additive/idempotent. Per-org uniqueness is STRICTLY WEAKER than global uniqueness, so no
-- existing row can violate it — this can only ever turn a false 500 into a successful insert, never
-- the reverse. Mirrors mig 088's pattern exactly.

-- 1) Drop the global unique on store_code (auto-named constraint from `store_code TEXT UNIQUE`).
ALTER TABLE storeops.stores DROP CONSTRAINT IF EXISTS stores_store_code_key;
-- Belt-and-suspenders: drop any other unique constraint solely on store_code, whatever it's named.
DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN
    SELECT c.conname FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'storeops' AND t.relname = 'stores' AND c.contype = 'u'
      AND (SELECT array_agg(a.attname::text) FROM unnest(c.conkey) k JOIN pg_attribute a
             ON a.attrelid = c.conrelid AND a.attnum = k) = ARRAY['store_code']::text[]
  LOOP
    EXECUTE format('ALTER TABLE storeops.stores DROP CONSTRAINT %I', r.conname);
  END LOOP;
END $$;

-- 2) Add per-(org, store_code) uniqueness (NULL store_codes are allowed and don't collide; the app
-- already rejects a blank store_code on create, so this only guards genuine duplicates per tenant).
CREATE UNIQUE INDEX IF NOT EXISTS stores_org_code_uidx
  ON storeops.stores (org_id, store_code) WHERE store_code IS NOT NULL;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 406 complete — storeops.stores.store_code is now unique PER ORG' AS status;
