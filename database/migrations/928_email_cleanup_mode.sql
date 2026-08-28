-- 928_email_cleanup_mode.sql — per-mailbox post-ingest cleanup (owner 2026-08-28: "clean the email so we
-- don't overload it"). When a mailbox's cleanup_mode = 'delete', the sweep deletes a REPORT email once its
-- data has been SUCCESSFULLY ingested (status ok + rows saved) — the data already lives in the platform, so
-- the source email is redundant. 'off' (default) keeps every email exactly as today.
--
-- Additive + idempotent + single-line-safe. The save path also tolerates this column being absent
-- (_table_has_column guard in put_email_config), so the app works before AND after this runs.
-- REVERT: alter table commcalc.email_sweep_config drop column if exists cleanup_mode;

alter table commcalc.email_sweep_config add column if not exists cleanup_mode text not null default 'off';

notify pgrst, 'reload schema';
select 'Migration 928 complete — email_sweep_config.cleanup_mode (off|delete)' as status;
