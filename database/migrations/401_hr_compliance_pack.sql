-- 401_hr_compliance_pack.sql — HR onboarding compliance pack (people-hr-compliance-pack, PARKED)
--
-- Root cause this migration backs (see docs/handoffs/people.md for the full write-up): the onboarding
-- checklist total/done is already computed LIVE against the current template (no per-employee snapshot
-- exists) — so the Brenda Romero / Eduardo Brito "5/5 without the IL W-4" vs Jose Utero "6/6 with it" split
-- was NOT a template-snapshot bug. It is a STATE-MATCHING bug: onboarding_task.applies_state is an exact
-- 2-letter code, but the intake 'state' field is free text — an employee who typed "Illinois" (or any
-- non-2-letter variant) never matches 'IL', so the state-gated task silently vanishes from BOTH the
-- numerator and denominator of their checklist instead of showing as outstanding. There was also no
-- MANDATORY flag independent of "currently visible in this employee's live-filtered checklist", and no
-- proactive reconciliation/notification when a template gains a requirement after a hire looks "done".
--
-- WHAT THIS ADDS (additive + idempotent — every ADD COLUMN is IF NOT EXISTS, every UPDATE is scoped +
-- re-runnable, no data is deleted):
--   1. storeops.onboarding_task.is_mandatory  — per-document mandatory flag (org-scoped via the task's own
--      org_id), default TRUE so today's behavior (everything in the active template counts) is unchanged.
--   2. storeops.onboarding_task.work_auth     — I-9 / work-authorization support docs, BLOCKING (item 4).
--      Backfills the two seeded work-auth tasks (key IN ('i9','id_docs')) to TRUE for every tenant that has
--      them; a tenant that renamed/removed those keys is unaffected (no-op).
--   3. storeops.onboarding_task.sample_path / sample_name — an admin-uploaded COMPLETED SAMPLE per
--      document (item 6), mirroring the existing template_path/template_name pattern from migration 080.
--   4. storeops.employee_onboarding_profile.dd_disclaimer_initials / dd_disclaimer_signed_at — the
--      direct-deposit disclaimer acknowledgment trail (item 3a). A dedicated pair of columns (not folded
--      into intake_data) so the ack is queryable/auditable without decrypting anything.
--   5. storeops.tenants config columns (item 2 / 3 / 4 — SAP-configurable, not hardcoded):
--        onboarding_upload_formats  TEXT[]   default {pdf,jpeg}   — allowed upload formats
--        dd_disclaimer_text         TEXT     seeded with a default disclaimer
--        work_auth_notice_text      TEXT     seeded with a default "payroll will be delayed" notice
--        routing_lookup_enabled     BOOLEAN  default true
--        routing_lookup_url         TEXT     seeded with the routingnumbers.info free JSON API
--
-- SAFE TO RUN LATE: every backend read of these columns degrades gracefully (try/except -> safe default)
-- per the agent contract §5 — nothing 500s if this hasn't run yet; the new behavior (mandatory reopen,
-- work-auth blocking gate, upload format enforcement, DD disclaimer, routing lookup) simply doesn't
-- activate until it has.

-- ── 1-3. onboarding_task: mandatory flag, work-auth blocking flag, sample-document columns ───────────
ALTER TABLE storeops.onboarding_task ADD COLUMN IF NOT EXISTS is_mandatory BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE storeops.onboarding_task ADD COLUMN IF NOT EXISTS work_auth    BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE storeops.onboarding_task ADD COLUMN IF NOT EXISTS sample_path  TEXT;
ALTER TABLE storeops.onboarding_task ADD COLUMN IF NOT EXISTS sample_name  TEXT;

COMMENT ON COLUMN storeops.onboarding_task.is_mandatory IS
  'Per-document mandatory flag. Default TRUE (matches pre-401 behavior: every active template task counts). An admin can mark a task optional (e.g. a nice-to-have training module) without it blocking checklist completion.';
COMMENT ON COLUMN storeops.onboarding_task.work_auth IS
  'I-9 / citizenship-work-authorization support document. TRUE = BLOCKING: the hire cannot advance to provisioned/active while any work_auth task is outstanding, server-enforced (see hr/router.py _blocking_gate), independent of the general HR override.';

UPDATE storeops.onboarding_task SET work_auth = true
 WHERE key IN ('i9', 'id_docs') AND work_auth IS DISTINCT FROM true;

-- ── 4. employee_onboarding_profile: direct-deposit disclaimer acknowledgment trail ────────────────────
ALTER TABLE storeops.employee_onboarding_profile ADD COLUMN IF NOT EXISTS dd_disclaimer_initials  TEXT;
ALTER TABLE storeops.employee_onboarding_profile ADD COLUMN IF NOT EXISTS dd_disclaimer_signed_at TIMESTAMPTZ;

-- ── 5. storeops.tenants: SAP-configurable columns for items 2 / 3 / 4 ──────────────────────────────────
ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS onboarding_upload_formats TEXT[] NOT NULL DEFAULT ARRAY['pdf','jpeg'];
ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS dd_disclaimer_text        TEXT;
ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS work_auth_notice_text     TEXT;
ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS routing_lookup_enabled    BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS routing_lookup_url        TEXT;

UPDATE storeops.tenants SET dd_disclaimer_text =
  'By providing bank account information for direct deposit, I certify the routing and account numbers '
  'above are correct. If I submit incorrect information, my employer and the payroll processing company '
  'are NOT liable for any loss, delay, or misdirection of my wages that results.'
 WHERE dd_disclaimer_text IS NULL;

UPDATE storeops.tenants SET work_auth_notice_text =
  'Your work-authorization documents (Form I-9 support documents) are still outstanding. '
  'Your payroll will be delayed until these documents are submitted.'
 WHERE work_auth_notice_text IS NULL;

UPDATE storeops.tenants SET routing_lookup_url = 'https://www.routingnumbers.info/api/data.json?rn={routing}'
 WHERE routing_lookup_url IS NULL;

COMMENT ON COLUMN storeops.tenants.onboarding_upload_formats IS
  'Allowed file formats for onboarding document uploads (driver''s license, filled/signed documents), lower-case no-dot (e.g. pdf, jpeg). Enforced server-side by content-type + magic-byte sniff, not just extension.';
COMMENT ON COLUMN storeops.tenants.routing_lookup_url IS
  'Bank routing-number lookup provider URL template, {routing} substituted with the 9-digit ABA number. Free no-key default: routingnumbers.info. Lookup is used only to display/confirm the bank name at entry (before storage) — never blocks submission when down/disabled (falls back to ABA checksum only).';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 401 complete — HR compliance pack (mandatory flag, work-auth gate, sample docs, DD disclaimer, upload-format + routing-lookup config)' AS status;
