-- 109_closing_rep_expense.sql
-- Daily-closing rep expense: an expense the SALES REP incurred during the day (e.g. supplies, cash
-- out of drawer for a business need). The rep enters the amount + a REQUIRED description; the DM
-- approves it with a single checkbox on the verify screen. Idempotent / additive.

alter table commcalc.daily_closing
  add column if not exists expense_amount      numeric,
  add column if not exists expense_description text,
  add column if not exists expense_approved    boolean default false,
  add column if not exists expense_approved_by  text,
  add column if not exists expense_approved_at  timestamptz;

notify pgrst, 'reload schema';
select '109 complete — commcalc.daily_closing rep-expense columns ready' as status;
