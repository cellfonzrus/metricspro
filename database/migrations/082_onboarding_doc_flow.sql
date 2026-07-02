-- 082_onboarding_doc_flow.sql — onboarding document send/return flow + online fill & sign
--
-- WHAT: (1) HR "Documents" board — track which employees have been SENT the onboarding packet and
-- which items came BACK (docs_sent_at on the profile). (2) Employees can FILL & SIGN an item ONLINE
-- (per-task form_data + a drawn signature stored in the onboarding-docs bucket) instead of printing
-- and uploading. (3) The system VALIDATES returned documents (online = required-field check;
-- uploaded fillable PDFs = AcroForm completeness + signature-field check) and RETURNS incomplete
-- ones to the employee with the exact missing fields listed (status 'returned' + email).
--
-- SAFE: additive + idempotent. Pre-082 rows keep working (reads .get() every new column). The
-- 'returned' status value is new; employee_onboarding.status has no CHECK constraint, so old rows
-- and old code are unaffected.

-- Per-employee task row: online form data, signature, and the return-for-corrections trail
ALTER TABLE storeops.employee_onboarding ADD COLUMN IF NOT EXISTS form_data       JSONB;
ALTER TABLE storeops.employee_onboarding ADD COLUMN IF NOT EXISTS signature_path  TEXT;
ALTER TABLE storeops.employee_onboarding ADD COLUMN IF NOT EXISTS signed_name     TEXT;
ALTER TABLE storeops.employee_onboarding ADD COLUMN IF NOT EXISTS signed_at       TIMESTAMPTZ;
ALTER TABLE storeops.employee_onboarding ADD COLUMN IF NOT EXISTS missing_fields  JSONB;
ALTER TABLE storeops.employee_onboarding ADD COLUMN IF NOT EXISTS returned_reason TEXT;
ALTER TABLE storeops.employee_onboarding ADD COLUMN IF NOT EXISTS returned_at     TIMESTAMPTZ;
ALTER TABLE storeops.employee_onboarding ADD COLUMN IF NOT EXISTS returned_by     TEXT;
ALTER TABLE storeops.employee_onboarding ADD COLUMN IF NOT EXISTS validation      JSONB;

-- Task definition: whether an online submission must carry a signature, and the (optional)
-- configurable fields the online fill & sign form collects for this item
ALTER TABLE storeops.onboarding_task ADD COLUMN IF NOT EXISTS requires_signature BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE storeops.onboarding_task ADD COLUMN IF NOT EXISTS form_fields        JSONB;

-- Profile: when the onboarding document packet was last sent to this employee
ALTER TABLE storeops.employee_onboarding_profile ADD COLUMN IF NOT EXISTS docs_sent_at TIMESTAMPTZ;

COMMENT ON COLUMN storeops.employee_onboarding.missing_fields IS
  'Fields the validator (or HR) found missing/blank when the document came back — shown to the employee on return.';
COMMENT ON COLUMN storeops.onboarding_task.form_fields IS
  'Optional online fill & sign form definition: [{key,label,required}] — rendered in the portal sign modal above the signature pad.';
