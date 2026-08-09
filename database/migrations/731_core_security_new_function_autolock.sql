-- 731_core_security_new_function_autolock.sql — platform-core band 700-799.
--
-- FIXES THE FORWARD GUARD THAT MIGRATION 724 ONLY *APPEARED* TO INSTALL.
--
-- 724 (2026-08-09) closed finding F1: 70 of 79 functions were EXECUTE-able by `anon`, nine of them
-- SECURITY DEFINER, one of them (`public.commcalc_auto_match_name`) a read path returning employee
-- names. The REVOKE half of 724 worked and is still holding — re-verified 2026-08-09 14:37Z:
-- anon 0/79 · authenticated 0/79 · service_role 79/79.
--
-- Its SECOND half — "so the next CREATE FUNCTION cannot re-open it" — DID NOT WORK, and it failed
-- silently. Measured 2026-08-09 ~14:50Z by creating a throwaway function in each schema inside a
-- DO block that RAISEd at the end (so every probe rolled back and prod was never written to):
--
--     schema=public   anon_exec=t  acl=[=X/postgres | postgres=X/postgres | service_role=X/postgres]
--     schema=core     anon_exec=t  acl=[same]        schema=commcalc anon_exec=t  acl=[same]
--     schema=storeops anon_exec=t  acl=[same]        schema=pos      anon_exec=t  acl=[same]
--     schema=notify   anon_exec=t  acl=[same]
--
-- `=X/postgres` is PUBLIC holding EXECUTE, and `anon` inherits from PUBLIC. So every one of the six
-- schemas was one `CREATE FUNCTION` away from re-opening exactly the hole F1 closed.
--
-- WHY 724's APPROACH CANNOT WORK (tested, not guessed — two further rolled-back probes):
--   * Re-running `ALTER DEFAULT PRIVILEGES … REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC` *after* the
--     GRANT, in case it was an ordering bug → still `anon_exec=t`. Not an ordering bug.
--   * Removing the `service_role` grant entry too, leaving no pg_default_acl row at all → the new
--     function came out with `proacl = NULL`, i.e. the HARD-WIRED default, which is PUBLIC EXECUTE.
--     That is the tell: at object-creation time Postgres starts from the hard-wired default and
--     merges the stored default-ACL entries onto it. A REVOKE is stored only as an *absence*, and an
--     absence cannot subtract the hard-wired PUBLIC grant. `ALTER DEFAULT PRIVILEGES … REVOKE …
--     FROM PUBLIC` therefore reports success and changes nothing here.
--   * The `supabase_admin`-owned default ACL on `public` (which does grant anon/authenticated) is a
--     separate residual we cannot touch: `ALTER DEFAULT PRIVILEGES FOR ROLE supabase_admin` raises
--     42501 because that role is above the migration role. It only governs objects supabase_admin
--     itself creates, and the event trigger below catches those too.
--
-- THE MECHANISM THAT DOES WORK: an event trigger on ddl_command_end. Verified permitted for this
-- (non-superuser) `postgres` role by a rolled-back CREATE EVENT TRIGGER probe before writing this.
-- It is strictly better than default privileges: it fires on the object regardless of which role
-- created it, which default ACL applied, or whether anybody remembered the boilerplate.
--
-- DEGRADES OPEN, DELIBERATELY. Every REVOKE/GRANT is wrapped in its own exception handler that
-- downgrades a failure to a WARNING. An event trigger that raises would abort the DDL that fired it
-- — i.e. one unlockable function would block every future migration in the platform. The detective
-- control (harness_anon_function_lockdown.py, added with this migration) is what catches anything
-- this lets through; the two together are safer than a guard that can brick migrations.
--
-- Additive + idempotent + re-runnable.

CREATE OR REPLACE FUNCTION core.lock_new_functions()
RETURNS event_trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $fn$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT objid, schema_name, object_identity
    FROM pg_event_trigger_ddl_commands()
    WHERE classid = 'pg_proc'::regclass
      AND NOT in_extension
      -- Our schemas only. Extension-owned and Supabase-internal schemas (auth, storage, realtime,
      -- graphql, vault …) are the platform's business, not ours, and revoking there could break
      -- Supabase itself.
      AND schema_name IN ('core', 'commcalc', 'storeops', 'pos', 'notify', 'public')
  LOOP
    BEGIN
      -- ROUTINE, not FUNCTION: covers procedures and aggregates, which also live in pg_proc.
      EXECUTE format('REVOKE ALL ON ROUTINE %s FROM PUBLIC', r.objid::regprocedure);
      EXECUTE format('REVOKE ALL ON ROUTINE %s FROM anon, authenticated', r.objid::regprocedure);
      -- Re-grant the one role the backend actually uses (app/core/database.py:163
      -- `SUPABASE_SERVICE_KEY or SUPABASE_KEY`), or the next migration's function is unusable.
      EXECUTE format('GRANT EXECUTE ON ROUTINE %s TO service_role', r.objid::regprocedure);
    EXCEPTION WHEN OTHERS THEN
      RAISE WARNING 'lock_new_functions: could not lock % — %', r.object_identity, SQLERRM;
    END;
  END LOOP;
END
$fn$;

REVOKE ALL ON ROUTINE core.lock_new_functions() FROM PUBLIC, anon, authenticated;

DROP EVENT TRIGGER IF EXISTS trg_lock_new_functions;
CREATE EVENT TRIGGER trg_lock_new_functions
  ON ddl_command_end
  WHEN TAG IN ('CREATE FUNCTION', 'CREATE PROCEDURE')
  EXECUTE FUNCTION core.lock_new_functions();

SELECT 'Migration 731 complete — new functions in our six schemas are now locked at CREATE time' AS status;
