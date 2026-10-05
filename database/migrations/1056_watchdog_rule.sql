-- 1056_watchdog_rule.sql — THE MANAGEMENT WATCHDOG'S THRESHOLDS, AS ROWS.
--
-- OWNER ASK 2026-10-05, verbatim (OWNER-QUOTE-BEGIN — the owner's own words, kept for provenance):
--   "Start the registry + cash watchdog , keep these reports in management dashboard under different
--    reports so it is easy for the management to review each area and take appropriate action , name
--    it Management Watch dog
--    Make the voids visible and track able
--    Which five modules write findings  and why do they write 3 different ways -"
-- (OWNER-QUOTE-END)
--
-- WHAT THIS MIGRATION IS FOR, AND WHAT IT IS NOT
-- ──────────────────────────────────────────────
-- The whole Management Watchdog needs exactly ONE new table, and it holds no findings and no money:
-- it holds the per-org THRESHOLDS the two new detectors read. That is deliberate.
--
--   • The findings go in `commcalc.flags`, which has existed since migration 002 and already has the
--     additive merge, the stable identity key and the retire-in-place behaviour that keeps a
--     manager's ruling alive (migration 287 + backend/app/modules/commcalc/flag_persist.py). A new
--     findings table would be the sibling derivation the index rules forbid.
--   • The review AREA each finding belongs to is a property of its TYPE, not of the finding, so it
--     lives in ONE registry (`backend/app/modules/commcalc/flag_registry.py`) and the read path
--     dereferences it. There is deliberately NO `area` column here or on `flags`: a column would be
--     a second copy and a future divergence (CLAUDE.md, "one fact, one home, dereferenced — never
--     copied").
--   • The cash detector needs no new ingest at all. `commcalc.closing_attempt` has carried
--     `entered_cash` / `entered_credit`, the point-of-sale figures `b2b_cash` / `b2b_credit`, the
--     variance directions and `auto_accepted` since migration 103. Nothing watched them.
--   • The void detector needs no new ingest either. `commcalc.raw_sales.voided` / `trans_type` have
--     always been there; every reader just threw those lines away.
--
-- 💰 MOVES NO MONEY. This migration creates one config table and seeds house-default threshold rows.
-- It books nothing, pays nothing, and alters no existing table, column, amount or constraint. No
-- money-touching change and no data rewrite is included, which is why it needs no owner money
-- approval beyond running it.
--
-- NOT A BACKFILL, STATED PLAINLY. Existing `commcalc.flags` rows keep the severity spelling they were
-- written with (`HIGH` / `MEDIUM` / `LOW` / `CRITICAL` from commcalc, `critical` / `warning` from
-- asset, account and payables). They are canonicalised ON READ by the registry, and new writes are
-- canonical at birth. Re-spelling history was rejected on purpose: a finding is an accusation against
-- a person with a manager's ruling attached, and rewriting stored rows to tidy a vocabulary is the
-- same class of erasure `flag_persist.py` exists to prevent.
--
-- RULE TWO — config, never code. No carrier, tenant, store or product name appears in this file. The
-- house defaults are seeded for the house org only; a tenant that wants different thresholds gets a
-- row of its own, and a tenant with no row inherits the code default in
-- `flag_registry.DEFAULT_PARAMS` (so the watchdogs work on the day the code ships, not on the day
-- somebody remembers to run this).
--
-- Idempotent and additive. Safe to run twice.
--
-- REVERT (1): drop table if exists commcalc.watchdog_rule;
--
-- Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §52.

-- ── (1) The one table. One row per (org, watchdog). ─────────────────────────────────────────────
create table if not exists commcalc.watchdog_rule (
  id         uuid primary key default gen_random_uuid(),
  org_id     uuid not null,
  -- The canonical flag type, as `flag_registry.TYPES` spells it. Deliberately TEXT and not an enum:
  -- the registry is the one home for the vocabulary, and an enum here would be a second copy that
  -- a migration has to chase every time a detector is added.
  flag_type  text not null,
  -- NULL means "no opinion", which the code reads as the house default (on) — NOT as off. An
  -- operator clearing a cell must not silently stop a cash check.
  enabled    boolean,
  -- Only the keys the registry declares for this type are honoured; anything else is ignored rather
  -- than trusted, so a typo cannot quietly disable a threshold.
  params     jsonb not null default '{}'::jsonb,
  note       text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- One rule per (org, type). This is what makes a seed re-runnable and an operator save idempotent.
create unique index if not exists watchdog_rule_org_type
  on commcalc.watchdog_rule (org_id, flag_type);

-- ── (2) RLS, the same shape every commcalc config table uses. ──────────────────────────────────
alter table commcalc.watchdog_rule enable row level security;
do $$
begin
  if not exists (select 1 from pg_policies
                 where schemaname = 'commcalc' and tablename = 'watchdog_rule'
                   and policyname = 'watchdog_rule_service') then
    create policy watchdog_rule_service on commcalc.watchdog_rule
      for all using (true) with check (true);
  end if;
end $$;

-- ── (3) House defaults, for the house org only. ────────────────────────────────────────────────
-- These MIRROR `flag_registry.DEFAULT_PARAMS` so an operator can see and edit them on the rules
-- screen rather than having to read code. The code remains the fallback, so the two cannot leave a
-- watchdog unconfigured — and `harness_flag_registry_lock.py` keeps the registry's own invariants.
--
-- Every default is deliberately QUIET on arrival. A watchdog that flags a third of store-days on day
-- one is the alert noise the house removed in index §19.17, and a manager who mutes a board never
-- un-mutes it. Tightening them is the tenant's call.
insert into commcalc.watchdog_rule (org_id, flag_type, enabled, params, note) values
  -- Cash & Closing. Dollars of variance before a drawer day is a finding; coin-level rounding is not
  -- a watchdog.
  ('00000000-0000-0000-0000-000000000001', 'CASH_OVER',  true, '{"tolerance": 20.0}'::jsonb,
   'Drawer over the point-of-sale figure by this many dollars or more.'),
  ('00000000-0000-0000-0000-000000000001', 'CASH_SHORT', true, '{"tolerance": 20.0}'::jsonb,
   'Drawer short by this many dollars or more. Graded worse than an over: an over is usually a miscount, a short is money that is not there.'),
  ('00000000-0000-0000-0000-000000000001', 'CREDIT_VARIANCE', true, '{"tolerance": 20.0}'::jsonb,
   'Card total away from the point-of-sale figure by this many dollars or more, either way.'),
  -- Size-independent on purpose: being waved through on the last try IS the finding.
  ('00000000-0000-0000-0000-000000000001', 'CASH_AUTO_ACCEPTED', true, '{"tolerance": 0.0}'::jsonb,
   'A closing accepted on the final try while still mismatched. Raised whatever the amount, because nobody agreed the count.'),
  ('00000000-0000-0000-0000-000000000001', 'CASH_AWAITING_CORRECTION', true, '{}'::jsonb,
   'Cash entered, the count refused, and the rep never came back. Real declared money in no closing at all.'),
  ('00000000-0000-0000-0000-000000000001', 'CASH_REPEAT_VARIANCE', true, '{"min_days": 3}'::jsonb,
   'The same person closing on this many out-of-tolerance days in one period. One bad count is a mistake; a run of them is a habit.'),
  -- Voids, Returns & Waived Fees. `min_lines` is the floor that stops "1 of 2 lines voided = 50%"
  -- filling the board with reps who barely sold.
  ('00000000-0000-0000-0000-000000000001', 'VOID_RATE_HIGH', true,
   '{"max_share": 0.05, "min_lines": 20}'::jsonb,
   'A person voiding more than this share of their lines, once they have at least min_lines lines.'),
  ('00000000-0000-0000-0000-000000000001', 'RETURN_RATE_HIGH', true,
   '{"max_share": 0.08, "min_lines": 20}'::jsonb,
   'A person returning more than this share of their lines, once they have at least min_lines lines.'),
  ('00000000-0000-0000-0000-000000000001', 'VOID_AFTER_SALE', true, '{}'::jsonb,
   'One device on both a counted sale and a voided line in the same period. The sales feed carries no time of day, so the order of the two is not claimed.'),
  ('00000000-0000-0000-0000-000000000001', 'VOID_UNATTRIBUTED', true, '{"min_lines": 1}'::jsonb,
   'Voided lines carrying nobody''s name, counted per store. A void with no rep cannot be coached or charged back.')
on conflict (org_id, flag_type) do nothing;

-- ── (4) NOTHING SENDS OR RUNS UNTIL THE LINE BELOW IS PASTED. ──────────────────────────────────
-- Stated as a step, not a footnote: the 2026-10-04 notes record two alert sweeps that shipped
-- correct, green and completely silent because their `cron.schedule` line was never run. One line,
-- one job, BOTH detectors (that is why there is a single `/watchdog/run-due` and not two).
--
-- Replace <BASE> with the API origin and <SECRET> with NOTIFY_RUN_SECRET, then run ONCE:
--
--   SELECT cron.schedule('watchdog-run-due', '35 * * * *', $$
--     SELECT net.http_post(
--       url     := '<BASE>/api/v1/commcalc/watchdog/run-due',
--       headers := jsonb_build_object('Content-Type','application/json',
--                                     'x-notify-secret','<SECRET>'),
--       body    := '{}'::jsonb);
--   $$);
--
-- Minute 35 is chosen to sit clear of the jobs already registered on this schema (notify :05,
-- billpay :05, manager-followup :10, dm-visit-assign :20, store-visit-alerts :25), so the hour's
-- http_post calls do not pile onto the same minute.
--
-- To check it afterwards:
--   SELECT jobname, schedule, active FROM cron.job WHERE jobname = 'watchdog-run-due';
--   SELECT * FROM cron.job_run_details
--     WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname = 'watchdog-run-due')
--     ORDER BY start_time DESC LIMIT 5;
--
-- And to see what it found, with no cron at all:
--   POST /api/v1/commcalc/watchdog/run-now?period=October%202026

select 'watchdog_rule ready — ' ||
       (select count(*)::text from commcalc.watchdog_rule
        where org_id = '00000000-0000-0000-0000-000000000001')
       || ' house default rules seeded' as status;
