-- 980_platform_operator_console.sql — separate the PLATFORM OPERATOR persona from the tenant persona
--
-- OWNER DIRECTIVE 2026-09-05 (sanjot@): "Need to separate the super admin access of
-- Sanjot@cellfonzrus.com from Cellfonz r us tenant, make a separate view for the super admin but the
-- option for the super admin to log in to any tenant from it is list of tenants dashboard an option
-- to log in from there, Tennat billing dashboard will be another module on the super admin side,
-- what other industry wide super admin controls are missing yet very import do a thorough research
-- and add those also."
--
-- THE PROBLEM IN ONE LINE. Platform authority today is `storeops.app_users.super_admin` — a boolean
-- on the row that ALSO says "this login is an employee of tenant T". The owner's power over the
-- whole platform is literally a column on their CellfonzRUs employment record. This migration gives
-- that authority a home of its own.
--
-- DUPLICATE CHECK (CLAUDE.md build gate, owner 2026-09-02). Searched docs/SYSTEM_DATA_FLOW_INDEX.md
-- for super-admin / tenant / impersonat / switch / audit / operator before writing a line. Everything
-- already present is REUSED, not rebuilt:
--   · core.router._require_super_admin        — the ONE gate. Still the only gate; the API layer
--                                               calls it and unions this registry on top.
--   · GET /core/tenants                       — the tenant directory. The console CALLS it.
--   · the cross-tenant switcher (x-active-org + tenant_middleware's super-admin no-rewrite bypass)
--                                             — the entry MECHANISM. This adds the record, the
--                                               expiry and the banner it never had; no new bypass.
--   · core.impersonation_* (mig 730)          — "view as employee". UNTOUCHED, and deliberately not
--                                               extended: entering a tenant ≠ wearing an employee's
--                                               face. `impersonate` stays DEFAULT-DENY with no
--                                               super-admin bypass.
--   · core.access_log (mig 856)               — stays the per-request trail. core.operator_action
--                                               here records INTENT ("entered tenant X because Y
--                                               until Z"), which a request log cannot express.
--   · core.system_check* (migs 970-972)       — the control box. Linked to, never recomputed.
--
-- ★ NO LOCKOUT — READ THIS BEFORE APPLYING ★
-- This migration CANNOT remove anyone's access, at any point, in any order:
--   · It is purely ADDITIVE. It creates four new tables and touches storeops.app_users NOT AT ALL —
--     no column dropped, no flag cleared, no row updated. Every existing super-admin keeps the exact
--     authority they have today.
--   · The backend treats a MISSING table as "no operator registry" and a MISSING policy row as
--     POLICY_DEFAULTS, and POLICY_DEFAULTS honors the legacy flag. So pre-migration, half-applied
--     and fully-applied all authorize the existing super-admin identically
--     (backend/harness_operator_console.py §A proves all six states).
--   · Section 5 SEEDS one `owner` operator row per EXISTING app_users.super_admin — derived from
--     DATA, never from a literal email (RULE TWO) — so the registry is populated the moment this
--     runs and the eventual cutover has nobody to lock out.
--   · The CUTOVER (stop honoring the legacy tenant flag) is a one-line UPDATE at the foot of this
--     file, COMMENTED OUT. Deploying code never performs it. The API refuses it while zero active
--     operators exist, and reversing it is the same UPDATE with `true`.
--
-- SAFE: additive + idempotent (create … if not exists / on conflict do nothing). Re-runnable.
-- MONEY: touches NO payout, rate, plan, commission, or paid/earned column. Access + audit only.
-- SECURITY: RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5). The backend
--           service role bypasses RLS; the frontend anon key is auth-only and never reaches these.
-- SCHEMA CHOICE: core.*, not a new schema — PostgREST serves only the exposed schemas
--           (public/commcalc/storeops/core/notify); a .schema("operator") call would 404. Same
--           reasoning migrations 053, 715, 800 and 970 recorded.

BEGIN;

CREATE SCHEMA IF NOT EXISTS core;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. THE SEPARATED IDENTITY — platform authority that belongs to NO tenant
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- Keyed by AUTH ID, with no org_id column at all. That absence is the entire point of the migration:
-- a row here says "this human operates the platform", and says nothing whatsoever about which
-- company employs them. A person can hold this and no tenant membership; a person can hold a tenant
-- membership and not this.
--
-- SCOPED ROLES (industry standard: support ≠ billing ≠ engineering ≠ owner). `operator_role` names a
-- capability set defined in backend/app/modules/core/operator.py::OPERATOR_ROLES. `owner` is
-- deliberately ALL capabilities, so seeding today's super-admins as `owner` is a perfect no-op on
-- their authority. `capabilities` is an optional per-row {capability: bool} override (a deny wins),
-- the same grant/deny precedence `_can_edit_setting` already uses for settings areas.
--
-- JUST-IN-TIME ELEVATION: `expires_at` NULL = a standing operator; set = authority that switches
-- itself off ("engineering, until Friday"). Enforced in `operator.operator_row_active`, not by a
-- sweep, so an expired row stops conferring authority the instant it expires even if nothing runs.
CREATE TABLE IF NOT EXISTS core.platform_operator (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  auth_id            UUID NOT NULL UNIQUE,          -- the login. NOT an org membership.
  email              TEXT,                          -- display/audit convenience; auth_id is the key
  operator_role      TEXT NOT NULL DEFAULT 'owner', -- owner | support | billing | engineering | readonly
  capabilities       JSONB,                         -- optional per-row {capability: bool} override
  is_active          BOOLEAN NOT NULL DEFAULT TRUE,
  expires_at         TIMESTAMPTZ,                   -- NULL = standing; set = time-boxed elevation
  notes              TEXT,
  granted_by_auth_id UUID,
  granted_by_email   TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS platform_operator_active_idx
  ON core.platform_operator (is_active, expires_at);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 2. THE POLICY — one row, and the ONLY place the cutover can be performed
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- RULE TWO (config, never code): no thresholds or switches live in a branch. An ABSENT row means
-- POLICY_DEFAULTS, and POLICY_DEFAULTS is exactly today's behaviour — which is what makes the
-- half-applied state safe.
--
-- `legacy_membership_flag_honored` IS THE CUTOVER. TRUE (default, and seeded TRUE below) means
-- storeops.app_users.super_admin still grants platform authority, precisely as it does now. The
-- owner flips it to FALSE when the registry is populated and they are ready; the API refuses the
-- flip while zero active operators exist, and flipping back is the same call with TRUE.
--
-- `require_entry_session` TRUE would make an entry session MANDATORY before a super-admin may act as
-- another tenant. That is ACCESS-CUTTING, so it ships FALSE and is a PROPOSAL, not a change: the
-- switcher keeps working untouched.
CREATE TABLE IF NOT EXISTS core.platform_operator_policy (
  id                             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  legacy_membership_flag_honored BOOLEAN NOT NULL DEFAULT TRUE,   -- ← THE CUTOVER SWITCH
  require_entry_session          BOOLEAN NOT NULL DEFAULT FALSE,  -- ← access-cutting; stays off
  entry_reason_required          BOOLEAN NOT NULL DEFAULT TRUE,
  entry_min_minutes              INT     NOT NULL DEFAULT 5,
  entry_max_minutes              INT     NOT NULL DEFAULT 60,
  entry_default_minutes          INT     NOT NULL DEFAULT 30,
  anomaly_burst_actions          INT     NOT NULL DEFAULT 25,
  anomaly_burst_minutes          INT     NOT NULL DEFAULT 10,
  anomaly_fanout_tenants         INT     NOT NULL DEFAULT 5,
  anomaly_denied_streak          INT     NOT NULL DEFAULT 5,
  updated_at                     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Exactly one policy row, ever. (Seeded below with the defaults = today's behaviour.)
CREATE UNIQUE INDEX IF NOT EXISTS platform_operator_policy_singleton
  ON core.platform_operator_policy ((true));

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 3. THE TAMPER-EVIDENT OPERATOR TRAIL
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- Append-only AND hash-chained: `hash` = sha256(prev_hash ‖ canonical(sealed fields)), so editing or
-- deleting ANY row breaks every link after it and `operator.verify_chain` reports the exact `seq`
-- where the chain parts. On a database the operator also administers this cannot PREVENT tampering
-- — nothing can — but it makes tampering undeniable, which is what "immutable, tamper-evident admin
-- audit" means in practice. The UPDATE/DELETE revoke below raises the bar for everything short of
-- the service role.
--
-- The operator's OWN identity is mandatory (`actor_auth_id`), never the tenant's — the owner's
-- directive: "audited with the operator's OWN identity, never anonymised behind the tenant".
CREATE TABLE IF NOT EXISTS core.operator_action (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  seq            BIGINT NOT NULL UNIQUE,      -- dense chain position; a gap IS a detected deletion
  actor_auth_id  UUID NOT NULL,               -- the OPERATOR, always
  actor_email    TEXT,
  action         TEXT NOT NULL,               -- tenant.enter | tenant.exit | operator.grant | …
  target_org_id  UUID,                        -- the tenant acted upon, when there is one
  target_ref     TEXT,                        -- free-text subject (tenant name, operator email, id)
  detail         JSONB NOT NULL DEFAULT '{}'::jsonb,   -- redacted server-side before it lands here
  prev_hash      TEXT NOT NULL,
  hash           TEXT NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS operator_action_actor_idx  ON core.operator_action (actor_auth_id, seq DESC);
CREATE INDEX IF NOT EXISTS operator_action_org_idx    ON core.operator_action (target_org_id, seq DESC);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 4. TENANT-ENTRY SESSIONS — the record the cross-tenant switcher never had
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- A super-admin can ALREADY act as any tenant: pick a company in the header switcher, the client
-- stores it and sends `x-active-org`, and tenant_middleware's super-admin branch honours it without
-- rewriting. Nothing is written down. This table is that missing record — who entered which company,
-- WHY, from what IP, and until when.
--
-- It is not a new privilege. `operator.entry_decision` grants exactly ("acting_org",) — the same
-- acting-tenant switch — and the harness FAILS if `impersonate` ever appears in that list.
CREATE TABLE IF NOT EXISTS core.operator_entry_session (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_auth_id UUID NOT NULL,
  actor_email   TEXT,
  org_id        UUID NOT NULL,
  reason        TEXT,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ NOT NULL,        -- HARD time-box; no revocation sweep required
  ended_at      TIMESTAMPTZ,
  ended_reason  TEXT,                        -- operator_exit | superseded | expired
  source_ip     TEXT,
  user_agent    TEXT
);
CREATE INDEX IF NOT EXISTS operator_entry_open_idx ON core.operator_entry_session (actor_auth_id, ended_at);
CREATE INDEX IF NOT EXISTS operator_entry_org_idx  ON core.operator_entry_session (org_id, started_at DESC);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 5. SEED — the policy row, and one operator identity per EXISTING super-admin
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- RULE TWO: derived entirely from DATA. No email literal, no org_id literal, no person named. If the
-- platform has three super-admins today it gets three `owner` rows; if it has none it gets none, and
-- the legacy flag (still honored) plus the house-admin bootstrap rung in `_require_super_admin`
-- remain the way in — unchanged.
--
-- This is the step that makes the eventual cutover a non-event: by the time the owner considers
-- flipping `legacy_membership_flag_honored`, every login that has access today already holds a
-- platform-operator record granting the same capabilities.
INSERT INTO core.platform_operator_policy (legacy_membership_flag_honored)
SELECT TRUE
WHERE NOT EXISTS (SELECT 1 FROM core.platform_operator_policy);

INSERT INTO core.platform_operator (auth_id, email, operator_role, is_active, notes)
SELECT DISTINCT ON (u.auth_id)
       u.auth_id,
       lower(u.email),
       'owner',
       TRUE,
       'Seeded by migration 980 from the existing storeops.app_users.super_admin flag. Same authority as before; this row is what survives the cutover.'
  FROM storeops.app_users u
 WHERE u.super_admin IS TRUE
   AND u.auth_id IS NOT NULL
 ORDER BY u.auth_id, u.last_login DESC NULLS LAST
ON CONFLICT (auth_id) DO NOTHING;

-- ── Security posture (AGENT_CONTRACT §5): RLS on, no policies, no anon/authenticated grants ────
-- Plus the append-only posture on the trail: even a compromised non-service role cannot rewrite it.
ALTER TABLE core.platform_operator        ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.platform_operator_policy ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.operator_action          ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.operator_entry_session   ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  BEGIN REVOKE ALL ON core.platform_operator, core.platform_operator_policy,
                      core.operator_action, core.operator_entry_session
         FROM anon, authenticated; EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN GRANT ALL ON core.platform_operator, core.platform_operator_policy,
                     core.operator_entry_session TO service_role; EXCEPTION WHEN OTHERS THEN NULL; END;
  -- The trail: INSERT + SELECT only, for everyone including service_role. An append-only table is a
  -- weaker promise than the hash chain, but the two together mean a tamper needs BOTH a grant change
  -- and a full chain rewrite.
  BEGIN GRANT SELECT, INSERT ON core.operator_action TO service_role; EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN REVOKE UPDATE, DELETE ON core.operator_action FROM service_role; EXCEPTION WHEN OTHERS THEN NULL; END;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 980 — platform operator console: separated identity, policy, tamper-evident trail, tenant-entry sessions' AS status;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- ⚠ THE CUTOVER — DELIBERATELY COMMENTED OUT. ACCESS-CUTTING. OWNER APPROVAL ONLY. ⚠
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- Running the statement below stops `storeops.app_users.super_admin` from granting platform
-- authority. AFTERWARDS, ONLY an active row in core.platform_operator gets anyone into the console
-- or any super-admin endpoint.
--
-- BEFORE YOU RUN IT (or better: use the Console → Operators page, which checks all of this for you
-- and REFUSES the flip when it would lock everybody out):
--   1. SELECT email, operator_role, is_active, expires_at FROM core.platform_operator;
--      — your own login MUST be there, active, and un-expired.
--   2. Confirm at least TWO active operators, so losing one account is not losing the platform.
--   3. Keep this rollback to hand — it restores today's behaviour instantly:
--        UPDATE core.platform_operator_policy SET legacy_membership_flag_honored = TRUE;
--
-- UPDATE core.platform_operator_policy SET legacy_membership_flag_honored = FALSE, updated_at = now();
--
-- SECOND PROPOSAL, also commented out — make the tenant-entry SESSION mandatory, so a super-admin
-- can no longer act as another tenant through the bare switcher without a reason and a time-box.
-- This is the strongest version of the owner's ask, and it is access-cutting, so it is theirs to
-- choose. Enforcement is NOT wired into tenant_middleware in this change: turning this on today
-- affects the console's own affordances only. See the PR comment.
--
-- UPDATE core.platform_operator_policy SET require_entry_session = TRUE, updated_at = now();

-- REVERT:
--   DROP TABLE IF EXISTS core.operator_entry_session;
--   DROP TABLE IF EXISTS core.operator_action;
--   DROP TABLE IF EXISTS core.platform_operator_policy;
--   DROP TABLE IF EXISTS core.platform_operator;
--   -- Nothing else to undo: this migration never modified storeops.app_users, so dropping these
--   -- four tables returns the platform exactly to its pre-980 authorization behaviour.
