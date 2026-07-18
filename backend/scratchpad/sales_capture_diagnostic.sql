-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- DIAGNOSTIC (READ-ONLY) — luxelink Sales Report undercount, '957 Pennsylvania Ave', July 1 2026
-- agent/commission/sales-capture-fix · 2026-07-18 · run in Supabase SQL Editor (paste each block)
-- Nothing here writes. Relay the results back so the root-cause branch can be confirmed.
-- ════════════════════════════════════════════════════════════════════════════════════════════════

-- ── 0. luxelink's org_id (NOT the house org 0000…0001) ─────────────────────────────────────────
select org_id, name, slug
from storeops.tenants
where name ilike '%lux%' or slug ilike '%lux%';
-- → note the org_id; substitute it for :LUX below (or leave the subquery form, which auto-resolves it).


-- ── 1. THE REPRO — do trans 1624 / 1641 / 1721 exist for this store-day, in EITHER table? ───────
-- (period-spelling duality handled: matches 'July 2026' OR '2026-07'; also matches on trans_date.)
with lux as (select org_id from storeops.tenants
             where name ilike '%lux%' or slug ilike '%lux%' limit 1)
select 'raw_sales' as src, r.trans_id, r.trans_date, r.store, r.salesperson,
       r.contract_type, r.department, r.category, r.product_desc, r.ext_price, r.gp, r.voided, r.trans_type
from commcalc.raw_sales r, lux
where r.org_id = lux.org_id
  and (r.period in ('July 2026','2026-07') or r.trans_date::text like '2026-07-01%')
  and (r.store ilike '%pennsylvania%' or r.store ilike '%957%')
  and r.trans_id in ('1624','1641','1721')
union all
select 'daily_sales_feed' as src, f.trans_id, f.trans_date, f.store, f.salesperson,
       f.contract_type, f.department, f.category, f.product_desc, f.ext_price, f.gp, f.voided, f.trans_type
from commcalc.daily_sales_feed f, lux
where f.org_id = lux.org_id
  and (f.period in ('July 2026','2026-07') or f.trans_date::text like '2026-07-01%')
  and (f.store ilike '%pennsylvania%' or f.store ilike '%957%')
  and f.trans_id in ('1624','1641','1721')
order by trans_id, src;
-- INTERPRETATION:
--   • rows appear under 'raw_sales' but NOT 'daily_sales_feed'  → CONFIRMS the masking branch (my fix
--     recovers them; feed led the store-day cell and dropped the raw-only transactions).
--   • rows appear under NEITHER  → INGEST GAP (feed missed them AND no monthly raw_sales upload has them):
--     re-upload the full 78-col monthly Sales Transaction Details for luxelink July, then the union shows
--     them regardless of the code fix. (My fix does NOT create rows that aren't in a table.)
--   • rows appear under BOTH (or feed only) but contract_type is a label the classifier doesn't know
--     (see block 3) → CLASSIFICATION branch (ct-map config), not masking.


-- ── 2. THE WHOLE store-day — what each table holds for 957 Pennsylvania Ave, July 1 (all trans) ──
with lux as (select org_id from storeops.tenants
             where name ilike '%lux%' or slug ilike '%lux%' limit 1)
select src, count(*) as line_items, count(distinct trans_id) as transactions,
       round(sum(ext_price)::numeric, 2) as ext_total
from (
  select 'raw_sales' src, trans_id, ext_price from commcalc.raw_sales r, lux
   where r.org_id = lux.org_id and (r.period in ('July 2026','2026-07') or r.trans_date::text like '2026-07-01%')
     and (r.store ilike '%pennsylvania%' or r.store ilike '%957%')
  union all
  select 'daily_sales_feed' src, trans_id, ext_price from commcalc.daily_sales_feed f, lux
   where f.org_id = lux.org_id and (f.period in ('July 2026','2026-07') or f.trans_date::text like '2026-07-01%')
     and (f.store ilike '%pennsylvania%' or f.store ilike '%957%')
) t
group by src;
-- If raw_sales.transactions > daily_sales_feed.transactions for this cell → the feed is incomplete for
-- this store-day and the OLD cell-grain union hid the difference. That IS the bug the fix addresses.


-- ── 3. CLASSIFICATION check — does luxelink's Contract Type vocabulary match the classifier? ────
-- The built-in classifier counts an ACTIVATION when contract_type contains byod/upgrade OR is in the
-- Activation/Port-In set OR contains activation/port-in/add a line/new line/aal/idv. A luxelink (MA /
-- Total-Wireless language) label the set misses classifies as 'not an activation' → shows as a txn but
-- 0 activations. This lists luxelink's July contract_type values + how many lines each; eyeball for
-- labels the classifier would miss (they need a Sales Report ⚙ Classification → contract-type map entry).
with lux as (select org_id from storeops.tenants
             where name ilike '%lux%' or slug ilike '%lux%' limit 1)
select coalesce(nullif(trim(contract_type),''),'(blank)') as contract_type, count(*) as line_items,
       count(distinct trans_id) as transactions
from commcalc.raw_sales r, lux
where r.org_id = lux.org_id and r.period in ('July 2026','2026-07')
group by 1 order by line_items desc;


-- ── 4. BLAST RADIUS — how many July luxelink transactions/lines/$ are raw-only (feed-masked)? ───
-- raw_sales July transactions whose trans_id is NOT anywhere in the July feed = exactly what the OLD
-- union could drop on a feed-led store-day (what the completeness fix recovers). Broken out by store so
-- '957 Pennsylvania Ave' can be compared to the rest.
with lux as (select org_id from storeops.tenants
             where name ilike '%lux%' or slug ilike '%lux%' limit 1),
feed_tids as (
  select distinct trim(trans_id) tid from commcalc.daily_sales_feed f, lux
  where f.org_id = lux.org_id and f.period in ('July 2026','2026-07') and coalesce(trim(f.trans_id),'') <> ''
)
select r.store,
       count(*) as raw_only_lines,
       count(distinct r.trans_id) as raw_only_transactions,
       round(sum(r.ext_price)::numeric, 2) as raw_only_ext_total
from commcalc.raw_sales r, lux
where r.org_id = lux.org_id and r.period in ('July 2026','2026-07')
  and coalesce(trim(r.trans_id),'') <> ''
  and lower(coalesce(r.voided,'')) not in ('true','yes','1','voided','void')
  and coalesce(r.trans_type,'') <> 'Return'
  and trim(r.trans_id) not in (select tid from feed_tids)
group by r.store
order by raw_only_transactions desc;
-- The grand total of raw_only_transactions across stores ≈ the transactions the Sales Report / Exec MTD /
-- Daily-Targets actuals were under-counting tenant-wide for July before this fix (upper bound: a raw-only
-- transaction on a store-day the feed did NOT cover was already shown via the cell fill, so the TRUE
-- recovered set is the subset whose store-day the feed also had — block 2 shows that for the repro store).
