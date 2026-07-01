-- 080_onboarding_task_template.sql
-- DEFAULT TEMPLATE DOCUMENT per onboarding item. HR uploads the blank/standard file (a fillable W-4, a
-- policy PDF, the handbook, an offer-letter template, …) once against a checklist item; every new hire
-- can then download it from their onboarding portal to complete + upload back. The file itself lives in
-- the existing private storage bucket 'onboarding-docs' under a 'templates/{org}/{task}/…' prefix — these
-- two columns just point at it (same posture as employee_onboarding.document_path for uploads).
--
-- Additive + idempotent + safe to re-run. No data change; existing checklists keep working (a task with
-- no template simply shows none). The backend degrades gracefully if this isn't applied yet — the upload
-- endpoint returns a clear "run migration 080" message and the read paths just omit the template fields.

ALTER TABLE storeops.onboarding_task ADD COLUMN IF NOT EXISTS template_path TEXT;
ALTER TABLE storeops.onboarding_task ADD COLUMN IF NOT EXISTS template_name TEXT;

SELECT 'onboarding_task template columns ready' AS status;
