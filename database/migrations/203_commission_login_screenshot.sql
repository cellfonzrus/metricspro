-- 203 (commission band) — portal-login screenshot capture
-- Persists the LAST page the headless portal-login browser SAW (base64 JPEG, ~50-100KB) on the
-- data_source row, so the operator debugs 2FA / bot-wall / login errors VISUALLY at
-- /commcalc/email-imports (📷 button + 2FA modal) instead of from text diagnostics.
-- Additive + idempotent; the backend degrades gracefully while this hasn't run
-- (screenshots are simply not stored, and /login/screenshot says to run this file).

alter table commcalc.data_source add column if not exists login_shot text;
alter table commcalc.data_source add column if not exists login_shot_at timestamptz;
