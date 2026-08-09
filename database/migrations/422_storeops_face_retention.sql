-- 422_storeops_face_retention.sql — mod-people band 400-499.
--
-- OWNER DECISION 2026-08-09: build the face-descriptor retention schedule + deletion job. Closes
-- security-plan item 9.2, the last open piece of the BIPA work shipped at migration 420 (face
-- recognition OFF for every tenant, master switch + per-employee assignment + consent).
--
-- ── THE RULE (owner-specified, verbatim triggers, "whichever is first" — 740 ILCS 14/15(a)) ───────
--   1. destroy 90 days after an employee's LAST EMPLOYMENT DAY (storeops.employees.termination_date,
--      already added by migration 417) — the "purpose satisfied" trigger. Tenant-configurable, see
--      face_retention_days below; NOT the industry-quoted "12 months" — the purpose for collecting a
--      clock-in descriptor is satisfied on the last day of employment, and BIPA's "whichever occurs
--      first" makes that bind well before any calendar bound.
--   2. destroy IMMEDIATELY on an employee's written request (BIPA gives them that right) —
--      implemented as a direct action (backend/app/modules/storeops/face_retention.py
--      `destroy_one_employee_request`), not a scheduled trigger.
--   3. destroy when a tenant disables face recognition AND opts to purge — the 77 already-enrolled
--      descriptors are deliberately KEPT on a plain disable (so re-enabling is instant, migration 420's
--      stated design); this migration adds the OPT-IN `face_recognition_purge_on_disable` so a tenant
--      that wants the stronger posture can have it.
--   4. an ABSOLUTE BACKSTOP of 3 years (1095 days) since an employee's LAST INTERACTION with their own
--      biometric template (enrollment, re-enrollment, or a clock-in that was actually face-matched —
--      see face_retention.py `_last_interaction_at`), for anyone who never gets a formal termination
--      date. This is BIPA's statutory outer bound and is NOT configurable — it is also the CEILING on
--      face_retention_days below, so no tenant can configure itself out of compliance.
--
-- RULE TWO (SAP-configurable): the 90-day figure is a per-tenant column with a sane default, not a
-- constant. RULE ONE (multi-tenant): every column carries org_id; the audit table is org-scoped on
-- every read AND write (face_retention.py, no exceptions).
--
-- ── WHAT THIS ADDS ───────────────────────────────────────────────────────────────────────────────
--   storeops.tenants:
--     face_retention_days               integer NOT NULL DEFAULT 90
--       CHECK (face_retention_days BETWEEN 1 AND 1095)  -- hard ceiling = the statutory 3-year bound
--     face_recognition_purge_on_disable boolean NOT NULL DEFAULT false  -- opt-in, see trigger 3 above
--
--   storeops.face_retention_log (NEW — the auditable destruction record; "we destroyed it" must be
--   evidenceable years later). Deliberately holds ONLY metadata: employee id/name, which trigger
--   fired, the dates that decided it, who ran it, and whether it was a dry run. NEVER the descriptor
--   vector itself (nothing in face_retention.py ever selects `descriptor` into a log row):
--     id                          uuid PK default gen_random_uuid()
--     org_id                      uuid NOT NULL
--     employee_id                 text          -- storeops.employees.employee_id (business id)
--     employee_name               text
--     trigger                     text NOT NULL CHECK IN ('purpose_satisfied','statutory_backstop',
--                                                          'employee_request','tenant_disabled_purge')
--     descriptor_id                uuid         -- the face_descriptors.id that was destroyed (row is
--                                                   gone after; kept here only as a reference, no FK)
--     descriptor_registered_at     timestamptz
--     descriptor_updated_at        timestamptz
--     last_interaction_at          timestamptz  -- what decided the statutory_backstop trigger, if any
--     termination_date             date         -- what decided the purpose_satisfied trigger, if any
--     retention_days_applied       integer      -- the day count actually used (tenant's or the 1095 backstop)
--     dry_run                      boolean NOT NULL DEFAULT false  -- true rows are a preview, never written by the job itself (see below)
--     destroyed_by                 text         -- email, or 'system:pg_cron'
--     destroyed_at                 timestamptz NOT NULL DEFAULT now()
--     notes                        text
--
-- Note: dry-run PREVIEWS never insert a log row (nothing was destroyed, so a "destroyed_at" row would
-- misstate the record) — the `dry_run` column exists for completeness / future direct-write callers,
-- not because face_retention.py currently writes any dry_run=true rows. See face_retention.py DEGRADE.
--
-- ── DEGRADE (AGENT_CONTRACT §5) ──────────────────────────────────────────────────────────────────
-- Every read in face_retention.py is wrapped in try/except and returns available=False until this
-- runs — the job is simply inert (destroys nothing, the /run-due cron entrypoint is a no-op) rather
-- than 500ing, matching every other config feature in this file (lunch deduction, face recognition
-- toggle, timeoff conflict mode). Nothing here removes the 77 existing descriptors on migrate; the
-- FIRST scheduled sweep after this runs computes destruction dates from data that is currently
-- entirely non-terminated (verified live 2026-08-09: 0 of 43/57/3 employees across the 3 tenants have
-- a termination_date set), so this migration alone destroys nothing — only actual HR terminations (or
-- an explicit employee request / tenant purge-opt-in) ever will.
--
-- Additive + idempotent. No anon/authenticated grants (new table gets RLS enabled + zero policies,
-- same posture as every other migration since the 2026-07-28 lockdown; app access is service-role only).
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS face_retention_days integer NOT NULL DEFAULT 90,
  ADD COLUMN IF NOT EXISTS face_recognition_purge_on_disable boolean NOT NULL DEFAULT false;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tenants_face_retention_days_chk') THEN
    ALTER TABLE storeops.tenants
      ADD CONSTRAINT tenants_face_retention_days_chk
      CHECK (face_retention_days BETWEEN 1 AND 1095);
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS storeops.face_retention_log (
  id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                      uuid NOT NULL,
  employee_id                 text,
  employee_name               text,
  trigger                     text NOT NULL,
  descriptor_id                uuid,
  descriptor_registered_at     timestamptz,
  descriptor_updated_at        timestamptz,
  last_interaction_at          timestamptz,
  termination_date             date,
  retention_days_applied       integer,
  dry_run                      boolean NOT NULL DEFAULT false,
  destroyed_by                 text,
  destroyed_at                 timestamptz NOT NULL DEFAULT now(),
  notes                        text
);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'face_retention_log_trigger_chk') THEN
    ALTER TABLE storeops.face_retention_log
      ADD CONSTRAINT face_retention_log_trigger_chk
      CHECK (trigger IN ('purpose_satisfied', 'statutory_backstop', 'employee_request', 'tenant_disabled_purge'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_face_retention_log_org ON storeops.face_retention_log (org_id, destroyed_at DESC);
CREATE INDEX IF NOT EXISTS ix_face_retention_log_org_emp ON storeops.face_retention_log (org_id, employee_id);

ALTER TABLE storeops.face_retention_log ENABLE ROW LEVEL SECURITY;
-- Zero policies, zero anon/authenticated grants — service-role only (AGENT_CONTRACT §5), matching
-- every table added since the 2026-07-28 lockdown.

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 422 complete — face-descriptor retention schedule (90d default, 1095d statutory ceiling) + destruction audit log' AS status;
