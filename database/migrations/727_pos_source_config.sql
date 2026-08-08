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

-- Backend-only access (mig-722 posture — NOT the legacy open_all pattern): RLS on with no
-- policies, anon/authenticated revoked, service_role only. This table decides whose data
-- lands in the commission ledger; a browser must never be able to touch it.
DO $$ BEGIN
  EXECUTE 'ALTER TABLE core.tenant_pos_setup ENABLE ROW LEVEL SECURITY';
  EXECUTE 'DROP POLICY IF EXISTS open_all ON core.tenant_pos_setup';
  EXECUTE 'REVOKE ALL ON core.tenant_pos_setup FROM anon, authenticated';
  EXECUTE 'GRANT ALL ON core.tenant_pos_setup TO service_role';
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

-- Backend-only access for the sales streams too (mig-722 posture): cross-tenant sales data
-- (rep names, customer emails, GP) must not be readable with the public anon key.
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['commcalc.pos_builtin_daily_sales','commcalc.pos_builtin_sales'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('REVOKE ALL ON %s FROM anon, authenticated', t);
    EXECUTE format('GRANT ALL ON %s TO service_role', t);
  END LOOP;
END $$;

-- ── 2b. Ledger provenance column (owner constraint 2026-08-08: "can't delete anything") ───────────
-- commcalc.daily_sales_feed / raw_sales (mig 047) had NO way to tell who wrote a row, so the
-- only safe idempotent re-sync was "delete the whole period", which would also destroy
-- historical imports, manual uploads and backfills sitting in that period. Adding `source`
-- lets promotion delete ONLY the rows it previously wrote.
-- Existing rows stay NULL on purpose: NULL is never equal to 'pos_builtin', so every row that
-- predates this migration becomes permanently un-deletable by the promotion path. No backfill,
-- no UPDATE against the live ledger — adding a nullable column is metadata-only in PG11+.
ALTER TABLE commcalc.daily_sales_feed ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE commcalc.raw_sales        ADD COLUMN IF NOT EXISTS source TEXT;
COMMENT ON COLUMN commcalc.daily_sales_feed.source IS
  'Writer provenance. NULL = pre-existing/external feed (never touched by POS promotion). ''pos_builtin'' = written by commcalc.pos_promote_period.';
COMMENT ON COLUMN commcalc.raw_sales.source IS
  'Writer provenance. NULL = pre-existing/external feed (never touched by POS promotion). ''pos_builtin'' = written by commcalc.pos_promote_period.';
-- Makes the scoped delete and the foreign-row guard index-only rather than a period scan.
CREATE INDEX IF NOT EXISTS daily_sales_feed_org_period_source_idx
  ON commcalc.daily_sales_feed (org_id, period, source);
CREATE INDEX IF NOT EXISTS raw_sales_org_period_source_idx
  ON commcalc.raw_sales (org_id, period, source);

-- ── 3. Atomic primary promotion ────────────────────────────────────────────────────────────────────
-- One transaction: replace the target period in daily_sales_feed / raw_sales from the
-- already-written built-in stream. The non-atomic alternative (delete via one HTTP call,
-- insert via others) could leave the LIVE ledger period wiped or half-written on a failure.
-- Guards baked in: refuses when the tenant's builtin_role isn't 'primary', when the external
-- POS isn't 'off' (its feed lands in these same tables — promotion would wipe it, violating
-- the never-merge rule), and when the stream period is empty (empty-abort).
-- NON-DESTRUCTIVE (owner constraint 2026-08-08): the DELETE is scoped to source='pos_builtin',
-- so it can only remove rows a previous promotion wrote. Rows written by anything else — the
-- external feed, historical imports, manual uploads, backfills — carry source NULL and are
-- physically outside the DELETE's reach.
-- The mirror-image failure is double-counting: if we insert alongside foreign rows for the same
-- period, totals silently double. So promotion REFUSES when the target period already holds
-- non-'pos_builtin' rows. Refusing is the only option that neither deletes nor double-counts;
-- resolving the overlap is a human decision, not something this function should guess at.
CREATE OR REPLACE FUNCTION commcalc.pos_promote_period(p_org UUID, p_period TEXT, p_mode TEXT)
RETURNS INT
LANGUAGE plpgsql
SET search_path = commcalc, core, pg_temp
AS $$
DECLARE
  v_setup   core.tenant_pos_setup;
  v_count   INT;
  v_foreign INT;
BEGIN
  IF p_mode NOT IN ('daily','monthly') THEN
    RAISE EXCEPTION 'mode must be daily or monthly';
  END IF;
  SELECT * INTO v_setup FROM core.tenant_pos_setup WHERE org_id = p_org;
  IF v_setup.org_id IS NULL OR v_setup.builtin_role IS DISTINCT FROM 'primary' THEN
    RAISE EXCEPTION 'promotion requires builtin_role = primary in tenant POS setup';
  END IF;
  IF v_setup.external_role IS DISTINCT FROM 'off' THEN
    RAISE EXCEPTION 'promotion is blocked while an external POS is configured (%) — its feed '
                    'lands in the same ledger tables and would be wiped (never-merge rule, '
                    'SAAS_FRAMEWORK §8)', v_setup.external_role;
  END IF;

  IF p_mode = 'daily' THEN
    SELECT COUNT(*) INTO v_count FROM commcalc.pos_builtin_daily_sales
     WHERE org_id = p_org AND period = p_period;
    IF v_count = 0 THEN
      RAISE EXCEPTION 'built-in stream has no rows for % — aborting so the ledger period is not wiped', p_period;
    END IF;
    SELECT COUNT(*) INTO v_foreign FROM commcalc.daily_sales_feed
     WHERE org_id = p_org AND period = p_period AND source IS DISTINCT FROM 'pos_builtin';
    IF v_foreign > 0 THEN
      RAISE EXCEPTION 'daily_sales_feed already holds % row(s) for % this module did not write '
                      '(source IS NULL — external feed, historical import or manual upload). '
                      'Promotion refuses: deleting them would lose data, inserting beside them '
                      'would double-count. Resolve the overlap before promoting.', v_foreign, p_period;
    END IF;
    DELETE FROM commcalc.daily_sales_feed
     WHERE org_id = p_org AND period = p_period AND source = 'pos_builtin';
    INSERT INTO commcalc.daily_sales_feed
      (org_id, period, period_month, period_year, store, salesperson, user_login, contract_type,
       department, category, product_desc, product_id, gp, ext_price, trans_id, trans_date,
       mdn, serial_1, register, tender_type, voided, trans_type, customer, email, customer_no,
       source)
    SELECT org_id, period, period_month, period_year, store, salesperson, user_login, contract_type,
           department, category, product_desc, product_id, gp, ext_price, trans_id, trans_date,
           mdn, serial_1, register, tender_type, voided, trans_type, customer, email, customer_no,
           'pos_builtin'
    FROM commcalc.pos_builtin_daily_sales
    WHERE org_id = p_org AND period = p_period;
  ELSE
    SELECT COUNT(*) INTO v_count FROM commcalc.pos_builtin_sales
     WHERE org_id = p_org AND period = p_period;
    IF v_count = 0 THEN
      RAISE EXCEPTION 'built-in stream has no rows for % — aborting so the ledger period is not wiped', p_period;
    END IF;
    SELECT COUNT(*) INTO v_foreign FROM commcalc.raw_sales
     WHERE org_id = p_org AND period = p_period AND source IS DISTINCT FROM 'pos_builtin';
    IF v_foreign > 0 THEN
      RAISE EXCEPTION 'raw_sales already holds % row(s) for % this module did not write '
                      '(source IS NULL — external feed, historical import or manual upload). '
                      'Promotion refuses: deleting them would lose data, inserting beside them '
                      'would double-count. Resolve the overlap before promoting.', v_foreign, p_period;
    END IF;
    DELETE FROM commcalc.raw_sales
     WHERE org_id = p_org AND period = p_period AND source = 'pos_builtin';
    INSERT INTO commcalc.raw_sales
      (org_id, period, period_month, period_year, store, salesperson, user_login, department,
       category, product_desc, product_id, gp, ext_price, trans_id, trans_date, contract_type,
       mdn, serial_1, register, tender_type, voided, trans_type, sku, source)
    SELECT org_id, period, period_month, period_year, store, salesperson, user_login, department,
           category, product_desc, product_id, gp, ext_price, trans_id, trans_date, contract_type,
           mdn, serial_1, register, tender_type, voided, trans_type, sku, 'pos_builtin'
    FROM commcalc.pos_builtin_sales
    WHERE org_id = p_org AND period = p_period;
  END IF;
  RETURN v_count;
END;
$$;
REVOKE ALL ON FUNCTION commcalc.pos_promote_period(UUID, TEXT, TEXT) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION commcalc.pos_promote_period(UUID, TEXT, TEXT) TO service_role;

COMMENT ON TABLE commcalc.pos_builtin_daily_sales IS
  'Built-in POS module daily sales stream (POS 2 when secondary). NEVER merged with the external feed — SAAS_FRAMEWORK.md §8. Promotion to daily_sales_feed only when core.tenant_pos_setup.builtin_role = primary.';
COMMENT ON TABLE commcalc.pos_builtin_sales IS
  'Built-in POS module monthly sales stream. Promotion to raw_sales only when core.tenant_pos_setup.builtin_role = primary.';
