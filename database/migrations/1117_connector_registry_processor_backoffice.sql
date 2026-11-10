-- 1117_connector_registry_processor_backoffice.sql
-- THE PER-STORE BACK-OFFICE CONNECTOR GETS ITS DOOR, IN THE SAME CHANGE THAT MAKES IT DISPATCHABLE.
--
-- Index §COMMCALC-67. Additive, idempotent, ONE ROW. Not money-touching and it turns nothing on.
--
-- WHY THIS FILE EXISTS. `commcalc.connector_registry` (mig 1014) is what fills the processor picker on
-- the portal-login form, what gives the Connectors page its cards, and what `_connector_label` reads
-- for a connector's name. With no row the kind cannot be CHOSEN, which is how two feeds once shipped
-- dispatchable and doorless (mig 1084's own header). `harness_connector_scope_lock.py` §I now fails
-- the build when a `_SOURCE_SCRAPERS` key is neither a registry key/alias nor excused in writing —
-- which is why this row ships in the same PR as its dispatcher.
--
-- WHY A NEW FILE rather than an edit to 1014 / 1084 / 1099: all three are applied, and the code mirror
-- `connector_registry.HOUSE_CONNECTORS` declares `MIGRATIONS` as a LIST in apply order whose seeds the
-- lock concatenates. A new house connector APPENDS a file and never edits an applied one.
--
-- NO HOST, deliberately, and ANY scope. This is a connector KIND — *a payment processor's back office
-- that issues one login per store* — not a vendor: the portal address is a value on the tenant's own
-- `commcalc.data_source` row, so a host here would be the single vendor spelling this registry exists
-- to keep out of the codebase (RULE TWO). applies_to_pos / applies_to_carrier are both '{}' (ANY)
-- because any tenant may have a processor that works this way, whatever POS or carrier it declared.
-- That is what "universal" means here; which tenant it is LIVE for is decided by which tenant creates
-- logins and enables them, not by a scope narrowed in a migration.
--
-- DUPLICATE CHECK (build gate): the existing `epay` row is the same processor's COMPANY-WIDE owner
-- portal — one login for the whole tenant, a different report family, landing in different tables, and
-- scoped to one carrier in mig 1014. It is not reused and it is not forked: this row is the per-store
-- back-office KIND, any scope, no host. The relationship between the two is stated here and in the
-- index rather than left for a reader to notice. `connector_instances` / `report_definitions` are
-- deliberately NOT seeded — an instance is a tenant's own declared login, and seeding one would invent
-- a connection nobody configured. `connector_route_policy` (mig 998) is untouched: no rows reads as
-- OPEN, and this migration does not open a route.
--
-- REVERT: DELETE FROM commcalc.connector_registry
--           WHERE org_id = '00000000-0000-0000-0000-000000000001'
--             AND key = 'processor_backoffice';
--         NOTIFY pgrst, 'reload schema';

INSERT INTO commcalc.connector_registry (org_id, key, aliases, label, host, kind, applies_to_pos, applies_to_carrier,
  defined_by, sort_order, notes) VALUES
  ('00000000-0000-0000-0000-000000000001', 'processor_backoffice', '{}', 'Payment processor back office (per-store logins)', NULL, 'portal', '{}', '{}', 'house', 160, 'A payment processor''s back office that issues ONE LOGIN PER STORE, pulled per store for the processor reconciliation (index §COMMCALC-67). The address, the report list, each report''s filters and landing target, the login''s own store and the date its history starts are declared per tenant on its own login row. Sibling of the company-wide owner-portal kind (''epay''), which is one login for the whole tenant and a different report family.')
-- DO NOTHING, not DO UPDATE, for the reason mig 1014's own seed states: a re-run must never overwrite
-- a scope an operator has since narrowed or widened. Still idempotent — a second run inserts nothing.
ON CONFLICT (org_id, key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT '1117 complete — the per-store back-office connector kind is now offerable; nothing is turned on and no feed pulls until a tenant declares its own logins' AS status;
