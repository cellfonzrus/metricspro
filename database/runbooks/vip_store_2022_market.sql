-- Distributor invoices: give store <2022> a market so it follows the market filter
--
-- RUN THIS YOURSELF in the Supabase SQL editor. Claude does not apply SQL.
--
-- WHY: the distributor feed's spelling for this store now binds to the store (via the
-- aliases you just ran), so the STORE filter finds it. But its market is BLANK, and no
-- market contains a store with no market, so the MARKET filter cannot find it.
--
-- WHERE: the row is in commcalc.store_mapping (NOT storeops.stores, which has no row for
-- this code), and its market is the empty string '' rather than NULL — which is why a
-- "market IS NULL" update would have matched nothing.
--
-- WHICH MARKET: the address is "228 N Wood Ave, Syosset, NY 11791" — Nassau County, Long
-- Island — and 'LI' is an existing market here, so that is the proposed value. The four
-- markets in use are NJ, NYC, PA and LI. If LI is wrong, change the one literal below.
--
-- This moves no money and creates nothing. It sets one column on one row.

BEGIN;

UPDATE commcalc.store_mapping
   SET market = 'LI'
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND store_code = '<2022>'
   AND COALESCE(NULLIF(TRIM(market), ''), NULL) IS NULL;

SELECT store_code, store_address, market
  FROM commcalc.store_mapping
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND store_code = '<2022>';

COMMIT;

-- REVERT: UPDATE commcalc.store_mapping SET market = ''
--          WHERE org_id = '00000000-0000-0000-0000-000000000001' AND store_code = '<2022>';
