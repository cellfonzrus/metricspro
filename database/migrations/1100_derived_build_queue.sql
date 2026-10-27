-- 1100_derived_build_queue.sql
-- Index §COMMCALC-41. Additive, idempotent. ONE NEW TABLE. NO MONEY MOVES, no existing row is read
-- or written, and nothing a person presses changes.
--
-- OWNER ASK, verbatim (2026-10-27): "The rebuild ledger or recalculate should be real time all
-- across, then tenant would not know when to run, so the data should be live update as soon as
-- something changes."
--
-- WHY A TABLE. `auto_calc` (§6l) already makes the rep commission calculation live: a landing queues
-- one request per (org, period) on commcalc.calc_status, a debounce coalesces a burst of uploads into
-- one run, and ONE conditional UPDATE claims it so exactly one process across every gunicorn worker
-- and the sweeps service runs it. The derived SNAPSHOTS - the device payable ledger, the denied-appeal
-- recovery ledger - are not keyed on a month, so they cannot ride calc_status's (org_id, period) key:
-- "what we owe" is a point-in-time snapshot of the whole shelf, not September's figure. This table is
-- the same queue keyed on (org_id, build_key) instead, and nothing else.
--
-- MEASURED, which is why it exists. 2026-10-27: commcalc.device_payable_ledger had last been built
-- 2026-09-04 while its feed (commcalc.asset_ledger) was uploaded 2026-10-23 - 53 days stale, max due
-- date already in the past, so every "what is owed next week" surface read EMPTY and nothing on the
-- page said why. The tenant was the scheduler, and nobody told the tenant.
--
-- NOT APPLYING THIS COSTS LIVENESS, NEVER CORRECTNESS. Without it the queue degrades to one per
-- process (`derived_builds._MEM`): the same debounce, the same outcome in the log, and every button
-- still works exactly as it does today. The code probes for this table every ten minutes, so applying
-- it takes effect with no restart and no redeploy.
--
-- REVERT:
--   drop table if exists commcalc.derived_build_status;

create table if not exists commcalc.derived_build_status (
  org_id        uuid        not null,
  -- The registry key from backend/app/modules/commcalc/derived_builds.py (BUILDS[*].key), e.g.
  -- 'payable_ledger'. Deliberately NOT a foreign key or an enum: the registry is the one home for
  -- which builds exist, and a second copy of that list in the schema is the divergence the house
  -- rules forbid. A row whose key this version does not know is dropped with a warning, not run.
  build_key     text        not null,
  -- The pending request: the LATEST landing's timestamp (the debounce counts from it), and what landed
  -- (one entry per landing, oldest first, capped - the first is always kept because it anchors the
  -- max-wait so a steady stream of uploads cannot starve the build). Both are set to null by the
  -- claim, which is what makes the claim a single conditional UPDATE.
  requested_at  timestamptz,
  landings      jsonb,
  -- The last outcome, as `derived_builds.record` wrote it: outcome, tone, the one sentence an operator
  -- reads, when it started and finished, how many rows it wrote, and the landings it was answering.
  last          jsonb,
  created_at    timestamptz not null default now(),
  primary key (org_id, build_key)
);

create index if not exists derived_build_status_pending_idx
  on commcalc.derived_build_status (requested_at)
  where requested_at is not null;

-- A MIGRATION GRANTS WHAT IT CREATES (house convention; from 2026-10-30 Supabase no longer grants new
-- tables automatically). service_role only - the backend reaches this through PostgREST and the
-- browser never reads it, so an `anon` grant would widen exposure rather than fix anything.
grant select, insert, update, delete on commcalc.derived_build_status to service_role;

comment on table commcalc.derived_build_status is
  'Index §COMMCALC-41. One row per (org, derived build): the pending rebuild request a landing queued, '
  'and the last outcome. The queue behind "the data updates itself as soon as something changes".';
