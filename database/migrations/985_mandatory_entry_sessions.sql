-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 985 — MANDATORY TENANT-ENTRY SESSIONS: the audit trail becomes a PRECONDITION, not a habit
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- Owner-approved follow-up to migrations 980/981. Proposal #2 of the two the operator-console build
-- ranked highest and deliberately did not ship.
--
-- WHAT 980 LEFT UNDONE (its own declared limit #1, verbatim): "Entry sessions are RECORDED, not yet
-- REQUIRED. `require_entry_session` defaults FALSE and is not wired into tenant_middleware, so the
-- bare switcher still works exactly as before." So the audited, time-boxed, banner'd way into a
-- tenant sat NEXT TO the unaudited one, and an operator could always take the quiet door.
--
-- WHAT THIS MIGRATION ADDS
--   1. Nothing new to decide with — `core.platform_operator_policy.require_entry_session` already
--      exists (mig 980) and already defaults FALSE. 985 is where it becomes ENFORCED, in
--      `app/core/tenant_middleware.py`'s super-admin branch, via the pure
--      `operator.entry_requirement_decision`.
--   2. `operator_entry_current_idx` — the index the per-request lookup needs
--      (actor_auth_id, org_id, started_at DESC). Without it the requirement would be a sequential
--      scan on every cross-tenant request.
--
-- SUPERSEDES A NOTE IN 980. Migration 980's commented-out block says of `require_entry_session`:
-- "Enforcement is NOT wired into tenant_middleware in this change: turning this on today affects the
-- console's own affordances only." As of 985 that is no longer true — turning it on now genuinely
-- refuses cross-tenant requests that have no open session. 980 is left byte-identical (it may
-- already be applied); this file is the current word.
--
-- ★ NOTHING CHANGES BY APPLYING THIS ★
-- `require_entry_session` keeps its FALSE default, the statement that turns it on is COMMENTED OUT
-- at the bottom, and the middleware returns "nothing to enforce" before any database work while it
-- is FALSE. Applying 985 adds one index and changes who can reach what by exactly nothing.
--
-- ★ AND IT CANNOT STRAND THE OWNER WHEN IT IS TURNED ON ★
--   · YOUR OWN HOME TENANT IS NEVER GATED. A requested org that is one of the login's own
--     memberships needs no entry session — including when this very ledger is unreadable. That is
--     the escape hatch the scoped-role work (mig 984) also depends on;
--   · a request that names no `x-active-org` claims no other tenant and is untouched;
--   · `/core/operator/*` is EXEMPT, so the console — where a session is opened, and where this
--     requirement is switched back off — can never be blocked by the requirement itself. So are
--     /core/me, /core/my-tenants, /core/bootstrap, the tenant directory and the status notice;
--   · `operator.policy_change_decision` REFUSES to turn this on when no active operator would hold
--     `tenant.enter` (nobody could ever enter anything);
--   · `OPERATOR_ENTRY_ENFORCE=0` in the environment kills it without touching the database at all;
--   · a foreign tenant with no session is REFUSED (403 + a code), never silently rewritten to some
--     other company — quietly serving a different tenant's data is the shape of incident §19.15.
-- `backend/harness_operator_entry_enforcement.py` proves each of those states.
--
-- Additive · idempotent · touches storeops.app_users not at all · no money column · no payout.

BEGIN;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. THE LOOKUP INDEX
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- The middleware asks exactly one question per cross-tenant request: "what is this operator's most
-- recent entry session for THIS company?" 980's `operator_entry_open_idx (actor_auth_id, ended_at)`
-- does not serve it — the org is in the predicate and `started_at DESC` is the ordering.
CREATE INDEX IF NOT EXISTS operator_entry_current_idx
  ON core.operator_entry_session (actor_auth_id, org_id, started_at DESC);

COMMENT ON COLUMN core.platform_operator_policy.require_entry_session IS
  'ACCESS-CUTTING. TRUE makes an open, unexpired core.operator_entry_session a PRECONDITION for a '
  'super-admin to act as a company they are not a member of (mig 985 wires it into '
  'tenant_middleware). Default FALSE = today, the bare cross-tenant switcher. The caller''s OWN '
  'memberships, /core/operator/*, /core/me, /core/my-tenants, /core/bootstrap, the tenant directory '
  'and the status notice are never gated by it. Kill switch: OPERATOR_ENTRY_ENFORCE=0.';

COMMIT;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 985 — mandatory tenant-entry sessions: the lookup index (the requirement itself stays OFF)' AS status;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- ⚠ MAKING ENTRY SESSIONS MANDATORY — DELIBERATELY COMMENTED OUT. ACCESS-CUTTING. OWNER ONLY. ⚠
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- After the statement below, a platform super-admin can no longer act as a company they are not a
-- member of without first opening an entry session (Console → Companies → Enter), which records who
-- entered, why, from what IP, and until when — and shows that company's own admins the same record
-- at GET /core/tenant-operator-access.
--
-- BEFORE YOU RUN IT:
--   1. Confirm you hold `tenant.enter`:
--      SELECT email, operator_role, is_active, expires_at FROM core.platform_operator;
--      (`owner` and `support` both include it; `billing` and `readonly` do not.)
--   2. Open one entry session from the console first and confirm the banner appears — that proves
--      the whole path works before it is required.
--   3. Keep these rollbacks to hand; either restores today's behaviour instantly, and the second
--      works even if the API is the thing that is broken:
--        UPDATE core.platform_operator_policy SET require_entry_session = FALSE;
--        -- or, without any database access at all: set OPERATOR_ENTRY_ENFORCE=0 and restart the API.
--   4. Note what this does NOT do: it never touches your own company, and it never touches a normal
--      (non-super-admin) login, whose org is rewritten to their membership as it always was.
--
-- UPDATE core.platform_operator_policy SET require_entry_session = TRUE, updated_at = now();

-- REVERT:
--   UPDATE core.platform_operator_policy SET require_entry_session = FALSE;
--   DROP INDEX IF EXISTS core.operator_entry_current_idx;
--   -- Nothing else to undo: 985 created no table, modified no existing row, and touched
--   -- storeops.app_users not at all. With the flag FALSE the middleware's super-admin branch is
--   -- byte-identical to its pre-985 self.
