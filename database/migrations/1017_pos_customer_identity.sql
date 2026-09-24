-- MIGRATION 1017: POS CUSTOMER IDENTITY — the other names a customer went by, and a reversible merge
-- (owner 2026-09-24; index §30.16). Additive + idempotent (safe to re-run). NOT money-touching: no sale, payment,
-- P&L or payout row is changed by this migration; it only adds a table and two nullable columns.
--
-- OWNER, verbatim: "if in the last 2 years data a customer came in twice to get phones on different names they
-- should be combined together and shows in the customer pages as separate line items".
--
-- WHAT IT ADDS
--   · pos.customer_aliases — one row per (org, customer, normalised name): a name this customer ALSO went by —
--     written when an upload's sale shares a phone line with the customer under another name within two years
--     (customer_identity.decide rule b), and when one customer is merged into another (the merged one's name).
--     alias_norm is customer_identity.norm_name(alias_name) — the matcher compares it.
--   · pos.customers.merged_into — the customer this record was merged into (NULL = not merged). A merged record is
--     kept (inactive), never deleted, so a merge can be undone.
--   · pos.customers.merge_record — what the merge moved ({into, at, by, reason, moved: {table: [row ids]},
--     was_active, alias_added}) — un-merge puts exactly those rows back.
--
-- The code works BEFORE this is applied: every reader probes these per column (core.column_tolerant) and, when
-- they are absent, the matcher still matches / combines (on the customer's phone lines, pos.activations) but does
-- not record the other name, merge / un-merge / the dedupe cleanup refuse, and each answer says "apply migration
-- 1017".
--
-- REQUIRES mig 728 (UNIQUE (org_id, id) on pos.customers — the target of the tenant-scoped foreign keys below).

DO $guard$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'customers_org_id_uniq' AND conrelid = 'pos.customers'::regclass) THEN
    RAISE EXCEPTION 'Migration 1017 needs migration 728 (pos.customers UNIQUE (org_id, id)) — apply 728 first.';
  END IF;
END $guard$;

-- ── 1. the other names a customer went by ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pos.customer_aliases (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  customer_id UUID NOT NULL,
  alias_name TEXT NOT NULL,                -- as it was written on the sale / the merged record
  alias_norm TEXT NOT NULL,                -- customer_identity.norm_name(alias_name) — what the matcher compares
  source TEXT,                             -- 'upload' (combined on a shared phone line) | 'merge'
  first_seen DATE,
  last_seen DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT customer_aliases_org_customer_norm_uniq UNIQUE (org_id, customer_id, alias_norm),
  CONSTRAINT customer_aliases_customer_id_fkey
    FOREIGN KEY (org_id, customer_id) REFERENCES pos.customers (org_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS pos_customer_aliases_norm ON pos.customer_aliases (org_id, alias_norm);
CREATE INDEX IF NOT EXISTS pos_customer_aliases_customer ON pos.customer_aliases (org_id, customer_id);

-- ── 2. a merge, recorded so it can be undone ───────────────────────────────────────────────────────────
ALTER TABLE pos.customers ADD COLUMN IF NOT EXISTS merged_into UUID NULL;
ALTER TABLE pos.customers ADD COLUMN IF NOT EXISTS merge_record JSONB NULL;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'customers_merged_into_fkey' AND conrelid = 'pos.customers'::regclass) THEN
    -- tenant-scoped like mig 728; SET NULL on the referencing column only (PostgreSQL 15+ column-list syntax, as 728)
    ALTER TABLE pos.customers ADD CONSTRAINT customers_merged_into_fkey
      FOREIGN KEY (org_id, merged_into) REFERENCES pos.customers (org_id, id) ON DELETE SET NULL (merged_into);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS pos_customers_merged_into ON pos.customers (org_id, merged_into) WHERE merged_into IS NOT NULL;

-- ── 3. the customer page's reads: a customer's lines, a sale's lines ───────────────────────────────────
CREATE INDEX IF NOT EXISTS pos_activations_org_customer ON pos.activations (org_id, customer_id);
CREATE INDEX IF NOT EXISTS pos_activations_org_sale ON pos.activations (org_id, sale_id);
CREATE INDEX IF NOT EXISTS pos_sales_org_customer ON pos.sales (org_id, customer_id);

-- ── Security: re-assert the mig 725 lockdown over the WHOLE pos schema (same block, idempotent) ──────────
-- Backend-only access: service_role bypasses RLS; anon/authenticated get nothing. Picks up customer_aliases.
DO $$
DECLARE t RECORD;
BEGIN
  EXECUTE 'GRANT USAGE ON SCHEMA pos TO service_role';
  EXECUTE 'REVOKE ALL ON SCHEMA pos FROM anon, authenticated';
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'pos' LOOP
    EXECUTE format('ALTER TABLE pos.%I ENABLE ROW LEVEL SECURITY', t.tablename);
  END LOOP;
  EXECUTE 'REVOKE ALL ON ALL TABLES    IN SCHEMA pos FROM anon, authenticated';
  EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA pos FROM anon, authenticated';
  EXECUTE 'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA pos FROM anon, authenticated';
  EXECUTE 'GRANT ALL ON ALL TABLES    IN SCHEMA pos TO service_role';
  EXECUTE 'GRANT ALL ON ALL SEQUENCES IN SCHEMA pos TO service_role';
  -- NO schema-wide function grant here: it re-opened the PII functions migration 909 switched off (repaired by
  -- 1019, locked by backend/harness_pos_grants_lock.py). This migration creates no function, so it grants none.
END $$;
-- pos.pii_key must stay callable ONLY from inside the sibling definer functions (as mig 725 leaves it).
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'pos' AND p.proname = 'pii_key') THEN
    REVOKE ALL ON FUNCTION pos.pii_key() FROM service_role;
  END IF;
END $$;

-- REVERT (only after un-merging every merged customer — a merged record's sales now sit on the survivor, and
-- dropping merge_record loses the list that puts them back):
--   DROP INDEX IF EXISTS pos.pos_sales_org_customer;
--   DROP INDEX IF EXISTS pos.pos_activations_org_sale;
--   DROP INDEX IF EXISTS pos.pos_activations_org_customer;
--   DROP INDEX IF EXISTS pos.pos_customers_merged_into;
--   ALTER TABLE pos.customers DROP CONSTRAINT IF EXISTS customers_merged_into_fkey;
--   ALTER TABLE pos.customers DROP COLUMN IF EXISTS merge_record;
--   ALTER TABLE pos.customers DROP COLUMN IF EXISTS merged_into;
--   DROP TABLE IF EXISTS pos.customer_aliases;
