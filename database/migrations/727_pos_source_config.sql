-- MIGRATION 727: DUAL-POS SOURCE CONFIG + BUILT-IN POS FEED TABLES (Phase 3 of the POS port)
-- Run in the Supabase SQL editor (after 726). Idempotent.
--
-- Owner design (2026-08-07): the EXISTING external (b2bsoft) sales-feed pipeline stays
-- exactly as it is — untouched tables, parsers, uploads, consumers. The built-in POS module
-- gets its OWN feed tables in the same shape, because a tenant may run it as their PRIMARY
-- POS, as a SECONDARY POS next to the external one, or not at all:
--
--   * PRIMARY built-in  → its data is PROMOTED into raw_sales / daily_sales_feed the same
--                         way the external feed lands today, so every existing consumer
--                         (calculator, P&L, closing, discrepancy) works unchanged.
--   * SECONDARY         → THE STREAMS NEVER MERGE (rules book: SAAS_FRAMEWORK.md §8).
--                         Secondary numbers are reported separately in every category
--                         ("sales under POS 1 and POS 2"). In 'add' mode they count toward
--                         end-of-day sales totals and qualifying sales pay commission —
--                         computed on their own stream, never blended into primary figures.
--                         In 'parallel' mode they are comparison-only.
--   * Reconciliation    → dual-POS tenants reconcile both totals against the COMBINED
--                         end-of-day total, unless the separate_registers checkbox is set,
--                         in which case each POS reconciles on its own.
--
-- Which mode a tenant runs is a TENANT-SETUP decision stored here (RULE TWO: a tenant-
-- tunable knob, not a constant). Defaults reproduce today's world exactly: external
-- primary, built-in off.

-- ── 1. Tenant POS-setup (new tenant-setup table; one row per tenant) ──────────────────────────────
CREATE TABLE IF NOT EXISTS core.tenant_pos_setup (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL UNIQUE,
  builtin_role       TEXT NOT NULL DEFAULT 'off'
                     CHECK (builtin_role IN ('off','primary','secondary')),
  external_role      TEXT NOT NULL DEFAULT 'primary'
                     CHECK (external_role IN ('off','primary','secondary')),
  secondary_mode     TEXT NOT NULL DEFAULT 'parallel'
                     CHECK (secondary_mode IN ('add','parallel')),
                     -- 'add': secondary sales count toward EOD totals + qualifying
                     --        commissions (own stream); 'parallel': comparison-only.
  separate_registers BOOLEAN NOT NULL DEFAULT false,
                     -- true: each POS reconciles as its own register; false: both totals
                     -- reconcile against the combined end-of-day total.
  notes              TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- at most one primary
  CHECK (NOT (builtin_role = 'primary' AND external_role = 'primary'))
);

DO $$ BEGIN
  EXECUTE 'ALTER TABLE core.tenant_pos_setup ENABLE ROW LEVEL SECURITY';
  EXECUTE 'DROP POLICY IF EXISTS open_all ON core.tenant_pos_setup';
  EXECUTE 'CREATE POLICY open_all ON core.tenant_pos_setup FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)';
  EXECUTE 'GRANT ALL ON core.tenant_pos_setup TO anon, authenticated, service_role';
END $$;

-- Every existing tenant gets today's behavior spelled out: external primary, built-in off.
INSERT INTO core.tenant_pos_setup (org_id)
SELECT t.org_id FROM storeops.tenants t
WHERE NOT EXISTS (SELECT 1 FROM core.tenant_pos_setup s WHERE s.org_id = t.org_id)
ON CONFLICT (org_id) DO NOTHING;

-- ── 2. Built-in POS feed tables (the "POS 2" stream when secondary; staging when primary) ─────────
-- Same column grain as commcalc.daily_sales_feed / commcalc.raw_sales so promotion is a
-- column-for-column copy and recon compares like with like. The built-in POS module writes
-- ONLY these two tables; promotion into raw_sales/daily_sales_feed happens exclusively for
-- tenants whose builtin_role = 'primary'.
CREATE TABLE IF NOT EXISTS commcalc.pos_builtin_daily_sales (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  period        TEXT NOT NULL,                 -- '%B %Y', BUSINESS_TZ month label
  period_month  INT,
  period_year   INT,
  store         TEXT,
  salesperson   TEXT,
  user_login    TEXT,
  contract_type TEXT,
  department    TEXT,
  category      TEXT,
  product_desc  TEXT,
  product_id    NUMERIC,
  gp            NUMERIC,
  ext_price     NUMERIC,
  trans_id      TEXT,
  trans_date    DATE,
  mdn           TEXT,
  serial_1      TEXT,
  register      TEXT,
  tender_type   TEXT,
  voided        TEXT,
  trans_type    TEXT,
  customer      TEXT,
  email         TEXT,
  customer_no   TEXT,
  synced_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pos_builtin_daily_period ON commcalc.pos_builtin_daily_sales (org_id, period);
CREATE INDEX IF NOT EXISTS pos_builtin_daily_trans  ON commcalc.pos_builtin_daily_sales (org_id, trans_id);
CREATE INDEX IF NOT EXISTS pos_builtin_daily_date   ON commcalc.pos_builtin_daily_sales (org_id, trans_date);

CREATE TABLE IF NOT EXISTS commcalc.pos_builtin_sales (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  period TEXT NOT NULL, period_month INT, period_year INT,
  store TEXT, salesperson TEXT, user_login TEXT,
  department TEXT, category TEXT, product_desc TEXT,
  product_id NUMERIC, gp NUMERIC, ext_price NUMERIC,
  trans_id TEXT, trans_date DATE, contract_type TEXT,
  mdn TEXT, serial_1 TEXT, register TEXT, tender_type TEXT,
  voided TEXT, trans_type TEXT, sku TEXT,
  synced_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pos_builtin_sales_period ON commcalc.pos_builtin_sales (org_id, period);
CREATE INDEX IF NOT EXISTS pos_builtin_sales_trans  ON commcalc.pos_builtin_sales (org_id, trans_id);

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['commcalc.pos_builtin_daily_sales','commcalc.pos_builtin_sales'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

COMMENT ON TABLE commcalc.pos_builtin_daily_sales IS
  'Built-in POS module daily sales stream (POS 2 when secondary). NEVER merged with the external feed — SAAS_FRAMEWORK.md §8. Promotion to daily_sales_feed only when core.tenant_pos_setup.builtin_role = primary.';
COMMENT ON TABLE commcalc.pos_builtin_sales IS
  'Built-in POS module monthly sales stream. Promotion to raw_sales only when core.tenant_pos_setup.builtin_role = primary.';
