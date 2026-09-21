-- 1014_connector_registry.sql — THE CONNECTOR REGISTRY: which connectors EXIST on the platform, and what
-- POS / carrier each one APPLIES TO — so a vendor-specific connector is offered to, and its health and
-- errors shown to, ONLY the tenants it applies to. Band 1000+ (platform). Additive + idempotent (safe to
-- re-run). NOT money-touching: nothing here reads or writes a rate, plan, tier, payout or any calculation
-- input — it decides WHICH connectors a tenant is shown.
--
-- OWNER (2026-09-21, a Verizon tenant whose declared POS is RQ), verbatim, on the Inventory Values page's
-- chip "🔌 RQ portal connection / last: error / The b2bsoft (wsreports.b2bsoft.com) portal client is not
-- reverse-engineered yet. Provide the b2bsoft login so the Inventory Aging report flow can be captured.":
--   "it says rq connection but refers to b2b reports"
--
-- THE CLASS (CLAUDE.md "A fix is a DESIGN fix"): connectors (portal sweeps, data-source logins, mailbox /
-- FTP pulls) had NO POS / carrier scope in any registry, so the ONE portal-sweep connector that exists — a
-- b2bsoft one — was offered to every tenant, labelled with whatever POS the tenant declared, and its stub
-- error (spelling the vendor) reached the page as the connector's last status. Report kinds solved the
-- same class in mig 1010 (`applies_to_pos` / `applies_to_carrier`, visible = applies ∩ declaration AND
-- defined, ONE visibility function on every surface, a build-failing lock). This table gives connectors
-- the SAME shape and the code REUSES that predicate (report_kinds.visible_kinds / carrier-scope
-- .reportKindsVisible with a `connector:` cap namespace) rather than a sibling.
--
-- DUPLICATE CHECK (build gate — what was searched and why none of it is this table):
--   · commcalc.connector_instances (039)  — a TENANT's rows: "this tenant has this vendor portal" (creds
--                                           table, schedule, sweep_kind). Instances, not definitions; no
--                                           house-default-with-tenant-override shape; no POS scope.
--   · commcalc.report_definitions (039/291) — the REPORTS a connector provides, carrier-scoped per row
--                                           (carrier_id). A report's scope, not the connector's.
--   · commcalc.data_source (083)           — portal LOGIN rows (`processor` = the connector slug).
--   · commcalc.connector_route_policy (998) — per (org, connector, ROUTE): is this ingest route open. A
--                                           policy on a connector, keyed on the same slug; it presumes
--                                           the connector applies to the tenant and says nothing about
--                                           whether it does.
--   · commcalc.report_kind (1010)          — report KINDS with applies_to_pos / applies_to_carrier — the
--                                           mechanism this table copies the SHAPE of (and whose
--                                           visibility function the code calls), one axis over.
-- None answers "which connectors exist, what POS / carrier does each apply to, who defined it". This does.
--
-- KEY VOCABULARY: `key` / `aliases` are the connector SLUGS the platform already dispatches on —
-- data_source.processor (router._SOURCE_SCRAPERS), connector_instances.sweep_kind (_sweep_registry),
-- connector_route_policy.connector, b2b_sweep_config.connector (998), the *_sweep_config tables through
-- router._CONNECTOR_SLUG_SOURCE. `aliases` carries the second spelling the dispatchers accept ('b2b' for
-- 'b2bsoft', 'total_access' for 'vidapay') so ONE row answers for both — never two rows that drift.
--
-- SCOPE VOCABULARY: `applies_to_pos` = codes of the pos_system term / pos_profile.pos_key (the same codes
-- report_kind.applies_to_pos uses); `applies_to_carrier` = commcalc.carrier.code. {} = any.
--
-- RULE TWO: vendor names live ONLY in the seeded rows below (and in the byte-equal code mirror
-- backend/app/modules/commcalc/connector_registry.HOUSE_CONNECTORS, which the harness parses this block
-- back against). No code path names a vendor to decide anything.
--
-- MULTI-TENANT: org_id NOT NULL + index. A HOUSE-org row is the platform default inherited by every
-- tenant; a tenant row for the same `key` overrides it — the mig-207 / 998 / 1010 inheritance shape.
--
-- DEGRADES: until this runs, connector_registry.load_registry() falls back to the code mirror with the
-- SAME rule and every payload says registry_ready:false — exactly the posture of mig 1010.
--
-- WHAT EACH SEEDED SCOPE FOLLOWS (so no tenant's view narrows by surprise):
--   b2bsoft   → applies_to_pos {b2bsoft}  — the POS whose reports portal it is (the owner's defect)
--   vidapay   → applies_to_carrier {total} — the master-agent portal of that carrier family
--   epay, vip → applies_to_carrier {boost} — the same scope the Upload page's tiles already carried and
--                                            the mig-1010 rows of their reports (payment_detail / mi_report
--                                            / comp_report / vip_workbook / asset_ledger) already declare
--   dlar      → any — its report kinds (dlar_rep / dlar_store, mig 1010) are any-scope; narrowing it is
--               one UPDATE on this row, never code
--   the three merchant card portals, the mailbox, the FTP drop, the closing sheet → any
--
-- REVERT:
--   -- to widen one connector for everyone: UPDATE commcalc.connector_registry SET applies_to_pos = '{}'
--   --   WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = '<key>';
--   -- to widen for ONE tenant only: INSERT a row with that tenant's org_id and the same key
--   -- (or the super-admin cap override `connector:<key>` = show, the existing ui_label_override 'cap' scope);
--   -- to undo this migration entirely: DROP TABLE IF EXISTS commcalc.connector_registry;
--   --   NOTIFY pgrst, 'reload schema';

CREATE TABLE IF NOT EXISTS commcalc.connector_registry (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  key                  TEXT NOT NULL,                       -- the connector slug the platform dispatches on
  aliases              TEXT[] NOT NULL DEFAULT '{}',        -- other slugs the dispatchers accept for the SAME connector
  label                TEXT NOT NULL,                       -- how copy names it (error / status strings dereference this)
  host                 TEXT,                                -- the portal host, for copy; NULL for a non-portal connector
  kind                 TEXT NOT NULL DEFAULT 'portal' CHECK (kind IN ('portal','mailbox','ftp','google_sa')),
  applies_to_pos       TEXT[] NOT NULL DEFAULT '{}',        -- POS codes (pos_system term / pos_profile.pos_key); {} = any
  applies_to_carrier   TEXT[] NOT NULL DEFAULT '{}',        -- carrier codes (commcalc.carrier.code); {} = any
  defined_by           TEXT NOT NULL DEFAULT 'house' CHECK (defined_by IN ('house','tenant')),
  defined_by_org       UUID,
  sort_order           INT NOT NULL DEFAULT 100,
  is_active            BOOLEAN NOT NULL DEFAULT true,
  notes                TEXT,
  updated_at           TIMESTAMPTZ DEFAULT NOW(),
  created_at           TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, key)
);
CREATE INDEX IF NOT EXISTS connector_registry_org ON commcalc.connector_registry (org_id);
COMMENT ON TABLE commcalc.connector_registry IS
  'THE CONNECTOR REGISTRY: every connector the platform has (portal sweeps, data-source logins, mailbox / FTP pulls), keyed on the slug the dispatchers already use, with what it APPLIES TO (POS / carrier codes; {} = any), who defined it and the label / host its copy dereferences. House org = defaults; a tenant row overrides per key. Visible to a tenant = applies-to ∩ declaration ≠ ∅ AND defined — the SAME predicate as commcalc.report_kind (report_kinds.visible_kinds / carrier-scope.reportKindsVisible, cap namespace connector:<key>), run by connector_registry.py on every connector surface.';
COMMENT ON COLUMN commcalc.connector_registry.aliases IS
  'Other slugs the platform dispatches the SAME connector under (router._SOURCE_SCRAPERS, sweep_kind, connector_route_policy.connector). One row answers for all of them.';

ALTER TABLE commcalc.connector_registry ENABLE ROW LEVEL SECURITY;
DO $p$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='connector_registry' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.connector_registry FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $p$;
GRANT ALL ON commcalc.connector_registry TO anon, authenticated, service_role;

-- ── THE HOUSE SEED — generated from backend/app/modules/commcalc/connector_registry.HOUSE_CONNECTORS
--    (the code mirror); harness_connector_scope_lock.py §A parses this block back and requires it EQUAL
--    to the mirror. Vendor names, hosts and POS / carrier CODES appear here as DATA only (RULE TWO).
--    ON CONFLICT DO NOTHING so a re-run never overwrites a scope an operator has since widened.
INSERT INTO commcalc.connector_registry (org_id, key, aliases, label, host, kind, applies_to_pos, applies_to_carrier,
  defined_by, sort_order, notes) VALUES
  ('00000000-0000-0000-0000-000000000001', 'b2bsoft', '{"b2b"}', 'B2B Soft wsreports', 'wsreports.b2bsoft.com', 'portal', '{"b2bsoft"}', '{}', 'house', 10, 'The POS reports portal (daily Sales Transaction Details, Inventory Aging). Applies to the POS whose portal it is; its pull route is closed by mig 998.'),
  ('00000000-0000-0000-0000-000000000001', 'vidapay', '{"total_access"}', 'VidaPay master-agent portal', 'www.vidapaycrm.com', 'portal', '{}', '{"total"}', 'house', 20, 'The master-agent (MA) portal family; total_access is the same portal under its other name.'),
  ('00000000-0000-0000-0000-000000000001', 'epay', '{}', 'ePay Owner Portal', 'ownerportal.epayworldwide.com', 'portal', '{}', '{"boost"}', 'house', 30, 'MI & ATU, comprehensive comp, payment detail — the same carrier scope its report kinds carry in mig 1010.'),
  ('00000000-0000-0000-0000-000000000001', 'dlar', '{}', 'Carrier KPI portal (DLAR)', 'boostelevatego.com', 'portal', '{}', '{}', 'house', 40, 'Store + rep KPI reports. Any-scope because its report kinds (dlar_rep / dlar_store) are any-scope; narrow it here, never in code.'),
  ('00000000-0000-0000-0000-000000000001', 'vip', '{}', 'Distributor portal', 'www.vipwireless.com', 'portal', '{}', '{"boost"}', 'house', 50, 'Distributor invoices, PayGo, asset ledger — the same carrier scope its report kinds carry in mig 1010.'),
  ('00000000-0000-0000-0000-000000000001', 'payanywhere', '{}', 'PayAnywhere — Payments Hub (external credit card)', 'www.paymentshub.com', 'portal', '{}', '{}', 'house', 60, 'Merchant card portal (mig 955). Any tenant may run a standalone card terminal.'),
  ('00000000-0000-0000-0000-000000000001', 'transfirst', '{}', 'TransFirst TransLink (POS merchant provider)', 'translink.transfirst.com', 'portal', '{}', '{}', 'house', 70, 'Merchant card portal (mig 955).'),
  ('00000000-0000-0000-0000-000000000001', 'businesstrack', '{}', 'ClientLine / BusinessTrack (POS merchant provider)', 'cl.businesstrack.com', 'portal', '{}', '{}', 'house', 80, 'Merchant card portal (mig 955).'),
  ('00000000-0000-0000-0000-000000000001', 'mailbox', '{"email_sweep"}', 'Email-ingested reports (mailbox)', NULL, 'mailbox', '{}', '{}', 'house', 100, 'The IMAP attachment sweep. Any POS or vendor that emails report files.'),
  ('00000000-0000-0000-0000-000000000001', 'ftp', '{}', 'FTP / SFTP drop', NULL, 'ftp', '{}', '{}', 'house', 110, 'The FTP-pull sweep.'),
  ('00000000-0000-0000-0000-000000000001', 'google_closing', '{}', 'Daily-closing responses sheet', 'docs.google.com', 'google_sa', '{}', '{}', 'house', 120, 'The closing-form responses sheet (service account).')
ON CONFLICT (org_id, key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
