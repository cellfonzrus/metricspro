-- 998_connector_route_policy.sql — WHICH INGEST ROUTE IS OPEN FOR A CONNECTOR (per org, per route).
-- Band 900–999 (platform). Additive + idempotent (safe to re-run). NOT money-touching: nothing here
-- reads or writes a rate, plan, tier, payout or any calculation input — it only decides WHICH WAY data
-- may arrive.
--
-- OWNER DIRECTIVE 2026-09-09, verbatim: "we are not doing the 2FA login for b2b as they sent an email
-- out to not do it, so make that gated by default for all unless it opens up later, only option for b2b
-- is email ingested reports"
--
-- The owner is relaying a VENDOR INSTRUCTION: B2B Soft told them not to use the portal's browser/2FA
-- login. The connector had run up 29 consecutive failures ("The b2bsoft session has expired — please
-- re-authenticate (Log in + enter the 2FA code).", commcalc.data_source d0c12f4f…, house org, last
-- delivery 2026-07-16), each one re-prompting a human to do the very thing the vendor forbade.
--
-- WHY A NEW TABLE, AND WHAT WAS CHECKED FIRST (duplicate gate, CLAUDE.md):
--   · commcalc.data_source.enabled       — the OPERATOR's own per-login on/off knob. Says "we don't use
--                                          this", carries no reason, is per ROW not per (org, connector),
--                                          and the next operator switches it back on. Left untouched.
--   · commcalc.portal_block_marker /     — mig 244's portal cooldown. Already answers "may we contact
--     data_source.blocked_until            this portal RIGHT NOW?", but its answer is a TIMER with a
--                                          `blocked_until` and its wording frames a deliberate closure
--                                          as a fault ("the portal has temporarily blocked us"). A
--                                          vendor instruction has no cooldown to expire. Left untouched
--                                          — and this table's SHAPE (org_id + connector + house-org
--                                          defaults inherited by every tenant) is copied from it.
--   · commcalc.report_pull_map.enabled   — WHICH REPORTS a pull fetches, not WHETHER the login may run.
--   · core.import_feed.enabled           — whether a tenant EXPECTS a feed (freshness/overdue), not
--                                          which route delivers it. Its `source_type` vocabulary
--                                          (pull | email_sweep | ftp | google_sa | manual_expected) IS
--                                          reused verbatim as this table's `route`, so a route slug
--                                          means the same thing in both places.
-- Nothing existing answered "is this ROUTE open for this CONNECTOR, for this org?", so this adds it at
-- the layer the others already live on, and every gate in code reads THIS one table.
--
-- RULE TWO. No vendor name enters any code path: the gate is keyed on the connector slug the platform
-- already dispatches on (commcalc.data_source.processor / router._SOURCE_SCRAPERS) and on the route
-- vocabulary above. The vendor's name appears ONLY as data, in the seeded rows below.
--
-- MULTI-TENANT (RULE ONE): org_id NOT NULL + index. A HOUSE-org row is the PLATFORM DEFAULT inherited
-- by every tenant; a tenant row for the same (connector, route) overrides it — the identical
-- inheritance shape mig 244 (portal_block_marker) and mig 207 (report_pull_map) use. The house org is
-- never a data scope here, only the default-row provider.
--
-- DEGRADES BOTH WAYS: until this runs, connector_route_policy.load_rows() returns [] and every gate is
-- INERT — the portal login behaves exactly as it does today. After it runs, the seeded rows apply.
--
-- REVERT:
--   -- to RE-OPEN the portal login for a connector (the owner's "unless it opens up later" — ONE row):
--   --   UPDATE commcalc.connector_route_policy SET allowed = true, updated_at = now()
--   --    WHERE org_id = '00000000-0000-0000-0000-000000000001' AND connector = 'b2bsoft';
--   --   (repeat for connector = 'b2b', the alias slug router._SOURCE_SCRAPERS also accepts)
--   -- to undo this migration entirely:
--   --   ALTER TABLE commcalc.b2b_sweep_config DROP COLUMN IF EXISTS connector;
--   --   DROP TABLE IF EXISTS commcalc.connector_route_policy;

-- ── (1) the gate ─────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.connector_route_policy (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL,
  connector     text NOT NULL,                    -- data_source.processor slug (e.g. the POS portal)
  route         text NOT NULL DEFAULT 'pull',     -- core.import_feed.source_type vocabulary
  allowed       boolean NOT NULL DEFAULT true,    -- false = this route is CLOSED for this connector
  reason        text,                             -- WHY, in the operator's own words. Shown verbatim.
  remedy_route  text,                             -- the route that IS supported instead
  remedy_label  text,                             -- how to name it to a human
  remedy_href   text,                             -- the page that route is configured/read on
  notes         text,
  updated_by    text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS connector_route_policy_org_idx
  ON commcalc.connector_route_policy (org_id);
CREATE UNIQUE INDEX IF NOT EXISTS connector_route_policy_uniq_idx
  ON commcalc.connector_route_policy (org_id, lower(connector), lower(route));

COMMENT ON TABLE commcalc.connector_route_policy IS
  'Per-org, per-connector INGEST ROUTE gate. One row per (org, connector, route): allowed=false closes '
  'that route with a stated reason and names the route that IS supported. A house-org row is the '
  'platform default inherited by every tenant; a tenant row for the same (connector, route) overrides '
  'it. Read by commcalc/connector_route_policy.py, which gates the portal-login pull, the manual Pull '
  'now, both interactive login entry points and the scheduled sweep. Re-opening a route is one UPDATE '
  'on one row — no code changes, nothing deleted.';
COMMENT ON COLUMN commcalc.connector_route_policy.route IS
  'Feed-shape vocabulary, shared verbatim with core.import_feed.source_type: pull | email_sweep | ftp '
  '| google_sa | manual_expected. A route is a SHAPE of arrival, never a vendor.';

ALTER TABLE commcalc.connector_route_policy ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY open_all ON commcalc.connector_route_policy
    FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN OTHERS THEN NULL; END $$;
GRANT ALL ON commcalc.connector_route_policy TO anon, authenticated, service_role;

-- ── (2) the seeded house DEFAULT: the portal login is CLOSED for this connector, for every org ────
-- ON CONFLICT DO NOTHING so a re-run can never resurrect a closure an operator has since re-opened,
-- and never overwrites their reason/notes. Two rows because router._SOURCE_SCRAPERS accepts both
-- slugs for the same portal ('b2bsoft' and the legacy alias 'b2b') — a login stored under either must
-- land on the same closed gate.
INSERT INTO commcalc.connector_route_policy
  (org_id, connector, route, allowed, reason, remedy_route, remedy_label, remedy_href, notes)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'b2bsoft', 'pull', false,
   'The vendor emailed us not to use their 2FA / browser login, so the platform does not attempt it '
   '(owner directive 2026-09-09).',
   'email_sweep', 'the email-ingested reports', '/commcalc/email-imports',
   'HOUSE DEFAULT inherited by every tenant. Set allowed=true here to re-open the portal login for '
   'everyone; insert a row with this org''s own org_id to re-open it for one tenant only. The stored '
   'credentials, saved session and every ingested row are untouched.'),
  ('00000000-0000-0000-0000-000000000001', 'b2b', 'pull', false,
   'The vendor emailed us not to use their 2FA / browser login, so the platform does not attempt it '
   '(owner directive 2026-09-09).',
   'email_sweep', 'the email-ingested reports', '/commcalc/email-imports',
   'Alias slug for the same portal (router._SOURCE_SCRAPERS registers both). See the b2bsoft row.')
ON CONFLICT DO NOTHING;

-- ── (3) name the connector the legacy per-vendor inventory sweep drives ───────────────────────────
-- commcalc.b2b_sweep_config predates the connector registry: it is one table per vendor, so the vendor
-- identity lived only in the TABLE NAME and the endpoint's own strings. That is precisely the RULE TWO
-- shape this platform avoids, and it is why the sweep could not be gated without a literal in code.
-- This column moves that identity into config, where the gate can read it. NULL ⇒ ungated (inert).
ALTER TABLE commcalc.b2b_sweep_config
  ADD COLUMN IF NOT EXISTS connector text;
COMMENT ON COLUMN commcalc.b2b_sweep_config.connector IS
  'Which connector this legacy per-vendor sweep drives — the same slug vocabulary as '
  'commcalc.data_source.processor. Read by the connector_route_policy gate so this sweep can be closed '
  'by config rather than by a vendor name in code. NULL = ungated.';
UPDATE commcalc.b2b_sweep_config SET connector = 'b2bsoft' WHERE connector IS NULL;

NOTIFY pgrst, 'reload schema';
