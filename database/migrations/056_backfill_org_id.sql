-- 056_backfill_org_id.sql — stamp every legacy NULL org_id to the house org.
--
-- WHY: the multi-tenant leak fixes add `.eq("org_id", org_id)` to reads. That is a NO-OP only if every
-- row already carries org_id. Some legacy rows (e.g. older raw_mi) were inserted with org_id NULL, so
-- the filter would DROP them and change results (caught: sales-analyzer churn changed when raw_mi was
-- scoped). Since there is exactly one tenant today, ALL existing data IS the house org — so backfilling
-- NULL → house is correct and makes the leak filters safe no-ops.
--
-- Idempotent (only touches NULLs). Loops over EVERY table that has an org_id column in the app schemas,
-- so it covers all org-scoped tables without enumerating them.

DO $$
DECLARE r RECORD; n BIGINT; total BIGINT := 0;
BEGIN
  FOR r IN
    SELECT table_schema, table_name
    FROM information_schema.columns
    WHERE column_name = 'org_id'
      AND table_schema IN ('commcalc', 'storeops', 'account', 'public')
    ORDER BY table_schema, table_name
  LOOP
    EXECUTE format('UPDATE %I.%I SET org_id = %L WHERE org_id IS NULL',
                   r.table_schema, r.table_name, '00000000-0000-0000-0000-000000000001');
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n > 0 THEN
      RAISE NOTICE 'backfilled % rows in %.%', n, r.table_schema, r.table_name;
      total := total + n;
    END IF;
  END LOOP;
  RAISE NOTICE 'Migration 056 complete — backfilled % NULL org_id rows total', total;
END $$;

SELECT 'Migration 056 complete — NULL org_id backfilled to house org' AS status;
