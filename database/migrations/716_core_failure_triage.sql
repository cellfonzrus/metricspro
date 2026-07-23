-- 716_core_failure_triage.sql — Failure-log TRIAGE UX + plain-English error registry + fix-request pipeline.
-- Band 700–799 (mod-platform-core). Additive + idempotent (safe to re-run).
--
-- OWNER DIRECTIVE 2026-07-23 (NON-money): make the flat Failure Logs list triageable.
--   (1) SOFT "reviewed" state on core.failure_log so an admin can CLEAR (mark reviewed — keep the row for
--       the audit trail, never delete) a whole group of similar errors at once; the default view is unreviewed.
--   (2) core.failure_kind_doc — the how-to-fix registry MOVED OUT OF CODE into DATA (RULE TWO): every failure
--       kind emitted in the codebase gets a plain-English "what this means" + "how to fix it" + "escalate when"
--       + a code-area hint, editable from the support docs editor. A code fallback still covers unknown kinds.
--   (3) storeops.support_fix_request — the approved-fix-request pipeline: support (or an admin from /failures)
--       CLUBS a group of similar failures into ONE request; a super_admin approves; approved requests form a
--       queue the operator/agent fleet picks up. NOTHING here edits code or prod data automatically.
--
-- MULTI-TENANT (RULE ONE): every new table has org_id uuid NOT NULL + an index on it. A failure row's org_id
--   = the TENANT it happened in; a failure_kind_doc's org_id = HOUSE (global registry, tenant override allowed
--   later); a support_fix_request's org_id = the owner org (the tenant that clubbed it, or HOUSE for a
--   support-created cross-tenant request; affected_orgs lists every tenant it spans + counts).
--
-- DEGRADES GRACEFULLY: until this runs, every read/write of these columns/tables is try/except-guarded → the
--   /failures + support pages show an honest empty state and the plain-English text falls back to the in-code
--   registry. No unrelated page breaks. Mirrors mig 112 / 715 style (RLS open_all, GRANT, NOTIFY reload).

-- ── (1) core.failure_log: soft REVIEWED triage state (distinct from status open/resolved/ignored) ──
ALTER TABLE core.failure_log
  ADD COLUMN IF NOT EXISTS reviewed     BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS reviewed_by  TEXT,
  ADD COLUMN IF NOT EXISTS reviewed_at  TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS failure_log_org_reviewed ON core.failure_log(org_id, reviewed);

-- ── (2) core.failure_kind_doc — plain-English how-to-fix registry (config-as-data) ────────────────
CREATE TABLE IF NOT EXISTS core.failure_kind_doc (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,                 -- HOUSE = global registry; a tenant org = future override
  kind           TEXT NOT NULL,                 -- == core.failure_log.category
  label          TEXT,                          -- short human title
  module         TEXT,                          -- nav module key (grouping / filtering)
  severity       TEXT DEFAULT 'warning',
  layman_meaning TEXT,                          -- "What this means" (non-technical)
  layman_fix     TEXT,                          -- "How to fix it" (non-technical)
  escalate_when  TEXT,                          -- when to send it to tech support
  code_hint      TEXT,                          -- module/file area hint for whoever ships the fix
  is_active      BOOLEAN NOT NULL DEFAULT true,
  updated_by     TEXT,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, kind)
);
CREATE INDEX IF NOT EXISTS failure_kind_doc_org_idx ON core.failure_kind_doc(org_id);

ALTER TABLE core.failure_kind_doc ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY open_all ON core.failure_kind_doc FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN OTHERS THEN NULL; END $$;
GRANT ALL ON core.failure_kind_doc TO anon, authenticated, service_role;

-- Seed the HOUSE (global) registry for EVERY failure kind emitted in the codebase. 9 columns / 9 values per
-- row (arity checked by harness_failure_triage.py — the class of bug Gate-1 caught on mig 715). Idempotent.
INSERT INTO core.failure_kind_doc (org_id, kind, label, module, severity, layman_meaning, layman_fix, escalate_when, code_hint) VALUES
  ('00000000-0000-0000-0000-000000000001', 'face_mismatch', 'Clock-in face did not match', 'storeops', 'warning',
   'A rep tried to clock in at the kiosk, but the live camera face did not match the face saved on their profile closely enough, so the punch was refused.',
   'Ask the rep to tap "Re-register my face" at the kiosk in good, even light. If it keeps happening to many reps, raise the Clock-in Face Sensitivity on the Failure Logs page toward 0.65 (looser). A manager can approve the punch right away with a kiosk override.',
   'The same rep keeps getting rejected right after a fresh re-registration in good lighting.',
   'frontend portal kiosk clock-in + storeops face enrollment; threshold = storeops.tenants.face_match_threshold'),

  ('00000000-0000-0000-0000-000000000001', 'clock_in_location', 'Clock-in blocked by location or schedule', 'storeops', 'info',
   'A rep tried to clock in at a store that is not their home store, is not on their schedule, and where they are not marked a floater, so the system blocked it.',
   'Add the shift for that store, mark the rep a floater for it, or have a manager approve the punch with an override.',
   'The rep is correctly scheduled or a floater for that store but is still blocked.',
   'storeops time-clock home/scheduled/floater gate + manager override'),

  ('00000000-0000-0000-0000-000000000001', 'upload_rejected', 'Data upload rejected', 'commissions', 'error',
   'A file that was uploaded could not be read because it had the wrong layout or was missing required columns, so nothing was imported.',
   'Confirm the file has all required columns and re-upload. For the daily sales feed the report must include Ext Price and GP; for commissions use the full 78-column Sales Transaction Details export.',
   'The file clearly has the required columns but is still rejected.',
   'commcalc upload parsers (sales / commissions); daily feed must carry Ext Price + GP'),

  ('00000000-0000-0000-0000-000000000001', 'sweep_error', 'Automated import or job failed', 'admin', 'error',
   'A scheduled background job (an email or portal import, or a nightly sweep) hit an error and could not finish. Existing data was left as-is.',
   'Check the connection at Data Imports (last status + Test connection) and confirm the mailbox or portal credentials and the file-name patterns. The sweep retries on its next run once the cause is fixed.',
   'Credentials and settings are confirmed correct but the job keeps failing.',
   'core.run_for_tenant guarded jobs; commcalc email-imports connectors'),

  ('00000000-0000-0000-0000-000000000001', 'tenant_guard', 'Background job refused — bad or inactive company', 'admin', 'error',
   'A background job fired for a company (tenant) that has no record, or one that is switched off, so it was refused to avoid writing data to the wrong place.',
   'Make sure the connector, subscription, or plan is filed under a real, active company at Companies (Tenants). Reactivate the company if it was switched off by mistake, or remove the stale setup.',
   'The company exists and is active but its jobs are still refused.',
   'core.run_for_tenant tenant-misfiling guard; storeops.tenants.is_active'),

  ('00000000-0000-0000-0000-000000000001', 'money_write_refused', 'Money update blocked (safety guard)', 'admin', 'error',
   'A background job tried to replace a whole company worth of money figures with numbers that looked wrong (all zero, or wiping out an existing balance), so the safety guard blocked it and left the data unchanged.',
   'A zero result is almost always missing input (no plan assigned, or an empty source file), not a real zero — fix the input, then re-run. If the zero is genuinely correct, adjust the money guard for that company.',
   'The input is confirmed correct and the write is legitimately zero but keeps getting blocked.',
   'core.run_for_tenant money guard; storeops.tenants.money_guard_config'),

  ('00000000-0000-0000-0000-000000000001', 'system_error', 'Unexpected system error', 'admin', 'error',
   'Something in the app crashed unexpectedly. The user saw a generic message with a reference code; the full technical detail is saved here under that code.',
   'Note the reference code shown to the user and open the matching entry here to read the detail. This usually needs a developer or tech support to fix the underlying cause.',
   'Always escalate a repeating system error to tech support, with the reference code.',
   'app.main HardeningMiddleware + core _masked_500; search failure_log detail by ref'),

  ('00000000-0000-0000-0000-000000000001', 'asset_upload_degraded_mode', 'Asset upload used the older (non-atomic) path', 'asset', 'warning',
   'An asset ledger upload worked, but used an older import method because a database upgrade has not been applied. If an upload were interrupted midway it could leave a partial ledger.',
   'Run migration 300 (asset ledger staging-swap) in the Supabase SQL editor to enable the safer atomic upload. Until then, uploads still work but are not interruption-safe.',
   'Migration 300 has been run but this warning still appears on every upload.',
   'asset/router _stage_and_swap_ledger; migration 300_asset_ledger_staging_swap.sql'),

  ('00000000-0000-0000-0000-000000000001', 'other', 'Other', 'admin', 'warning',
   'A failure that does not fit a known category. The details describe what happened.',
   'Review the detail on the entry and resolve it manually. If it is unclear, escalate to tech support.',
   'The cause is unclear from the detail.',
   'generic fallback category')
ON CONFLICT (org_id, kind) DO NOTHING;

-- ── (3) storeops.support_fix_request — the clubbed fix-request pipeline ────────────────────────────
-- storeops schema (support_ prefix, same as support_case) so it sits in the SAME PostgREST-exposed schema.
CREATE TABLE IF NOT EXISTS storeops.support_fix_request (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,              -- owner org: the tenant that clubbed it, or HOUSE (support-created)
  kind               TEXT,                       -- the clubbed failure kind (core.failure_log.category)
  module             TEXT,
  title              TEXT,
  summary            TEXT,                       -- plain-English summary
  proposed_action    TEXT,
  code_hint          TEXT,                       -- module/file hint for whoever ships the fix
  sample_failure_ids JSONB,                      -- array of core.failure_log ids clubbed into this request
  affected_orgs      JSONB,                      -- [{"org_id","org_name","count"}]
  failure_count      INT DEFAULT 0,
  status             TEXT NOT NULL DEFAULT 'new',-- new | pending_approval | approved | in_progress | resolved | rejected
  created_by         TEXT,
  approved_by        TEXT,                       -- the super_admin who approved/rejected
  approved_at        TIMESTAMPTZ,
  resolution         TEXT,
  resolved_at        TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS support_fix_request_org_idx    ON storeops.support_fix_request(org_id);
CREATE INDEX IF NOT EXISTS support_fix_request_status_idx ON storeops.support_fix_request(status);

ALTER TABLE storeops.support_fix_request ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY open_all ON storeops.support_fix_request FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN OTHERS THEN NULL; END $$;
GRANT ALL ON storeops.support_fix_request TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT '716 complete — failure_log.reviewed + core.failure_kind_doc (9 seeded kinds) + storeops.support_fix_request' AS status;
