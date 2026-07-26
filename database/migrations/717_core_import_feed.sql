-- 717_core_import_feed.sql — UNIVERSAL IMPORT-HEALTH registry + freshness-evidence RPCs.
-- Band 700–799 (mod-platform-core). Additive + idempotent (safe to re-run).
--
-- OWNER DIRECTIVE 2026-07-25 (verbatim): "the commision of vida pay is suppoed to run on a schedule,
-- the system should update the latest import time and if any imports are not scheduled as defined in
-- the entire system it should come up as a pop for every admin as soon as they log in on the main page
-- and take them to the upload menu to manually upload the data or fix the import channel - this should
-- be built universally for all uploads now and going forward for all tenants. also the admin should be
-- notified for pending mappings or duplicate data"
--
-- WHAT THIS ADDS
--   (1) core.import_feed — THE registry of every import feed a tenant expects, with its EXPECTED CADENCE
--       and the page an admin goes to in order to fix it. One row per feed per org. Rows are AUTO-DERIVED
--       from the schedule the system ALREADY knows (commcalc.email_sweep_config patterns, ftp_sweep_config
--       patterns, the *_sweep_config portal sweeps, commcalc.data_source portal logins [VidaPay/T-CETRA],
--       commcalc.report_definitions + connector_instances) and are then FULLY EDITABLE / disable-able by a
--       tenant admin (RULE TWO: config table + admin UI, zero hard-coded tenant/feed logic).
--   (2) core.import_evidence(org) — ONE round trip returning the AUTHORITATIVE "when did data last
--       actually arrive" evidence for every feed shape, aggregated IN POSTGRES (never 40k rows into
--       Python). Read-only. Adds NO new write path to any ingest — it only reads the trails the existing
--       ingests already write.
--   (3) core.import_table_freshness(org, specs) — generic, identifier-safe max(<ts col>) probe for feeds
--       whose only evidence is the raw table itself (a tenant loaded by backfill before mig 202 existed).
--
-- MULTI-TENANT (RULE ONE): core.import_feed carries org_id uuid NOT NULL + an index; UNIQUE is
--   (org_id, feed_key) so two tenants may hold the same feed_key independently. BOTH RPCs take p_org and
--   filter EVERY source on org_id — there is no cross-tenant read path here.
--
-- DEGRADES GRACEFULLY: until this runs, /core/import-feeds + /core/attention return an honest empty
--   payload with a "run migration 717" hint (every read is try/except-guarded) and the admin popup simply
--   never fires. No unrelated page breaks. Mirrors mig 112 / 716 style (RLS open_all, GRANT, NOTIFY reload).
--
-- NOT MONEY-TOUCHING: nothing here reads or writes a rate, plan, tier, payout or rep_commissions row.

-- ── (1) the registry ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS core.import_feed (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  feed_key       TEXT NOT NULL,                 -- stable, deterministic key (see derived_from)
  label          TEXT NOT NULL,                 -- human label shown in the popup / admin page
  module         TEXT,                          -- nav module key ('commissions' | 'closing' | 'asset' …)
  source_type    TEXT NOT NULL DEFAULT 'manual_expected',
                                                -- email_sweep | ftp | pull | google_sa | manual_expected
  cadence_hours  NUMERIC NOT NULL DEFAULT 24,   -- expected MAX hours between successful imports
  grace_hours    NUMERIC NOT NULL DEFAULT 6,    -- slack before "overdue" is declared
  deep_link      TEXT,                          -- the page an admin fixes/uploads at
  evidence       JSONB NOT NULL DEFAULT '[]'::jsonb,
                                                -- ordered probe list, e.g.
                                                -- [{"kind":"email","account":"default","upload_type":"daily_sales"},
                                                --  {"kind":"upload_trace","upload_type":"daily_sales"}]
                                                -- the FIRST probe is the CHANNEL probe (did the configured
                                                -- channel deliver); last_success = MAX over all probes
                                                -- (did the data arrive at all, incl. a manual upload).
  enabled        BOOLEAN NOT NULL DEFAULT true, -- false = this tenant does not expect this feed
  auto_derived   BOOLEAN NOT NULL DEFAULT false,-- true = created by the derive pass (vs hand-added)
  derived_from   TEXT,                          -- provenance, e.g. 'commcalc.email_sweep_config:default:*Sales*'
  muted_until    TIMESTAMPTZ,                   -- snooze: suppress from the popup until this time
  notes          TEXT,
  updated_by     TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, feed_key)
);
CREATE INDEX IF NOT EXISTS import_feed_org_idx     ON core.import_feed(org_id);
CREATE INDEX IF NOT EXISTS import_feed_org_enabled ON core.import_feed(org_id, enabled);

COMMENT ON TABLE core.import_feed IS
  'Per-tenant registry of expected data imports (feed_key, expected cadence, deep link to fix/upload). Auto-derived from the existing sweep/connector config and then editable at /admin/import-health. Drives the admin login attention popup.';

ALTER TABLE core.import_feed ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY open_all ON core.import_feed FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN OTHERS THEN NULL; END $$;
GRANT ALL ON core.import_feed TO anon, authenticated, service_role;

-- ── (2) freshness evidence, aggregated in Postgres ────────────────────────────────────────────────
-- ONE call returns every "last successful arrival" signal the app already records:
--   kind='upload_trace' k1=upload_type                  → mig 202 universal ingest trace (manual + sweeps)
--   kind='email'        k1=account      k2=upload_type  → commcalc.email_processed (per mailbox + pattern)
--   kind='ftp'          k1=upload_type                  → commcalc.ftp_processed
--   kind='sweep'        k1=<config table> k2=account    → the sweep's own last_run_at / last_status
--   kind='source'       k1=data_source.id               → portal-login pull last_run_at / last_status
-- Each block is independently exception-guarded so a table that does not exist yet on a given database
-- (e.g. mig 202 un-run) silently contributes no rows instead of failing the whole call.
-- SECURITY INVOKER (default) on purpose: the *_sweep_config tables hold PORTAL CREDENTIALS and have
-- anon/authenticated REVOKED. A SECURITY DEFINER function would have handed their contents to anon.
-- Only service_role (what the backend uses) may execute it.
CREATE OR REPLACE FUNCTION core.import_evidence(p_org uuid)
RETURNS TABLE (kind text, k1 text, k2 text, last_success timestamptz, last_status text, n bigint)
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
  -- universal ingest trace (mig 202): any path that actually SAVED rows of a given upload_type
  BEGIN
    RETURN QUERY
      SELECT 'upload_trace'::text, coalesce(t.upload_type, '')::text, NULL::text,
             max(t.created_at), 'ok'::text, count(*)::bigint
        FROM commcalc.upload_trace t
       WHERE t.org_id = p_org
         AND coalesce(t.rows_saved, 0) > 0
         AND coalesce(t.status, 'ok') IN ('ok', 'partial')
       GROUP BY t.upload_type;
  EXCEPTION WHEN OTHERS THEN NULL; END;

  -- email sweep: per mailbox account + upload_type, only attachments that really ingested rows
  BEGIN
    RETURN QUERY
      SELECT 'email'::text, coalesce(e.account, 'default')::text, coalesce(e.upload_type, '')::text,
             max(e.processed_at), 'ok'::text, count(*)::bigint
        FROM commcalc.email_processed e
       WHERE e.org_id = p_org
         AND lower(coalesce(e.status, '')) = 'ok'
         AND coalesce(e.rows_saved, 0) > 0
       GROUP BY e.account, e.upload_type;
  EXCEPTION WHEN OTHERS THEN NULL; END;

  -- ftp sweep: per upload_type (ftp_processed has no account dimension)
  BEGIN
    RETURN QUERY
      SELECT 'ftp'::text, coalesce(f.upload_type, '')::text, NULL::text,
             max(f.processed_at), 'ok'::text, count(*)::bigint
        FROM commcalc.ftp_processed f
       WHERE f.org_id = p_org
         AND lower(coalesce(f.status, '')) = 'ok'
         AND coalesce(f.rows_saved, 0) > 0
       GROUP BY f.upload_type;
  EXCEPTION WHEN OTHERS THEN NULL; END;

  -- the sweep configs' own run markers (one row per config row; k2 = mailbox account where it applies)
  BEGIN
    RETURN QUERY
      SELECT 'sweep'::text, 'email_sweep_config'::text, coalesce(c.account, 'default')::text,
             c.last_run_at, coalesce(c.last_status, '')::text, 1::bigint
        FROM commcalc.email_sweep_config c WHERE c.org_id = p_org;
  EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN
    RETURN QUERY
      SELECT 'sweep'::text, 'ftp_sweep_config'::text, NULL::text,
             c.last_run_at, coalesce(c.last_status, '')::text, 1::bigint
        FROM commcalc.ftp_sweep_config c WHERE c.org_id = p_org;
  EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN
    RETURN QUERY
      SELECT 'sweep'::text, 'dlar_sweep_config'::text, NULL::text,
             c.last_run_at, coalesce(c.last_status, '')::text, 1::bigint
        FROM commcalc.dlar_sweep_config c WHERE c.org_id = p_org;
  EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN
    RETURN QUERY
      SELECT 'sweep'::text, 'epay_sweep_config'::text, NULL::text,
             c.last_run_at, coalesce(c.last_status, '')::text, 1::bigint
        FROM commcalc.epay_sweep_config c WHERE c.org_id = p_org;
  EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN
    RETURN QUERY
      SELECT 'sweep'::text, 'vip_sweep_config'::text, NULL::text,
             c.last_run_at, coalesce(c.last_status, '')::text, 1::bigint
        FROM commcalc.vip_sweep_config c WHERE c.org_id = p_org;
  EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN
    RETURN QUERY
      SELECT 'sweep'::text, 'b2b_sweep_config'::text, NULL::text,
             c.last_run_at, coalesce(c.last_status, '')::text, 1::bigint
        FROM commcalc.b2b_sweep_config c WHERE c.org_id = p_org;
  EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN
    RETURN QUERY
      SELECT 'sweep'::text, 'closing_sweep_config'::text, NULL::text,
             c.last_run_at, coalesce(c.last_status, '')::text, 1::bigint
        FROM commcalc.closing_sweep_config c WHERE c.org_id = p_org;
  EXCEPTION WHEN OTHERS THEN NULL; END;

  -- portal-login pulls (VidaPay / Total Access / b2bsoft …): one row per data_source login
  BEGIN
    RETURN QUERY
      SELECT 'source'::text, s.id::text, coalesce(s.processor, '')::text,
             s.last_run_at, coalesce(s.last_status, '')::text, 1::bigint
        FROM commcalc.data_source s WHERE s.org_id = p_org;
  EXCEPTION WHEN OTHERS THEN NULL; END;
END;
$fn$;

REVOKE ALL ON FUNCTION core.import_evidence(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.import_evidence(uuid) TO service_role;

-- ── (3) generic raw-table freshness probe (opt-in per feed) ───────────────────────────────────────
-- p_specs = [{"schema":"commcalc","table":"raw_ma_commission","column":"created_at"}, …]
-- Identifier-safe: every name is validated against information_schema and injected with format(%I),
-- so a hand-edited feed row can never turn this into SQL injection. A table/column that does not
-- exist (or lacks org_id) simply yields NULL for that spec instead of raising.
CREATE OR REPLACE FUNCTION core.import_table_freshness(p_org uuid, p_specs jsonb)
RETURNS TABLE (spec_key text, last_success timestamptz)
LANGUAGE plpgsql STABLE AS $fn$
DECLARE
  s        jsonb;
  v_schema text;
  v_table  text;
  v_col    text;
  v_ts     timestamptz;
BEGIN
  IF p_specs IS NULL OR jsonb_typeof(p_specs) <> 'array' THEN
    RETURN;
  END IF;
  FOR s IN SELECT * FROM jsonb_array_elements(p_specs) LOOP
    v_schema := coalesce(s->>'schema', 'commcalc');
    v_table  := s->>'table';
    v_col    := coalesce(s->>'column', 'created_at');
    CONTINUE WHEN v_table IS NULL;
    -- both the timestamp column AND org_id must really exist on that relation
    CONTINUE WHEN NOT EXISTS (
      SELECT 1 FROM information_schema.columns c
       WHERE c.table_schema = v_schema AND c.table_name = v_table AND c.column_name = v_col)
      OR NOT EXISTS (
      SELECT 1 FROM information_schema.columns c
       WHERE c.table_schema = v_schema AND c.table_name = v_table AND c.column_name = 'org_id');
    v_ts := NULL;
    BEGIN
      EXECUTE format('SELECT max(%I) FROM %I.%I WHERE org_id = $1', v_col, v_schema, v_table)
        INTO v_ts USING p_org;
    EXCEPTION WHEN OTHERS THEN v_ts := NULL; END;
    spec_key := v_schema || '.' || v_table || '.' || v_col;
    last_success := v_ts;
    RETURN NEXT;
  END LOOP;
END;
$fn$;

REVOKE ALL ON FUNCTION core.import_table_freshness(uuid, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.import_table_freshness(uuid, jsonb) TO service_role;

NOTIFY pgrst, 'reload schema';
SELECT '717 complete — core.import_feed registry + core.import_evidence() + core.import_table_freshness()' AS status;
