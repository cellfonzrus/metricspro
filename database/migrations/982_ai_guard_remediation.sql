-- 982_ai_guard_remediation.sql — put the auto-remediation AI diagnosis behind the SHARED guard
--
-- OWNER DIRECTIVE 2026-09-05 (sanjot@): the AI "must be protected from third party misuse of the ai
-- api and only restricted to this module". Mig 972 built that guard for the control box and named
-- `remediation/propose` as the next adopter; docs/SYSTEM_DATA_FLOW_INDEX.md §20 carries it as a
-- declared gap ("currently org-param-gated only"). This closes it.
--
-- WHAT WAS OPEN. `POST /api/v1/remediation/propose` (mig 097) makes an outbound Anthropic call with
-- the caller's own free text. The tenant middleware verifies a bearer token and rewrites org_id, so
-- the call was reachable by ANY signed-in user of ANY tenant at ANY role — a sales rep, a store
-- manager — with no role check, no rate limit, no budget and no audit. The product's navigation
-- (frontend/src/lib/rbac.ts) only ever offered the console to helpdesk-module users with
-- company-wide or market-wide scope; that rule simply was not enforced anywhere a request passes.
--
-- WHAT IT IS NOW. `core/control_box.ai_guard_decision`, purpose `remediation_diagnose`:
--   · AUTHORIZATION = the purpose's own predicate (`module_scope`: module `helpdesk` + scope
--     all/market, or a platform super-admin) — the SAME rule the navigation already applies, now
--     enforced server-side. NOT super-admin: this is a tenant console, and super-admin-only would
--     delete a working tenant feature rather than protect it.
--   · Everything else is the control box's, unchanged and applied identically: per-org rate limit,
--     per-org daily call + token budget, an audit row for EVERY attempt including refusals, and
--     bounded input (control characters stripped, non-empty, capped by `max_input_chars`; the audit
--     stores a DIGEST of the issue text, never the tenant's words).
--   · Degrades exactly as before: a refusal returns no AI verdict, which is what an absent API key
--     has always returned, so /propose ESCALATES to a human instead of raising.
--
-- ⚠ THIS NARROWS ACCESS, DELIBERATELY, AND THE OWNER APPROVED IT. After this, a store manager, a
-- sales rep, or any login without the helpdesk module can no longer spend the AI key from this
-- endpoint; they get the ordinary "escalated for a person to handle" answer. NOBODY WHO CAN
-- LEGITIMATELY USE THE CONSOLE TODAY LOSES IT. The narrowing itself lives in application code
-- (remediation/router.py + core/control_box.py) — this migration only sets the CEILINGS for the new
-- purpose, so deleting the seeded row below does NOT reopen the endpoint; it just falls back to the
-- house defaults in `control_box.DEFAULT_AI_CONFIG`.
--
-- WHAT IS REUSED, NOT REBUILT (CLAUDE.md duplicate-check build gate):
--   · `core.ai_budget_config` + `core.ai_call_audit` (mig 972) — the SAME two tables, keyed by the
--     `purpose` discriminator they were designed around. No new table, no second meter.
--   · `core.token_rates` (mig 718) stays the ONLY $/MTok source — the audit stores TOKENS ONLY.
--   · `control_box.ai_guard_decision` / `ai_audit_row` / `rollup_usage` — the same pure functions.
--   · `core/ai_gate.py` (new, this commit) is the ONE reader of ai_budget_config and the ONE writer
--     of ai_call_audit; `control_box_api`'s private helpers now delegate to it instead of being
--     copied a third time.
--   · `billing/ai_meter.record()` is untouched and still METERS ONLY — metering is not
--     authorization (§21), and this endpoint was already metered before it was authorized.
--
-- SAFE: additive + idempotent, one config row. Re-runnable.
-- MONEY: touches NO payout, rate, plan or commission column. It bounds API SPEND, not payroll.
-- SECURITY: no grants changed; mig 972's RLS-on / no-policies / no-anon-grants posture is inherited.

BEGIN;

-- House ceiling for the remediation triage purpose (CLAUDE.md house org; a tenant may override with
-- its own row, and `enabled=false` switches the AI off for that tenant while /propose keeps working
-- in manual mode and keeps escalating — the console never depended on the model to function).
-- max_input_chars 3000 is deliberately the SAME bound the endpoint already applied to the issue
-- text, so an authorized caller sees byte-identical behaviour.
INSERT INTO core.ai_budget_config (org_id, purpose, enabled, max_calls_per_hour, daily_call_cap,
                                   daily_token_cap, max_input_chars, notes)
VALUES ('00000000-0000-0000-0000-000000000001', 'remediation_diagnose', true, 20, 60, 300000, 3000,
        'Auto-remediation issue triage. Authorized by the helpdesk module + market/company scope '
        '(nav parity), NOT super-admin. The console works with the AI off: it escalates to a human.')
ON CONFLICT (org_id, purpose) DO NOTHING;

COMMIT;

-- Deny codes this purpose can write to core.ai_call_audit.deny_code (free text, no CHECK — the
-- column was left generic in mig 972 precisely so a new purpose adds no schema change):
--   not_remediation_operator  — the caller lacks the helpdesk module or a broad enough scope
--   wrong_purpose             — an unregistered purpose was asked for (fail-closed)
--   unknown_authorizer        — a purpose declared a predicate that does not exist (fail-closed)
--   no_subject                — the issue text was empty after bounding (nothing to send)
--   disabled / no_key / rate_limited / budget_exhausted — as mig 972
--
-- Who has been refused lately on this purpose (the probe signal):
--   select created_at, actor_email, deny_code from core.ai_call_audit
--    where purpose = 'remediation_diagnose' and not allowed order by created_at desc limit 50;
--
-- REVERT (removes only the CEILING; the authorization narrowing is in application code and reverts
-- with the commit, not with this file):
--   DELETE FROM core.ai_budget_config
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND purpose = 'remediation_diagnose';

SELECT 'Migration 982 — remediation AI diagnosis routed through the shared guard (purpose remediation_diagnose)' AS status;
