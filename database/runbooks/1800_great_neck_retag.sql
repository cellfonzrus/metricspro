-- =====================================================================================
-- 1800 Great Neck Rd — retag 7 mis-keyed closings onto the store that already exists
-- Owner request 2026-10-01 ("add 1800 to the store roster"); see the finding below.
-- RUN THIS YOURSELF in the Supabase SQL editor. Claude does not apply SQL.
--
-- WHY NOT A NEW ROSTER ROW:
--   • `B-1800` is ALREADY in storeops.stores (market LI, entity 3fe5f9fd-…).
--   • storeops.store_alias ALREADY maps sales_file_spelling '1800 Great Neck rd'
--     -> that same entity.
--   • The two share staff (Leslie Martinez, Anjali Sharma) and never both close on the
--     same day. It is ONE store: 73 closings under 'B-1800' + 7 under '1800GreatNeckRd'.
--   Adding '1800GreatNeckRd' to the roster would make it a SECOND store and split one
--   store's history permanently. The rows are wrong, not the roster.
--
-- SAFETY, verified against live data before this file was written:
--   • none of the 7 dates has a B-1800 closing already  -> no same-day duplicate
--   • the 7 recomputed dedup_keys collide with none of B-1800's 73 -> no guard clash
--   • no duplicate (employee, date) pair inside the 7
--   • dedup_key is recomputed IN THE SAME UPDATE (it embeds store_code:
--     org|store_code|employee_lower|close_date), so the duplicate guard stays correct.
-- =====================================================================================

-- STEP 1 — DRY RUN. Expect exactly 7 rows. Read them before going on.
SELECT id, close_date, employee_name, store_code, store_name, dedup_key AS dedup_key_now,
       '00000000-0000-0000-0000-000000000001|B-1800|' || lower(employee_name) || '|'
         || to_char(close_date, 'YYYY-MM-DD') AS dedup_key_after
  FROM commcalc.daily_closing
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND store_code = '1800GreatNeckRd'
 ORDER BY close_date;

-- STEP 2 — the retag. Idempotent: re-running it matches nothing once step 2 has run.
-- Guarded so it can never overwrite a row that would collide with an existing B-1800 day.
UPDATE commcalc.daily_closing d
   SET store_code = 'B-1800',
       store_name = 'B-1800',
       dedup_key  = '00000000-0000-0000-0000-000000000001|B-1800|'
                    || lower(d.employee_name) || '|' || to_char(d.close_date, 'YYYY-MM-DD'),
       updated_at = now()
 WHERE d.org_id = '00000000-0000-0000-0000-000000000001'
   AND d.store_code = '1800GreatNeckRd'
   AND NOT EXISTS (
         SELECT 1 FROM commcalc.daily_closing x
          WHERE x.org_id = d.org_id
            AND x.store_code = 'B-1800'
            AND x.close_date = d.close_date
            AND lower(x.employee_name) = lower(d.employee_name));
-- Expect: UPDATE 7

-- STEP 3 — verify. Expect 0 rows left under the old code, and 80 under B-1800.
SELECT store_code, count(*) AS closings, min(close_date) AS first, max(close_date) AS last
  FROM commcalc.daily_closing
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND store_code IN ('1800GreatNeckRd', 'B-1800')
 GROUP BY store_code ORDER BY store_code;

-- STEP 4 — keep the alias so a future mis-key still resolves to this store. Already
-- present as '1800 Great Neck rd'; this adds the SPACELESS spelling the closings used.
-- Safe/idempotent: ON CONFLICT DO NOTHING, and nothing else in the org uses this value.
INSERT INTO storeops.store_alias (org_id, entity_id, alias_kind, alias_value, source)
VALUES ('00000000-0000-0000-0000-000000000001',
        '3fe5f9fd-356c-4d62-b40a-6e5d1f6fdd68',
        'sales_file_spelling', '1800GreatNeckRd', 'owner retag 2026-10-01')
ON CONFLICT DO NOTHING;

-- REVERT (step 2 only; keep the alias):
--   UPDATE commcalc.daily_closing SET store_code='1800GreatNeckRd', store_name='1800 Great Neck Rd',
--          dedup_key='00000000-0000-0000-0000-000000000001|1800GreatNeckRd|'||lower(employee_name)||'|'||to_char(close_date,'YYYY-MM-DD')
--    WHERE org_id='00000000-0000-0000-0000-000000000001' AND store_code='B-1800'
--      AND close_date IN ('2026-08-05','2026-08-19','2026-08-20','2026-08-27','2026-09-23','2026-09-28','2026-09-30')
--      AND employee_name IN ('Angelica Escobar','Leslie Martinez','Abdul Kakar','Anjali Sharma');
--   (check the 7 ids from STEP 1 first — the revert's date+name filter is broader than the 7 rows.)
