-- 983_ai_guard_lease_extraction.sql — ONE AI DOOR: the lease / insurance extraction adopts the guard
--
-- OWNER DIRECTIVE 2026-09-05 (sanjot@): the AI "must be protected from third party misuse of the ai
-- api". Mig 972 built the shared guard; `backend/app/modules/storeops/doc_intel_ai.py`'s own header
-- then wrote down what adopting it would require: *"Converging the two needs the guard to grow a
-- purpose whose authorization check is the lease gate rather than super-admin — a small, deliberate
-- change to ONE shared decision function, which is exactly the point of having one."* This is that
-- change, plus the ceiling for the new purpose.
--
-- WHAT WAS UNBOUNDED. `POST /storeops/document-extract` (mig 965) was properly AUTHORIZED already —
-- `store_lease.can_see_lease`, management roles, fail-closed — but nothing else: no rate limit, no
-- daily budget, no per-call audit. An authorized manager could loop a 40-page lease through the
-- model all day and the only record of it was the metering counter (which bills, but never refuses).
--
-- HOW THE GUARD WAS GENERALISED WITHOUT BEING WEAKENED. `control_box.AI_PURPOSES` is now a REGISTRY:
-- each purpose NAMES the predicate that authorizes it, and `AI_AUTHORIZERS` holds the predicates.
--   · `control_box_triage`  -> `super_admin`   (UNCHANGED — still platform super-admin, nothing else)
--   · `remediation_diagnose`-> `module_scope`  (mig 982: helpdesk module + market/company scope)
--   · `lease_extraction`    -> `lease_access`  (THIS migration: `store_lease.can_see_lease`)
-- The lease predicate reads the capability flag ONLY — it deliberately does NOT fall back to
-- super-admin, so a purpose is satisfied on its OWN predicate or not at all (a platform super-admin
-- who does not hold the lease capability is refused there, proven in the harness; in production
-- `can_see_lease` grants a super-admin that capability, so nobody's real access changed).
-- FAIL-CLOSED IS STRUCTURAL: an unknown/unregistered/missing purpose is refused, a purpose naming a
-- predicate that does not exist authorizes NOBODY, and a predicate that raises denies. There is no
-- "no check" fallback anywhere in the decision.
-- AND WIDENING THE PREDICATE WIDENED NOTHING ELSE: the bounded server-validated subject (this org's
-- own document id, re-validated against an org-scoped lookup — never free text, never another
-- tenant's document), the per-org rate limit, the per-org daily call and token budget, and the audit
-- of EVERY attempt including refusals all apply to this purpose exactly as they do to the control
-- box's. Proven DB-free in backend/harness_ai_guard_purposes.py (111 checks) and at the call site in
-- backend/harness_doc_intel.py §K.
--
-- WHAT IS REUSED, NOT REBUILT (CLAUDE.md duplicate-check build gate):
--   · `core.ai_budget_config` + `core.ai_call_audit` (mig 972) — the same two tables, one more
--     `purpose` value. No new table, no second meter, and NO cost column: mig 718's
--     `core.token_rates` stays the only $/MTok source.
--   · `core/ai_gate.py` (mig 982) — the ONE reader of the ceiling and ONE writer of the audit.
--   · `store_lease.can_see_lease` (mig 946) — the SAME gate, still enforced at the route; the guard
--     restates it as a predicate rather than inventing a second rule.
--   · `billing/ai_meter.record()` — untouched, still metering only. Metering is not authorization.
--
-- WHAT THIS DOES NOT CHANGE. Nothing about what is sent to the model: still the tenant's own
-- document plus a server-built prompt, with NOTHING from `storeops.store_lease` in it (above all the
-- ACH/bank columns — this module has no code path that reads that row at all), and returned snippets
-- still masked for bank-ish digit runs by `doc_intel.scrub_snippet`. Nothing about money: an
-- extraction is still a quarantined draft, and `doc_intel.apply_plan` is still the only door to a
-- live column. Nothing about event-loop safety: the route still hops to a worker thread
-- (SEV-1 2026-07-30) with an explicit timeout x (1 + max_retries).
--
-- DEGRADES CLEANLY, AS BEFORE. No API key, or a tenant that switched AI off, still returns the clean
-- empty `not_extracted` draft the UI explains — never an exception. A rate-limit / budget /
-- authorization refusal is a 403 carrying the reason and nothing else about internal state.
--
-- SAFE: additive + idempotent, one config row. Re-runnable.
-- MONEY: touches NO payout, rate, plan or commission column. It bounds API SPEND, not payroll.
-- SECURITY: no grants changed; mig 972's RLS-on / no-policies / no-anon-grants posture is inherited.

BEGIN;

-- House ceiling for document extraction. Deliberately DIFFERENT numbers from the control box's: a
-- lease is a 40-page PDF read with adaptive thinking, so a single call legitimately costs far more
-- tokens than a 120-word triage note, while the call COUNT stays modest (documents are uploaded a
-- few at a time, by hand). RULE TWO: a tenant may override with its own row, and `enabled=false`
-- switches automatic reading off for that tenant while every lease/insurance screen keeps working —
-- the fields are simply typed in by hand, exactly as with no API key.
-- max_input_chars is not the bound that matters here (the document is sent as a file block, not as
-- text), and is left at the house value; the real bounds are the call and token caps.
INSERT INTO core.ai_budget_config (org_id, purpose, enabled, max_calls_per_hour, daily_call_cap,
                                   daily_token_cap, max_input_chars, notes)
VALUES ('00000000-0000-0000-0000-000000000001', 'lease_extraction', true, 20, 80, 3000000, 12000,
        'Lease / insurance / COI document extraction. Authorized by store_lease.can_see_lease '
        '(management roles), NOT super-admin. Token cap is large on purpose: one 40-page lease read '
        'with adaptive thinking costs far more than a triage note. AI off = type the fields by hand.')
ON CONFLICT (org_id, purpose) DO NOTHING;

COMMIT;

-- Deny codes this purpose can write to core.ai_call_audit.deny_code (free text, no CHECK):
--   not_lease_access  — the caller does not hold the lease/insurance management gate
--   unknown_check     — the subject was not this org's resolved document id (cross-tenant/garbage)
--   disabled / no_key — degrade to a clean empty draft, not an error
--   rate_limited / budget_exhausted — 403 with the reason
--
-- What document reading cost this tenant today (tokens; $ joins core.token_rates, mig 718):
--   select count(*) filter (where allowed) as calls,
--          sum(input_tokens + output_tokens) filter (where allowed) as tokens,
--          count(*) filter (where not allowed) as refusals
--     from core.ai_call_audit
--    where purpose = 'lease_extraction' and created_at > now() - interval '24 hours';
--
-- REVERT (removes only the CEILING — the purpose registry row and the wiring are application code
-- and revert with the commit; deleting this row falls back to the house defaults in
-- control_box.DEFAULT_AI_CONFIG, which are TIGHTER, never looser):
--   DELETE FROM core.ai_budget_config
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND purpose = 'lease_extraction';

SELECT 'Migration 983 — lease/insurance extraction routed through the shared AI guard (purpose lease_extraction)' AS status;
