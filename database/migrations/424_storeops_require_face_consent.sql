-- 424_storeops_require_face_consent.sql — people band.
--
-- CONSENT BEFORE COLLECTION, ENFORCED BY THE DATABASE.
--
-- BIPA 740 ILCS 14/15(b) forbids CAPTURING a biometric identifier until the subject has been informed
-- in writing of the collection, its specific purpose and the length of term, and has signed a written
-- release. Until this migration nothing in the system enforced that: `POST /timeclock/face` checked
-- only whether the FEATURE was on for the employee. An employee recorded as `declined` was blocked
-- (face_recognition.resolve_employee_face), but an employee with NO consent record at all sailed
-- straight through — and "no record" was the state of every single enrolled descriptor on 2026-08-09
-- (77 of 77, across two companies).
--
-- WHY A DATABASE TRIGGER AND NOT ONLY AN APPLICATION CHECK. The application gate ships alongside this
-- and is the one users will actually see (a clear 403 rather than a 500). But an application check
-- protects one code path, and this data is the kind you cannot un-collect: the kiosk bundle is cached
-- in browsers, a future endpoint could write the same table, and a backfill script bypasses routers
-- entirely. The invariant worth having is not "the enroll endpoint checks consent", it is "a face
-- template cannot exist in this database without a signed, dated consent record" — and only the
-- database can say that.
--
-- FAIL CLOSED, DELIBERATELY, AND NOTE THE CONTRAST. Migration 732's import guard and 731's function
-- lock both degrade OPEN, because failing closed there would stop the business. Here the asymmetry
-- runs the other way: refusing to store a face template costs one employee one retry at a kiosk,
-- while storing one without consent is a per-person statutory-damages exposure that cannot be undone
-- by deleting the row afterwards. When the cost of a wrong "yes" is unbounded and the cost of a wrong
-- "no" is a retry, the guard raises.
--
-- WHAT IT DOES NOT DO:
--   * It does not touch the 76 descriptors already on file. They predate it, deleting them is a
--     business/legal decision and not a migration's to make, and a trigger that retroactively broke
--     reads would be indefensible. It binds every future write.
--   * It does not fire on DELETE — the retention job (migration 422) must always be able to destroy,
--     and a consent problem is never a reason to keep biometric data longer.
--   * It does not fire on an UPDATE that leaves the descriptor unchanged (register_count, updated_at),
--     so ordinary bookkeeping on an existing row is unaffected.
--
-- Additive + idempotent + re-runnable.

CREATE OR REPLACE FUNCTION storeops.require_face_consent()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
  c_status text;
  c_at     timestamptz;
BEGIN
  -- Bookkeeping-only UPDATE (no new biometric data) — not a collection event.
  IF TG_OP = 'UPDATE' AND NEW.descriptor IS NOT DISTINCT FROM OLD.descriptor THEN
    RETURN NEW;
  END IF;

  SELECT face_consent_status, face_consent_at
    INTO c_status, c_at
    FROM storeops.employees
   WHERE org_id = NEW.org_id AND employee_id = NEW.employee_id
   LIMIT 1;

  IF c_status IS DISTINCT FROM 'signed' THEN
    RAISE EXCEPTION
      'BIPA 15(b): no signed consent on file for employee % — a face template may not be stored',
      NEW.employee_id
      USING ERRCODE = 'check_violation',
            HINT = 'Record the employee''s written release first (PUT /storeops/employees/{id}/face-config with consent=signed and the real signing date), then enroll.';
  END IF;

  -- A consent record with no date, or dated in the future, is not evidence that consent PRECEDED
  -- collection — which is the entire requirement. At INSERT, now() IS the moment of collection.
  IF c_at IS NULL OR c_at > now() THEN
    RAISE EXCEPTION
      'BIPA 15(b): consent for employee % is not dated at or before this collection',
      NEW.employee_id
      USING ERRCODE = 'check_violation',
            HINT = 'Set face_consent_at to the date the employee actually signed the release.';
  END IF;

  RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS trg_require_face_consent ON storeops.face_descriptors;
CREATE TRIGGER trg_require_face_consent
  BEFORE INSERT OR UPDATE ON storeops.face_descriptors
  FOR EACH ROW EXECUTE FUNCTION storeops.require_face_consent();

SELECT 'Migration 424 complete — a face template cannot be stored without a signed, dated consent record' AS status;
