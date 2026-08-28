-- 920_storeops_sync_market_neutral.sql   (storeops schema; carrier-neutrality fix; supersedes the
-- sync_to_commcalc() body defined in 003_storeops.sql)
--
-- COMPLIANCE FIX (Part 1): 003_storeops.sql defined storeops.sync_to_commcalc() with
-- `COALESCE(NEW.market,'Boost')` when copying a new/updated StoreOps store into commcalc.store_mapping.
-- That hardcoded the string 'Boost' as a store's MARKET default — both a market/carrier confusion (a
-- store's market is not a carrier) AND a Boost leak: a Total/Luxelink (non-Boost) tenant whose store
-- rows carried a NULL market would get the literal 'Boost' written into commcalc.store_mapping.market
-- and surfaced in every market-labelled report. This migration replaces ONLY that default with a
-- carrier-neutral empty string. Everything else in the function is byte-identical to 003.
--
-- NO MONEY MATH CHANGES: market is a display/label + grouping key; the payout engine does not read it
-- as a dollar. A NULL market now falls back to '' (blank) instead of 'Boost'.
--
-- NOTE: per the SSOT blueprint this trigger is DEFINED but NOT ATTACHED (store sync flows through the
-- app-side resolver, migration 916/917), so recreating the function attaches nothing and moves no row on
-- its own. It is fixed here so the function can never re-introduce a Boost default if it is ever wired.
--
-- Runs single-line-safe in the Supabase SQL editor. REVERT: re-run 003_storeops.sql's function body.

CREATE OR REPLACE FUNCTION storeops.sync_to_commcalc()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  BEGIN
    IF TG_OP = 'DELETE' THEN
      UPDATE commcalc.store_mapping SET is_active = false WHERE store_code = OLD.store_code;
      RETURN OLD;
    END IF;
    INSERT INTO commcalc.store_mapping (store_address,location_name,store_code,market,is_active,created_at)
    VALUES (
      COALESCE(NEW.address, NEW.store_code, 'Unknown'),
      COALESCE(NEW.address, NEW.store_code, 'Unknown'),
      NEW.store_code, COALESCE(NEW.market,''),
      COALESCE(NEW.is_active,true), NOW()
    )
    ON CONFLICT (org_id, store_code) DO UPDATE SET
      store_address = COALESCE(NEW.address, EXCLUDED.store_address),
      market = COALESCE(NEW.market, EXCLUDED.market),
      is_active = COALESCE(NEW.is_active, true);
  EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'CommCalc sync failed for %: %', NEW.store_code, SQLERRM;
  END;
  RETURN NEW;
END; $$;
