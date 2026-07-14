-- 402_hr_multifile_documents.sql — multi-file onboarding documents + delete audit (people-4, PARKED)
--
-- BUG THIS FIXES: uploading a new file for an onboarding document (e.g. a Social Security card) DESTROYED
-- the reference to the previous one. Root cause (see docs/handoffs/people.md for the full write-up):
-- `_do_onboard_upload` (hr/router.py) has always UPSERTED a single row per (org_id, employee_id, task_id)
-- with ONE scalar `document_path`/`document_name` pair. Every new upload for the same task overwrote that
-- pair. The storage object itself was NEVER deleted (there is no `.remove()` call anywhere in the upload
-- path, and every upload writes to a fresh `{org_id}/{employee_id}/{uuid}_{filename}` path) — so the OLD
-- FILE is orphaned in the `onboarding-docs` bucket, not destroyed. It is not automatically recoverable
-- (the storage path has no task_id in it, and the old `document_path` value that pointed at it was
-- overwritten in place, so there is no DB record of where it was) — hr/router.py's new
-- `.../orphaned-files` + `.../reattach-orphan` endpoints (this package) give HR a human-in-the-loop tool
-- to find and re-attach exactly this class of pre-fix orphan by filename.
--
-- WHAT THIS ADDS (additive + idempotent — ADD COLUMN IF NOT EXISTS, UPDATE scoped + re-runnable, no data
-- deleted):
--   1. storeops.employee_onboarding.documents  JSONB NOT NULL DEFAULT '[]' — the list of files attached to
--      one onboarding document/task (SS-card front + back, a multi-page form, …). Each element:
--        { id, path, name, content_type, uploaded_at, uploaded_by, uploaded_role }
--      `uploaded_role` is 'employee' | 'admin' | 'recovered' — it is what the employee-delete permission
--      gate (hr/router.py `_employee_can_delete_document`) checks: an employee may only delete a file
--      whose uploaded_role is 'employee' AND only while the task's status is still 'pending'.
--   2. Backfill: every existing row's single document_path/document_name becomes documents[0] — additive,
--      idempotent (only touches rows where documents is still empty AND document_path is set, so re-running
--      this migration, or running it after new multi-file rows already exist, is a no-op for those rows).
--      `uploaded_role` is backfilled 'unknown' (not 'employee') for pre-402 data — deliberately: it means
--      pre-402 uploads are NEVER self-delete-eligible by an employee (HR/admin can still delete them any
--      time), which is the conservative, safe default for data whose original uploader role was never
--      recorded.
--
-- `document_path` / `document_name` (existing columns) are UNCHANGED in shape and semantics — they keep
-- mirroring "the most recently uploaded file" for every caller that hasn't been updated to read the
-- `documents` list yet (never a silent regression for an untouched consumer).
--
-- SAFE TO RUN LATE: `_do_onboard_upload` tries the full row (with `documents`) first, then falls back to
-- the pre-402 column set, then to the pre-082 legacy shape, exactly the same three-tier degrade pattern
-- this file already uses for pre-082 databases — nothing 500s if this hasn't run yet. Until it runs, a new
-- upload for a task that already has a document simply overwrites it (today's existing, pre-fix behavior)
-- — multi-file + delete only activate once this migration is applied.

ALTER TABLE storeops.employee_onboarding
  ADD COLUMN IF NOT EXISTS documents JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN storeops.employee_onboarding.documents IS
  'List of every file uploaded for this onboarding document (append-only via the API; a file is only ever removed by an explicit, audited delete). Each element: {id, path, name, content_type, uploaded_at, uploaded_by, uploaded_role}. document_path/document_name mirror the most recent entry for back-compat.';

UPDATE storeops.employee_onboarding
   SET documents = jsonb_build_array(
         jsonb_build_object(
           'id', gen_random_uuid()::text,
           'path', document_path,
           'name', document_name,
           'content_type', NULL,
           'uploaded_at', COALESCE(submitted_at::text, updated_at::text, now()::text),
           'uploaded_by', NULL,
           'uploaded_role', 'unknown'
         )
       )
 WHERE document_path IS NOT NULL
   AND (documents IS NULL OR documents = '[]'::jsonb);

CREATE INDEX IF NOT EXISTS idx_employee_onboarding_documents_gin
  ON storeops.employee_onboarding USING gin (documents);

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 402 complete — multi-file onboarding documents (append not replace) + delete audit' AS status;
