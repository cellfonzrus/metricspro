-- 1116_portal_pull_backlog.sql
-- Index §COMMCALC-67. Additive, idempotent. ONE NEW TABLE + THREE NEW COLUMNS on an existing one.
-- NO MONEY MOVES: nothing here reads or writes a rate, plan, payout, commission or P&L row, no
-- schedule is registered, no credential is touched, and no feed starts pulling.
--
-- OWNER ASK, verbatim (2026-11-10): "Create access to these back office to pull reports from epay for
-- each individual store for epay reconciliation in boost, make it universal but turn it on for boost
-- only. Each store has their individual logins, we will pull all avaibake reports very day and first
-- time from the beginning of the year 2026 till date then update very day autonomously".
--
-- WHAT WAS MISSING, AS A CLASS RATHER THAN AS THIS PORTAL. The platform already has ONE portal pull
-- loop (`commcalc/portal_report_pull.py`, index §COMMCALC-07) that three feeds ride as adapters. Two
-- general facts it could not state are what this ask needed, and neither is this tenant's or this
-- vendor's:
--
--   (1) A LOGIN CAN BELONG TO A STORE. `commcalc.data_source` is "one row per portal login" and had no
--       store: every existing feed logs in once for the whole company. A processor that issues one
--       login per store means the login itself is the store attribution — a fact `epay_ingest`
--       previously could only get from the terminal→store registry, which is why an unmapped terminal
--       landed money under NO store.
--   (2) A PULL CAN OWE HISTORY. `portal_report_pull.date_range` answers exactly one question — the
--       last N days ending yesterday, clamped to MAX_WINDOW_DAYS so a daily pull cannot re-fetch
--       months and get the tenant rate-limited. That clamp is right and stays. But it left the loop
--       unable to be asked "every day since the first of the year, once", so a connector that owed
--       history had nowhere to put it except a SECOND pull loop with its own window arithmetic — the
--       duplicate defect the house rules forbid, which would have arrived with the first tenant who
--       said "catch me up".
--
-- So the history is a BACKLOG, not a wider window: the source declares where its history starts, the
-- loop is handed the oldest span it still owes, and what it answered is recorded. The daily pull keeps
-- its own recent window the moment nothing is owed. One home: `commcalc/portal_backfill.py`, which
-- DEREFERENCES the loop's ceiling rather than keeping a second copy of it.
--
-- WHY `empty` IS AN ANSWER. A window the portal had nothing for will have nothing for it tomorrow
-- either; re-asking forever is how a backfill never finishes. A window that FAILED is retried up to
-- the code's MAX_ATTEMPTS and then stops blocking the rest — and is REPORTED as stuck, because a gap
-- in the history is shown as a gap, never skipped quietly and never counted as done.
--
-- UNIVERSAL, AND ON FOR NOBODY BY ITSELF. Every column here is per-login config; no carrier, tenant,
-- vendor or portal name appears in this file or in the code that reads it (RULE TWO). Any tenant can
-- use it the day it declares a login. Nothing is switched on by applying this: a pull still needs a
-- tenant to create its own login rows and authenticate once.
--
-- DUPLICATE CHECK (build gate — docs/SYSTEM_DATA_FLOW_INDEX.md §16/§17/§18 and the §COMMCALC-07
-- adapter register were searched before anything was created):
--   · `commcalc.data_source` (mig 083 + 11 later column migrations) — THE registry of portal logins.
--     EXTENDED with three columns. NOT forked: a second "per-store login" table would be a second
--     answer to "which logins do we have", which is exactly the drift the index rules forbid.
--   · `commcalc.derived_build_status` (mig 1100) — a queue keyed (org, build_key) for DERIVED
--     SNAPSHOT rebuilds. Read and rejected deliberately: it answers "this snapshot is stale, rebuild
--     it", with no notion of a date span, and a rebuild is idempotent over the whole shelf. A
--     backfill is a set of SPANS each of which must be asked for exactly once. Same shape of queue,
--     different key; folding one into the other would give both a column the other never sets.
--   · `commcalc.report_definitions.refresh_days` / `arrears_days` / `empty_stale_after_days`
--     (migs 039 / 1042) — how FRESH a feed must be and how late it posts. REUSED by the sweep window
--     floor already; they answer "how far back does a ROUTINE pull reach", not "which historical
--     spans are still owed". Deliberately not overloaded.
--   · `commcalc.portal_backoff` (mig 244) — the cooldown a portal imposes on us. A different fact,
--     already dereferenced by the loop; untouched here.
--   · `core.job_run` (mig 701) / `commcalc.import_audit` — the EVIDENCE of what a run did. Still the
--     home for that; this table holds the far smaller fact of which spans remain owed, which a job
--     log cannot answer without re-deriving it from every run ever made.
--   · `commcalc.raw_epay_daily_tx` (mig 903) — where the processor's transaction detail lands. REUSED
--     through its existing ingest (`epay_ingest.ingest`); NO new landing table is created here, and
--     the per-store feed lands in the same rows the bill-pay and fee recons already read.
--
-- REVERT:
--   drop table if exists commcalc.portal_pull_backlog;
--   alter table commcalc.data_source drop column if exists store_code,
--     drop column if exists history_from, drop column if exists history_chunk_days;

-- ── 1. a login can belong to a store, and can be asked for history ──────────────────────────────
alter table commcalc.data_source
  -- The store every row pulled through THIS login belongs to. A company-wide login leaves it NULL and
  -- behaves exactly as it does today. Deliberately TEXT and not a foreign key: it is the tenant's own
  -- store code, resolved through the platform's one store resolver by the readers, and a constraint
  -- here would reject a login created before its store row exists.
  add column if not exists store_code        text,
  -- The date this login's history should start from. NULL (the default) = this login owes no history
  -- and only ever pulls the recent window — today's behaviour for every existing login.
  add column if not exists history_from      date,
  -- How many days one history window covers. NULL = the code's house default. Clamped in code to the
  -- pull loop's own ceiling, so a wide declaration cannot become a month-long portal request.
  add column if not exists history_chunk_days int;

comment on column commcalc.data_source.store_code is
  'Index §COMMCALC-67. The store whose own login this row is, for a portal that issues one login per store. NULL = a company-wide login (every pre-2026-11 login). Read by commcalc/processor_backoffice_sweep.py and passed to epay_ingest.resolve_stores as the answer for a terminal the declared merchant registry cannot place; the registry still wins where it has one.';
comment on column commcalc.data_source.history_from is
  'Index §COMMCALC-67. The date this login''s report history should start from, once. NULL = owes no history (default). Read ONLY by commcalc/portal_backfill.py.';
comment on column commcalc.data_source.history_chunk_days is
  'Index §COMMCALC-67. How many days one backfill window covers for this login. NULL = portal_backfill.DEFAULT_CHUNK_DAYS; clamped in code to portal_report_pull.MAX_WINDOW_DAYS.';

-- ── 2. which spans has this login already answered ──────────────────────────────────────────────
create table if not exists commcalc.portal_pull_backlog (
  org_id       uuid not null,
  -- The login the span belongs to (commcalc.data_source.id). No foreign key, for the reason mig 1100
  -- states about build_key: the readers already scope every query by org_id and source_id, and a
  -- cascade here would silently delete the record of what was pulled when a login is re-created.
  source_id    uuid not null,
  -- Which report the span covers. '*' means the login's whole declared report set, which is what the
  -- loop pulls in one pass (portal_backfill.ALL_REPORTS — spelled once, in code). The column exists so
  -- a future per-report backfill needs no migration; nothing writes anything else today.
  report_key   text not null default '*',
  window_from  date not null,
  window_to    date not null,
  -- DERIVED from what the pull reported, never asserted beside it (portal_backfill.state_of):
  --   done   — rows landed for this span;
  --   empty  — the pull completed and the portal had nothing for these days (an ANSWER, see header);
  --   failed — anything else, including a refusal with a named reason in last_error.
  state        text not null default 'failed',
  attempts     int  not null default 0,
  rows_landed  int  not null default 0,
  last_error   text,
  last_attempt_at timestamptz,
  created_at   timestamptz not null default now(),
  primary key (org_id, source_id, report_key, window_from, window_to)
);

create index if not exists portal_pull_backlog_owed_idx
  on commcalc.portal_pull_backlog (org_id, source_id, window_from)
  where state <> 'done';

-- A MIGRATION GRANTS WHAT IT CREATES (house convention; from 2026-10-30 Supabase no longer grants new
-- public tables automatically). service_role ONLY: the backend reaches this through PostgREST and the
-- browser never reads it, so an `anon` grant would widen exposure rather than fix anything.
grant select, insert, update, delete on commcalc.portal_pull_backlog to service_role;

comment on table commcalc.portal_pull_backlog is
  'Index §COMMCALC-67. One row per (org, portal login, report set, date span): which spans of a login''s declared history have been ANSWERED, and what happened. Written and read only by commcalc/portal_backfill.py, which hands the one pull loop the oldest span still owed and otherwise leaves the daily recent window exactly as it was. Not a feed and not evidence: core.job_run holds what a run did, this holds only what is still owed.';

notify pgrst, 'reload schema';
select '1116 complete — a portal login can now belong to a store and be asked for history; nothing is turned on and no feed pulls until a tenant declares its own login' as status;
