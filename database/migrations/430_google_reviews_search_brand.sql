-- 430 — google_review_config.search_brand: resolve the BUSINESS at an address, not the address itself.
--
-- WHY (verified live against Google, 2026-08-10, with the tenant's own key):
--
--   textQuery "104-08 Lefferts Blvd, South Richmond Hill, NY 11419"
--     -> displayName "104-08 Lefferts Blvd"      rating: none   reviews: none      (a POSTAL ADDRESS)
--   textQuery "wireless store 104-08 Lefferts Blvd, Richmond Hill, NY"
--     -> displayName "Total Wireless"            rating: 4.3    reviews: 6         (the BUSINESS)
--
-- `resolve_place_for_store` searched on the store's bare address, so Google returned the street
-- address — a place with no rating and no reviews. Every store would have resolved "successfully" and
-- produced nothing to report. This was invisible behind the earlier 403 (the key was referrer-locked),
-- so fixing the key alone would have swapped one silent empty result for another.
--
-- Notably, the brand word does not have to be RIGHT: searching "Boost Mobile <address>" also returned
-- "Total Wireless", because Google matches the business AT that address. So any business-ish token
-- flips the query from postal-address to storefront — which is why one per-tenant string is enough and
-- a per-store name column is not required.
--
-- Per-tenant, not hard-coded: this is a multi-tenant platform and "wireless store" is only right for a
-- phone dealer (SAP-configurable rule, AGENT_CONTRACT §3). NULL keeps today's address-only behaviour.

ALTER TABLE storeops.google_review_config
  ADD COLUMN IF NOT EXISTS search_brand text;

COMMENT ON COLUMN storeops.google_review_config.search_brand IS
  'Business token prepended to the Places text search, e.g. "wireless store" or "Boost Mobile". Without '
  'it a bare address resolves to the POSTAL ADDRESS (no rating, no reviews) instead of the storefront. '
  'Does not need to match the store brand exactly — Google matches the business at that address.';

-- Seed the two tenants that are wireless dealers. Deliberately NOT a column DEFAULT: a future tenant in
-- another industry must state its own token rather than inherit a phone-shop assumption.
UPDATE storeops.google_review_config
   SET search_brand = 'wireless store'
 WHERE search_brand IS NULL;
