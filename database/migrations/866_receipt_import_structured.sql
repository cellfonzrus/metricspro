-- MIGRATION 866: STRUCTURED, PER-POS, EDITABLE + REPRINTABLE RECEIPT IMPORT
-- ─────────────────────────────────────────────────────────────────────────────────────────────
-- Owner request (2026-08): tenants upload receipts from different POS systems (RQ / Wireless Zone,
-- B2B / TCC, …), each with a DIFFERENT layout. On upload the tenant picks which POS it came from;
-- the receipt is parsed into ONE editable structured Document (header, item lines, totals, sections,
-- legal footer). Description / qty / tax / pricing stay editable, and the Document can be REPRINTED
-- later in the SAME format. Nothing about a format is hardcoded — see app/modules/pos/receipt_formats.
--
-- Additive only: four nullable columns on the existing pos.receipt_imports ledger (migration 864).
-- The `document` JSONB is the editable + reprintable record; the existing denormalized columns
-- (imei/phone/customer_name/device_name/total/sale_date) remain the search keys, derived from it.

ALTER TABLE pos.receipt_imports ADD COLUMN IF NOT EXISTS pos_source TEXT;    -- 'rq' | 'b2b' | …
ALTER TABLE pos.receipt_imports ADD COLUMN IF NOT EXISTS invoice_no TEXT;    -- receipt/invoice/ref #
ALTER TABLE pos.receipt_imports ADD COLUMN IF NOT EXISTS salesperson TEXT;
ALTER TABLE pos.receipt_imports ADD COLUMN IF NOT EXISTS document JSONB;     -- editable structured doc

CREATE INDEX IF NOT EXISTS pos_receipt_imports_invoice
  ON pos.receipt_imports(org_id, invoice_no);
CREATE INDEX IF NOT EXISTS pos_receipt_imports_source
  ON pos.receipt_imports(org_id, pos_source, created_at DESC);

-- Optional per-tenant remembered POS choice for the upload picker's default. One row per org.
CREATE TABLE IF NOT EXISTS pos.receipt_import_prefs (
  org_id         UUID PRIMARY KEY,
  default_source TEXT,
  updated_at     TIMESTAMPTZ DEFAULT NOW()
);
