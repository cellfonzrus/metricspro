-- =====================================================================================
-- MERGE THE SPLIT IDENTITIES OF THE TWO NEW STORES — 1800 Great Neck Rd and 1115 Liberty Ave
-- …and attach the two dealer doors whose residual has been booking company-wide.
--
-- Owner request 2026-10-02 ("merge it", after "check those against the 2 new doors we
-- acquired, 1800 and 1115").
--
-- RUN THIS YOURSELF in the Supabase SQL editor. Claude does not apply SQL.
-- Every figure and row id below was read from live data on 2026-10-02 before this was written.
--
-- ─────────────────────────────────────────────────────────────────────────────────────
-- WHAT IS WRONG
-- ─────────────────────────────────────────────────────────────────────────────────────
-- Each new store appears TWICE in the books, with its REVENUE on one identity and its
-- COSTS on the other (September 2026, measured):
--
--     store:1115 Liberty Ave      revenue $13,741.21      net  +$10,147.30
--     store:B-1115                revenue $     0.00      net  -$16,250.98
--     store:1800 Great Neck Rd    revenue $ 8,298.63      net   +$5,409.32
--     store:B-1800                revenue $     0.00      net  -$16,165.25
--
-- So each store reports as one profitable half and one loss-making half, and neither half
-- is the store.
--
-- WHY. `account/coa.store_resolver` collapses spellings of ONE store through this chain:
--     1. exact store_mapping.store_address (case-insensitive)
--     2. store_aliases.alias -> store_code -> store_address
--     3. the raw string IS a store_code -> that row's store_address
--     4. unambiguous leading street number -> a known address
--     5. unmappable -> the cleaned raw string, kept as-is
-- Step 3 is the one that makes a CODE resolve to an ADDRESS, and it needs the mapping row
-- to carry BOTH. Neither store does:
--     • B-1800's row has store_address = 'B-1800' — a PLACEHOLDER where the address belongs,
--       so 'B-1800' resolves to itself and never reaches '1800 Great Neck Rd'.
--     • B-1115 has NO store_mapping row at all, so 'B-1115' and '1115 Liberty Ave' both
--       fall through to step 5 and become two different stores.
--
-- The source tables are NOT at fault and are NOT touched here. Each legitimately carries its
-- own spelling, and after this runbook they all resolve to one store:
--     daily_sales_feed.store      '1800 Great Neck rd' x1173   '1115 Liberty Ave' x635
--     raw_comp_report.business_address  '…Copiague, NY 11726' x366  '…Brooklyn, NY 11208' x208
--     vip_invoices / vip_invoice_lines.location, rep_commissions.store, raw_sales.store,
--     inventory_value.store       both addresses
--     daily_closing.store_code    'B-1800' x74, '1800GreatNeckRd' x7, 'B-1115' x80
--
-- PROVEN BEFORE WRITING: the real `coa.store_resolver` was run against the proposed rows
-- (read-only, no writes). Every spelling collapses from 2 canonical stores to 1, per store:
--     'B-1800' -> '1800 Great Neck Rd'        (today: 'B-1800')
--     '1800 Great Neck Rd' / '1800 Great Neck rd' / '1800GreatNeckRd' -> '1800 Great Neck Rd'
--     'B-1115' -> '1115 Liberty Ave'          (today: 'B-1115')
--     '1115 Liberty Ave' -> '1115 Liberty Ave'
--
-- ─────────────────────────────────────────────────────────────────────────────────────
-- SAFETY, each verified against live data before this file was written
-- ─────────────────────────────────────────────────────────────────────────────────────
--  • NO LEGAL-ENTITY CROSSING. commcalc.store_companies maps BOTH 1800 halves to the SAME
--    company (885412a0-b105-42c5-af91-77f043c66d27), so merging them moves no money between
--    entities. '1115 Liberty Ave' already has its company (0afb6471-d894-4069-a6e0-cf635123c1b5)
--    and the 'B-1115' half has no company row, so the merged store keeps the right one.
--  • NO NUMBER-KEY AMBIGUITY (resolver step 4). The only mapping address starting '1800' is
--    '1800 Great Neck Rd'; nothing starts '1115'. After step 1 below both 1800 rows carry the
--    same address, so the number key stays unambiguous rather than becoming a conflict.
--  • BOTH DOORS ARE UNCLAIMED. Neither 0013t00001Y15oMAAR nor 0018000000eXGzwAAG appears as a
--    salesforce_id on any store_mapping row, so step 3 cannot create an ambiguous door (an
--    ambiguous door is DROPPED by residual_subs.salesforce_store_map and would book company-wide).
--  • THE DUPLICATE 1800 ROW IS KEPT ON PURPOSE. Deleting the '1800GreatNeckRd' row would strand
--    the 7 daily_closing rows still keyed to that code (they would fall to resolver step 5 and
--    become a THIRD identity). Both rows end up carrying the same address, which is harmless:
--    the resolver maps both to one canonical store.
--  • EVERY STATEMENT BELOW IS IDEMPOTENT — re-running the file matches nothing a second time.
--
-- ─────────────────────────────────────────────────────────────────────────────────────
-- WHICH DOOR IS WHICH, and how that was established (so you can overrule it)
-- ─────────────────────────────────────────────────────────────────────────────────────
-- Both doors first appear in raw_mi in AUGUST 2026 and in no earlier period, consistent with
-- two stores acquired around then. Attribution comes from two independent sources:
--   0013t00001Y15oMAAR -> 1800 Great Neck Rd.  Its reps (Anjali Sharma, Leslie Martinez) work
--     127 shifts at B-1800 vs 31 at B-1750 and 13 at B-103; Anjali filed 55 closings at B-1800
--     (and 1 under '1800GreatNeckRd'), Leslie 19.  Residual: $3,136.41 Aug + $4,361.17 Sep.
--   0018000000eXGzwAAG -> 1115 Liberty Ave.  Nafiz filed 27 closings at B-1115 vs 2 elsewhere
--     and sells on this door; Asad Umar 15 at B-1115 vs 7 at B-2612.  Its dominant rep
--     (Mohammed Sami, 85% of the door's rows) splits between B-2612 and B-1115, and B-2612
--     ALREADY has its own mapped door — so this one is 1115 by elimination.
--     Residual: $3,390.63 Aug + $4,582.45 Sep.
-- The 1800 attribution is strong; the 1115 one rests on elimination plus the two reps who are
-- mostly at B-1115. If you know otherwise, change the salesforce_id in step 3 — nothing else
-- in this file depends on which door is which.
-- =====================================================================================


-- ─── STEP 0 — DRY RUN. Read this before running anything. ────────────────────────────
-- Expect: B-1800 with store_address 'B-1800' (the placeholder), 1800GreatNeckRd with the real
-- address, no B-1115 row at all, and both salesforce_id columns NULL.
SELECT store_code, store_address, market, city, state, salesforce_id, is_active, id
  FROM commcalc.store_mapping
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND (store_code IN ('B-1800', '1800GreatNeckRd', 'B-1115')
        OR store_address IN ('B-1800', '1800 Great Neck Rd', '1115 Liberty Ave'))
 ORDER BY store_code;


-- ─── STEP 1 — 1800: put the real address on the row that holds the placeholder ───────
-- This is the whole 1800 fix. 'B-1800' then resolves through step 3 of the chain to
-- '1800 Great Neck Rd', which is where the store's revenue already is.
UPDATE commcalc.store_mapping
   SET store_address = '1800 Great Neck Rd'
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND store_code = 'B-1800'
   AND store_address = 'B-1800';          -- idempotent: matches nothing once applied
-- Expect: UPDATE 1


-- ─── STEP 2 — 1115: create the mapping row it never had ──────────────────────────────
-- store_code + store_address together are what let resolver step 3 join 'B-1115' to the
-- address. market from storeops.stores ('LI'); city/state from the carrier's own
-- business_address ('1115 Liberty Ave Brooklyn, NY 11208').
INSERT INTO commcalc.store_mapping (org_id, store_code, store_address, market, city, state, is_active)
SELECT '00000000-0000-0000-0000-000000000001', 'B-1115', '1115 Liberty Ave', 'LI', 'Brooklyn', 'NY', true
 WHERE NOT EXISTS (
   SELECT 1 FROM commcalc.store_mapping
    WHERE org_id = '00000000-0000-0000-0000-000000000001'
      AND (store_code = 'B-1115' OR store_address = '1115 Liberty Ave'));
-- Expect: INSERT 0 1


-- ─── STEP 3 — attach the two dealer doors (this is the money step) ───────────────────
-- Until now every raw_mi row on these doors booked COMPANY-WIDE because no mapping row
-- claimed them: $6,527.04 in August and $8,943.62 in September. After this, each store's
-- own residual lands on the store. The consolidated P&L does not move either way
-- (engine._scoped sums by_store ∪ company_wide for the consolidated scope).
UPDATE commcalc.store_mapping
   SET salesforce_id = '0013t00001Y15oMAAR'                   -- 1800 Great Neck Rd
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND store_code = 'B-1800'
   AND salesforce_id IS NULL;                                  -- idempotent
-- Expect: UPDATE 1

UPDATE commcalc.store_mapping
   SET salesforce_id = '0018000000eXGzwAAG'                   -- 1115 Liberty Ave
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND store_code = 'B-1115'
   AND salesforce_id IS NULL;                                  -- idempotent
-- Expect: UPDATE 1


-- ─── STEP 4 — VERIFY ─────────────────────────────────────────────────────────────────
-- 4a. Expect 3 rows: B-1800 and 1800GreatNeckRd BOTH on '1800 Great Neck Rd' (one carrying
--     the door), and B-1115 on '1115 Liberty Ave' with its door.
SELECT store_code, store_address, market, salesforce_id
  FROM commcalc.store_mapping
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND store_address IN ('1800 Great Neck Rd', '1115 Liberty Ave')
 ORDER BY store_address, store_code;

-- 4b. Expect 0 rows — no mapping row may still carry a CODE in its address column, which is
--     the defect this runbook fixes. (Other stores with the same shape are listed for you;
--     they are pre-existing and not touched here: B-2778, '<2022>', 'Cellular Services'.)
SELECT store_code, store_address
  FROM commcalc.store_mapping
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND store_code IN ('B-1800', 'B-1115')
   AND upper(store_address) = upper(store_code);

-- 4c. Expect each door on exactly ONE row. A door on two rows is AMBIGUOUS and
--     residual_subs.salesforce_store_map drops it — the money would book company-wide again.
SELECT salesforce_id, count(*) AS rows_claiming_it
  FROM commcalc.store_mapping
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND salesforce_id IN ('0013t00001Y15oMAAR', '0018000000eXGzwAAG')
 GROUP BY salesforce_id;


-- ─── STEP 5 — RECOMPUTE, or none of this shows on the P&L ────────────────────────────
-- The P&L reads per-period SNAPSHOTS (commcalc.account_statements); nothing above changes a
-- stored row. `statement_engine.compute_and_store` PURGES a period's snapshots before writing
-- the fresh set, so the stale half-scopes ('store:B-1800', 'store:B-1115') disappear on the
-- recompute rather than lingering beside the merged store.
--
-- Note the auto-recompute sweep will NOT pick this up on its own: its staleness test is
-- "newest ingest is newer than the snapshot", and this is a config change, not an ingest.
-- Ask Claude to recompute July/August/September 2026, or POST /account/compute/{period}.
--
-- No SQL in this step.


-- =====================================================================================
-- REVERT
--   -- step 3 (detach the doors; residual returns to company-wide):
--   UPDATE commcalc.store_mapping SET salesforce_id = NULL
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND salesforce_id IN ('0013t00001Y15oMAAR', '0018000000eXGzwAAG');
--   -- step 2 (remove the 1115 row; its two spellings split again):
--   DELETE FROM commcalc.store_mapping
--    WHERE org_id = '00000000-0000-0000-0000-000000000001' AND store_code = 'B-1115';
--   -- step 1 (restore the placeholder; 1800 splits again):
--   UPDATE commcalc.store_mapping SET store_address = 'B-1800'
--    WHERE org_id = '00000000-0000-0000-0000-000000000001' AND store_code = 'B-1800';
-- A recompute is needed after reverting too, for the same reason as step 5.
--
-- SEPARATE, STILL OUTSTANDING, NOT PART OF THIS FILE
--   • database/runbooks/1800_great_neck_retag.sql — 7 daily_closing rows still keyed
--     '1800GreatNeckRd'. After THIS runbook they resolve to the right store in the P&L, but
--     the closing-side screens (DM Verify, Cash Pickup, the §48 accountability chain) group by
--     store_code, so those 7 days still show under a separate code until that file is run.
--   • storeops.stores.address for B-1800 is NULL. Cosmetic for the P&L (which resolves through
--     store_mapping) but it is why the roster shows the store with no address.
--   • commcalc.store_companies still holds a row for the address 'B-1800'. Harmless — it points
--     at the SAME company as '1800 Great Neck Rd' — and dead once nothing resolves to 'B-1800'.
--   • B-1115 is recorded in market 'LI' while its carrier address is in Brooklyn. That is your
--     categorisation to keep or change; this file preserves it rather than guessing.
-- =====================================================================================
