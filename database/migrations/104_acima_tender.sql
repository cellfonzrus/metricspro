-- 104_acima_tender.sql
-- ACIMA (lease-to-own) becomes a first-class TENDER alongside the existing 6 (cash / credit /
-- external CC / gift / store account / zelle). Adds the t_acima column to the rep-entered closing
-- row and to the per-attempt log, so the daily-closing form, the 3-way tender recon, and the
-- attempt/management-review views all carry it. Idempotent / additive — safe to re-run.

alter table commcalc.daily_closing   add column if not exists t_acima numeric;
alter table commcalc.closing_attempt  add column if not exists t_acima numeric;
