-- 724_core_security_anon_function_lockdown.sql — platform-core band 700-799.
--
-- CLOSES THE GAP MIGRATION 722 LEFT OPEN. 722 revoked every TABLE/SEQUENCE/FUNCTION privilege from
-- `anon` and `authenticated`, which was correct as far as it went — but in Postgres a newly created
-- function grants EXECUTE to **PUBLIC** by default, and `REVOKE ... FROM anon, authenticated` does
-- NOT remove a grant held by PUBLIC. Every function created (or CREATE OR REPLACE'd) in these schemas
-- after 722 therefore came back anon-executable through the PUBLIC default, and several migrations
-- (067, 076, 077, 079, 708) additionally handed out `GRANT EXECUTE ... TO anon, authenticated`
-- explicitly.
--
-- ── MEASURED STATE BEFORE THIS MIGRATION (live, 2026-08-09) ───────────────────────────────────────
--   70 of 79 functions across core/commcalc/storeops/pos/public were executable by `anon`.
--   9 of those were SECURITY DEFINER — they run as `postgres` and therefore BYPASS RLS entirely.
--
-- The one that matters most is `public.commcalc_auto_match_name(text, text)`:
--   * SECURITY DEFINER, owner postgres, anon-executable, and it RETURNS TEXT — so unlike the other
--     eight (void/trigger) it is a READ path, not just a write path.
--   * Its body runs `select name from employees where lower(name) like lower($1 || ' %') and is_active`
--     and returns the match. `public.employees` holds 47 rows (45 active). An anonymous caller could
--     therefore enumerate real employee names by brute-forcing first-name prefixes — RLS never applies,
--     because SECURITY DEFINER is exactly a bypass.
--   * It also INSERTs into public.commcalc_name_map on EVERY call, including the no-match branch, so
--     it doubles as an unauthenticated table-growth vector.
--   * PROVEN, not assumed: `SET LOCAL ROLE anon; SELECT public.commcalc_auto_match_name(...)` executed
--     successfully and would have written 1 row (the probe ran inside a DO block that raised at the
--     end, so it rolled back and prod was not written to).
--
-- Neither the DDIA plan nor the security plan catches this class, because both inspect table policies
-- and never look at function grants. Recorded in docs/PLAN_REVIEW_2026-08-09.md as finding F1.
--
-- ── WHAT THIS DOES ────────────────────────────────────────────────────────────────────────────────
--   1. REVOKE EXECUTE ON ALL FUNCTIONS from PUBLIC **and** anon/authenticated, per schema. Revoking
--      PUBLIC is the part 722 missed and is what actually closes it.
--   2. ALTER DEFAULT PRIVILEGES so functions created LATER by postgres/service_role/supabase_admin are
--      not auto-granted to PUBLIC either — otherwise the next migration silently re-opens this.
--   3. Re-GRANT to service_role, which is the ONLY role the backend uses (app/core/database.py:163
--      `SUPABASE_SERVICE_KEY or SUPABASE_KEY`).
--
-- ── WHY THIS BREAKS NOTHING ───────────────────────────────────────────────────────────────────────
--   * The backend authenticates as service_role, which is re-granted below.
--   * The frontend's anon key is used for AUTH ONLY — `grep -rn "supabase\.from(|supabase\.rpc(|
--     supabase\.schema(" frontend/src` returns ZERO hits. Nothing in the browser calls a database
--     function directly.
--   * TRIGGER functions (`handle_new_user`, `sync_to_commcalc`, `sync_store_to_commcalc`,
--     `sync_sfid_to_store_mapping`, `soft_delete_shift`) are unaffected: Postgres checks EXECUTE on a
--     trigger function when the TRIGGER IS CREATED, not each time it fires.
--   * No extension functions are touched. All 7 functions in `public` are application functions
--     (`pg_depend` shows no extension dependency); pgcrypto/uuid-ossp/pg_stat_statements live in the
--     `extensions` schema and pg_cron/plpgsql in pg_catalog, none of which this migration names.
--   * `pos` is included for completeness even though `anon` already lacks USAGE on it.
--
-- Idempotent — REVOKE/GRANT are declarative; re-running converges to the same state.
DO $$
DECLARE
  sch  TEXT;
  ownr TEXT;
  schemas TEXT[] := ARRAY['core', 'commcalc', 'storeops', 'notify', 'pos', 'public'];
  owners  TEXT[] := ARRAY['postgres', 'service_role', 'supabase_admin'];
BEGIN
  FOREACH sch IN ARRAY schemas LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = sch) THEN
      CONTINUE;
    END IF;

    -- (1) The actual fix: PUBLIC first (that is the default grant every new function is born with),
    --     then the named web roles in case an explicit grant was also issued (migs 067/076/077/079/708).
    EXECUTE format('REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA %I FROM PUBLIC', sch);
    EXECUTE format('REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA %I FROM anon, authenticated', sch);

    -- (2) Stop the next CREATE FUNCTION from re-opening it. Default privileges are recorded PER
    --     CREATING ROLE, so this has to name each role that actually creates objects here.
    --     Only for roles we are actually a member of: ALTER DEFAULT PRIVILEGES FOR ROLE x requires
    --     membership in x, and `supabase_admin` is above the migration role, so attempting it raises
    --     42501 and (under --tx) rolls the whole migration back. Skipping it is correct rather than
    --     merely convenient: supabase_admin does not create this application's functions — postgres
    --     and service_role do, and both are covered. The step-1 REVOKE already closes what exists
    --     today regardless of who created it.
    FOREACH ownr IN ARRAY owners LOOP
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ownr)
         AND pg_has_role(current_user, ownr, 'MEMBER') THEN
        EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC', ownr, sch);
        EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I REVOKE EXECUTE ON FUNCTIONS FROM anon, authenticated', ownr, sch);
        EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT EXECUTE ON FUNCTIONS TO service_role', ownr, sch);
      END IF;
    END LOOP;

    -- (3) The backend must keep working.
    EXECUTE format('GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA %I TO service_role', sch);
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';

-- Proof line: this must come back 0. Any non-zero row is a function the public anon key can still call.
SELECT count(*) AS anon_executable_functions_remaining
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname IN ('core', 'commcalc', 'storeops', 'notify', 'pos', 'public')
  AND has_function_privilege('anon', p.oid, 'EXECUTE');
