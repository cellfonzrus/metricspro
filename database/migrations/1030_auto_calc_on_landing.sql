-- 1030_auto_calc_on_landing.sql  (mod-commission)  — WRITTEN, NOT APPLIED. Surfaced for owner approval.
--
-- AUTO-CALCULATION ON LANDING (owner 2026-09-28: "when sept is uploaded the system should calculate
-- automatically without manual intervention"). Index §6l. Code: backend/app/modules/commcalc/auto_calc.py.
--
-- Every lander (the upload pages, the Email Auto-Import, the FTP drop, the onboarding intake, the feed →
-- raw_sales derivation, the DLAR / ePay / portal sweeps, the store-guard release, the built-in POS sync, the
-- commission import wizard, the MA manual upload) calls ONE hook, `auto_calc.landed(...)`, after it writes rows
-- into a table the Run Calculation reads (data_lineage_registry.COMMISSION_CALC_FEEDS). The hook QUEUES one
-- request per (org, month) touched; a background poller runs THE standard Run Calculation (router._run_calculation,
-- every guard armed) once that month's uploads have been quiet for the org's debounce window, and records the
-- outcome for the Rep Incentive page.
--
-- WHAT THIS ADDS — five nullable columns, no new table, no data change beyond ONE house config value:
--
--   commcalc.calc_status (UNIQUE org_id, period — the existing per-month status row):
--     auto_calc_requested_at  TIMESTAMPTZ  the LATEST landing of a pending request; NULL = nothing pending.
--                                          The debounce anchor and the claim predicate: the poller claims with
--                                          UPDATE … SET auto_calc_requested_at = NULL WHERE org_id=$1 AND period=$2
--                                          AND auto_calc_requested_at <= <the value it judged due> — a second
--                                          worker, or a landing that moved the timestamp, matches zero rows.
--     auto_calc_landings      JSONB        what landed into the pending request [{at, source, table, filename,
--                                          rows}], capped at 40 (the first always kept).
--     auto_calc_last          JSONB        the last outcome {outcome: calculated | calculated_with_errors |
--                                          refused | failed | busy | off | running, tone, period, at, started_at,
--                                          message, reps, landings, sentence} — served by GET /calc-status/{period}
--                                          as `auto_calc` and shown on the Rep Incentive page.
--
--   commcalc.commission_org_config (THE per-org money-policy row; RULE TWO):
--     auto_calc_on_landing        BOOLEAN  NULL = inherit the house org's row, then the code default (TRUE).
--     auto_calc_debounce_minutes  INTEGER  NULL = inherit, then the code default (5). Clamped 1..240 in code.
--   Read by ONE function, auto_calc.load_config (locked by backend/harness_auto_calc_lock.py).
--
-- HOUSE DEFAULT = ON (set explicitly on the house row below, and the code default agrees):
--   (1) the owner's directive is platform-wide and the house org is the owner's; (2) before this, every Boost
--   tenant was already recalculated on each DLAR sweep and every plan-mode tenant on each email-sweep promotion —
--   those two inline triggers are now this one hook, so OFF would switch recalculations off that tenants rely on;
--   (3) the run is the Run Calculation a person presses, with the unconfigured-tenant refusal, the zero-wipe guard
--   and the single-flight recompute guard all armed, and its outcome — including a refusal — is shown on the page.
--   A tenant that wants attended recalculation sets ITS row to false.
--
-- NO PERIOD LOCK EXISTS for a rep-commission month (index §6l / §19): a late correction to an older month
-- recalculates that month, as a manual Run Calculation would. A lock, when built, belongs in auto_calc.run_one.
--
-- BEFORE THIS RUNS: the hook detects the absent columns and degrades to a PROCESS-LOCAL queue (same debounce, one
-- calculation per process) recording its outcome as a calc_notices entry of type 'auto_calc' (mig 247) — so the
-- DLAR / email-promotion recalculations that existed before never stop while this SQL waits.
--
-- SAFE: additive, idempotent, nullable. NOTHING is paid differently by applying it; the data it changes is ONE
-- config value on the house org's row, and only where that value is still NULL.
-- RLS: no new table, no new policy surface, no GRANT to anon/authenticated. The backend uses the service role.
--
-- REVERT:
--   ALTER TABLE commcalc.calc_status DROP COLUMN IF EXISTS auto_calc_requested_at,
--                                    DROP COLUMN IF EXISTS auto_calc_landings,
--                                    DROP COLUMN IF EXISTS auto_calc_last;
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS auto_calc_on_landing,
--                                              DROP COLUMN IF EXISTS auto_calc_debounce_minutes;
--   (Dropping them returns the hook to its process-local fallback — auto-calculation keeps running; to stop it,
--    set auto_calc_on_landing = false BEFORE reverting, or set AUTO_CALC_POLLER=0 on the services.)

ALTER TABLE commcalc.calc_status
  ADD COLUMN IF NOT EXISTS auto_calc_requested_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS auto_calc_landings     JSONB,
  ADD COLUMN IF NOT EXISTS auto_calc_last         JSONB;

COMMENT ON COLUMN commcalc.calc_status.auto_calc_requested_at IS
  'Latest data landing of a PENDING auto-calculation for this (org, period); NULL = nothing pending. The poller '
  'runs the standard Run Calculation once uploads have been quiet for commission_org_config.auto_calc_debounce_minutes '
  '(default 5) and claims it by setting this back to NULL (index §6l, backend/app/modules/commcalc/auto_calc.py).';

COMMENT ON COLUMN commcalc.calc_status.auto_calc_landings IS
  'What landed into the pending auto-calculation: [{at, source, table, filename, rows}], capped at 40.';

COMMENT ON COLUMN commcalc.calc_status.auto_calc_last IS
  'Outcome of the last auto-calculation of this (org, period): calculated / calculated_with_errors / refused / '
  'failed / busy / off / running, with the landings it came from and the sentence the Rep Incentive page shows.';

-- A pending-request scan reads only rows with a timestamp — keep it an index lookup, not a table scan.
CREATE INDEX IF NOT EXISTS calc_status_auto_calc_pending_idx
  ON commcalc.calc_status (auto_calc_requested_at)
  WHERE auto_calc_requested_at IS NOT NULL;

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS auto_calc_on_landing       BOOLEAN,
  ADD COLUMN IF NOT EXISTS auto_calc_debounce_minutes INTEGER;

COMMENT ON COLUMN commcalc.commission_org_config.auto_calc_on_landing IS
  'Recalculate a month automatically when its data lands (any upload / sweep / intake). NULL = inherit the house '
  'org''s row, then the code default TRUE. FALSE = attended recalculation only (the landing is still recorded on '
  'the Rep Incentive page as "auto-calculation is off").';

COMMENT ON COLUMN commcalc.commission_org_config.auto_calc_debounce_minutes IS
  'Minutes a month''s uploads must be quiet before its auto-calculation runs (a burst of files runs ONE '
  'calculation). NULL = inherit the house row, then 5. Clamped 1..240.';

-- THE HOUSE DEFAULT, as a config value (the code default agrees). Only where the house row exists and the value
-- has never been set — an explicit owner choice is never overwritten by a re-run.
UPDATE commcalc.commission_org_config
   SET auto_calc_on_landing = TRUE
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND auto_calc_on_landing IS NULL;

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 1030 complete — auto-calculation on landing (calc_status.auto_calc_*; commission_org_config.auto_calc_on_landing / auto_calc_debounce_minutes)' AS status;
