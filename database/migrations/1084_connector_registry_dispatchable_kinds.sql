-- 1084_connector_registry_dispatchable_kinds.sql
-- A FEED THE PLATFORM DISPATCHES BUT THE REGISTRY DOES NOT NAME HAS NO DOOR.
--
-- Owner report 2026-10-15, verbatim: "as a new user how do i know where to integrate the teamportal
-- to get the pos data to compare to teh daily closing".
--
-- He could not, and the answer was not a missing screen. Two connector KINDS are dispatchable —
-- `router._SOURCE_SCRAPERS` has a handler for each, so a login row with that processor pulls on the
-- mig-956 cron — and neither had a row in commcalc.connector_registry (mig 1014). The registry is
-- what fills the processor picker on the portal-login form, what gives the Connectors page its
-- cards, and what `_connector_label` reads for a connector's name. With no row:
--   · the picker does not OFFER the kind (its options are the registry's `kind='portal'` rows), so
--     an operator would have to type the internal slug, which nothing on screen tells them;
--   · the Connectors page — the screen whose whole job is "which feed supplies each data set" —
--     shows no card at all; and
--   · every surface naming the connector falls through to connector_registry.NEUTRAL_LABEL.
-- The feed ran correctly and was unreachable. Measured against production this date: 11 registry
-- rows, 9 dispatchable keys, 2 of them absent.
--
-- THE CLASS (CLAUDE.md "A fix is a DESIGN fix"). The instance is the POS-report feed, shipped three
-- days ago. The class is that "the platform can run this feed" and "a tenant can find this feed"
-- were two unrelated facts, so a feed could ship dispatchable and doorless and nothing said so.
-- Looking for the class found the sibling immediately: `supply_vendor` (index §36 / §51) had been
-- dispatchable with no registry row since it shipped, so no tenant could pick it either. Both are
-- registered here. The ratchet is in code, not in this file:
-- harness_connector_scope_lock.py §I now fails the build when a `_SOURCE_SCRAPERS` key is not a
-- registry key or alias and is not excused in writing, so the NEXT feed cannot ship without a door.
--
-- WHY THIS IS A SECOND MIGRATION rather than an edit to 1014. 1014 is applied (verified this date,
-- 11 rows). Its seed is mirrored byte-for-byte in `connector_registry.HOUSE_CONNECTORS` and the lock
-- compared the mirror against that ONE file, so adding a house connector meant editing an applied
-- migration. That is why leaving a feed out of the registry was the path of least resistance. The
-- mirror now declares `MIGRATIONS` as a LIST and the lock concatenates their seeds in order, so this
-- file appends and the next one will too.
--
-- NO HOST ON EITHER ROW, deliberately. These are connector KINDS, not vendors: the portal address is
-- a value on the tenant's own `commcalc.data_source` row. A host here would be the single vendor
-- spelling this registry exists to keep out of the codebase (RULE TWO). `ftp` carries none for the
-- same reason. applies_to_pos / applies_to_carrier are both '{}' (ANY) because any tenant may run a
-- back-office reports portal or order from a supply vendor, whatever POS or carrier it declared.
--
-- DUPLICATE CHECK (build gate — index §12a.2, §16/§17 and the §COMMCALC-07 register were searched):
--   · commcalc.connector_registry (mig 1014) — THE registry of which connectors exist and what they
--     apply to. EXTENDED with two rows. Not forked, no new table, no new column.
--   · commcalc.connector_instances / report_definitions — what the Connectors page lists per org.
--     Deliberately NOT seeded here: an instance is a tenant's own declared login, not a house fact,
--     and seeding one would invent a connection nobody configured. The registry row is what makes
--     the kind OFFERABLE; the operator still creates their own login row.
--   · commcalc.report_pull_map (mig 207) — how a consumer finds a pulled feed's table. Already
--     carries the POS-report feed's house row (mig 1078). Different question, untouched.
--   · commcalc.connector_route_policy (mig 998) — whether a pull route is OPEN. No rows reads as
--     OPEN, so nothing is needed and nothing is added: this migration does not open a route.
--
-- NOT MONEY-TOUCHING, AND IT TURNS NOTHING ON. Two rows in a registry of names and scopes. No rate,
-- plan, payout or calculation input is read or written, no schedule is registered, no credential is
-- touched, and no feed starts pulling: a pull still needs a tenant to create its own login row and
-- authenticate once. What changes is that the kind can be CHOSEN.
--
-- REVERT: DELETE FROM commcalc.connector_registry
--           WHERE org_id = '00000000-0000-0000-0000-000000000001'
--             AND key IN ('pos_report', 'supply_vendor');
--         NOTIFY pgrst, 'reload schema';

INSERT INTO commcalc.connector_registry (org_id, key, aliases, label, host, kind, applies_to_pos, applies_to_carrier,
  defined_by, sort_order, notes) VALUES
  ('00000000-0000-0000-0000-000000000001', 'pos_report', '{}', 'Back-office POS reports', NULL, 'portal', '{}', '{}', 'house', 90, 'A back-office POS reports portal — the daily report of what each worker sold, per declared category, for checking against what the reps declared at closing (index §COMMCALC-07). The address, the report list and the category mapping are declared per tenant on its own login row.'),
  ('00000000-0000-0000-0000-000000000001', 'supply_vendor', '{}', 'Supply vendor portal (ordering)', NULL, 'portal', '{}', '{}', 'house', 140, 'A supply vendor''s own portal: the catalogue crawl and the draft order (index §36 / §51). Registered 2026-10-15 as the sibling of the POS-report kind — it had been dispatchable with no registry row since it shipped, so no tenant could pick it either.')
-- DO NOTHING, not DO UPDATE, for the reason mig 1014's own seed states: a re-run must never
-- overwrite a scope an operator has since narrowed or widened. Still idempotent — a second run
-- inserts nothing and changes nothing.
ON CONFLICT (org_id, key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT '1084 complete — the two dispatchable connector kinds are now offerable; nothing is turned on and no feed pulls until a tenant declares its own login' AS status;
