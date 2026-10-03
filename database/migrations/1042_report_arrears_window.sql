-- 1042_report_arrears_window.sql
-- A SOURCE THAT POSTS IN ARREARS MUST BE ASKED FOR A WINDOW THAT CAN CONTAIN DATA (index §19.41).
--
-- WHY. commcalc.report_definitions already carries refresh_months (how many closed months to re-pull)
-- and refresh_days (how wide a day-grain window is). The day-grain path reads refresh_days ONLY, and
-- the house row for the compensation report carries refresh_days = 1 — so every nightly run asked an
-- IN-ARREARS source for TODAY, received zero rows, and recorded a clean "nothing posted". Measured,
-- house org, core.job_run 2026-10-03T03:31:01Z: window '2026-10-03..2026-10-03', rows 0, mode
-- 'no_data', run status SUCCEEDED. Two months of commission statements never landed and no alert
-- fired. refresh_months = 3 is set on that same row and the day-grain path never reads it.
--
-- WHAT THIS ADDS. `arrears_days` — how many days late this source posts — per org, per report, with
-- the house default row inheriting as every other report_definitions setting does. The sweep's window
-- is max(refresh_days, arrears_days), so the window can never again be narrower than the arrears.
-- `empty_stale_after_days` — how long a run of zero-row pulls may continue before the platform stops
-- believing it is the source being quiet. NULL on both means "use the house default in
-- backend/app/modules/commcalc/empty_pull_verdict.py" (DEFAULT_ARREARS_DAYS / DEFAULT_STALE_AFTER_DAYS).
--
-- RULE TWO: no carrier, tenant or portal name appears here. `report_key` values are the report keys
-- this table has always been keyed by. Behaviour is config rows; code reads them.
--
-- MONEY-TOUCHING: it changes WHICH SLICE the sweep asks a carrier portal for, and therefore which
-- commission rows land. It writes no payout and recomputes nothing. SURFACED FOR OWNER APPROVAL —
-- not applied by this change.

ALTER TABLE commcalc.report_definitions
  ADD COLUMN IF NOT EXISTS arrears_days integer,
  ADD COLUMN IF NOT EXISTS empty_stale_after_days integer;

COMMENT ON COLUMN commcalc.report_definitions.arrears_days IS
  'How many days late this source posts a day''s data. The day-grain sweep window is '
  'max(refresh_days, arrears_days), so an in-arrears feed is never asked a question it cannot '
  'answer. NULL = the house default (empty_pull_verdict.DEFAULT_ARREARS_DAYS). Index §19.41.';
COMMENT ON COLUMN commcalc.report_definitions.empty_stale_after_days IS
  'How long an unbroken run of zero-row pulls stays believable before the platform reports it as '
  'unverified rather than as the source being quiet. NULL = house default. Index §19.41.';

-- THE CONFIG REPAIR. The compensation report is published in arrears; a 7-day window both reaches
-- back far enough for a zero to be a real answer and keeps the nightly pull cheap (the portal returns
-- a date range in one run and storage splits it per day, so a 7-day window is one download).
-- Day-grain rows only — a month-grain report has no day window to widen.
UPDATE commcalc.report_definitions
   SET arrears_days = 7
 WHERE report_key = 'comp_report'
   AND arrears_days IS DISTINCT FROM 7;

-- REVERT:
--   UPDATE commcalc.report_definitions SET arrears_days = NULL WHERE report_key = 'comp_report';
--   ALTER TABLE commcalc.report_definitions DROP COLUMN IF EXISTS arrears_days;
--   ALTER TABLE commcalc.report_definitions DROP COLUMN IF EXISTS empty_stale_after_days;
-- Reverting restores the one-day window, i.e. the defect: the nightly pull goes back to asking an
-- in-arrears source for today only.
