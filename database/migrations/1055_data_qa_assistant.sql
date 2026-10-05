-- 1055_data_qa_assistant.sql — THE IN-APP DATA ASSISTANT adopts the shared AI guard (purpose data_qa)
--
-- OWNER DIRECTIVE 2026-10-05 (sanjot@): *"We need to create a self generated AI inside our platform
-- that is smart enough to answer any questions related to the data ingested into the system,
-- perform calculations, create a pivot table or create graphs or answer a question like which was
-- my best store and how much revenue did it make or who is best sales person or who is pulling me
-- down, or what is needed to pull sales up. This intelligence needs to be built and improved as
-- more and more data will be ingested."*
--
-- WHAT THE APPLICATION CODE IN THIS PR DOES, so this file is read in context (index §52):
--   · `core/data_qa_registry.py` — the ONE declaration of which business question is served by
--     which EXISTING report endpoint. The assistant runs the platform's own reports; it writes no
--     SQL and touches no raw table, so no number it states is a second derivation of money.
--   · `core/data_qa_compute.py` — the ONE home for the arithmetic (group, rank, pivot, compare,
--     chart). The model chooses a grouping; this code does the adding up.
--   · `core/data_qa_agent.py` — the tool loop. Each report read is made IN-PROCESS with the
--     caller's own bearer token, so `TenantScopeMiddleware` resolves the org and each endpoint's own
--     RBAC (`storeops.caller_scope` → `scope_keyset`) decides which stores come back. The assistant
--     is exactly as blind as the person using it.
--   · `core/data_qa_api.py` — `GET /core/data-qa/status`, `POST /core/data-qa`.
--
-- WHAT IS REUSED, NOT REBUILT (CLAUDE.md duplicate-check build gate). This migration adds NO table
-- and NO column. What was checked, and what is reused instead:
--   · "is there already an in-app assistant?" — YES: `POST /helpdesk/ai-assist` (the PRODUCT/how-to
--     assistant, which states in its own system prompt that it has no database access). It is
--     EXTENDED rather than duplicated: the same `ai_assistant` entitlement gates both, and the same
--     `components/AiAssistant.tsx` panel now routes a data question to the new door and a how-to
--     question to the existing one. There is still ONE "Ask AI" in the product.
--   · "is there already a place that computes these numbers?" — YES, one per question, and the
--     registry names them: §3 `_sales_cell_agg` (the single row-level pass behind the Sales Report,
--     Executive MTD and Daily Targets), §4/§4c the P&L, §5 the targets engine and action plan,
--     §13 store resolution. None is re-derived.
--   · "is there already an AI spend guard?" — YES: `core.ai_budget_config` + `core.ai_call_audit`
--     (mig 972), one more `purpose` value, read by `core/ai_gate.py` (mig 982) and written by
--     `billing/ai_meter.record()`. No new table, no second meter, no cost column — mig 718's
--     `core.token_rates` stays the only $/MTok source.
--
-- WHO MAY ASK. `control_box.AI_PURPOSES['data_qa']` authorizes on `module_scope`: the `ai_assistant`
-- module plus a reporting scope of `all`, `market` or `company`. A self-scoped login (a rep) is
-- refused THE ASSISTANT — not its data: every report it would have read stays on its own page,
-- scoped as always. Fail-closed is structural and inherited: an unregistered purpose is refused, a
-- purpose naming a predicate that does not exist authorizes NOBODY, and a predicate that raises
-- denies (proven DB-free in backend/harness_ai_guard_purposes.py and backend/harness_data_qa_lock.py).
--
-- THE QUESTION IS BOUNDED TEXT, AND CANNOT BECOME A QUERY. Like remediation triage (mig 982) the
-- caller types words, so the subject rule is `bounded_text`: stripped of control characters, capped
-- by the org's `max_input_chars`, and audited as a DIGEST rather than as a copy of everything anyone
-- ever typed. What the text can reach is narrow by construction — the model may only NAME a question
-- from the registry, every parameter is re-validated against that registry's patterns, and the
-- catalog shown to the model contains no URL at all, so no model output is ever interpreted as a
-- route. Proven over SQL, traversal, scheme, newline, control-character and over-long inputs in
-- backend/harness_data_qa_registry.py §F.
--
-- "IMPROVED AS MORE DATA IS INGESTED" — the mechanism, stated so nobody expects model training.
-- `data_qa_registry.answerable()` offers only the questions whose module is switched on for the
-- tenant, so the day a tenant's feed lands and its module comes on, the matching questions appear
-- with no code change; and the index rule means every NEW report registers here in the PR that
-- builds it. The assistant's reach grows with the platform's, reviewable in a diff.
--
-- SAFE: additive + idempotent, one config row, no schema change. Re-runnable.
-- MONEY: touches NO payout, rate, plan, commission or P&L column. It bounds API SPEND only, and the
-- assistant itself is READ-ONLY by construction — the registry holds no write route and the reader
-- issues only GET (backend/harness_data_qa_lock.py §A4, §B1).
-- SECURITY: no grants changed; mig 972's RLS-on / no-policies / no-anon-grants posture is inherited.

BEGIN;

-- House ceiling for the data assistant. Deliberately different from the other purposes' numbers,
-- because ONE question here is a TOOL LOOP: the model fetches a report, asks for a pivot, maybe
-- fetches a second report, then answers. So the per-call token cost is several turns' worth, while
-- the call COUNT stays human-paced (a person asking questions in a panel, not a sweep).
--   max_calls_per_hour  30  — a working session of questions, not a script
--   daily_call_cap     120  — four such sessions in a day
--   daily_token_cap   6,000,000 — the loop's turns plus the rows a report returns
--   max_input_chars 12,000 — the house value; the user's typed question is far below it, and the
--                            report ROWS never pass through this bound (they reach the model as
--                            tool results, already capped by DATA_QA_MAX_ROWS_TO_MODEL in code)
-- RULE TWO: a tenant overrides with its own row, and `enabled=false` switches the assistant off for
-- that tenant while every report it reads keeps working on its own page — the same graceful shape as
-- having no API key at all.
INSERT INTO core.ai_budget_config (org_id, purpose, enabled, max_calls_per_hour, daily_call_cap,
                                   daily_token_cap, max_input_chars, notes)
VALUES ('00000000-0000-0000-0000-000000000001', 'data_qa', true, 30, 120, 6000000, 12000,
        'In-app data assistant (index §52). Answers questions about the tenant''s own data by '
        'running the platform''s OWN reports as the signed-in user — no SQL, no raw tables, no '
        'second derivation. Authorized by the ai_assistant module + a market/company/all reporting '
        'scope, NOT super-admin. One question is a multi-turn tool loop, hence the larger token cap '
        'against a human-paced call cap. Assistant off = every report stays on its own page.')
ON CONFLICT (org_id, purpose) DO NOTHING;

COMMIT;

-- Deny codes this purpose can write to core.ai_call_audit.deny_code (free text, no CHECK):
--   not_data_qa_operator — the login lacks the ai_assistant module or a broad enough reporting scope
--   no_subject           — nothing was asked
--   disabled / no_key    — degrade to a sentence the user can act on, never an error
--   rate_limited / budget_exhausted — the guard's own worded reason, shown in the panel
--
-- What the assistant cost this tenant today (tokens; $ joins core.token_rates, mig 718):
--   select count(*) filter (where allowed) as questions,
--          sum(input_tokens + output_tokens) filter (where allowed) as tokens,
--          count(*) filter (where not allowed) as refusals
--     from core.ai_call_audit
--    where purpose = 'data_qa' and created_at > now() - interval '24 hours';
--
-- Switch the assistant off for ONE tenant without touching anyone else (RULE TWO, a config row):
--   insert into core.ai_budget_config (org_id, purpose, enabled)
--   values ('<that org id>', 'data_qa', false)
--   on conflict (org_id, purpose) do update set enabled = false;
--
-- REVERT: (removes only the CEILING — the purpose registry row and the wiring are application code
-- and revert with the commit; deleting this row falls back to the house defaults in
-- control_box.DEFAULT_AI_CONFIG, which are TIGHTER, never looser):
--   DELETE FROM core.ai_budget_config
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND purpose = 'data_qa';

SELECT 'Migration 1055 — in-app data assistant routed through the shared AI guard (purpose data_qa)' AS status;
