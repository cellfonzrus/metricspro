-- 301_asset_purchase_orders.sql — mod-asset: Purchase Orders module (band 300–399).
--
-- WHAT: a real-world-shaped PO lifecycle sitting alongside asset_ledger/device_payable_ledger, purpose-built
-- for OWNER-DISPATCHED BUILD 2026-07-23 ("Purchase Orders" under the asset domain):
--   1) commcalc.po_vendor          — per-org vendor/supplier roster (pick-don't-type source, RULE THREE).
--   2) commcalc.po_number_seq + commcalc.next_po_number() — atomic per-org, per-year PO numbering
--      (PO-<year>-<0001>), via one UPSERT...RETURNING so two concurrent "Create PO" clicks for the same org
--      can never collide on a number (a plain SELECT max()+1 in application code would race).
--   3) commcalc.purchase_order + commcalc.purchase_order_line — the PO header (vendor, ship-to store/market,
--      buyer, status lifecycle draft→submitted→partially_received→received→closed→cancelled, dates,
--      subtotal/total) and its line items (sku/model, qty ordered, unit/extended cost, qty received).
--   4) commcalc.po_receipt + commcalc.po_receipt_unit — receiving events against one PO line (qty + date,
--      partial receipts supported by inserting multiple receipt rows over time) with OPTIONAL per-unit
--      IMEI/serial capture (po_receipt_unit) — capturing serials is what lets the Sold Tally / Aging reports
--      join a received unit to commcalc.raw_sales / ePay commcalc.raw_payment_detail by IMEI (exact-confidence
--      match); units received without a serial still count for aging/tally at a lower, explicitly-labeled
--      confidence (qty-window estimate against the same SKU/store), never silently dropped.
--   5) commcalc.po_settings — one row per org, aging_flag_days (management-defined "how many days unsold
--      after receiving is too long", default 10) — a tenant-configurable threshold, not a hardcoded constant
--      (RULE TWO), editable from the module's own Purchase Orders → Aging page (admin-gated).
--
-- ORG-SCOPE: every table carries org_id uuid not null + an index on it (RULE ONE). next_po_number() and every
-- application write are org_id-scoped; a PO/vendor/receipt can never be created under the wrong tenant.
--
-- DEGRADE GRACEFULLY (contract §5): backend/app/modules/asset/router.py wraps every PO read/write in
-- try/except and returns a clear "Purchase Orders migration pending — ask the operator to run 301" message
-- instead of a 500 until this runs; no other asset page depends on these tables, so nothing else can break.
-- Additive + idempotent (IF NOT EXISTS / CREATE OR REPLACE): safe to re-run.

CREATE TABLE IF NOT EXISTS commcalc.po_vendor (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  name           TEXT NOT NULL,
  contact_name   TEXT,
  email          TEXT,
  phone          TEXT,
  terms          TEXT,          -- free-form ("Net 30", "COD", …) — mirrors payables.distributors.terms_days loosely, kept independent (a PO vendor need not be a configured distributor)
  notes          TEXT,
  is_active      BOOLEAN NOT NULL DEFAULT true,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_po_vendor_org ON commcalc.po_vendor (org_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_po_vendor_org_name ON commcalc.po_vendor (org_id, lower(name));

-- Atomic per-org, per-year PO number sequence.
CREATE TABLE IF NOT EXISTS commcalc.po_number_seq (
  org_id       UUID NOT NULL,
  seq_year     INT NOT NULL,
  last_number  INT NOT NULL DEFAULT 0,
  PRIMARY KEY (org_id, seq_year)
);

CREATE OR REPLACE FUNCTION commcalc.next_po_number(p_org_id UUID, p_year INT DEFAULT NULL)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
  v_year INT := COALESCE(p_year, EXTRACT(year FROM now())::INT);
  v_n    INT;
BEGIN
  INSERT INTO commcalc.po_number_seq (org_id, seq_year, last_number)
  VALUES (p_org_id, v_year, 1)
  ON CONFLICT (org_id, seq_year)
  DO UPDATE SET last_number = commcalc.po_number_seq.last_number + 1
  RETURNING last_number INTO v_n;
  RETURN 'PO-' || v_year::TEXT || '-' || lpad(v_n::TEXT, 4, '0');
END;
$$;

CREATE TABLE IF NOT EXISTS commcalc.purchase_order (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                  UUID NOT NULL,
  po_number               TEXT NOT NULL,
  order_date              DATE NOT NULL DEFAULT current_date,
  vendor_id               UUID REFERENCES commcalc.po_vendor(id),
  vendor_name_snapshot    TEXT,          -- vendor's name AT CREATE TIME, so a later vendor-record edit never rewrites history on an already-issued PO
  ship_to_store           TEXT,
  market                  TEXT,
  buyer                   TEXT,          -- display name of the creating user (auto from the session, not typed)
  status                  TEXT NOT NULL DEFAULT 'draft',   -- draft | submitted | partially_received | received | closed | cancelled
  subtotal                NUMERIC(12,2) NOT NULL DEFAULT 0,
  total                   NUMERIC(12,2) NOT NULL DEFAULT 0,
  expected_delivery_date  DATE,
  notes                   TEXT,
  source                  TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'forecast' (created from the recommendation proposal)
  created_by              TEXT,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_po_org ON commcalc.purchase_order (org_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_po_org_number ON commcalc.purchase_order (org_id, po_number);
CREATE INDEX IF NOT EXISTS ix_po_org_status ON commcalc.purchase_order (org_id, status);

CREATE TABLE IF NOT EXISTS commcalc.purchase_order_line (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  po_id          UUID NOT NULL REFERENCES commcalc.purchase_order(id) ON DELETE CASCADE,
  line_no        INT NOT NULL DEFAULT 1,
  sku            TEXT,
  device_model   TEXT NOT NULL,
  qty_ordered    INT NOT NULL DEFAULT 0,
  unit_cost      NUMERIC(12,2) NOT NULL DEFAULT 0,
  extended_cost  NUMERIC(12,2) NOT NULL DEFAULT 0,
  qty_received   INT NOT NULL DEFAULT 0,
  store          TEXT,          -- denormalized copy of the header's ship_to_store at line-create time (report convenience)
  market         TEXT,
  notes          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_pol_org ON commcalc.purchase_order_line (org_id);
CREATE INDEX IF NOT EXISTS ix_pol_po ON commcalc.purchase_order_line (po_id);

CREATE TABLE IF NOT EXISTS commcalc.po_receipt (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  po_id          UUID NOT NULL REFERENCES commcalc.purchase_order(id) ON DELETE CASCADE,
  po_line_id     UUID NOT NULL REFERENCES commcalc.purchase_order_line(id) ON DELETE CASCADE,
  received_date  DATE NOT NULL DEFAULT current_date,
  qty_received   INT NOT NULL DEFAULT 0,
  received_by    TEXT,
  notes          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_por_org ON commcalc.po_receipt (org_id);
CREATE INDEX IF NOT EXISTS ix_por_po ON commcalc.po_receipt (po_id);
CREATE INDEX IF NOT EXISTS ix_por_line ON commcalc.po_receipt (po_line_id);

CREATE TABLE IF NOT EXISTS commcalc.po_receipt_unit (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  receipt_id     UUID NOT NULL REFERENCES commcalc.po_receipt(id) ON DELETE CASCADE,
  po_line_id     UUID NOT NULL REFERENCES commcalc.purchase_order_line(id) ON DELETE CASCADE,
  imei           TEXT,
  serial         TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_poru_org ON commcalc.po_receipt_unit (org_id);
CREATE INDEX IF NOT EXISTS ix_poru_receipt ON commcalc.po_receipt_unit (receipt_id);
CREATE INDEX IF NOT EXISTS ix_poru_imei ON commcalc.po_receipt_unit (org_id, imei);

CREATE TABLE IF NOT EXISTS commcalc.po_settings (
  org_id           UUID PRIMARY KEY,
  aging_flag_days  INT NOT NULL DEFAULT 10,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Same "open_all, backend enforces org scoping via the query param" convention as the rest of commcalc
-- (see 002_commcalc.sql, 300_asset_ledger_staging_swap.sql) — NOT the deliberately-locked-down
-- service-role-only convention used by agency's between-tenant money-config tables (mig 220/222); PO data
-- is ordinary per-tenant transactional data, same sensitivity class as asset_ledger.
DO $$
DECLARE
  t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'commcalc.po_vendor', 'commcalc.po_number_seq', 'commcalc.purchase_order',
    'commcalc.purchase_order_line', 'commcalc.po_receipt', 'commcalc.po_receipt_unit',
    'commcalc.po_settings'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
  END LOOP;
END $$;

GRANT ALL ON commcalc.po_vendor, commcalc.po_number_seq, commcalc.purchase_order,
  commcalc.purchase_order_line, commcalc.po_receipt, commcalc.po_receipt_unit, commcalc.po_settings
  TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION commcalc.next_po_number(UUID, INT) TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT '301 complete — Purchase Orders module (po_vendor, purchase_order(+line), po_receipt(+unit), po_settings, next_po_number())' AS status;
