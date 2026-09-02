-- 930_email_sweep_in_progress_lock.sql — per-mailbox in-progress lock for the email sweep
-- (mailbox hygiene, 2026-09-01 incident follow-up)
--
-- WHY: a backlog sweep of a large mailbox can run LONGER than the mailbox's cadence. The run-due
-- worker (post-#169: schedules advance up front, sweeps run on a dedicated thread) would then start
-- a SECOND sweep of the same mailbox on the next tick — two threads re-downloading the same
-- not-yet-done messages and racing their journal writes. Observed live 2026-09-01 while the org-2
-- backlog drained: six identical 'duplicate' journal rows within one second, and the completion
-- stamp delayed for hours because each overlapping sweep restarted the fetch.
--
-- The worker stamps sweeping_since when it starts a mailbox and clears it when done (success or
-- crash); a tick skips any mailbox whose stamp is fresher than the code's stale window
-- (EMAIL_SWEEP_LOCK_STALE_MINUTES, 180 min), so a crashed/redeploy-killed sweep self-heals — the
-- stale stamp simply stops blocking. Code reads a missing column as unlocked and its stamp writes
-- no-op, so applying this migration is what turns the lock on; nothing breaks unapplied.
--
-- NO data changes. Additive, idempotent.
-- REVERT: alter table commcalc.email_sweep_config drop column if exists sweeping_since;

alter table commcalc.email_sweep_config
  add column if not exists sweeping_since timestamptz;

comment on column commcalc.email_sweep_config.sweeping_since is
  'Set while a run-due sweep of this mailbox is in flight; a fresh stamp makes the next tick skip '
  'the mailbox instead of starting an overlapping sweep. Stale stamps (see '
  'EMAIL_SWEEP_LOCK_STALE_MINUTES in router.py) are ignored, so a crashed sweep never wedges the lock.';

notify pgrst, 'reload schema';
select 'Migration 930 — email-sweep per-mailbox in-progress lock (sweeping_since) added' as status;
