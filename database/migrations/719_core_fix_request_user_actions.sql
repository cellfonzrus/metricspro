-- 719_core_fix_request_user_actions.sql — "FIXED + what you must do" on the auto-fix board.
-- Band 700–799 (mod-platform-core). Additive + idempotent (safe to re-run). Extends mig 718.
--
-- OWNER DIRECTIVE 2026-07-30: "when a fix request has been fixed (shipped), the app must show it as FIXED
-- and surface any actions the user must take for that fix to actually work (run SQL, set env var, fix
-- config, re-upload data), with a per-action mark-done checklist."
--
-- THE GAP THIS CLOSES. Half the fixes this pipeline ships are inert until a HUMAN does something outside
-- the codebase: run a migration in the Supabase SQL editor, set a Railway env var, correct a mapping row,
-- re-upload a reduced export. Today that instruction lives only in a handoff paragraph and in chat, so a
-- shipped fix can sit dead for weeks while the board says "pushed" and everybody believes it is done.
-- After this migration the required steps are STRUCTURED DATA on the fix row, rendered as a checklist the
-- owner ticks off, and a shipped fix with an outstanding step is loudly "Action required" instead of green.
--
-- WHAT THIS ADDS — two columns on core.fix_requests. NOTHING ELSE.
--   user_actions  jsonb NOT NULL DEFAULT '[]'  — the checklist. Each element:
--       { "id":          "<stable id, uuid or slug>",
--         "kind":        "sql" | "env" | "config" | "data" | "other",   -- VALIDATED IN APP (see below)
--         "instruction": "<what the human must do, verbatim — e.g. the SQL block to paste>",
--         "status":      "pending" | "done",
--         "done_by":     "<email of the super-admin who ticked it>"  | null,
--         "done_at":     "<iso8601>"                                 | null }
--   resolved_note text  — the short plain-English "what shipped" summary shown on the board next to FIXED.
--
-- WHY NO CHECK CONSTRAINT ON THE JSON. `kind` is validated server-side in
-- app/modules/core/fix_pipeline.py (USER_ACTION_KINDS, one source of truth, unit-proven in
-- harness_fix_pipeline.py section I). A jsonb-shape CHECK here would duplicate that rule in a second place
-- where it can drift, and would make a future kind an operator-run ALTER instead of a code change. The
-- column is deliberately a plain jsonb with a safe default.
--
-- THE PUSH-GATE TRIGGER IS NOT TOUCHED. core.fix_requests_guard() and its status rules (pushed only from
-- approved, with approved_by/approved_at) are exactly as mig 718 left them — this migration adds columns
-- and nothing else. The guard's `NEW.updated_at := now()` keeps firing for these writes for free.
--
-- MULTI-TENANT (RULE ONE): no new table, so no new org_id surface. Every read AND write of these columns
-- goes through the existing org-scoped fix_requests paths (`.eq("org_id", org)`), and the mark-done
-- endpoint takes org_id as a QUERY PARAM and stamps it on the write, like every other route in the module.
--
-- RLS POSTURE (AGENT_CONTRACT §5): core.fix_requests already has RLS enabled with ZERO policies and ZERO
-- anon/authenticated grants (mig 718). Adding a column changes none of that, and this file grants nothing.
--
-- DEGRADES GRACEFULLY: until this runs, the board reads user_actions as [] (the app defaults it), shows no
-- checklist, and every write that carries these fields silently retries without them and returns a
-- "run migration 719" hint — so triage, builds, status transitions and the $ accounting keep working
-- untouched. Nothing anywhere else in the app is affected.
--
-- NOT MONEY-TOUCHING: no rate, plan, tier, payout, commission or P&L row is read or written.

ALTER TABLE core.fix_requests
  ADD COLUMN IF NOT EXISTS user_actions jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE core.fix_requests
  ADD COLUMN IF NOT EXISTS resolved_note text;

COMMENT ON COLUMN core.fix_requests.user_actions IS
  'Checklist of steps the HUMAN must take for this shipped fix to actually work: [{id, kind (sql|env|config|data|other), instruction, status (pending|done), done_by, done_at}]. Written at ship time (and by the triage service secret for config/data findings); ticked off only by a super-admin browser request, which appends to the audit trail. `kind` is validated in app (fix_pipeline.USER_ACTION_KINDS), deliberately not by a jsonb CHECK, so it stays one source of truth. A pushed row with any pending action shows as "Action required", never as a clean green FIXED.';

COMMENT ON COLUMN core.fix_requests.resolved_note IS
  'Short plain-English "what shipped" summary shown next to FIXED on /admin/fix-requests. Free text written at Gate-1/ship time; never parsed by code.';

NOTIFY pgrst, 'reload schema';
SELECT '719 complete — core.fix_requests.user_actions (jsonb, default []) + resolved_note (text); push-gate trigger untouched' AS status;
