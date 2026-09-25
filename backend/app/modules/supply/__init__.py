"""Supply Ordering — vendor price compare, cart optimizer, assisted order + confirmation capture (index §36).

Modules: pricing_core (THE pure pricing logic, shared with tools/vendor_price_compare), catalog_scrape (THE
read-only catalog reader, shared with the kit), ordering_logic (pure decisions), store (DB), portal (browser),
router (/api/v1/supply). Keep this file import-free: commcalc/router imports supply.portal at load time.
"""
