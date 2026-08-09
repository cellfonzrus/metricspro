-- 420_storeops_face_recognition_toggle.sql — mod-people band 400-499.
--
-- OWNER DIRECTIVE 2026-08-09: "disable the face recognition feature for all tenants for now, keep the
-- option of turning on at a later date; when turning on, as if the face recognition consent has been
-- signed by all employees, and it should be assigned per employee."
--
-- Also closes the highest-stakes item in docs/metricspro-security-plan.md Phase 9 (biometric
-- compliance / BIPA): face geometry is regulated biometric data, and the Chicago-area stores sit under
-- Illinois BIPA, which carries a private right of action. Turning the capture OFF platform-wide is the
-- strongest available mitigation while the consent paperwork and retention schedule are settled.
--
-- ── WHAT THIS ADDS ────────────────────────────────────────────────────────────────────────────────
--   storeops.tenants (tenant-wide MASTER switch — RULE TWO: config table + admin UI, never hard-coded):
--     face_recognition_enabled          boolean NOT NULL DEFAULT false  -- OFF for every existing tenant
--     face_recognition_default_for_employees boolean NOT NULL DEFAULT true
--     face_recognition_enabled_at       timestamptz NULL   -- audit: when the master switch last went ON
--     face_recognition_enabled_by       text        NULL   -- audit: who turned it on
--
--   storeops.employees (per-employee ASSIGNMENT + consent record):
--     face_recognition_enabled  boolean     NULL  -- NULL = inherit the tenant default; true/false = assigned
--     face_consent_status       text        NULL  -- 'signed' | 'declined' | NULL (nothing recorded yet)
--     face_consent_at           timestamptz NULL
--     face_consent_source       text        NULL  -- 'assumed_on_enable' | 'manual' | 'declined'
--
-- ── SEMANTICS (deliberately NOT the same as migration 418's lunch override) ────────────────────────
-- `face_recognition_enabled` on tenants is a MASTER switch, not merely a default: when it is false the
-- feature is off for EVERY employee of that tenant, and no per-employee value can turn it back on. The
-- lunch-deduction pattern (per-field independent override) would let one stray employee row re-enable
-- biometric capture on a tenant the owner has switched off — unacceptable for regulated data. The
-- per-employee assignment only has meaning while the master switch is ON:
--
--     tenant.face_recognition_enabled = false            -> OFF for everyone (hard gate)
--     employee.face_recognition_enabled = false          -> OFF for that employee
--     employee.face_consent_status      = 'declined'     -> OFF for that employee (consent wins)
--     employee.face_recognition_enabled = true           -> ON for that employee
--     employee.face_recognition_enabled IS NULL          -> tenant.face_recognition_default_for_employees
--
-- `face_recognition_default_for_employees` is what a NULL (unassigned) employee inherits once the
-- master switch goes on. Default true = flipping the switch back on restores exactly today's behaviour
-- for everyone; set it false in the admin UI to get "nobody, except the people I explicitly assign".
--
-- ── CONSENT ("as if signed by all employees") ─────────────────────────────────────────────────────
-- This migration does NOT pre-stamp consent, because at OFF nobody's face is being captured and a
-- consent row dated today would be a fabricated record. Instead the BACKEND stamps it at the moment
-- the owner turns the master switch on (PUT /storeops/timeclock/face-config): every employee of that
-- tenant with no consent record gets face_consent_status='signed', face_consent_at=now(),
-- face_consent_source='assumed_on_enable'. That is the owner's instruction implemented as an explicit,
-- dated, per-employee audit row rather than as a silent assumption — a real BIPA question ("show me
-- this person's consent and its date") gets an answerable row, and the 'assumed_on_enable' source
-- states honestly how it was obtained. An employee already recorded as 'declined' is never re-stamped.
--
-- ── EXISTING DATA ─────────────────────────────────────────────────────────────────────────────────
-- The 77 already-enrolled storeops.face_descriptors rows are LEFT IN PLACE and simply stop being read
-- or written while the feature is off. That is what makes "turn it on at a later date" instant instead
-- of a re-enrollment campaign. (Security-plan Phase 9.2 asks for a retention/destruction schedule for
-- these descriptors — that is a separate, owner-decision package; see docs/handoffs/people.md.)
--
-- ── DEGRADE (AGENT_CONTRACT §5) ───────────────────────────────────────────────────────────────────
-- face_recognition.py wraps every read of these columns in try/except and FAILS CLOSED (enabled=false)
-- whenever they don't exist yet. So the application code disables the feature the moment it deploys,
-- migration or no migration — deliberately the opposite of migration 418's "degrade = change nothing",
-- because here the safe direction and the requested direction are the same one: off.
--
-- Additive + idempotent. No new table -> no new GRANT/RLS (same posture as migrations 409/418).
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS face_recognition_enabled boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS face_recognition_default_for_employees boolean NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS face_recognition_enabled_at timestamptz NULL,
  ADD COLUMN IF NOT EXISTS face_recognition_enabled_by text NULL;

ALTER TABLE storeops.employees
  ADD COLUMN IF NOT EXISTS face_recognition_enabled boolean NULL,
  ADD COLUMN IF NOT EXISTS face_consent_status text NULL,
  ADD COLUMN IF NOT EXISTS face_consent_at timestamptz NULL,
  ADD COLUMN IF NOT EXISTS face_consent_source text NULL;

-- Idempotent value guard (mirrors migration 418's CHECK-constraint-if-absent pattern).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'employees_face_consent_status_chk') THEN
    ALTER TABLE storeops.employees
      ADD CONSTRAINT employees_face_consent_status_chk
      CHECK (face_consent_status IS NULL OR face_consent_status IN ('signed', 'declined'));
  END IF;
END $$;

-- Re-running must never silently re-enable a tenant the owner has since turned ON: ADD COLUMN IF NOT
-- EXISTS leaves an existing column (and its values) completely untouched, so there is deliberately no
-- UPDATE statement here. The DEFAULT false is what disables every tenant on the FIRST run.
NOTIFY pgrst, 'reload schema';
SELECT 'Migration 420 complete — face recognition OFF for every tenant; per-employee assignment + consent columns ready'
       AS status;
