-- MIGRATION 864: POS RECEIPT IMPORT (secondary-POS receipt capture → sales record)
-- ─────────────────────────────────────────────────────────────────────────────────────────────
-- Owner request (2026-08): a store using MetricsPro as a SECONDARY POS wants to bring its
-- primary-system sales in by photographing the paper/printed receipt. The photo is OCR'd
-- (Claude vision, same engine as the closing deposit-slip reader), the fields are matched to the
-- POS tables, and a normal sale is created in the tenant's OWN receipt series — searchable by
-- IMEI, phone and customer name, with an optional note that also lands on the customer.
--
-- Design decisions baked in here:
--   • An imported receipt becomes a FIRST-CLASS pos.sales row (its own transaction_id series) —
--     not a separate silo — flagged with pos.sales.source = 'receipt_import'.
--   • pos.sale_items.product_id is a NOT-NULL FK, so OCR lines that don't map to a catalog product
--     are preserved in pos.sales.receipt (JSONB) rather than forced into a fake product. IMEI/phone/
--     customer are ALSO denormalized onto pos.receipt_imports for fast, indexed search.
--   • The uploaded image + raw model JSON + normalized fields are retained on pos.receipt_imports
--     for audit and re-review.
-- Additive only: a new table + one nullable column. No existing row or constraint changes.

-- 1) Mark where a sale came from. NULL / 'native' = a real in-app checkout (today's behavior);
--    'receipt_import' = created from a photographed receipt. Nullable so no backfill is needed.
ALTER TABLE pos.sales ADD COLUMN IF NOT EXISTS source TEXT;

-- 2) The receipt-import ledger: one row per photographed receipt.
CREATE TABLE IF NOT EXISTS pos.receipt_imports (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  store_code   TEXT,
  sale_id      UUID REFERENCES pos.sales(id) ON DELETE SET NULL,      -- the created sale
  customer_id  UUID REFERENCES pos.customers(id) ON DELETE SET NULL,  -- matched/created customer
  status       TEXT NOT NULL DEFAULT 'imported'
               CHECK (status IN ('parsed','imported','failed','needs_review','voided')),
  image_path   TEXT,        -- storage path of the uploaded receipt photo (audit)
  raw_ocr      JSONB,       -- the vision model's raw JSON response
  parsed       JSONB,       -- normalized fields (items, totals, etc.)
  notes        TEXT,        -- note entered at upload (also copied to the customer's notes)
  -- Denormalized search keys (also present inside `parsed`, duplicated here for indexed lookup):
  imei          TEXT,       -- primary device IMEI/serial off the receipt
  phone         TEXT,       -- customer phone (digits)
  customer_name TEXT,
  device_name   TEXT,       -- primary device/product description
  total         NUMERIC(12,2),
  sale_date     DATE,
  uploaded_by   TEXT,       -- storeops employee_id of the uploader
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS pos_receipt_imports_org_created
  ON pos.receipt_imports(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS pos_receipt_imports_imei
  ON pos.receipt_imports(org_id, imei);
CREATE INDEX IF NOT EXISTS pos_receipt_imports_phone
  ON pos.receipt_imports(org_id, phone);
CREATE INDEX IF NOT EXISTS pos_receipt_imports_name
  ON pos.receipt_imports(org_id, lower(customer_name));
CREATE INDEX IF NOT EXISTS pos_receipt_imports_store
  ON pos.receipt_imports(org_id, store_code, created_at DESC);
