-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 984 — SCOPED OPERATOR ROLES, ENFORCED ON THE PRE-EXISTING SUPER-ADMIN ENDPOINTS
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- Owner-approved follow-up to migrations 980/981. Proposal #1 of the two the operator-console build
-- ranked highest and deliberately did not ship.
--
-- WHAT 980 LEFT UNDONE (its own declared limit #2, verbatim): "Scoped roles are not enforced on
-- existing endpoints. Only the NEW console endpoints gate on capabilities; every pre-existing
-- super-admin endpoint still answers as it always did." So `support`, `billing`, `engineering` and
-- `readonly` were LABELS: any of them could still call /core/ip-block, /core/super-admins or
-- /billing/pricing, because those surfaces ask `_require_super_admin` and nothing else.
--
-- WHAT THIS MIGRATION ADDS
--   1. `core.platform_operator_policy.enforce_scoped_roles` — THE SCOPE SWITCH. FALSE by default.
--   2. `core.operator_route_capability` — the per-platform override of the route→capability map
--      that lives as a house DEFAULT in `app/modules/core/operator.py` (RULE TWO: config, never a
--      branch; same shape as `core.module_route_map` from migration 974).
--
-- ★ NOTHING IS NARROWED BY APPLYING THIS ★
-- The new column defaults FALSE, the seed that would turn it on is COMMENTED OUT at the bottom, and
-- the code returns "not enforced" before reading anything else while it is FALSE. Applying 984
-- changes who can do what by exactly nothing. The override table ships EMPTY.
--
-- ★ AND IT CANNOT LOCK THE OWNER OUT WHEN IT IS TURNED ON ★
--   · `owner` is every capability by definition, so the seeded owner rows from 980 keep full reach;
--   · a legacy super-admin (while `legacy_membership_flag_honored` is TRUE — the shipped default)
--     also carries every capability, and so does the house-admin bootstrap rung;
--   · an UNMAPPED route is never gated (it is not guessed onto a neighbouring capability);
--   · the operator console prefix, the identity/bootstrap routes and the whole impersonation prefix
--     are STRUCTURALLY exempt — the control that switches enforcement back off can never be gated
--     by enforcement;
--   · `operator.policy_change_decision` REFUSES to enable this while nobody would still hold
--     `policy.write` to disable it again, and warns out loud at exactly one holder;
--   · `OPERATOR_ENFORCE=0` in the environment kills it without touching the database at all.
-- `backend/harness_operator_scope_enforcement.py` proves each of those states.
--
-- Additive · idempotent · touches storeops.app_users not at all · no money column · no payout.

BEGIN;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. THE SCOPE SWITCH
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- FALSE  (default, and what every existing row keeps) = every pre-existing super-admin endpoint
--        answers exactly as it does today; a scoped role only gates the console. TODAY'S BEHAVIOUR.
-- TRUE   = `core.router._require_super_admin` additionally consults `operator.endpoint_decision`,
--        so a `support` operator stops being all-powerful outside the console.
ALTER TABLE core.platform_operator_policy
  ADD COLUMN IF NOT EXISTS enforce_scoped_roles BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN core.platform_operator_policy.enforce_scoped_roles IS
  'ACCESS-CUTTING. TRUE makes scoped operator roles gate the pre-existing super-admin endpoints. '
  'Default FALSE = today. Flip from Console -> Operators (which refuses it when it would leave '
  'nobody holding policy.write), or with the commented-out UPDATE at the foot of migration 984.';

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 2. THE ROUTE → CAPABILITY OVERRIDE MAP  (RULE TWO: config, never code)
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- The house default map lives in `operator.ROUTE_CAPABILITIES` and covers the super-admin surfaces
-- that exist today. Rows here are appended AFTER it, so a platform can gate a new surface, or
-- re-point an existing one at a different capability, WITHOUT a deploy — the same relationship
-- `core.module_route_map` (mig 974) has with `module_usage.MODULE_ROUTE_MAP`.
--
-- A row names a ROUTE and a CAPABILITY. It can never name a tenant, a person, a carrier or an org:
-- there is no org_id column here, deliberately, because platform authority does not belong to a
-- tenant (that is the whole point of the 980 separation).
--
-- `method` is an HTTP verb or '*'. Resolution: longest `route_prefix` wins; at equal length a
-- verb-specific row beats '*'; a row whose `capability` is not in the code vocabulary is IGNORED
-- (a typo must never manufacture, or silently remove, authority).
CREATE TABLE IF NOT EXISTS core.operator_route_capability (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  method        TEXT NOT NULL DEFAULT '*',
  route_prefix  TEXT NOT NULL,                 -- full ASGI path prefix, e.g. '/api/v1/core/ip-block'
  capability    TEXT NOT NULL,                 -- must be one of operator.ALL_CAPABILITIES
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  notes         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS operator_route_capability_key
  ON core.operator_route_capability (method, route_prefix);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 3. LOCKDOWN — same posture as 980: RLS on, no policies, no anon/authenticated grants
-- ══════════════════════════════════════════════════════════════════════════════════════════════
ALTER TABLE core.operator_route_capability ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  BEGIN REVOKE ALL ON core.operator_route_capability FROM anon, authenticated;
  EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN GRANT ALL ON core.operator_route_capability TO service_role;
  EXCEPTION WHEN OTHERS THEN NULL; END;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 984 — scoped operator roles: the enforcement switch (OFF) + the route->capability override map (EMPTY)' AS status;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- ⚠ TURNING ENFORCEMENT ON — DELIBERATELY COMMENTED OUT. ACCESS-CUTTING. OWNER APPROVAL ONLY. ⚠
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- Running the statement below makes a scoped operator role gate every mapped super-admin surface.
--
-- PREFER THE CONSOLE: Console -> Operators shows `GET /core/operator/enforcement`, which lists, per
-- operator, exactly which route prefixes they would stop being able to reach — and the POST refuses
-- the flip outright when nobody would still hold `policy.write` to undo it.
--
-- BEFORE YOU RUN IT:
--   1. SELECT email, operator_role, is_active, expires_at FROM core.platform_operator;
--      -- confirm YOUR row is present, active, un-expired and `owner` (owner = every capability).
--   2. Confirm `legacy_membership_flag_honored` is still TRUE, or that at least one active `owner`
--      row exists. Either one alone is enough to keep full reach:
--      SELECT legacy_membership_flag_honored, enforce_scoped_roles FROM core.platform_operator_policy;
--   3. Keep these two rollbacks to hand; either restores today's behaviour instantly, and the second
--      one works even if the console itself is unreachable:
--        UPDATE core.platform_operator_policy SET enforce_scoped_roles = FALSE;
--        -- or, without any database access at all: set OPERATOR_ENFORCE=0 and restart the API.
--
-- UPDATE core.platform_operator_policy SET enforce_scoped_roles = TRUE, updated_at = now();
--
-- EXAMPLE override rows — also commented out. Nothing is mapped by config on a fresh install; the
-- house default map in operator.ROUTE_CAPABILITIES is the whole of it.
--
-- INSERT INTO core.operator_route_capability (method, route_prefix, capability, notes) VALUES
--   ('*',   '/api/v1/core/audit',        'audit.read',    'gate the retention sweep as an audit act'),
--   ('GET', '/api/v1/core/export-event', 'audit.read',    'reads of the export trail'),
--   ('*',   '/api/v1/payables',          'billing.write', 'vendor payables is a billing surface')
-- ON CONFLICT (method, route_prefix) DO NOTHING;

-- REVERT:
--   UPDATE core.platform_operator_policy SET enforce_scoped_roles = FALSE;
--   ALTER TABLE core.platform_operator_policy DROP COLUMN IF EXISTS enforce_scoped_roles;
--   DROP TABLE IF EXISTS core.operator_route_capability;
--   -- Nothing else to undo: 984 modified no existing row and touched storeops.app_users not at all.
--   -- With the column gone, `operator.effective_policy` falls back to POLICY_DEFAULTS
--   -- (enforce_scoped_roles = False) and the gate is byte-identical to its pre-984 self.
