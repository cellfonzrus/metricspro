-- 1099_connector_registry_supplier_invoice.sql
-- THE SUPPLIER-INVOICE CONNECTOR GETS ITS DOOR, IN THE SAME CHANGE THAT MAKES IT DISPATCHABLE.
--
-- Owner, 2026-10-27, asked for supplier invoices to be pulled and reconciled against the sales
-- reports. The login row he had already created sat on `processor = 'other'`, which no dispatcher
-- serves, so Run now stamped "scraper for 'other' not wired yet" and stopped.
--
-- WHY THIS FILE EXISTS AT ALL, and why it is not an afterthought. Mig 1084 fixed a class: "the
-- platform can dispatch this feed" and "a tenant can find this feed" had been two unrelated facts,
-- so two feeds shipped dispatchable and doorless. commcalc.connector_registry (mig 1014) is what
-- fills the processor picker on the portal-login form, what gives the Connectors page its cards, and
-- what `_connector_label` reads for a connector's name — with no row, the kind cannot be CHOSEN.
-- `harness_connector_scope_lock.py` §I now fails the build when a `_SOURCE_SCRAPERS` key is not a
-- registry key or alias and is not excused in writing, which is why this row is in the same PR as the
-- dispatcher rather than six weeks later. The ratchet did its job on the first feed after it.
--
-- WHY A THIRD MIGRATION rather than an edit to 1014 or 1084. Both are applied. The mirror in
-- `connector_registry.HOUSE_CONNECTORS` declares `MIGRATIONS` as a LIST in apply order and the lock
-- concatenates their seeds, so a new house connector APPENDS a file and never edits an applied one.
--
-- NO HOST, deliberately. This is a connector KIND, not a supplier: the portal address is a value on
-- the tenant's own `commcalc.data_source` row. A host here would be the single vendor spelling this
-- registry exists to keep out of the codebase (RULE TWO). `ftp` and the two kinds mig 1084 added
-- carry none for the same reason. applies_to_pos / applies_to_carrier are both '{}' (ANY) because any
-- tenant may be invoiced by a supplier, whatever POS or carrier it declared.
--
-- DUPLICATE CHECK (build gate — index §12a.2a, §16/§17 and the §COMMCALC-07 register were searched):
--   · commcalc.connector_registry (mig 1014 + 1084) — THE registry of which connectors exist and what
--     they apply to. EXTENDED with one row. Not forked, no new table, no new column.
--   · the 'vip' registry row (a distributor portal, host www.vipwireless.com) — a VENDOR row for one
--     distributor's portal, scoped to one carrier, whose feed writes the distributor-only invoice
--     tables. NOT reused: this is the supplier-neutral KIND, any scope, no host, landing in
--     commcalc.supplier_invoice_line (mig 1098). The relationship between the two is stated in
--     1098's header and in the index, not left for a reader to notice.
--   · commcalc.connector_instances / report_definitions — what the Connectors page lists per org.
--     Deliberately NOT seeded: an instance is a tenant's own declared login, not a house fact, and
--     seeding one would invent a connection nobody configured.
--   · commcalc.report_pull_map (mig 207) — how a consumer finds a pulled feed's table. The new
--     feed's house row is in mig 1098, with its own report_key. Different question.
--   · commcalc.connector_route_policy (mig 998) — whether a pull route is OPEN. No rows reads as
--     OPEN, so nothing is needed and nothing is added: this migration does not open a route.
--
-- NOT MONEY-TOUCHING, AND IT TURNS NOTHING ON. One row in a registry of names and scopes. No rate,
-- plan, payout or calculation input is read or written, no schedule is registered, no credential is
-- touched, and no feed starts pulling: a pull still needs a tenant to create its own login row and
-- authenticate once. What changes is that the kind can be CHOSEN — and that the login row already
-- sitting on 'other' can be moved onto a processor the platform actually serves.
--
-- REVERT: DELETE FROM commcalc.connector_registry
--           WHERE org_id = '00000000-0000-0000-0000-000000000001'
--             AND key = 'supplier_invoice';
--         NOTIFY pgrst, 'reload schema';

INSERT INTO commcalc.connector_registry (org_id, key, aliases, label, host, kind, applies_to_pos, applies_to_carrier,
  defined_by, sort_order, notes) VALUES
  ('00000000-0000-0000-0000-000000000001', 'supplier_invoice', '{}', 'Supplier invoices (billing portal)', NULL, 'portal', '{}', '{}', 'house', 150, 'A supplier''s own portal publishing the invoices it has billed us, pulled so every charge can be checked against the sale that should have collected for it (index §COMMCALC-38). The address, the report list, the date filters and the column vocabulary are declared per tenant on its own login row. Supplier-neutral: the distributor-only invoice feed (''vip'') is its sibling, and index §COMMCALC-38 states why they are not yet one.')
-- DO NOTHING, not DO UPDATE, for the reason mig 1014's own seed states: a re-run must never overwrite
-- a scope an operator has since narrowed or widened. Still idempotent — a second run inserts nothing.
ON CONFLICT (org_id, key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT '1099 complete — the supplier-invoice connector kind is now offerable; nothing is turned on and no feed pulls until a tenant declares its own login' AS status;
