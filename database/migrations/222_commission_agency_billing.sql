-- MIGRATION 222: AGENCY MODULE (Phase 1) — INVOICING + EQUIPMENT-TRANSFER INTAKE
-- Band 200–299 (mod-commission). Additive + idempotent. REQUIRES mig 220 first (FKs → agency_link).
-- Design: docs/designs/agency-module-schema.md (REV C). org_id = the MASTER agent's org.
--
-- WHAT: the MASTER's bill to the SA (agency_invoice + agency_invoice_line) and the MASTER→SA equipment
-- movement the margin billing consumes (agency_equipment_transfer: feed | ocr | manual). agency_invoice is
-- created BEFORE agency_equipment_transfer because the transfer's billed_invoice_id FKs the invoice.
--
-- N3 (owner 2026-07-18): OCR rows land confirm_status='unconfirmed' and must be human-confirmed (gated on the
-- 'agency' setting area) before they bill; a period bills CONFIRMED rows only and rolls UNCONFIRMED forward.
-- N1: doc_path/tax_exempt_doc_path are SOFT refs into the (core-provisioned) agency-docs bucket.
--
-- SECURITY — DELIBERATELY service-role only (same rationale + notify.send_artifact precedent as mig 220):
-- invoices carry money. RLS enabled, NO open_all policy, REVOKE from anon/authenticated.
--
-- DEGRADES GRACEFULLY: until this runs, invoice/transfer endpoints return a "run migration 222" notice (400).

-- ── 8) agency_invoice — the MASTER's bill to the SA (margins + fees + other + tax). Frozen at issue. ──
CREATE TABLE IF NOT EXISTS commcalc.agency_invoice (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  link_id                UUID NOT NULL REFERENCES commcalc.agency_link(id) ON DELETE CASCADE,
  period                 TEXT NOT NULL,             -- 'June 2026' (read via _pvariants for the 'YYYY-MM' duality)
  period_start           DATE,
  period_end             DATE,
  status                 TEXT NOT NULL DEFAULT 'draft',   -- 'draft' | 'issued' | 'paid' | 'void'
  equipment_margin_total NUMERIC NOT NULL DEFAULT 0,
  store_fee_total        NUMERIC NOT NULL DEFAULT 0,
  other_charge_total     NUMERIC NOT NULL DEFAULT 0,
  holdback_total_memo    NUMERIC NOT NULL DEFAULT 0,  -- informational (Q3: holdback netted in settlement, Phase 2)
  subtotal               NUMERIC NOT NULL DEFAULT 0,  -- margins + fees + other
  taxable_snapshot       BOOLEAN NOT NULL DEFAULT false, -- Q1: link.taxable frozen at generation
  tax_rate_snapshot      NUMERIC NOT NULL DEFAULT 0,     -- Q1: link.tax_rate frozen at generation
  tax_total              NUMERIC NOT NULL DEFAULT 0,     -- taxable_snapshot ? tax_rate_snapshot × subtotal : 0
  total                  NUMERIC NOT NULL DEFAULT 0,     -- subtotal + tax_total
  issued_at              TIMESTAMPTZ,
  due_date               DATE,
  paid_at                TIMESTAMPTZ,
  payment_ref            TEXT,
  regenerated_at         TIMESTAMPTZ,                 -- last draft recompute
  notes                  TEXT,
  created_by             TEXT,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agency_invoice_lk     ON commcalc.agency_invoice (org_id, link_id, period);
CREATE INDEX IF NOT EXISTS agency_invoice_status ON commcalc.agency_invoice (org_id, status);

-- ── 9) agency_invoice_line — one row per computed charge; method/value/qty snapshotted at generation ──
CREATE TABLE IF NOT EXISTS commcalc.agency_invoice_line (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  invoice_id       UUID NOT NULL REFERENCES commcalc.agency_invoice(id) ON DELETE CASCADE,
  source_type      TEXT NOT NULL,                 -- 'equipment_margin' | 'store_fee' | 'other_charge' | 'holdback'(memo)
  source_id        UUID,                          -- agency_equipment_margin / _charge (/ holdback_rule) row
  transfer_id      UUID,                          -- agency_equipment_transfer row billed (equipment_margin lines)
  link_store_id    UUID,                          -- agency_link_store when per-store
  carrier_id       UUID,
  description      TEXT,
  qty              NUMERIC NOT NULL DEFAULT 1,
  unit_amount      NUMERIC NOT NULL DEFAULT 0,
  method           TEXT,                           -- snapshot
  value            NUMERIC,                        -- snapshot ($ or %)
  proration_factor NUMERIC NOT NULL DEFAULT 1,     -- Q2: active_days / period_days (1 = full)
  amount           NUMERIC NOT NULL DEFAULT 0,     -- computed line total
  sort             INT NOT NULL DEFAULT 0,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agency_invoice_line_inv ON commcalc.agency_invoice_line (org_id, invoice_id, sort);

-- ── 7) agency_equipment_transfer — MASTER→SA equipment movement that margin billing consumes (Q5/N3) ──
CREATE TABLE IF NOT EXISTS commcalc.agency_equipment_transfer (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  link_id            UUID NOT NULL REFERENCES commcalc.agency_link(id) ON DELETE CASCADE,
  link_store_id      UUID REFERENCES commcalc.agency_link_store(id) ON DELETE SET NULL,  -- destination store (NULL = link-wide)
  carrier_id         UUID REFERENCES commcalc.carrier(id) ON DELETE SET NULL,
  period             TEXT,                          -- billing period the transfer falls in ('June 2026')
  transfer_date      DATE,
  equip_class_value  TEXT,                          -- product-class ref (matches agency_equipment_margin)
  product_ref        TEXT,                          -- sku / product_id / product_desc (SOFT ref to raw_catalog)
  product_desc       TEXT,
  qty                NUMERIC NOT NULL DEFAULT 0,
  unit_cost          NUMERIC NOT NULL DEFAULT 0,    -- the master's cost (margin basis when markup_basis='cost')
  source             TEXT NOT NULL DEFAULT 'feed',  -- 'feed' | 'ocr' | 'manual'
  doc_path           TEXT,                          -- SOFT agency-docs ref to the uploaded invoice (when source='ocr')
  doc_name           TEXT,
  ocr_confidence     NUMERIC,                        -- parser confidence 0..1 (source='ocr')
  ocr_model          TEXT,                           -- the model that interpreted it (accounts-module precedent)
  confirm_status     TEXT NOT NULL DEFAULT 'confirmed', -- 'unconfirmed' | 'confirmed' | 'rejected'; OCR path sets 'unconfirmed'
  confirmed_by       TEXT,
  confirmed_at       TIMESTAMPTZ,
  billed_invoice_id  UUID REFERENCES commcalc.agency_invoice(id) ON DELETE SET NULL,  -- set at ISSUE (idempotency)
  notes              TEXT,
  created_by         TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agency_equip_transfer_lk  ON commcalc.agency_equipment_transfer (org_id, link_id, period, confirm_status);
CREATE INDEX IF NOT EXISTS agency_equip_transfer_bill ON commcalc.agency_equipment_transfer (org_id, link_id, billed_invoice_id);

-- ── SECURITY LOCKDOWN — service-role only on all three (see header) ──────────────────────────────────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'commcalc.agency_invoice','commcalc.agency_invoice_line','commcalc.agency_equipment_transfer'
  ] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('REVOKE ALL ON %s FROM anon, authenticated', t);
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 222 complete — agency invoice/line + equipment_transfer, service-role only' AS status;
