-- 200_commission_b2bsoft_pos_profile.sql — the b2bsoft POS "standard profile" (mod-commission, band 200-299)
--
-- WHY (owner escalation 2026-07-14): the house/Boost tenant's sales-ingest mailbox was set up BY HAND
-- and every other b2bsoft tenant (luxelink/Total) has been re-created by hand too — so a new tenant's
-- ingest is "identical to house" only if someone remembers every rule. That hand-copy is exactly how the
-- luxelink mailbox landed enabled=false, with a bad password, AND filed under the HOUSE org_id (which would
-- ingest Total sales into Boost). This migration makes the b2bsoft setup a CONFIG-DRIVEN template so a new
-- tenant is standard BY CONSTRUCTION, not by hand.
--
-- Two objects, both ADDITIVE + IDEMPOTENT (safe to re-run):
--   1. commcalc.pos_profile        — per-tenant, UI-editable POS standard profile (SAP-configurable rule:
--                                     the standard filename rules / imap defaults / schedule / report defs
--                                     live in a config table, not hard-coded per tenant). One row per
--                                     (org_id, pos_key). Seeded for the house org + every existing tenant.
--   2. commcalc.sales_feed_daily_health(org, from, to) — the per-day ingest-health aggregate that powers
--                                     the "is my file ingesting?" view on /commcalc/email-imports (missing
--                                     day = no delivery, 0-priced = degraded/parse, ingested = healthy).
--
-- NON-money: this configures INGEST plumbing (which files land, and a read-only health view). It does not
-- change any commission rate/tier/plan/calculator. The apply endpoint that consumes this profile is
-- strictly ADDITIVE (it only adds missing standard rules + fills blank defaults; it never removes a rule,
-- clobbers a saved password, or flips an existing report_definitions.auto toggle).

-- ── 1. pos_profile: the UI-editable, per-tenant POS standard-profile registry ────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.pos_profile (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,
  pos_key            TEXT NOT NULL,               -- 'b2bsoft' (a POS SYSTEM key, never a tenant/carrier name)
  label              TEXT,
  imap_defaults      JSONB DEFAULT '{}'::jsonb,   -- {imap_port,use_ssl,mailbox,since_days}; host/creds are per-tenant
  filename_rules     JSONB DEFAULT '[]'::jsonb,   -- [{pattern,upload_type,note}] — the standard mailbox rules
  schedule_defaults  JSONB DEFAULT '{}'::jsonb,   -- {frequency,hour}
  report_defs        JSONB DEFAULT '[]'::jsonb,   -- [{report_key,label,source_name,target_table,upload_endpoint,period_mode,auto,sort_order}]
  is_active          BOOLEAN DEFAULT true,
  created_at         TIMESTAMPTZ DEFAULT now(),
  updated_at         TIMESTAMPTZ DEFAULT now(),
  UNIQUE (org_id, pos_key)
);
CREATE INDEX IF NOT EXISTS pos_profile_org ON commcalc.pos_profile (org_id);

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['commcalc.pos_profile'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

-- ── 2. seed_pos_profile_b2bsoft(org): the canonical b2bsoft standard, seeded PER tenant ──────────────
-- Idempotent (ON CONFLICT DO NOTHING) so it never overwrites a tenant's edited profile. The content
-- mirrors the frontend DEFAULT_RULES + migration 039's B2B Soft report_definitions — one source of truth.
CREATE OR REPLACE FUNCTION commcalc.seed_pos_profile_b2bsoft(p_org uuid)
RETURNS void LANGUAGE plpgsql AS $fn$
BEGIN
  INSERT INTO commcalc.pos_profile (org_id, pos_key, label, imap_defaults, filename_rules, schedule_defaults, report_defs)
  VALUES (
    p_org, 'b2bsoft', 'B2B Soft (standard)',
    '{"imap_port":993,"use_ssl":true,"mailbox":"INBOX","since_days":14}'::jsonb,
    $rules$[
      {"pattern":"*Sales*Transaction*Details*","upload_type":"daily_sales","note":"daily B2B sales export — use the full-column \"for Metrics pro\" report (Ext Price + GP)"},
      {"pattern":"*Inventory*Aging*","upload_type":"inventory_aging","note":"b2bsoft inventory aging → Balance-Sheet inventory value"},
      {"pattern":"*X-Report*","upload_type":"x_report","note":"POS X-report tender summary → daily-closing cash/credit recon"}
    ]$rules$::jsonb,
    '{"frequency":"hourly","hour":7}'::jsonb,
    $defs$[
      {"report_key":"sales","label":"Sales Transactions","source_name":"Sales Transaction Details (78-col)","target_table":"raw_sales","upload_endpoint":"commcalc/upload/sales","period_mode":"current","auto":true,"sort_order":10},
      {"report_key":"inventory","label":"Inventory Aging","source_name":"Inventory Aging","target_table":"inventory_value","upload_endpoint":null,"period_mode":"snapshot","auto":false,"sort_order":20}
    ]$defs$::jsonb
  )
  ON CONFLICT (org_id, pos_key) DO NOTHING;
END;
$fn$;
GRANT EXECUTE ON FUNCTION commcalc.seed_pos_profile_b2bsoft(uuid) TO anon, authenticated, service_role;

-- Seed the house org + back-fill every existing tenant now (idempotent; skips any that already has a row).
DO $seed$
DECLARE t record;
BEGIN
  PERFORM commcalc.seed_pos_profile_b2bsoft('00000000-0000-0000-0000-000000000001');
  BEGIN
    FOR t IN SELECT org_id FROM storeops.tenants LOOP
      PERFORM commcalc.seed_pos_profile_b2bsoft(t.org_id);
    END LOOP;
  EXCEPTION WHEN undefined_table THEN
    NULL;  -- storeops.tenants absent in a bare env → house seed above still applied
  END;
END $seed$;

-- ── 3. sales_feed_daily_health: per-day ingest-health aggregate (read-only; powers the UI panel) ──────
-- Groups the daily B2B feed by trans_date so "did day X's sales land?" is answerable directly. Uses the
-- existing (org_id, trans_date) index. n_rows=0 for a day ⇒ missing (no delivery/ingest); n_rows>0 with
-- n_priced=0 ⇒ degraded/price-less export; n_priced>0 ⇒ healthy. STABLE + org-scoped.
CREATE OR REPLACE FUNCTION commcalc.sales_feed_daily_health(p_org uuid, p_from date, p_to date)
RETURNS TABLE(trans_date date, n_rows bigint, n_priced bigint, amount numeric)
LANGUAGE sql STABLE AS $$
  SELECT f.trans_date,
         count(*)::bigint AS n_rows,
         count(*) FILTER (WHERE coalesce(f.ext_price, 0) <> 0)::bigint AS n_priced,
         round(coalesce(sum(f.ext_price), 0)::numeric, 2) AS amount
  FROM commcalc.daily_sales_feed f
  WHERE f.org_id = p_org
    AND f.trans_date IS NOT NULL
    AND f.trans_date BETWEEN p_from AND p_to
  GROUP BY f.trans_date
  ORDER BY f.trans_date;
$$;
GRANT EXECUTE ON FUNCTION commcalc.sales_feed_daily_health(uuid, date, date) TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 200 complete — b2bsoft POS profile + ingest-health RPC' AS status;
