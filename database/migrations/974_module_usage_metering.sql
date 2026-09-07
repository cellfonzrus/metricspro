-- 974_module_usage_metering.sql — per-module usage counters ("bill each call on all modules")
--
-- OWNER DIRECTIVE 2026-09-05 (sanjot@): "it should bill each call on all modules, nothing is for
-- free, and have an itemized statement for the tenant for a clear visibility".
--
-- ══ THROUGHPUT — THE DECISION THIS MIGRATION IS SHAPED BY ═══════════════════════════════════════
-- "Each call on all modules" is a different order of magnitude from the AI meter (mig 972): AI calls
-- are a handful per tenant per day; API calls are thousands per tenant per hour. Two shapes were
-- considered and one was rejected on the record:
--
--   REJECTED — A ROW PER CALL. Maximally detailed, but it puts a database write in the path of every
--     request and grows without bound. This platform already took a SEV-1 (2026-07-30) from work run
--     inline on the request path, and `core.access_log` — the one existing per-request writer — only
--     gets away with it by DETACHING the write. Per-call forensic detail already exists there; a
--     second per-call table would be a duplicate derivation of the same fact.
--
--   CHOSEN — COUNTERS PER (org, module, day). The backend counts in memory
--     (billing/module_usage.UsageAccumulator) and flushes the whole batch periodically through ONE
--     round trip to `core.bump_module_usage`. The request path pays a dict increment: no I/O, no
--     lock held across a network call, nothing on the event loop.
--
--     THROUGHPUT ASSUMPTION, stated so it can be checked: one flush per interval per process,
--     carrying at most (tenants x modules-touched) rows — for this platform, tens of rows every
--     30 seconds, not thousands per second. The counter table grows at
--     (tenants x modules x days), i.e. roughly 20 modules x 365 days = ~7k rows per tenant per year:
--     trivially indexable and cheap to aggregate for a monthly statement.
--
--     COST OF THE CHOICE, stated honestly: a process killed between flushes loses at most one
--     interval of counts, so this UNDER-counts slightly under a hard crash. For a usage bill that is
--     the correct direction to be wrong — never bill for calls we cannot evidence.
--
-- ══ WHAT IS BILLED, AND WHAT IS DELIBERATELY NOT ═══════════════════════════════════════════════
-- "Nothing is for free" means no module may LACK a price. It does not mean a tenant should pay for
-- work the tenant never asked for. Billing a tenant for our own cron retry storm is both wrong and
-- the fastest way to make an invoice untrustworthy. So every counter row splits the count:
--     billable_calls   tenant-initiated (a signed-in actor acting in that tenant) — BILLED
--     system_calls     pg_cron ticks, `*/run-due` sweeps, webhooks, internal service-role calls —
--                      COUNTED AND SHOWN on the statement, never charged
--     anonymous_calls  unauthenticated public endpoints — attributable to no tenant
-- Both are stored, so the decision is VISIBLE and REVERSIBLE: if the owner decides sweeps should be
-- billed, that is a change to the statement, not a re-instrumentation — the numbers already exist.
--
-- ══ HONESTY ════════════════════════════════════════════════════════════════════════════════════
-- A route whose module cannot be determined is counted under the literal module `unmapped`, never
-- dropped and never guessed onto a neighbouring module. It surfaces on the operator grid and on the
-- statement. `main.py:_mounted_modules` exists because a hardcoded module literal went stale and
-- "CONFIDENTLY MISREPRESENTS the deployment"; the same bug here would mean a module silently
-- billing nothing, so the route map is derived and its gaps are shown.
--
-- SAFE: additive + idempotent. Re-runnable.
-- MONEY: this table is an INPUT to billing (counts, not currency). No rate, price, payout or
--   commission column is read or written here. Pricing lives in mig 975.
-- SECURITY: RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5). The bump
--   function is SECURITY DEFINER with EXECUTE granted to service_role ONLY — a tenant must never be
--   able to write their own usage counters.

BEGIN;

CREATE SCHEMA IF NOT EXISTS core;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. THE COUNTERS — one row per (tenant, module, day)
-- ══════════════════════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS core.module_usage_daily (
  org_id          UUID NOT NULL,
  module          TEXT NOT NULL,      -- an entitlement module key, or 'unmapped' (honest catch-all)
  usage_date      DATE NOT NULL,
  calls           BIGINT NOT NULL DEFAULT 0,   -- everything observed
  billable_calls  BIGINT NOT NULL DEFAULT 0,   -- tenant-initiated only — the number that bills
  system_calls    BIGINT NOT NULL DEFAULT 0,   -- our crons/sweeps/webhooks — shown, never charged
  anonymous_calls BIGINT NOT NULL DEFAULT 0,
  first_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (org_id, module, usage_date)
);
-- The index the monthly statement reads: this tenant, this date range.
CREATE INDEX IF NOT EXISTS module_usage_daily_org_date
  ON core.module_usage_daily(org_id, usage_date DESC);
-- The index the operator's cross-tenant view reads: what did the whole platform do on this day.
CREATE INDEX IF NOT EXISTS module_usage_daily_date
  ON core.module_usage_daily(usage_date DESC);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 2. THE BUMP RPC — one round trip per flush, additive by construction
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- Takes the whole drained batch as JSONB and folds it in with `calls = calls + excluded.calls`.
-- ADDITIVE, NOT ASSIGNMENT: two backend processes (or a retried flush) must never overwrite each
-- other's counts. That is also what makes the accumulator's restore-on-failure safe — a flush that
-- half-succeeded and is retried adds the same delta twice at worst, which is why the flusher only
-- restores on a FAILED call, never on an ambiguous one.
CREATE OR REPLACE FUNCTION core.bump_module_usage(p_rows JSONB)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
  r JSONB;
  n INTEGER := 0;
BEGIN
  IF p_rows IS NULL OR jsonb_typeof(p_rows) <> 'array' THEN
    RETURN 0;
  END IF;
  FOR r IN SELECT * FROM jsonb_array_elements(p_rows) LOOP
    -- A malformed entry is SKIPPED, never allowed to abort the batch: losing one counter is far
    -- better than losing the whole flush (and with it every tenant's counts for that interval).
    CONTINUE WHEN COALESCE(r->>'org_id', '') = ''
              OR COALESCE(r->>'module', '') = ''
              OR COALESCE(r->>'usage_date', '') = '';
    BEGIN
      INSERT INTO core.module_usage_daily AS m
        (org_id, module, usage_date, calls, billable_calls, system_calls, anonymous_calls)
      VALUES (
        (r->>'org_id')::uuid,
        left(r->>'module', 120),
        (r->>'usage_date')::date,
        GREATEST(0, COALESCE((r->>'calls')::bigint, 0)),
        GREATEST(0, COALESCE((r->>'billable_calls')::bigint, 0)),
        GREATEST(0, COALESCE((r->>'system_calls')::bigint, 0)),
        GREATEST(0, COALESCE((r->>'anonymous_calls')::bigint, 0))
      )
      ON CONFLICT (org_id, module, usage_date) DO UPDATE SET
        calls           = m.calls           + EXCLUDED.calls,
        billable_calls  = m.billable_calls  + EXCLUDED.billable_calls,
        system_calls    = m.system_calls    + EXCLUDED.system_calls,
        anonymous_calls = m.anonymous_calls + EXCLUDED.anonymous_calls,
        last_seen       = NOW();
      n := n + 1;
    EXCEPTION WHEN OTHERS THEN
      NULL;   -- a bad org_id/date cast skips that row only
    END;
  END LOOP;
  RETURN n;
END;
$fn$;

REVOKE ALL ON FUNCTION core.bump_module_usage(JSONB) FROM public;
DO $$ BEGIN
  BEGIN REVOKE ALL ON FUNCTION core.bump_module_usage(JSONB) FROM anon, authenticated;
    EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN GRANT EXECUTE ON FUNCTION core.bump_module_usage(JSONB) TO service_role;
    EXCEPTION WHEN OTHERS THEN NULL; END;
END $$;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 3. ROUTE MAP OVERRIDES (RULE TWO — a new module is registered, not hard-coded)
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- `billing/module_usage.DEFAULT_ROUTE_MODULE` is the code default; a row here overrides it, so a
-- deployment can map a new router prefix onto a billable module (or mark it infrastructure) with no
-- code change. An unmapped prefix is NEVER guessed — it counts under 'unmapped' and shows up.
CREATE TABLE IF NOT EXISTS core.module_route_map (
  prefix      TEXT PRIMARY KEY,        -- the /api/v1/<prefix> segment
  module_key  TEXT,                    -- an entitlement module key; NULL = infrastructure, not billed
  is_infra    BOOLEAN NOT NULL DEFAULT false,
  note        TEXT,
  updated_by  TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE core.module_usage_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.module_route_map   ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  BEGIN REVOKE ALL ON core.module_usage_daily, core.module_route_map FROM anon, authenticated;
    EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN GRANT ALL ON core.module_usage_daily, core.module_route_map TO service_role;
    EXCEPTION WHEN OTHERS THEN NULL; END;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 974 — per-module usage counters + additive bump RPC (no write on the request path)' AS status;

-- What did this tenant use last month, and how much of it did WE initiate:
--   SELECT module, sum(billable_calls) AS billed, sum(system_calls) AS ours
--     FROM core.module_usage_daily
--    WHERE org_id = '<tenant>' AND usage_date BETWEEN '2026-08-01' AND '2026-08-31'
--    GROUP BY 1 ORDER BY 2 DESC;
-- Routes nobody has mapped to a billable module (these bill NOTHING until mapped):
--   SELECT sum(calls) FROM core.module_usage_daily WHERE module = 'unmapped';
--
-- REVERT:
--   DROP FUNCTION IF EXISTS core.bump_module_usage(JSONB);
--   DROP TABLE IF EXISTS core.module_route_map;
--   DROP TABLE IF EXISTS core.module_usage_daily;
