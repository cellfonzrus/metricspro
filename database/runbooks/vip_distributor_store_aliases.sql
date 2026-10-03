-- =====================================================================================
-- TEACH THE ORG VOCABULARY THE DISTRIBUTOR'S OWN SPELLING OF TWO STORES
--
-- Found while building the Distributor Invoices standard filters (index §15v, owner
-- 2026-10-03 "add date range and market with standard filters for distributor invoices").
--
-- RUN THIS YOURSELF in the Supabase SQL editor. Claude does not apply SQL.
-- Every figure below was READ FROM LIVE DATA on 2026-10-03 before this was written.
--
-- ─────────────────────────────────────────────────────────────────────────────────────
-- WHAT IS WRONG
-- ─────────────────────────────────────────────────────────────────────────────────────
-- `commcalc.vip_invoices.location` is a store address in the DISTRIBUTOR's spelling. The
-- org vocabulary (storeops.stores ∪ commcalc.store_mapping ∪ commcalc.store_aliases)
-- binds 26 of the 29 spellings on this feed. Three it does not:
--
--     1 S 60th St            214 invoices   $1,236,218.17   store is B-60TH, market PA
--     1598 Mt Ephraim Ave    259 invoices   $  906,282.94   store is B-1598, market PA
--     228 N Wood Ave          11 invoices   $  229,126.70   store is <2022>, NO MARKET
--
-- The first two are pure spelling drift against an address the vocabulary already holds:
--
--     B-60TH  knows  '1 S 60TH ST, PHILADELPHIA'  and  '1 S 60TH STREET'
--     B-1598  knows  '1598 MOUNT EPHRAIM AVE'     ("Mount", not "Mt")
--
-- Neither squashes onto the distributor's string, and the leading street numbers 1 and
-- 1598 are claimed by more than one identity, so the resolver fails CLOSED — correctly.
--
-- CONSEQUENCE TODAY: picking a market on Distributor Invoices excludes those invoices.
-- The report SAYS SO (the amber "could not match the distributor's spelling" line) rather
-- than shrinking a total silently, and both stores remain reachable by picking their
-- distributor spelling in the store filter. They just do not follow the market filter.
--
-- ─────────────────────────────────────────────────────────────────────────────────────
-- WHY A store_aliases ROW IS THE FIX, AND NOT CODE
-- ─────────────────────────────────────────────────────────────────────────────────────
-- "Which store does this spelling name?" has ONE home — `core.scope.build_market_index`,
-- whose `alias_keys` is fed by exactly this table — and EVERY caller reads it: the P&L
-- store/market filter, the Balance Sheet, the store pickers, the device-cost basis, and
-- now Distributor Invoices. So two rows here fix the spelling for all of them at once.
-- Teaching the report its own fuzzy rule would be a second copy of that fact, which is
-- the patchwork the house rules forbid, and it would still leave the P&L blind.
--
-- An alias is NOT a store: `build_market_index` folds `alias_rows` into `alias_keys` only
-- — never into `stores`, never into a market's membership, never into the grant picker.
-- It is a row-matching synonym for a store_code that already exists, and nothing else.
--
-- ─────────────────────────────────────────────────────────────────────────────────────
-- PROVED BEFORE PROPOSING (backend/harness_vip_invoice_filter.py §F9/§F10)
-- ─────────────────────────────────────────────────────────────────────────────────────
-- With these two rows present, over the live index rebuilt with them:
--     '1 S 60th St'          -> B-60TH / B-1     and binds market PA
--     '1598 Mt Ephraim Ave'  -> B-1598 / B-2778  and binds market PA
--     the store picker collapses 33 options -> 31, one per physical store (index §13e)
--     spellings this feed carries that nothing binds: NONE
--
-- MOVES NO MONEY. This changes no booking rule, no rate and no amount. It changes which
-- store a spelling RESOLVES to, which is already what every report does with the other 26.
-- The store is the same store in both halves; this just stops a filter failing closed.
--
-- ─────────────────────────────────────────────────────────────────────────────────────
-- THE THIRD ONE IS NOT A SPELLING PROBLEM — IT NEEDS YOUR DECISION
-- ─────────────────────────────────────────────────────────────────────────────────────
-- '228 N Wood Ave' resolves fine (store_code <2022>, address
-- '228 N Wood Ave, Syosset, NY 11791'), but THAT STORE IS IN NO MARKET, so no market
-- filter can ever include its 11 invoices / $229,126.70.
--
-- There is a row spelled 'Cellular Services Dot net LLC (228 N Wood Ave, Syosset, New
-- York, 11791)' carrying market LI — but "Cellular Services" is a COMPANY, not a store
-- (index §13d), so that is not an answer, it is the same split identity in another place.
--
-- NO SQL IS PROPOSED FOR IT. If <2022> belongs to LI, the market assignment is yours to
-- make in Store Setup; it affects every market-filtered report, not just this one.
-- =====================================================================================

BEGIN;

-- Idempotent: commcalc.store_aliases is UNIQUE on (org_id, LOWER(TRIM(alias))), so one
-- alias string can belong to AT MOST ONE store_code and re-running this is a no-op.
INSERT INTO commcalc.store_aliases (org_id, alias, store_code)
VALUES
  ('00000000-0000-0000-0000-000000000001', '1 S 60th St',         'B-60TH'),
  ('00000000-0000-0000-0000-000000000001', '1598 Mt Ephraim Ave', 'B-1598')
ON CONFLICT DO NOTHING;

-- Verify BEFORE committing: expect exactly the two rows below, plus the 7 that already existed.
SELECT alias, store_code
  FROM commcalc.store_aliases
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
 ORDER BY alias;

COMMIT;

-- REVERT:
--   DELETE FROM commcalc.store_aliases
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND LOWER(TRIM(alias)) IN ('1 s 60th st', '1598 mt ephraim ave');
--
-- AFTER RUNNING: the market index is cached per org in the backend. It refreshes on its own,
-- or immediately on the next store/market write (core.scope.invalidate_market_index).
