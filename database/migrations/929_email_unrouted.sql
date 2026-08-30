-- 929_email_unrouted.sql — surface "a report arrived but no import rule matched it" (owner 2026-08-29:
-- "the email has the data but the system did not get them"). The email sweep now records every DATA-file
-- attachment that was delivered but matched NO filename rule (usually a report renamed at the source, e.g.
-- b2b recreating "Sales Transaction Details" as "…Legacy New") into this column, and the freshness banner
-- reads it — so a silently frozen feed becomes a visible prompt to add a rule instead of stale numbers.
--
-- Value is a JSON array of filenames (text). Additive + idempotent + single-line-safe. The sweep guards the
-- write with _table_has_column and the freshness report guards the read, so the app works before AND after
-- this runs — this only turns on the on-screen banner surfacing (the once-a-day alert fires either way).
-- REVERT: alter table commcalc.email_sweep_config drop column if exists last_unrouted;

alter table commcalc.email_sweep_config add column if not exists last_unrouted text;

notify pgrst, 'reload schema';
select 'Migration 929 complete — email_sweep_config.last_unrouted (JSON array of unmatched report filenames)' as status;
