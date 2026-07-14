-- 706_core_multi_tenant_membership.sql — ONE login credential may belong to MULTIPLE tenants.
--
-- WHY (platform-core-9, OWNER REQUEST 2026-07-14): a single person who works for more than one
-- tenant should sign in with ONE credential and, after the password check, pick which tenant to act
-- as (a dropdown that appears ONLY when the identity maps to >1 org). Today storeops.app_users has
-- auth_id UNIQUE (mig 015) — so one Supabase login = exactly one tenant. Migration 088 worked around
-- that by AUTO-MINTING a tenant-aliased login (local+slug@domain, a distinct auth account) per tenant.
--
-- WHAT THIS DOES: relax the GLOBAL uniqueness on auth_id to per-(auth_id, org_id), so the SAME auth
-- login can hold one app_users row PER tenant it belongs to. Each row keeps its own role / market /
-- store scope / permissions, so per-tenant roles are preserved natively (a person can be Admin in one
-- tenant and Sales Rep in another). This makes app_users itself the membership table — memberships are
-- DATA (SAP-configurable rule), no hard-coded tenant list anywhere.
--
-- BACK-COMPAT WITH MIG 088 (HARD REQUIREMENT): the aliased logins minted by mig 088 each have their
-- OWN auth_id and a SINGLE app_users row, so under the new model they are simply single-membership
-- identities — the picker never shows for them and the tenant middleware resolves their one org
-- exactly as before. Nothing about the existing aliased logins changes. Going forward the backend
-- PREFERS a shared-auth_id membership over minting a new alias (see core._provision_login), but the
-- alias path stays as an explicit fallback, so old and new behaviours coexist.
--
-- SAFE: additive + idempotent. Per-(auth_id, org_id) uniqueness is WEAKER than the global unique it
-- replaces, so no existing row can violate it (every current auth_id has exactly one row). No FK
-- references app_users, so dropping the global unique is a no-op for referential integrity. Until this
-- runs, the app is unchanged: the middleware/router membership logic degrades to "one row per auth_id"
-- (a single-membership identity), which is exactly today's behaviour.

-- 1) Drop the GLOBAL unique on auth_id (auto-named `app_users_auth_id_key` from `auth_id UUID UNIQUE`
--    in mig 015). Belt-and-suspenders: drop ANY unique constraint whose columns are exactly [auth_id],
--    whatever it happens to be named in this environment.
ALTER TABLE storeops.app_users DROP CONSTRAINT IF EXISTS app_users_auth_id_key;
DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN
    SELECT c.conname FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'storeops' AND t.relname = 'app_users' AND c.contype = 'u'
      AND (SELECT array_agg(a.attname::text) FROM unnest(c.conkey) k JOIN pg_attribute a
             ON a.attrelid = c.conrelid AND a.attnum = k) = ARRAY['auth_id']::text[]
  LOOP
    EXECUTE format('ALTER TABLE storeops.app_users DROP CONSTRAINT %I', r.conname);
  END LOOP;
END $$;
-- Also drop a bare UNIQUE INDEX solely on auth_id, if one exists independent of a constraint.
DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN
    SELECT i.relname FROM pg_index x
    JOIN pg_class i ON i.oid = x.indexrelid
    JOIN pg_class t ON t.oid = x.indrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'storeops' AND t.relname = 'app_users'
      AND x.indisunique AND NOT x.indisprimary
      AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
             FROM pg_attribute a WHERE a.attrelid = t.oid AND a.attnum = ANY(x.indkey))
          = ARRAY['auth_id']::text[]
  LOOP
    EXECUTE format('DROP INDEX IF EXISTS storeops.%I', r.relname);
  END LOOP;
END $$;

-- 2) Per-(auth_id, org_id) uniqueness = at most ONE membership row per (login, tenant). NULL auth_id
--    (a person invited but not yet given a login) is allowed and never collides.
CREATE UNIQUE INDEX IF NOT EXISTS app_users_auth_org_uidx
  ON storeops.app_users (auth_id, org_id) WHERE auth_id IS NOT NULL;

-- 3) Keep the non-unique lookup index on auth_id (mig 015 created it; ensure it survives). This is the
--    hot path for "which tenants does this login belong to" in the tenant middleware.
CREATE INDEX IF NOT EXISTS app_users_auth ON storeops.app_users (auth_id);

-- 4) Optional per-membership "home tenant" marker: when a login belongs to >1 tenant, this row is the
--    one auto-selected if the client sends no (or an invalid) active-tenant choice. NULL for everyone
--    today; resolution falls back to the earliest-created membership, so no backfill is needed.
ALTER TABLE storeops.app_users ADD COLUMN IF NOT EXISTS is_default_org BOOLEAN;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 706 complete — app_users.auth_id is now unique PER (auth_id, org_id); one login can span tenants' AS status;
