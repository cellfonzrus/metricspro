-- 103_closing_tender_recon.sql
-- Daily Closing: (1) per-tender capture on the closing sheet to match the X-report's 6 tender types,
-- (2) a 3-try close-attempt log (direction-only prompts, auto-accept the 3rd try, management review).
-- Idempotent / additive — safe to re-run.

-- ── (1) Six tender columns on the rep-entered closing row (mirror the X-report tender types) ──────
alter table commcalc.daily_closing
  add column if not exists t_cash       numeric,   -- Cash
  add column if not exists t_credit     numeric,   -- Credit (internal / POS-integrated card)
  add column if not exists t_ext_cc     numeric,   -- External Credit Card (separate terminal)
  add column if not exists t_gift       numeric,   -- Gift Card
  add column if not exists t_store_acct numeric,   -- Store Account
  add column if not exists t_zelle      numeric,   -- Zelle / CashApp
  -- 3-try close-flow bookkeeping (surfaced on the management-review page, hidden from DMs)
  add column if not exists attempts      integer default 1,
  add column if not exists auto_accepted boolean default false,  -- accepted on the 3rd try while still mismatched
  add column if not exists mgmt_flag     boolean default false;   -- needs management review

-- ── (2) One row per submission ATTEMPT (the rep may try up to 3 times; every try is logged) ──────
create table if not exists commcalc.closing_attempt (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null,
  close_date     date not null,
  period         text,
  store_code     text,
  store_address  text,
  sfid           text,
  employee_name  text,
  attempt_no     integer not null,
  -- what the rep entered on this try (management review sees the amounts; the rep never does)
  entered_cash   numeric,
  entered_credit numeric,
  t_cash numeric, t_credit numeric, t_ext_cc numeric, t_gift numeric, t_store_acct numeric, t_zelle numeric,
  -- the B2B target this try was checked against + the resulting direction (no amount shown to the rep)
  b2b_cash       numeric,
  b2b_credit     numeric,
  cash_dir       text,   -- 'short' | 'over' | 'ok'
  credit_dir     text,   -- 'over'  | 'under'| 'ok'
  blocked        boolean default false,   -- this try was rejected (rep must recount) — attempts 1..2
  accepted       boolean default false,   -- this try was accepted (matched, or the 3rd try)
  auto_accepted  boolean default false,   -- accepted on the 3rd try while still mismatched
  created_at     timestamptz default now()
);
create index if not exists closing_attempt_lookup
  on commcalc.closing_attempt (org_id, close_date, store_code, employee_name);
create index if not exists closing_attempt_period
  on commcalc.closing_attempt (org_id, period);
