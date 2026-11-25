-- 1131_goal_rollforward_policy.sql
-- ────────────────────────────────────────────────────────────
-- HOW NEXT MONTH'S GOAL FOLLOWS FROM THIS MONTH'S IS A DECLARED POLICY (owner 2026-11-25)
--
-- Owner (Sanjot), verbatim: "Update the Nov goals for boost same as Oct and set that as default for
-- tenant as we discussed - it will be the same if they did not achieve the goal if they met the goal
-- then it will be 10% higher than they achieved".
--
-- THE RULE: a MISSED goal carries forward unchanged. A MET goal becomes `stretch_factor` times what
-- the store ACTUALLY ACHIEVED. A category with no prior goal has nothing to roll and says so.
--
-- THE DEFECT THIS CLOSES — two halves of one class.
--
--  1. THE WRONG BASIS. `commcalc/router._carry_forward_map` already carried a missed goal forward and
--     stretched a met one by 10%, but it stretched to 10% above the GOAL rather than above the
--     ACHIEVEMENT. Measured on live October 2026 house data, five store-categories beat their goal and
--     the two readings disagree on every one:
--
--       B-6507 accessories  goal $7,500  achieved $8,471  ->  old $8,250   declared $9,318
--       B-2778 activations  goal 75      achieved 78      ->  old 82       declared 86
--       B-117  upgrades     goal 45      achieved 52      ->  old 50       declared 57
--       B-151  activations  goal 61      achieved 65      ->  old 67       declared 72
--       B-5619 upgrades     goal 50      achieved 51      ->  old 55       declared 56
--
--     The divergence is widest where a store does BEST, and the old rule gave it the SMALLER
--     stretch — it measured the plan instead of the performance.
--
--  2. THE RATE WAS CODE. `STRETCH_FACTOR = 1.10` was a module constant in `router.py`. RULE TWO:
--     behaviour is per-org config rows with house defaults. A tenant wanting 5%, or wanting to
--     stretch off the plan, had nowhere to say so — and a constant cannot be a per-tenant answer.
--
-- Both are the same class, so the fix is the MECHANISM: the rule and its rate are declared here, the
-- arithmetic lives once in `commcalc/goal_rollforward.py`, and every caller dereferences it.
-- `backend/harness_goal_rollforward.py` §H fails the build if the router grows a second copy of
-- either, scanned over tokenised code so that a comment remembering the deleted constant is read as
-- documentation rather than as a dependency.
--
-- A HOUSE DEFAULT IS CORRECT HERE. The 2026-10-30 directive bans a default that is really one
-- tenant's VOCABULARY standing behind an argument nobody passed. This is a POLICY — a rate, a basis
-- and a switch — which that same directive names as exactly where a house default stays right. What
-- is NOT defaulted is the tenant: `goal_rollforward.policy_for` takes `org_id` as a REQUIRED
-- argument, so a caller that has not asked which tenant cannot get an answer.
--
-- NO SEED, AND THAT IS THE POINT. An EMPTY table means every tenant behaves by
-- `goal_rollforward.HOUSE_DEFAULT` — basis 'achieved', factor 1.10, auto-fill ON — which IS the
-- owner's rule. So this migration being unapplied degrades to the RIGHT behaviour rather than to no
-- behaviour, and a tenant only needs a row here to DIFFER from the house rule. Seeding four
-- identical rows would create four places for one fact to drift.
--
-- AUTO-FILL FOR EVERY TENANT (owner's choice on the decision card, 2026-11-25). Measured the same
-- day by running the REAL `_carry_forward_map` against production for all four tenants, which is the
-- only way this was going to be right:
--
--   Cellfonz R Us  29 stores,  8 declared categories, 5 stretch / 79 carry / 119 new -> 28 rows
--   Luxelink       20 stores, 17 declared categories, 1 stretch / 35 carry / 284 new -> 20 rows
--   NY LOGISTICS    1 store,  20 declared categories,              1 carry /  18 new ->  1 row
--   Vzone           1 store,   4 declared categories,                          3 new ->  0 rows
--
-- Each tenant reads its OWN declared vocabulary (8 / 17 / 20 / 4 categories), which is the
-- 2026-10-30 directive's requirement and was verified rather than assumed.
--
-- NY LOGISTICS IS THE INTERESTING ONE, and it corrects a claim worth not repeating: it has no
-- `commcalc.targets` row in ANY month, so "nothing to carry" looks obvious — but its store carries
-- `storeops.stores.monthly_target = 39000`, which `_carry_forward_map` has ALWAYS used as the
-- accessories goal when there is no prior targets row. So it legitimately carries $39,000 forward.
-- That fallback is pre-existing behaviour and is right (the store does have a declared monthly
-- accessory goal); the lesson is that "this tenant has no goals" cannot be answered from the
-- `targets` table alone, because a goal has TWO homes for accessories.
--
-- Vzone writes nothing, and for the honest reason: its store's `monthly_target` is 0 and it has no
-- prior goal, so every category reads 'new' and the writer skips an all-zero carry rather than
-- creating a row of zeroes. Auto-fill cannot invent a goal for a tenant that has never set one.
--
-- THE STATUS COLUMNS ARE NOT THE AUTHORITY. `last_filled_period` / `last_run_at` / `last_result`
-- record what a run SAID it did; whether a month actually has goals is answered by reading
-- `commcalc.targets` itself. That is the §19.18 lesson stated as a design constraint ("a sweep's
-- status is not a reliable record of what it did"): the run-due entrypoint decides whether to fill
-- by reading the goals, never by trusting these columns, so a lost or lying status row cannot cause
-- a month to be skipped or filled twice.
--
-- NOT MONEY. `targets_engine.achieved_for_cat` pays on ACTUALS and never reads a goal, so no row
-- here can move a payout. It sets what a store is ASKED to do, not what anyone is paid. It touches
-- no rate, plan, payout or commission column.
--
-- Additive and idempotent.
--
-- REVERT:
--   DROP FUNCTION IF EXISTS commcalc.ensure_goal_rollforward_cron(text, text);
--   DROP TABLE IF EXISTS commcalc.goal_rollforward_policy;
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commcalc.goal_rollforward_policy (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,
  -- 'achieved' = factor x what the store achieved (the owner's rule).
  -- 'target'    = factor x the goal it was set (the pre-2026-11-25 reading, kept DECLARABLE so a
  --               tenant preferring to stretch the plan needs a config row rather than code).
  stretch_basis      TEXT,
  -- The multiplier applied when the goal was MET. The backend floors this at 1.0: a "stretch" that
  -- LOWERS a goal a store just beat is a typo, not a reading of this rule, and a typo in a config
  -- row must not quietly cut every goal on the platform.
  stretch_factor     NUMERIC,
  -- Whether next month's goals are written without anyone pressing anything.
  auto_fill          BOOLEAN,
  -- Informational only — see "THE STATUS COLUMNS ARE NOT THE AUTHORITY" above.
  last_filled_period TEXT,
  last_run_at        TIMESTAMPTZ,
  last_result        TEXT,
  updated_by         UUID,
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- ONE policy per tenant. A second row would be two answers to one question, and the reader would
  -- then have to pick a winner.
  UNIQUE (org_id)
);

CREATE INDEX IF NOT EXISTS goal_rollforward_policy_org
  ON commcalc.goal_rollforward_policy (org_id);

-- The basis vocabulary, mirroring `goal_rollforward.STRETCH_BASES`. NULL is allowed and means
-- UNDECLARED (inherit the house default) — a NULL column is the absence of a declaration, not a
-- declaration of nothing. Added separately and guarded so re-running does not fail on an existing
-- constraint.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'goal_rollforward_basis_chk'
      AND conrelid = 'commcalc.goal_rollforward_policy'::regclass
  ) THEN
    ALTER TABLE commcalc.goal_rollforward_policy
      ADD CONSTRAINT goal_rollforward_basis_chk
      CHECK (stretch_basis IS NULL OR stretch_basis IN ('achieved', 'target'));
  END IF;
END $$;

-- A factor below 1.0 cannot shrink a goal (the backend floors it too; stated here so a hand-written
-- row gets a database error instead of silently being ignored).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'goal_rollforward_factor_chk'
      AND conrelid = 'commcalc.goal_rollforward_policy'::regclass
  ) THEN
    ALTER TABLE commcalc.goal_rollforward_policy
      ADD CONSTRAINT goal_rollforward_factor_chk
      CHECK (stretch_factor IS NULL OR stretch_factor >= 1.0);
  END IF;
END $$;

ALTER TABLE commcalc.goal_rollforward_policy ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'commcalc'
                 AND tablename = 'goal_rollforward_policy' AND policyname = 'open_all') THEN
    CREATE POLICY open_all ON commcalc.goal_rollforward_policy FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

-- A MIGRATION GRANTS WHAT IT CREATES (house convention; from 2026-10-30 Supabase no longer grants
-- new public tables automatically, and the backend reaches every relation through PostgREST).
-- service_role ONLY: the browser never reads this table, so an `anon` grant would widen exposure
-- rather than fix anything. Enforced by harness_data_api_grant_guard.py (index §CORE-13).
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.goal_rollforward_policy TO service_role;


-- ────────────────────────────────────────────────────────────
-- THE MONTHLY AUTO-FILL SCHEDULES ITSELF (migs 922/940/950/956/971 pattern)
--
-- THE STANDING LESSON THIS OBEYS (mig 950 header, owner 2026-09-01): "an automation whose repair
-- step is a human click defeats the automation". Mig 241 shipped its cron as a COMMENTED-OUT block
-- for a human to paste and nothing proves anyone ever did; mig 411's "daily 6am" sweep had no job at
-- all. Goals that fill themselves are the LAST thing that may depend on someone remembering, because
-- the whole point of the owner's directive is that nobody has to.
--
-- HOW: a SECURITY DEFINER function the BACKEND calls as service_role on EVERY boot, so the job is
-- (re)scheduled with the CURRENT API_PUBLIC_URL + NOTIFY_RUN_SECRET. No human SQL, no flag day on a
-- secret rotation, and a lost cron job self-heals on the next deploy. NO literal secret is embedded
-- in this file; the backend passes its own env values at call time.
--
-- ONE GLOBAL JOB, DAILY TICK, MONTHLY WORK: /commcalc/targets/roll-forward/run-due walks every
-- tenant, resolves each one's declared policy, and fills only where auto_fill is on AND the current
-- month has no goals yet. So one job serves all tenants and the per-org policy — not the tick —
-- decides whether anything happens. A daily rather than monthly tick deliberately: a monthly cron
-- that misses its single firing (deploy, outage, clock) waits a whole month, whereas a daily tick
-- that finds the month already filled is one indexed read and does nothing.
--
-- 06:40 UTC: the platform already fires the email sweep, the reviews sweep and the portal pulls on
-- the hour, and goals must not queue behind the feeds whose actuals they are computed from.
--
-- Fail-soft exactly like migs 922/940/950/956/971: a missing secret, or pg_cron/pg_net not
-- installed, returns a notice string instead of raising — boot must never fail because scheduling
-- was unavailable, and the manual "Roll forward from last month" button still works.
-- ────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION commcalc.ensure_goal_rollforward_cron(p_url text, p_secret text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
  v_jobid bigint;
  v_cmd   text;
BEGIN
  IF COALESCE(p_url, '') = '' OR COALESCE(p_secret, '') = '' THEN
    RETURN 'skipped: url or secret not configured';
  END IF;
  -- %L safely quotes each literal (the secret never appears in this migration — only in cron.job's
  -- stored command, exactly like the email-sweep / reviews / data-sources / system-check jobs).
  v_cmd := format(
    'select net.http_post(url := %L, headers := jsonb_build_object(%L, %L, %L, %L));',
    rtrim(p_url, '/') || '/api/v1/commcalc/targets/roll-forward/run-due',
    'Content-Type', 'application/json',
    'X-Notify-Secret', p_secret
  );
  -- Idempotent unschedule-then-schedule: cron.schedule() with an existing job name already REPLACES
  -- it; the explicit unschedule additionally clears any half-state from a failed prior
  -- registration. Both sides are fail-soft.
  BEGIN
    PERFORM cron.unschedule('goal-rollforward-run-due');
  EXCEPTION WHEN OTHERS THEN
    NULL;
  END;
  BEGIN
    v_jobid := cron.schedule('goal-rollforward-run-due', '40 6 * * *', v_cmd);
  EXCEPTION WHEN OTHERS THEN
    RETURN 'cron unavailable (pg_cron/pg_net not installed?): ' || SQLERRM;
  END;
  RETURN 'scheduled job ' || COALESCE(v_jobid::text, '?');
END;
$fn$;

GRANT EXECUTE ON FUNCTION commcalc.ensure_goal_rollforward_cron(text, text) TO service_role;
