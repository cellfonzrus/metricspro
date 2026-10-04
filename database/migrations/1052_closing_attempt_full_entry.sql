-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- 1052 — A BLOCKED SUBMIT KEEPS EVERYTHING THE REP TYPED, NOT ONLY THE MONEY
--        (owner bug report 2026-10-04, 117 E Burnside Ave / 2026-10-01; index §29.12)
--
-- OWNER, verbatim: "If the stops the reform entering the 1st closing and tells them to correct it,
-- the rep tries again but stops at 2 or even after the 1st attempt, the system should say to correct
-- the entries like it does but also save the last entered data in thr system so the system is not
-- blank at any time like what happened with Abid"
--
-- WHAT WAS MEASURED LIVE FIRST (read-only, 2026-10-04). B-117 / 2026-10-01, employee "Rana": two
-- `commcalc.closing_attempt` rows two seconds apart, both `blocked`; entered cash $2,826.00 vs POS
-- $2,631.83 ($194.17 over), entered credit $270.00 vs POS $146.59 ($123.41 over), t_zelle $225.00;
-- and NO `commcalc.daily_closing` row for that store-day. The 3-try gate blocks tries 1-2 and
-- auto-accepts the 3rd; he stopped at two.
--
-- SO THE MONEY WAS NEVER LOST — `closing_attempt` has carried all eight tender columns, the POS
-- figures and the variance directions since mig 103. What it dropped was the rest of the form: the
-- accessory sale, the three counts, the remarks and the envelope photo. A rep returning for try
-- three had to retype all of it, which is a reason to stop. These five columns close that, so
-- `closing/unfinished_day.resume_entry` can hand the form back filled in.
--
-- ADDITIVE AND IDEMPOTENT. No column is dropped, no row is written, no existing column is read or
-- rewritten. Nothing here touches `commcalc.daily_closing` at all — an unfinished day deliberately
-- creates no closing row, which is what keeps un-corrected figures out of every money report (the
-- owner chose "not until corrected" on 2026-10-04).
--
-- NO AMOUNT COLUMN IS ADDED, READ OR WRITTEN BY THIS FILE. `acc_sale` is the accessory GROSS the rep
-- types on the form and is already stored on `daily_closing`; it is carried here only so the form can
-- be refilled, and no report reads it from this table.
-- ════════════════════════════════════════════════════════════════════════════════════════════════

-- ── 1. The rest of the submit form, so a blocked try can be resumed ────────────────────────────
--    Column names and types mirror `commcalc.daily_closing` exactly, so the resume path hands back
--    values the submit endpoint can accept unchanged.

alter table commcalc.closing_attempt add column if not exists acc_sale          numeric(12,2);
alter table commcalc.closing_attempt add column if not exists upgrade_count     integer;
alter table commcalc.closing_attempt add column if not exists new_line_count    integer;
alter table commcalc.closing_attempt add column if not exists postpaid_count    integer;
alter table commcalc.closing_attempt add column if not exists remarks           text;
alter table commcalc.closing_attempt add column if not exists envelope_picture  text;

comment on column commcalc.closing_attempt.acc_sale is
  'Accessory gross as typed on the try. Resume-only (closing/unfinished_day.ENTRY_COLUMNS); no report reads it here.';
comment on column commcalc.closing_attempt.upgrade_count is
  'Upgrades as typed on the try. Resume-only; no report reads it here.';
comment on column commcalc.closing_attempt.new_line_count is
  'New lines as typed on the try. Resume-only; no report reads it here.';
comment on column commcalc.closing_attempt.postpaid_count is
  'Postpaid as typed on the try. Resume-only; no report reads it here.';
comment on column commcalc.closing_attempt.remarks is
  'Rep remarks as typed on the try. Resume-only; no report reads it here.';
comment on column commcalc.closing_attempt.envelope_picture is
  'Envelope photo path from the try, so a resumed submit need not re-photograph. Resume-only.';

-- REVERT (1):
--   alter table commcalc.closing_attempt drop column if exists acc_sale;
--   alter table commcalc.closing_attempt drop column if exists upgrade_count;
--   alter table commcalc.closing_attempt drop column if exists new_line_count;
--   alter table commcalc.closing_attempt drop column if exists postpaid_count;
--   alter table commcalc.closing_attempt drop column if exists remarks;
--   alter table commcalc.closing_attempt drop column if exists envelope_picture;
-- Reverting only costs a resumed form its non-money fields; `closing/router._log_attempt` degrades
-- to the mig-103 column set and still records every try (harness §G pins that).


-- ── 2. Finding the store-days somebody started and did not finish ──────────────────────────────
--    The dashboard, the DM-verify screen and the deadline alert each ask "which store-days on this
--    date have tries?" — one index serves all three. Not unique: a store-day has one row per try.

create index if not exists closing_attempt_store_day
  on commcalc.closing_attempt (org_id, close_date, store_code);

-- REVERT (2): drop index if exists commcalc.closing_attempt_store_day;


-- ── 3. Verification — what this migration should look like afterwards ──────────────────────────
--    Read-only; safe to re-run. Expected on the house org as measured 2026-10-04: exactly one
--    store-day (B-117 / 2026-10-01) with real tries and no closing.
do $$
declare
  n_cols integer;
  n_unfinished integer;
begin
  select count(*) into n_cols
    from information_schema.columns
   where table_schema = 'commcalc' and table_name = 'closing_attempt'
     and column_name in ('acc_sale','upgrade_count','new_line_count','postpaid_count',
                         'remarks','envelope_picture');
  if n_cols <> 6 then
    raise exception 'ABORT: expected 6 resume columns on commcalc.closing_attempt, found %', n_cols;
  end if;

  select count(*) into n_unfinished
    from (
      select a.org_id, a.close_date, a.store_code
        from commcalc.closing_attempt a
        left join commcalc.daily_closing d
               on d.org_id = a.org_id
              and d.close_date = a.close_date
              and d.store_code = a.store_code
       where coalesce(a.refused, false) = false
         and d.id is null
       group by a.org_id, a.close_date, a.store_code
    ) s;

  raise notice '1052 OK — 6 resume columns present; % store-day(s) started and not finished', n_unfinished;
end $$;
