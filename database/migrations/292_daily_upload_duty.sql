-- 292_daily_upload_duty.sql   (mod-commission, band 200-299)
--
-- OWNER DIRECTIVE 2026-08-09 (verbatim intent):
--   • "if the item is not marked auto ingest then it should not appear as a notification"
--   • "MA commission, TX and handset need to be updated daily"
--   • "all items which need daily uploads should be assigned one person who will upload the daily
--      data with the proper instructions"
--   • "as soon as they log in those are the things it should prompt them to do"
--   • "have them set up daily notification at a certain hour"
--   • "in case of an auto update fails it should prompt the designated user to upload the required
--      file with exact date range and instructions"
--   • "the user will not be able to handle the error if any and that should be communicated to the
--      designated person in case of an error"
--
-- MODEL. A duty is a property of a REPORT, so it lives on the registry the Imports page already
-- edits (commcalc.report_definitions) rather than in a parallel table. Assignment is PER REPORT with
-- a TENANT-LEVEL DEFAULT: setting only the tenant default reproduces the owner's "one person does all
-- the daily uploads" exactly, while a per-report override covers the case where that stops being true.
-- Nothing is hard-coded to MA/TX/handset -- those are simply the first three rows flagged, per the
-- SAP-configurable rule.
--
-- The required DATE RANGE is deliberately NOT stored: it is derived at read time from the target
-- table's own last successful load through today, so it cannot go stale or disagree with the data.
--
-- Additive + idempotent. Moves NO payout number, rate, plan or paid/earned column.

begin;

-- ── per-report duty ─────────────────────────────────────────────────────────────────────────────
alter table commcalc.report_definitions
    add column if not exists daily_upload        boolean not null default false,
    add column if not exists upload_assignee     text,
    add column if not exists upload_instructions text,
    add column if not exists reminder_hour       smallint,
    add column if not exists reminder_minute     smallint not null default 0;

comment on column commcalc.report_definitions.daily_upload is
    'This report must be uploaded EVERY DAY. Drives the login prompt and the daily reminder. '
    'Independent of `auto`: an auto report that FAILS falls back to the same duty.';
comment on column commcalc.report_definitions.upload_assignee is
    'storeops.app_users.email of the ONE person responsible for uploading this report. NULL falls '
    'back to the tenant default (commcalc.report_upload_defaults.default_assignee). Errors for this '
    'report are routed here -- an ordinary user cannot action an ingest failure.';
comment on column commcalc.report_definitions.upload_instructions is
    'Exactly what the assignee must do: which portal, which menu, which filters. Shown verbatim in '
    'the login prompt and in the failure notification, next to the computed date range.';
comment on column commcalc.report_definitions.reminder_hour is
    'Local hour (0-23) to remind the assignee. NULL falls back to the tenant default.';

-- ── tenant defaults: the owner''s "one person" answer ───────────────────────────────────────────
create table if not exists commcalc.report_upload_defaults (
    org_id            uuid primary key,
    default_assignee  text,
    reminder_hour     smallint not null default 9,
    reminder_minute   smallint not null default 0,
    timezone          text     not null default 'America/New_York',
    updated_at        timestamptz not null default now()
);

comment on table commcalc.report_upload_defaults is
    'Tenant-wide daily-upload duty defaults. Setting default_assignee alone gives the owner''s '
    '"one person uploads everything daily" model; a per-report upload_assignee overrides it.';

-- ── flag the three the owner named, for the tenant that actually has them ───────────────────────
-- Keyed by target_table, NOT by the hand-typed report_key, because those keys carry typos
-- ("MA Dailt TX SubMA"). Only rows that exist are touched; no tenant is assumed.
update commcalc.report_definitions
   set daily_upload = true,
       upload_instructions = coalesce(nullif(upload_instructions,''),
           case target_table
             when 'raw_ma_commission'  then
               'VidaPay portal -> Reports -> MA Commission Details. Set the date range shown below, '
               'export to Excel, then upload here. One file per range; do not merge months.'
             when 'raw_ma_daily_tx'    then
               'VidaPay portal -> Reports -> MA Daily Tx (SubMA). Set the date range shown below, '
               'export to Excel, then upload here. This is the ONLY source for total residual.'
             when 'raw_ma_fulfillment' then
               'VidaPay portal -> Reports -> MA Marketplace Handset Fulfillment Orders. Set the date '
               'range shown below, export to Excel, then upload here.'
           end),
       updated_at = now()
 where target_table in ('raw_ma_commission','raw_ma_daily_tx','raw_ma_fulfillment');

-- Seed a defaults row for every tenant that has at least one daily-upload report, so the Imports
-- page always has something to edit. Assignee intentionally left NULL -- naming a person is the
-- owner's call, and a wrong default would silently address someone else's duty to them.
insert into commcalc.report_upload_defaults (org_id)
select distinct d.org_id from commcalc.report_definitions d
 where d.daily_upload
   and not exists (select 1 from commcalc.report_upload_defaults r where r.org_id = d.org_id);

create index if not exists report_definitions_daily_upload_idx
    on commcalc.report_definitions (org_id, daily_upload) where daily_upload;

commit;
