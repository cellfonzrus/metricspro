-- 290_commission_report_schedule_and_grain.sql   (mod-commission, band 200-299)
--
-- WHY. Two live defects on the ePay sweep, both caused by per-report behaviour being hard-coded in
-- epay_sweep.py instead of configured per report in the registry the Connectors page already edits:
--
--   1. `report_definitions.refresh_months` has existed as a column (and is set to 3 for mi_report)
--      but NOTHING READS IT. The multi-month refresh was a Python constant, COMP_REFRESH_MONTHS,
--      applied to the comp leg only. MI therefore only ever pulled the CURRENT month, so every
--      closed month froze at whatever day its last sweep ran: June 2026 MI is stuck at 06-23 and
--      July at 07-08, ~$45k and ~$105k short of the run rate. The setting was inert.
--
--   2. The Comprehensive Comp report is a DAILY report whose data posts late in the evening. The
--      connector has ONE `hour` driving all three reports, so comp was being pulled at 06:00 ET
--      when the portal legitimately has nothing posted yet, and every run failed with "contained
--      no rows". It needs its own 23:30 ET slot without moving MI or payment_detail off 06:00.
--
-- Additive + idempotent. Schema only: this moves NO payout number, rate, plan, schedule of pay, or
-- paid/earned column. It configures WHEN and HOW WIDE a portal report is fetched.
--
-- NOTE ON DEFAULTS: every column is added with a default that reproduces TODAY'S behaviour, so
-- applying this migration on its own changes nothing until an operator (or the follow-up seed
-- below) sets a value.

-- ── per-report fetch width ──────────────────────────────────────────────────────────────────
-- refresh_months already exists (int, default 1). refresh_days is its day-grain sibling, for
-- reports like Comprehensive Comp that are fetched by date range rather than by month.
alter table commcalc.report_definitions
    add column if not exists refresh_days smallint not null default 1;

comment on column commcalc.report_definitions.refresh_days is
    'Day-grain reports: how many trailing days (including today) each sweep re-fetches. 1 = today '
    'only. Raising it makes a missed run self-heal, at no extra portal cost when the report accepts '
    'a date RANGE (Comprehensive Comp returns every day in the range in a single run).';

comment on column commcalc.report_definitions.refresh_months is
    'Month-grain reports: how many months (current + N-1 prior closed) each sweep re-fetches, for '
    'reports that keep accruing after month end. Read by epay_sweep._expand_jobs.';

-- ── per-report schedule ─────────────────────────────────────────────────────────────────────
-- NULL = "use the connector's own schedule", which is the current behaviour for every report.
alter table commcalc.report_definitions
    add column if not exists sweep_hour smallint,
    add column if not exists sweep_minute smallint not null default 0,
    add column if not exists sweep_timezone text,
    add column if not exists sweep_next_run_at timestamptz,
    add column if not exists sweep_last_run_at timestamptz,
    add column if not exists sweep_last_status text,
    add column if not exists sweep_last_detail text;

comment on column commcalc.report_definitions.sweep_hour is
    'Local hour (0-23) this specific report is pulled, overriding the connector''s single hour. '
    'NULL = follow the connector. Comprehensive Comp needs 23:30 because the carrier posts the '
    'day''s compensation late in the evening; a 06:00 pull returns an empty report.';

comment on column commcalc.report_definitions.sweep_timezone is
    'IANA zone for sweep_hour/sweep_minute. NULL = the connector''s timezone.';

alter table commcalc.report_definitions
    drop constraint if exists report_definitions_sweep_hour_ck;
alter table commcalc.report_definitions
    add constraint report_definitions_sweep_hour_ck
    check (sweep_hour is null or (sweep_hour between 0 and 23));

alter table commcalc.report_definitions
    drop constraint if exists report_definitions_sweep_minute_ck;
alter table commcalc.report_definitions
    add constraint report_definitions_sweep_minute_ck
    check (sweep_minute between 0 and 59);

-- run-due scans for reports whose own slot has come round.
create index if not exists report_definitions_sweep_due_idx
    on commcalc.report_definitions (sweep_next_run_at)
    where sweep_hour is not null;

-- ── the two settings this was built for ─────────────────────────────────────────────────────
-- Comprehensive Comp: pull at 23:30 in the connector's zone, today only. Scoped to rows that are
-- actually the ePay comp report, and written for EVERY tenant that has one (no org_id literal).
update commcalc.report_definitions
   set sweep_hour = 23,
       sweep_minute = 30,
       refresh_days = greatest(refresh_days, 1)
 where report_key = 'comp_report'
   and sweep_hour is distinct from 23;

-- MI keeps the connector's 06:00 slot; refresh_months is already 3 where it matters and is now
-- actually read. This line only fills a NULL/0 so the column can be trusted by the sweep.
update commcalc.report_definitions
   set refresh_months = 1
 where refresh_months is null or refresh_months < 1;
