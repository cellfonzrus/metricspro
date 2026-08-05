-- 275_commission_recompute_guard.sql — single-flight guard state for the commission recompute
--
-- WHY (red finding, docs/handoffs/commission.md 2026-08-04 → fixed 2026-08-05):
--   A recompute is a DELETE-then-INSERT over commcalc.rep_commissions / flags / chargeback_items with no
--   database lock. commcalc.calc_status.calc_status was WRITTEN as 'running' by POST /calculate but never
--   READ by anything, so nothing refused a second concurrent run. Two recomputes only failed to overlap by
--   accident: _run_calculation was an `async def` background task, so Starlette awaited it ON the single
--   uvicorn event loop — which is also why every recompute (documented >300 s) froze the entire product for
--   its whole duration. Moving the calc to the threadpool removes that accident, so the guard becomes real.
--
-- WHAT THIS ADDS — three nullable columns, no new table, no data change:
--   calc_status.calc_started_at   when the CURRENT run claimed the slot. Makes the claim's WHERE clause
--                                 expressible and lets a crashed run be TAKEN OVER instead of wedging
--                                 recomputes forever.
--   calc_status.calc_run_id       which run holds/last held the slot (diagnostics; also what the endpoint
--                                 hands to the background task so it doesn't re-claim its own slot).
--   commission_org_config
--     .calc_stale_minutes         RULE TWO: the takeover threshold is a tenant-tunable setting, not a
--                                 constant. NULL → the code default (20 minutes). Clamped 1..1440 in code.
--                                 Editable at /commcalc/plan-installments → Tenant pay settings.
--
-- THE CLAIM, in SQL terms (issued by PostgREST from _calc_guard_acquire):
--   UPDATE commcalc.calc_status
--      SET calc_status='running', calc_started_at=now, calc_run_id=<token>
--    WHERE org_id=$1 AND period=$2
--      AND (calc_status IS NULL OR calc_status <> 'running'
--           OR calc_started_at IS NULL OR calc_started_at < <now - stale_minutes>)
--   RETURNING *;
--   Under READ COMMITTED a second session blocks on the row lock, then re-evaluates the predicate against
--   the winner's committed version ('running' + a fresh calc_started_at) and matches ZERO rows. Exactly one
--   caller gets a row back. The existing UNIQUE (org_id, period) arbitrates the first-ever insert.
--
-- SAFE: additive + idempotent + nullable. NOTHING pays differently. Until this runs, the guard detects the
-- missing columns, logs, and FAILS OPEN to exactly today's behaviour (mark 'running', proceed unguarded) —
-- the product is never worse than main while the SQL is pending.
-- RLS: no new table, so no new policy surface; no GRANT to anon/authenticated (contract §5). Backend uses
-- the service role.

ALTER TABLE commcalc.calc_status
  ADD COLUMN IF NOT EXISTS calc_started_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS calc_run_id     TEXT;

COMMENT ON COLUMN commcalc.calc_status.calc_started_at IS
  'When the CURRENT recompute claimed this (org, period) slot. Drives the single-flight guard''s '
  'stale-run takeover: a row still marked running older than commission_org_config.calc_stale_minutes '
  '(default 20) is presumed dead and the next Calculate takes it over. NULL = legacy/unknown = takeable.';

COMMENT ON COLUMN commcalc.calc_status.calc_run_id IS
  'Opaque id of the run holding/last holding this slot. POST /calculate claims the slot and hands this '
  'token to the background task so the task does not re-claim (and refuse) its own run.';

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS calc_stale_minutes INTEGER;

COMMENT ON COLUMN commcalc.commission_org_config.calc_stale_minutes IS
  'Minutes after which a recompute still marked running is presumed DEAD and may be taken over by the '
  'next Calculate. NULL => code default 20. Clamped to 1..1440. Set it above your longest real recompute.';

-- A claim reads/writes exactly one row by (org_id, period); the existing UNIQUE (org_id, period) index
-- already serves it. No new index needed.

SELECT 'Migration 275 complete — recompute single-flight guard state (calc_started_at, calc_run_id, calc_stale_minutes)' AS status;
