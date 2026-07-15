-- 502_closing_dupe_release_deposit_ocr.sql
-- retail-ops-7: (1) duplicate-submission fix + management RELEASE override for daily_closing,
-- (3) bank-deposit slip OCR + configurable match-target, (4) config backing the 4th tender-recon leg.
-- Additive / idempotent — safe to re-run. Degrades gracefully if not yet applied (every backend read/
-- write this migration supports catches the "column/table doesn't exist" case and falls back to the
-- pre-502 shape — see closing/router.py).

-- ══════════════════════════════════════════════════════════════════════════════════════════════════
-- (1) ONE ACTIVE SUBMISSION PER (org, store, employee, close_date) — the double-submit bug fix.
--
-- Today `POST /closing/row` does a bare `.insert()` — a rep who submits twice creates a SECOND
-- daily_closing row, and closing_summary/recon SUM every row for a store, silently DOUBLING declared
-- cash/credit. Fix: the backend now checks for an existing row before inserting and refuses a second
-- submit (409) unless a manager has RELEASED it — a release unlocks that EXACT row for one corrected
-- resubmit (an UPDATE, never a second INSERT). Every release + correction is audited.
--
-- WHY NOT A PLAIN `UNIQUE(org_id, store_code, employee_name, close_date)` CONSTRAINT: duplicate rows
-- may ALREADY exist in production from the very bug this migration fixes. A bare unique constraint
-- would fail outright the moment it hit an existing duplicate pair, aborting this entire migration
-- (and every later-numbered one that depends on it having run) until someone manually deduped first.
-- Instead this ships a PARTIAL unique index keyed on a computed `dedup_key`, backfilled ONLY for
-- (store, employee, day) combos that are CURRENTLY CLEAN (exactly one row) — those get real DB-level
-- protection immediately. Existing duplicate groups are left with `dedup_key = NULL` (Postgres treats
-- NULL as distinct in a unique index, so they never collide with anything) until the owner reviews the
-- read-only GET /closing/duplicates report and resolves them by hand (NEVER auto-deleted here); a
-- follow-up migration can then backfill their dedup_key too, once clean. This is a
-- "constraint-after-dedup-report" strategy applied incrementally rather than as a single blocking
-- all-or-nothing constraint. Every row written through the app from now on (fresh submissions AND
-- release→corrected-resubmit updates) gets dedup_key populated immediately, so new duplicates are
-- blocked at the DB layer (defense-in-depth against a double-click race) from the moment this runs,
-- even though old ones are untouched.

alter table commcalc.daily_closing add column if not exists released_at      timestamptz;
alter table commcalc.daily_closing add column if not exists released_by     text;
alter table commcalc.daily_closing add column if not exists release_note    text;
alter table commcalc.daily_closing add column if not exists dedup_key       text;
alter table commcalc.daily_closing add column if not exists corrected_at    timestamptz;  -- audit: last release->resubmit UPDATE
alter table commcalc.daily_closing add column if not exists correction_count integer default 0;

-- Backfill dedup_key ONLY for combos that are currently a clean single row (see rationale above).
with combo_counts as (
  select org_id, coalesce(store_code,'') as store_code,
         lower(coalesce(employee_name,'')) as emp, close_date, count(*) as cnt
  from commcalc.daily_closing
  group by org_id, coalesce(store_code,''), lower(coalesce(employee_name,'')), close_date
)
update commcalc.daily_closing d
set dedup_key = d.org_id::text || '|' || coalesce(d.store_code,'') || '|' ||
                lower(coalesce(d.employee_name,'')) || '|' || d.close_date::text
from combo_counts c
where d.dedup_key is null
  and d.org_id = c.org_id
  and coalesce(d.store_code,'') = c.store_code
  and lower(coalesce(d.employee_name,'')) = c.emp
  and d.close_date = c.close_date
  and c.cnt = 1;

-- Partial unique index — only rows with a real dedup_key AND not currently released are constrained.
create unique index if not exists daily_closing_one_active_per_rep_day
  on commcalc.daily_closing (dedup_key)
  where dedup_key is not null and released_at is null;

-- ══════════════════════════════════════════════════════════════════════════════════════════════════
-- (3) Bank-deposit slip OCR (Claude vision) + configurable match target, on the EXISTING
--     commcalc.bank_deposit table (mig 107). Never blocks a deposit from saving; mismatch/unreadable/
--     unavailable are all honest, non-blocking statuses surfaced on the row.

alter table commcalc.bank_deposit add column if not exists ocr_amount       numeric;
alter table commcalc.bank_deposit add column if not exists ocr_date        text;
alter table commcalc.bank_deposit add column if not exists ocr_bank_name   text;
alter table commcalc.bank_deposit add column if not exists ocr_match       text;    -- matched|mismatch|unreadable|ocr_unavailable|pending|manual_confirmed
alter table commcalc.bank_deposit add column if not exists ocr_detail      jsonb;   -- raw model output / error (audit)
alter table commcalc.bank_deposit add column if not exists match_target    text;    -- basis THIS deposit was checked against (frozen at OCR time)
alter table commcalc.bank_deposit add column if not exists declared_amount numeric; -- the computed $ for that basis (frozen at OCR time)
alter table commcalc.bank_deposit add column if not exists manual_confirmed boolean default false;  -- degrade path when OCR is unavailable

-- Per-tenant: which basis a bank-deposit slip's OCR'd amount is checked against, + an optional
-- per-tenant OCR model override (default = a cheap vision model; NOT hard-coded into Python — see
-- closing/router.py `_deposit_config`, which falls back to the same default when this table/row is
-- absent so an un-configured tenant still gets OCR, just with the default model).
create table if not exists commcalc.closing_deposit_config (
  org_id       uuid primary key,
  match_target text not null default 'total_cash',   -- 'bill_payment_cash' | 'store_cash' | 'total_cash'
  ocr_model    text,                                   -- null = use the coded default (haiku)
  updated_at   timestamptz default now()
);

-- RLS: open_all to match every sibling commcalc config table.
alter table commcalc.closing_deposit_config enable row level security;
do $$ begin
  create policy open_all on commcalc.closing_deposit_config for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
grant all on commcalc.closing_deposit_config to anon, authenticated, service_role;

notify pgrst, 'reload schema';
select '502 complete — daily_closing release/dedup + bank_deposit OCR + closing_deposit_config ready' as status;
