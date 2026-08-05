-- 722_core_security_anon_lockdown.sql
-- ============================================================================================
-- SECURITY HARDENING (owner directive 2026-08-05) — CODIFY THE ANON/AUTHENTICATED LOCKDOWN.
--
-- CONTEXT. The public frontend bundle carries the Supabase ANON key by design (it is used ONLY for
-- auth: signInWithPassword). Early migrations (001/002/003/004/008/010/014/021/301/…) shipped the
-- dangerous "open_all" posture — `CREATE POLICY open_all ... TO anon, authenticated USING(true)`
-- plus `GRANT ALL ... TO anon, authenticated` (often schema-wide: GRANT ON ALL TABLES IN SCHEMA).
-- Any such table is readable AND writable straight from the browser console via PostgREST, bypassing
-- the FastAPI backend, its auth, its RBAC and its org scoping.
--
-- A manual advisory remediation on 2026-07-28 already REVOKEd these grants LIVE (verified 2026-08-05:
-- every app table returns `42501 permission denied` to the anon key over PostgREST). BUT that
-- remediation lives only in the running database — it is NOT in these migration files. Consequences
-- this migration fixes:
--   • REGRESSION RISK: re-running any early migration, or any NEW migration that re-adds an open_all
--     grant, silently re-opens that table.
--   • PROVISIONING RISK: standing up a fresh Supabase project / new tenant environment from these
--     migration files reproduces the holes wholesale.
--   • SOURCE-OF-TRUTH DRIFT: the committed schema disagrees with production.
--
-- WHAT THIS DOES (idempotent, additive, safe to re-run): for every table in the app schemas it
-- ENABLES RLS, DROPS any `open_all` policy, REVOKES ALL from anon + authenticated (tables, sequences,
-- functions), sets ALTER DEFAULT PRIVILEGES so future objects are not auto-granted to anon/auth, and
-- (re)GRANTS to service_role. The backend uses the SERVICE ROLE, which BYPASSES RLS and is granted
-- here, so the application is UNAFFECTED (this only removes the anon/authenticated public door — the
-- frontend never used it for data, only auth). This matches the current LIVE posture; on prod it is a
-- no-op that keeps the two in sync.
--
-- After running, reload PostgREST's schema cache:  NOTIFY pgrst, 'reload schema';
-- ============================================================================================

DO $$
DECLARE
  sch   text;
  r     record;
  schemas text[] := ARRAY['core', 'commcalc', 'storeops', 'notify', 'assets'];
BEGIN
  FOREACH sch IN ARRAY schemas LOOP
    -- Skip a schema that doesn't exist in this environment.
    IF NOT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = sch) THEN
      CONTINUE;
    END IF;

    -- 1) Revoke every table/sequence/function privilege from the public web roles, schema-wide.
    EXECUTE format('REVOKE ALL ON ALL TABLES    IN SCHEMA %I FROM anon, authenticated', sch);
    EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM anon, authenticated', sch);
    EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA %I FROM anon, authenticated', sch);
    -- Keep the backend working: the service role bypasses RLS and needs the grants.
    EXECUTE format('GRANT ALL ON ALL TABLES    IN SCHEMA %I TO service_role', sch);
    EXECUTE format('GRANT ALL ON ALL SEQUENCES IN SCHEMA %I TO service_role', sch);
    EXECUTE format('GRANT ALL ON ALL FUNCTIONS IN SCHEMA %I TO service_role', sch);

    -- 2) Stop FUTURE objects (created by postgres or service_role) from auto-granting anon/auth.
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL ON TABLES    FROM anon, authenticated', sch);
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL ON SEQUENCES FROM anon, authenticated', sch);
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL ON FUNCTIONS FROM anon, authenticated', sch);

    -- 3) Per-table: enable RLS and drop the permissive open_all policy if present. With anon/auth
    --    grants gone, an accidental open_all is inert, but we remove it so the intent is explicit and
    --    a future re-grant cannot revive a public door.
    FOR r IN
      SELECT tablename FROM pg_tables WHERE schemaname = sch
    LOOP
      EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', sch, r.tablename);
      EXECUTE format('DROP POLICY IF EXISTS open_all ON %I.%I', sch, r.tablename);
    END LOOP;
  END LOOP;
END $$;

-- Make PostgREST forget any cached anon-visible definitions immediately.
NOTIFY pgrst, 'reload schema';
