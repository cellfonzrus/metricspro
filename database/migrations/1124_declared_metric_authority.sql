-- 1124_declared_metric_authority.sql — WHICH SOURCE ANSWERS "what did this store-day do": the
-- reps' own closing declaration ('declaration', the house default and what every tenant has today),
-- or the store's own POS sales report ('feed', commcalc.portal_metric_day, mig 1078).
-- Band 1000+ (platform). Additive + idempotent (safe to re-run).
--
-- MONEY-TOUCHING — it decides which figures the daily incentive is computed from, so it is surfaced
-- for owner approval before it is applied. Nothing is paid by it: GET /commcalc/daily-incentive
-- computes only and persists nothing (index §76.4), and that posture is unchanged.
--
-- OWNER, 2026-11-17, verbatim: "daily numbers shouold come from the daily sales report i just
-- uploaded, make a way to upload this report" and "let the feed be the authority till all reps are
-- fully trained to enter daily data, keep teh switch closed but open later when needed".
--
-- WHY A COLUMN AND NOT A HOUSE DEFAULT. Which of two sources a business trusts is a fact about THAT
-- business's operation — one tenant's reps are trained and declare every day, another's are not yet.
-- A house default either way would answer for a tenant that has not been asked. So:
--
--   NULL = nothing declared = 'declaration' = exactly what every tenant reads today.
--
-- That is deliberate and it is the whole safety of this file: applying it to production changes NO
-- tenant's figures until somebody sets a value. An absent value that had read as 'feed' would start
-- paying off a feed because nobody answered a question (the 2026-11-12 lesson, §STOREOPS-07).
--
-- WHAT IT UNLOCKS, measured on the tenant that asked for it: its reps have declared ONE line in the
-- whole of October 2026, so the declaration side answers ~nothing and its plan computes to ~$0 —
-- while its own POS export carries every day of the month. Nothing about that is repaired by code:
-- it is repaired by letting the business say which source it trusts.
--
-- REVERT: alter table commcalc.commission_org_config drop column if exists declared_metric_authority;
--         (every tenant then resolves to 'declaration', which is this file's own NULL behaviour.)

alter table if exists commcalc.commission_org_config
  add column if not exists declared_metric_authority text;

comment on column commcalc.commission_org_config.declared_metric_authority is
  'Which source answers what a store-day did, for the daily incentive: ''declaration'' (what the '
  'reps typed at closing — commcalc.closing_metric_line, the house default) or ''feed'' (the '
  'store''s own POS report — commcalc.portal_metric_day). NULL = house default. Resolved by '
  'commcalc/metric_authority.resolve — the one home; no caller compares this string itself.';

-- A value this platform does not recognise must never decide a payout. `metric_authority.resolve`
-- REFUSES an unknown value rather than falling back, so this constraint stops the typo being
-- STORED; the two together mean a mistyped authority can neither be saved nor silently honoured.
do $$
begin
  if not exists (select 1 from pg_constraint
                 where conname = 'commission_org_config_metric_authority_ck') then
    alter table commcalc.commission_org_config
      add constraint commission_org_config_metric_authority_ck
      check (declared_metric_authority is null
             or declared_metric_authority in ('declaration', 'feed'));
  end if;
end $$;

-- The backend reaches every relation through PostgREST. `commission_org_config` already exists, so
-- this is a re-grant, not a new one, and it is here so a re-run leaves the table reachable whatever
-- else has happened to it.
grant select, insert, update, delete on commcalc.commission_org_config to service_role;
