-- MIGRATION 1019: the SSN / driver's-licence functions stay switched off (repair of 1017; index §30.16)
-- Idempotent (safe to re-run). NOT money-touching. Changes permissions only; no table, column or row.
--
-- WHY. Migration 909 (owner decision) switched off the SSN / DL access functions by revoking EXECUTE from every role,
-- service_role included. Migration 1017 re-asserted the mig-725 POS lockdown block, and that block contains
--   GRANT ALL ON ALL FUNCTIONS IN SCHEMA pos TO service_role
-- which re-granted those functions to service_role (1017 re-revoked only pos.pii_key). anon / authenticated were never
-- re-granted — the same block revokes them — so browsers and signed-in users could not reach them; the backend's
-- service key could. This puts 909's state back, word for word.
--
-- THE CLASS: a copied schema-wide GRANT undoes a later, narrower REVOKE. Locked by
-- backend/harness_pos_grants_lock.py: no migration numbered after 909 may grant ALL FUNCTIONS IN SCHEMA pos, and
-- none may grant one of these four functions.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
              WHERE n.nspname = 'pos' AND p.proname = 'customer_pii_set') THEN
    REVOKE ALL ON FUNCTION pos.customer_pii_set(UUID, UUID, TEXT, TEXT) FROM PUBLIC, anon, authenticated, service_role;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
              WHERE n.nspname = 'pos' AND p.proname = 'customer_pii_get') THEN
    REVOKE ALL ON FUNCTION pos.customer_pii_get(UUID, UUID) FROM PUBLIC, anon, authenticated, service_role;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
              WHERE n.nspname = 'pos' AND p.proname = 'customer_pii_last4') THEN
    REVOKE ALL ON FUNCTION pos.customer_pii_last4(UUID, UUID) FROM PUBLIC, anon, authenticated, service_role;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
              WHERE n.nspname = 'pos' AND p.proname = 'pii_key') THEN
    REVOKE ALL ON FUNCTION pos.pii_key() FROM PUBLIC, anon, authenticated, service_role;
  END IF;
END $$;

-- CHECK (run after; expect 0 rows): any role other than the owner that can still execute one of the four.
--   SELECT p.proname, r.rolname
--     FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
--     CROSS JOIN (VALUES ('service_role'), ('anon'), ('authenticated')) AS r(rolname)
--    WHERE n.nspname = 'pos' AND p.proname IN ('customer_pii_set','customer_pii_get','customer_pii_last4','pii_key')
--      AND has_function_privilege(r.rolname, p.oid, 'EXECUTE');
--
-- REVERT: none — reverting would re-open functions the owner switched off in 909. Re-enabling them is a separate,
-- deliberate GRANT with an application change (see 909's header).
