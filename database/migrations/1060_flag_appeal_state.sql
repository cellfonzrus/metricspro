-- MIGRATION 1060: AN APPEAL HAS A HOME ON THE FINDING IT IS AN APPEAL OF
-- Band 1000+ (mod-platform-core). Additive + idempotent (safe to re-run). No data is moved, no money.
--
-- WHY (owner 2026-10-06): *"ti should be under flags which shows the acrrier has not paid the
-- commimssion … and have to see what is the apeal status and whether they got paid int eh following
-- month s"*. The Commission Withholding report (index §55) publishes one finding per activation the
-- carrier took commission back on. A manager rules on it — appeal filed, won, denied, written off —
-- and that ruling has to survive next month's upload of the same feed.
--
-- WHERE THE APPEAL STATE GOES, AND WHY HERE RATHER THAN SOMEWHERE ELSE
-- ────────────────────────────────────────────────────────────────────
-- Three homes were possible and two were rejected:
--
--   · a NEW table — rejected. It would be a third store of "money the carrier has not paid and what
--     we are doing about it", and the duplicate-check build gate exists to stop exactly that.
--   · commcalc.discrepancy_results — rejected. Its appeal columns (mig 947) already carry this
--     workflow, but its ROWS are two recon engines' output, each delete-then-inserting its own
--     (org, period, source) slice. A clawback finding is not a discrepancy row and adding a third
--     slice owner to a money engine's table is not a free change.
--   · commcalc.flags — CHOSEN. The findings ARE flag rows, so `flag_persist` already guarantees the
--     one property an appeal needs: a manager's decision is never erased by the next run, and a
--     condition that clears is retired in place rather than deleted. The owner asked for the report
--     to live under Flags, and the ruling belongs on the row the manager was looking at.
--
-- ONE STATE MACHINE, TWO HOMES — NOT TWO STATE MACHINES
-- ─────────────────────────────────────────────────────
-- The column NAMES below are byte-identical to migration 947's on discrepancy_results, and that is
-- the point: `commcalc/discrepancy_appeals.py` is the ONE pure state machine, its `apply_appeal`
-- builds the row patch, and the same function patches either table with no branch and no second
-- truth table to drift. `harness_appeal_one_machine_lock.py` fails the build if a second set of
-- transitions appears, or if a writer of these columns bypasses it.
--
-- NO CHECK CONSTRAINT, deliberately, and mirroring 947: the legal states and the legal TRANSITIONS
-- are one fact, enforced in the state machine that knows both. A CHECK could only restate the
-- states — the weaker half — and would then be a second copy to keep in step.
--
-- DEGRADES GRACEFULLY: un-run, the report still lists every finding and its recovery and payment
-- legs; only the appeal control is withheld, and the endpoint reports `appeals_ready: false` rather
-- than erroring (the same posture mig 947 shipped with).
--
-- REVERT:
--   ALTER TABLE commcalc.flags
--     DROP COLUMN IF EXISTS appeal_status, DROP COLUMN IF EXISTS appeal_note,
--     DROP COLUMN IF EXISTS appealed_by,   DROP COLUMN IF EXISTS appealed_at;
--   DROP INDEX IF EXISTS commcalc.flags_org_appeal;
--   DROP INDEX IF EXISTS commcalc.flags_org_type_status;
--   NOTIFY pgrst, 'reload schema';

-- ── a. appeal state on the finding ───────────────────────────────────────────────────────────────
ALTER TABLE commcalc.flags
  ADD COLUMN IF NOT EXISTS appeal_status TEXT,
  ADD COLUMN IF NOT EXISTS appeal_note   TEXT,
  ADD COLUMN IF NOT EXISTS appealed_by   TEXT,
  ADD COLUMN IF NOT EXISTS appealed_at   TIMESTAMPTZ;

COMMENT ON COLUMN commcalc.flags.appeal_status IS
  'Appeal workflow state on a finding (mig 1060): appeal_filed | appeal_won | appeal_denied | '
  'written_off; NULL = no appeal activity, the honest default on every detector-written row. The '
  'legal states AND the legal transitions are ONE fact, held by commcalc/discrepancy_appeals.py '
  '(pure; proof harness_discrepancy_appeals.py) — the same state machine that drives '
  'discrepancy_results.appeal_status, which is why these column names match it byte for byte. '
  'Written only by PATCH /commcalc/commission-withholding/{flag_id}/appeal; never by a detector, '
  'and never by the flag_persist merge (a ruling is not re-derived data).';
COMMENT ON COLUMN commcalc.flags.appeal_note IS
  'Free-text note attached with the last appeal transition (clamped to 2000 by the state machine).';
COMMENT ON COLUMN commcalc.flags.appealed_by IS
  'Auth uid of the person who set the current appeal state (router._caller_uid — a UUID or NULL, '
  'never a sentinel; index §19.34). NULL reads as "system" through frontend actor.ts::actorLabel.';
COMMENT ON COLUMN commcalc.flags.appealed_at IS
  'When the current appeal state was set (UTC). NULL when there is no appeal state.';

-- ── b. the two reads this report makes ───────────────────────────────────────────────────────────
-- Partial, so neither index carries the ~20k rows that have no appeal and no interest here.
CREATE INDEX IF NOT EXISTS flags_org_appeal
  ON commcalc.flags (org_id, appeal_status)
  WHERE appeal_status IS NOT NULL;

-- The report's own query: one flag type, open findings, newest first. `flag_type` has no index of
-- its own on this table and the Watchdog area pages scan it too, so this earns its keep beyond §55.
CREATE INDEX IF NOT EXISTS flags_org_type_status
  ON commcalc.flags (org_id, flag_type, status, transaction_date DESC);

NOTIFY pgrst, 'reload schema';
