-- 024_expenses_backfill.sql — backfill commcalc.store_expenses from the legacy public table.
--
-- The Store Expenses page (and GP report) read commcalc.store_expenses, but expenses entered
-- before/after the one-time 005 copy still live in public.commcalc_store_expenses → the page shows
-- nothing. This re-copies any rows the new table is missing. Idempotent + guarded (no-op if the
-- old table doesn't exist). Period strings are preserved as-is.
--
-- FIRST, run this DIAGNOSTIC to see where your data is + what period format it uses:
--   SELECT 'new' src, period, count(*), sum(amount) FROM commcalc.store_expenses GROUP BY period
--   UNION ALL
--   SELECT 'old' src, period, count(*), sum(amount) FROM public.commcalc_store_expenses GROUP BY period;
-- If the OLD rows use a different period format than "Month YYYY" (e.g. "2026-06"), tell me and
-- I'll add a normalization step; otherwise this backfill makes them show immediately.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'public' AND table_name = 'commcalc_store_expenses') THEN
    INSERT INTO commcalc.store_expenses
      (id, org_id, period, store_code, expense_name, expense_type, amount, created_at)
    SELECT gen_random_uuid(), o.org, o.period, o.store_code, o.expense_name,
           o.expense_type, COALESCE(o.amount, 0), NOW()
    FROM public.commcalc_store_expenses o
    WHERE COALESCE(o.amount, 0) <> 0
      AND o.store_code IS NOT NULL AND o.expense_name IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM commcalc.store_expenses n
        WHERE n.org_id = o.org AND n.period = o.period
          AND n.store_code = o.store_code AND n.expense_name = o.expense_name);
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 024 complete — store_expenses backfilled from legacy table (if present)' AS status;
