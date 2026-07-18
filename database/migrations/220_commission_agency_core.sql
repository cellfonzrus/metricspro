-- MIGRATION 220: AGENCY MODULE (Phase 1) — CORE CONFIG TABLES
-- Band 200–299 (mod-commission). Additive + idempotent (safe to re-run).
-- Design: docs/designs/agency-module-schema.md (REV C). org_id = the MASTER agent's org.
--
-- WHAT: the Master-Agent → Sub-Agent relationship + its money CONFIG — link, per-carrier scoping, the
-- sub's store roster (attribution key + charge scope), commission holdback rules, equipment-margin markup
-- rules, and the free-form/recurring charge catalog (incl. the per-store monthly flat fee). No settlement
-- engine here (Phase 2 = tables 10–11); no invoicing tables here (mig 222). NO seed rows.
--
-- SECURITY — DELIBERATELY service-role only (breaks the old commcalc `open_all` convention on purpose):
-- these tables hold BETWEEN-tenant money configuration (holdback %, equipment markup, fees). They must NOT
-- be reachable with the public anon key. Following the notify.send_artifact precedent (mig 713): RLS is
-- ENABLED with NO permissive policy and NO grants to anon/authenticated, so PostgREST denies all
-- anon/authenticated access; the backend reaches them ONLY through the service role (which bypasses RLS).
-- Idempotent: strip any open_all policy / anon-authenticated grant a prior run or a schema-wide statement
-- may have attached.
--
-- DEGRADES GRACEFULLY: until this runs, every agency endpoint's try/except returns a "run migration 220"
-- notice (400, never 500) and no unrelated page breaks.

-- ── 1) agency_link — one row per (master org → sub agent) relationship ──────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.agency_link (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                  UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',  -- the MASTER agent's org
  sub_kind                TEXT NOT NULL DEFAULT 'tenant',     -- 'tenant' | 'external'
  sub_org_id              UUID,                               -- storeops.tenants.org_id when tenant (SOFT ref)
  sub_name                TEXT NOT NULL,                      -- external party name; cached display for a tenant sub
  sub_contact_name        TEXT,
  sub_contact_email       TEXT,
  sub_contact_phone       TEXT,
  bill_company_id         UUID REFERENCES commcalc.companies(id) ON DELETE SET NULL,  -- which master legal entity bills
  status                  TEXT NOT NULL DEFAULT 'draft',      -- 'draft' | 'active' | 'suspended' | 'ended'
  taxable                 BOOLEAN NOT NULL DEFAULT false,     -- Q1: unchecked = wholesale/exempt (no tax)
  tax_rate                NUMERIC NOT NULL DEFAULT 0,         -- e.g. 0.06 = 6% applied to the invoice total when taxable
  tax_exempt_doc_path     TEXT,                               -- SOFT ref to the resale/exemption cert (agency-docs bucket, N1)
  tax_exempt_doc_name     TEXT,
  tax_exempt_doc_at       TIMESTAMPTZ,
  default_proration_mode  TEXT NOT NULL DEFAULT 'full',       -- Q2: 'full' | 'prorated' (charge inherits unless set)
  sub_portal_scope        TEXT NOT NULL DEFAULT 'totals',     -- Q7: FIXED to 'totals' in v1
  holdback_visible_to_sub BOOLEAN NOT NULL DEFAULT false,     -- Q3: sub sees NOTHING about holdbacks unless true
  rate_change_mode        TEXT NOT NULL DEFAULT 'period_anchor', -- Q9: 'period_anchor' | 'split_period'
  sub_consent_status      TEXT NOT NULL DEFAULT 'not_requested', -- 'not_requested'|'pending'|'accepted'|'declined'|'revoked'
  sub_consent_at          TIMESTAMPTZ,
  sub_consent_by          TEXT,
  effective_start         DATE,
  effective_end           DATE,                               -- NULL = open
  is_active               BOOLEAN NOT NULL DEFAULT true,
  notes                   TEXT,
  created_by              TEXT,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agency_link_org ON commcalc.agency_link (org_id, status);
CREATE INDEX IF NOT EXISTS agency_link_sub ON commcalc.agency_link (sub_org_id);  -- sub-side read + cycle guard

-- ── 2) agency_link_carrier — per-carrier SCOPING (zero rows = all carriers) ─────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.agency_link_carrier (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  link_id     UUID NOT NULL REFERENCES commcalc.agency_link(id) ON DELETE CASCADE,
  carrier_id  UUID NOT NULL REFERENCES commcalc.carrier(id) ON DELETE CASCADE,   -- picked (RULE THREE)
  is_active   BOOLEAN NOT NULL DEFAULT true,
  created_by  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, link_id, carrier_id)
);
CREATE INDEX IF NOT EXISTS agency_link_carrier_lk ON commcalc.agency_link_carrier (org_id, link_id);

-- ── 3) agency_link_store — the SA's store roster AS THE MASTER SEES IT (attribution key + charge scope) ─
CREATE TABLE IF NOT EXISTS commcalc.agency_link_store (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  link_id         UUID NOT NULL REFERENCES commcalc.agency_link(id) ON DELETE CASCADE,
  store_kind      TEXT NOT NULL DEFAULT 'storeops',    -- 'storeops' | 'store_mapping' | 'external'
  store_id        BIGINT,                              -- storeops.stores.id (SOFT ref)
  store_code      TEXT,                                -- store_mapping.store_code / raw_sales key
  store_address   TEXT,                                -- canonical store_mapping.store_address
  store_label     TEXT,                                -- free-form label for an external sub's store
  effective_start DATE,                                -- store's active-from (drives proration; Q2)
  effective_end   DATE,                                -- store's active-to  (NULL = open)
  is_active       BOOLEAN NOT NULL DEFAULT true,
  created_by      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, link_id, store_kind, store_code, store_address, store_label)
);
CREATE INDEX IF NOT EXISTS agency_link_store_lk   ON commcalc.agency_link_store (org_id, link_id);
CREATE INDEX IF NOT EXISTS agency_link_store_code ON commcalc.agency_link_store (org_id, store_code);

-- ── 4) agency_holdback_rule — commission the MASTER holds back, per link, per item scope (Phase 2 uses it) ─
CREATE TABLE IF NOT EXISTS commcalc.agency_holdback_rule (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  link_id         UUID NOT NULL REFERENCES commcalc.agency_link(id) ON DELETE CASCADE,
  scope_kind      TEXT NOT NULL DEFAULT 'all',    -- 'all'|'ledger_bucket'|'commission_component'|'statement_line_type'|'product_class'|'carrier'
  scope_value     TEXT,                           -- picked ref value (NULL for 'all'/'carrier'); prefilled from real data
  carrier_id      UUID REFERENCES commcalc.carrier(id) ON DELETE CASCADE,  -- NULL = any carrier
  method          TEXT NOT NULL DEFAULT 'percent',-- 'flat' | 'percent'
  value           NUMERIC NOT NULL DEFAULT 0,     -- 0.10 = 10% (percent) OR dollar amount (flat)
  percent_basis   TEXT NOT NULL DEFAULT 'scope_gross',  -- when percent: the bucket/line gross amount
  flat_per        TEXT NOT NULL DEFAULT 'activation',   -- Q4: 'activation' | 'line_item' | 'invoice'
  priority        INT NOT NULL DEFAULT 100,       -- tiebreaker within the SAME specificity: LOWER wins
  effective_start DATE,
  effective_end   DATE,
  is_active       BOOLEAN NOT NULL DEFAULT true,
  notes           TEXT,
  created_by      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agency_holdback_lk ON commcalc.agency_holdback_rule (org_id, link_id, scope_kind, priority);

-- ── 5) agency_equipment_margin — markup the MASTER bills on equipment (RATE config) ─────────────────
CREATE TABLE IF NOT EXISTS commcalc.agency_equipment_margin (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  link_id           UUID NOT NULL REFERENCES commcalc.agency_link(id) ON DELETE CASCADE,
  equip_class_kind  TEXT NOT NULL DEFAULT 'product_class',
  equip_class_value TEXT NOT NULL,                          -- 'device' | 'accessory' | tenant-named class (picked)
  carrier_id        UUID REFERENCES commcalc.carrier(id) ON DELETE CASCADE,  -- NULL = any carrier
  method            TEXT NOT NULL DEFAULT 'percent',        -- 'flat' | 'percent' MARKUP
  value             NUMERIC NOT NULL DEFAULT 0,             -- 0.15 = 15% markup (percent) OR $/unit (flat)
  markup_basis      TEXT NOT NULL DEFAULT 'cost',           -- when percent: 'cost' | 'ext_price' | 'gp'
  priority          INT NOT NULL DEFAULT 100,
  effective_start   DATE,
  effective_end     DATE,
  is_active         BOOLEAN NOT NULL DEFAULT true,
  notes             TEXT,
  created_by        TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agency_equip_margin_lk ON commcalc.agency_equipment_margin (org_id, link_id, equip_class_value);

-- ── 6) agency_charge — free-form recurring / other charge catalog, per link ─────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.agency_charge (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  link_id         UUID NOT NULL REFERENCES commcalc.agency_link(id) ON DELETE CASCADE,
  label           TEXT NOT NULL,                  -- 'Monthly store fee', 'Marketing co-op', 'Signage'…
  method          TEXT NOT NULL DEFAULT 'flat',   -- 'flat' | 'percent'
  value           NUMERIC NOT NULL DEFAULT 0,     -- $ (flat) OR fraction (percent)
  percent_basis   TEXT,                           -- when percent: 'invoice_subtotal'|'holdback_total'|'equipment_margin_total'
  cadence         TEXT NOT NULL DEFAULT 'monthly',-- 'monthly' | 'one_time' | 'per_invoice'
  proration_mode  TEXT NOT NULL DEFAULT 'default',-- Q2: 'default'(inherit link) | 'full' | 'prorated'
  link_store_id   UUID REFERENCES commcalc.agency_link_store(id) ON DELETE CASCADE,  -- NULL = link-wide
  effective_start DATE,
  effective_end   DATE,
  is_active       BOOLEAN NOT NULL DEFAULT true,
  notes           TEXT,
  created_by      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agency_charge_lk ON commcalc.agency_charge (org_id, link_id, cadence);

-- ── SECURITY LOCKDOWN — service-role only on all six (see header) ────────────────────────────────────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'commcalc.agency_link','commcalc.agency_link_carrier','commcalc.agency_link_store',
    'commcalc.agency_holdback_rule','commcalc.agency_equipment_margin','commcalc.agency_charge'
  ] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);   -- undo any prior open_all / schema-wide grant
    EXECUTE format('REVOKE ALL ON %s FROM anon, authenticated', t);
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 220 complete — agency core config (link/carrier/store/holdback/margin/charge), service-role only' AS status;
