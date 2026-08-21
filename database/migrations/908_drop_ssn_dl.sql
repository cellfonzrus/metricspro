-- 908_drop_ssn_dl.sql — REMOVE Social Security numbers and driver's licence data from the platform.
--
-- ⚠️ THIS MIGRATION DESTROYS DATA AND CANNOT BE UNDONE. ⚠️
--
-- It permanently erases every stored SSN and driver's licence number. The plaintext exists nowhere
-- else — not in a backup this migration can reach, not in a report, nowhere — so once this runs the
-- values are gone. That is the point: the owner asked for this category of data to be removed from
-- the system, and the safest place for data you do not need is nowhere.
--
-- READ BEFORE RUNNING:
--   · If any carrier or compliance process requires you to evidence an identity check performed at
--     the counter (activation disputes and chargeback defence are the usual reasons), satisfy
--     yourself that you do not depend on these records first. Nothing else in the platform reads
--     them, but only you know what your carriers ask you for.
--   · The application code that wrote and read these fields is removed in the same change, so the
--     app is already not collecting them by the time you run this. Running it is therefore safe to
--     schedule; delaying it only means the old values sit there longer.
--
-- WHAT GOES:
--   pos.customers.ssn_enc               full SSN, encrypted        (end customers)
--   pos.customers.driver_license_enc    full licence number, encrypted
--   pos.customers.driver_license_state  the issuing state          (plaintext)
--   pos.customer_pii_set / _get / _last4  the three access functions
--   pos.pii_key() + the Vault key       nothing left to encrypt
--   storeops.employee_onboarding_profile.verify_ssn4   last-4 SSN used as an identity gate
--
-- WHAT STAYS: everything else about a customer (name, contacts, address) and every other HR field.
-- Employee SSN was never stored by the platform (see migration 079) — it lives only inside the
-- uploaded W-4 / I-9 documents, which this does not touch, because payroll and tax filing need them.

-- ── 1. Overwrite before dropping ──────────────────────────────────────────────────────────────
-- DROP COLUMN alone leaves the old bytes recoverable in dead tuples until a VACUUM FULL reclaims
-- them. Overwriting first means the values are replaced in the live heap; step 4 then reclaims.
UPDATE pos.customers
   SET ssn_enc = NULL,
       driver_license_enc = NULL,
       driver_license_state = NULL
 WHERE ssn_enc IS NOT NULL
    OR driver_license_enc IS NOT NULL
    OR driver_license_state IS NOT NULL;

-- ── 2. Drop the access functions ──────────────────────────────────────────────────────────────
-- Before the columns, so nothing is left referring to them.
DROP FUNCTION IF EXISTS pos.customer_pii_set(UUID, UUID, TEXT, TEXT);
DROP FUNCTION IF EXISTS pos.customer_pii_get(UUID, UUID);
DROP FUNCTION IF EXISTS pos.customer_pii_last4(UUID, UUID);
DROP FUNCTION IF EXISTS pos.pii_key();

-- ── 3. Drop the columns ───────────────────────────────────────────────────────────────────────
ALTER TABLE pos.customers DROP COLUMN IF EXISTS ssn_enc;
ALTER TABLE pos.customers DROP COLUMN IF EXISTS driver_license_enc;
ALTER TABLE pos.customers DROP COLUMN IF EXISTS driver_license_state;

-- ── 4. Reclaim the storage holding the old values ─────────────────────────────────────────────
-- VACUUM FULL rewrites the table and cannot run inside a transaction block. If your SQL editor
-- wraps statements in one, this line errors harmlessly — run it on its own afterwards. Until it
-- runs, the erased bytes may still sit in dead tuples on disk.
VACUUM FULL pos.customers;

-- ── 5. The HR identity gate ───────────────────────────────────────────────────────────────────
-- Onboarding links could be gated on either date of birth or last-4 SSN. The SSN option is being
-- removed, so any link currently gated on it would have no answer a new starter could give. Those
-- links are DEACTIVATED rather than silently switched to date-of-birth: quietly changing which
-- secret opens a door is worse than closing it and having someone reissue the link.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'storeops' AND table_name = 'employee_onboarding_profile'
                AND column_name = 'verify_ssn4') THEN
    UPDATE storeops.employee_onboarding_profile
       SET token_active = false,
           verify_kind = NULL,
           verify_ssn4 = NULL
     WHERE verify_kind = 'ssn4';
    ALTER TABLE storeops.employee_onboarding_profile DROP COLUMN verify_ssn4;
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 908 complete — SSN and driver''s licence data removed from pos.customers and the HR identity gate' AS status;
