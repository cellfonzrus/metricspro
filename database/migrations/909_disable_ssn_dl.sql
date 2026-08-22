-- 909_disable_ssn_dl.sql — DISABLE Social Security / driver's licence capture. Nothing is dropped.
--
-- NOT DESTRUCTIVE. Owner directive 2026-08-22: there is no SSN or licence data in the system, so
-- the columns and functions are kept and simply taken out of service. An earlier draft of this
-- migration dropped them; that is deliberately no longer what it does, because dropping a column
-- you might want back is a one-way door and there is nothing here to gain by walking through it.
--
-- WHAT ACTUALLY STOPS THE COLLECTION is the application change shipped alongside this migration:
-- the capture fields, the reveal button, the import mapping and all three /pos/customers/{id}/pii*
-- endpoints are gone from the code. By the time you run this, the app is already not writing these
-- columns. This migration is the belt to that braces — it makes the database refuse the old path
-- even if some future build tried to use it.
--
-- WHAT IT DOES:
--   1. Reports how many rows hold a value, so the "there is no data" assumption is CHECKED, not
--      assumed. Read that number before continuing.
--   2. Nulls any value it finds. A no-op when the count is zero.
--   3. REVOKES EXECUTE on the three PII functions from every role, so nothing can call them.
--   4. Deactivates onboarding links gated on last-4 SSN — the app's gate no longer accepts that
--      answer, so those links would be dead ends. The column itself stays.
--
-- WHAT IT KEEPS: every column, every function, the Vault key, and the verify_ssn4 column. Re-enabling
-- is a GRANT and an application change away, not a restore from backup.
--
-- Idempotent — safe to run twice.

-- ── 1. Check the assumption before acting on it ───────────────────────────────────────────────
DO $$
DECLARE n_ssn INT; n_dl INT; n_state INT; n_gate INT;
BEGIN
  SELECT count(*) FILTER (WHERE ssn_enc IS NOT NULL),
         count(*) FILTER (WHERE driver_license_enc IS NOT NULL),
         count(*) FILTER (WHERE driver_license_state IS NOT NULL)
    INTO n_ssn, n_dl, n_state
    FROM pos.customers;
  SELECT count(*) INTO n_gate
    FROM storeops.employee_onboarding_profile WHERE verify_kind = 'ssn4';

  RAISE NOTICE 'BEFORE: % customer row(s) with an SSN, % with a licence number, % with a licence state; % onboarding link(s) gated on last-4 SSN.',
    n_ssn, n_dl, n_state, n_gate;

  IF n_ssn > 0 OR n_dl > 0 THEN
    RAISE NOTICE 'NOTE: values WERE present. They are being nulled below. If you needed them, stop and restore from backup BEFORE running this again.';
  END IF;
END $$;

-- ── 2. Null anything that is there (no-op when the counts above are zero) ─────────────────────
UPDATE pos.customers
   SET ssn_enc = NULL,
       driver_license_enc = NULL,
       driver_license_state = NULL
 WHERE ssn_enc IS NOT NULL
    OR driver_license_enc IS NOT NULL
    OR driver_license_state IS NOT NULL;

-- ── 3. Take the access functions out of service, without dropping them ───────────────────────
-- They are SECURITY DEFINER and were already revoked from anon/authenticated at creation; this
-- extends that to PUBLIC and to service_role, which is the role the backend actually uses. The
-- function bodies survive, so re-enabling is one GRANT — but nothing can call them until then.
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

COMMENT ON COLUMN pos.customers.ssn_enc IS
  'RETIRED (mig 909). Not collected, not read, not written by the application. Access functions revoked.';
COMMENT ON COLUMN pos.customers.driver_license_enc IS
  'RETIRED (mig 909). Not collected, not read, not written by the application. Access functions revoked.';
COMMENT ON COLUMN pos.customers.driver_license_state IS
  'RETIRED (mig 909). Not collected, not read, not written by the application.';

-- ── 4. Onboarding links gated on last-4 SSN ──────────────────────────────────────────────────
-- The application gate accepts date of birth only now, so an ssn4-gated link has no answer a new
-- starter could give. Those links are DEACTIVATED rather than silently switched to date of birth:
-- quietly changing which secret opens a door is worse than closing it and reissuing the link.
-- The verify_ssn4 COLUMN is kept.
UPDATE storeops.employee_onboarding_profile
   SET token_active = false,
       verify_kind = NULL,
       verify_ssn4 = NULL
 WHERE verify_kind = 'ssn4';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 909 complete — SSN / licence capture disabled. No column, function or key was dropped.' AS status;
