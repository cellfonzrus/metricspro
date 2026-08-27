-- MIGRATION 865: ENCRYPT RECEIPT-IMPORT PII AT REST + BLIND-INDEX SEARCH
-- ─────────────────────────────────────────────────────────────────────────────────────────────
-- Owner request (2026-08): "make sure all uploaded data or any data in the system is fully
-- encrypted so even if somebody hacks it and exports the data it cannot be used anywhere."
--
-- pos.receipt_imports (migration 864) stored the photographed receipt's PII — customer name,
-- phone, IMEI, device — as PLAINTEXT in denormalized columns and inside the parsed/raw_ocr JSONB.
-- The application now writes those columns as application-layer CIPHERTEXT (app.core.crypto,
-- 'enc:v1:' Fernet envelope), so a raw DB export / leaked service-role key yields only opaque
-- tokens. An encrypted column can't be searched, so we add keyed HMAC BLIND-INDEX columns: the
-- database holds an irreversible token (not the value), and lookups match on the token.
--
-- Additive only: three nullable columns + their indexes. No data is rewritten by this migration —
-- new imports populate the columns; existing rows are re-encrypted by the backfill (below).

-- Exact-match tokens (keyed HMAC of the normalized value):
ALTER TABLE pos.receipt_imports ADD COLUMN IF NOT EXISTS phone_bidx  TEXT;  -- HMAC(digits(phone))
ALTER TABLE pos.receipt_imports ADD COLUMN IF NOT EXISTS imei_bidx   TEXT;  -- HMAC(digits(imei))
-- Word-level tokens across customer_name + device_name (space-joined HMAC per word), so a partial
-- name/device search still works over the ciphertext (searching 'smith' matches 'John Smith'):
ALTER TABLE pos.receipt_imports ADD COLUMN IF NOT EXISTS search_bidx TEXT;

CREATE INDEX IF NOT EXISTS pos_receipt_imports_phone_bidx
  ON pos.receipt_imports(org_id, phone_bidx);
CREATE INDEX IF NOT EXISTS pos_receipt_imports_imei_bidx
  ON pos.receipt_imports(org_id, imei_bidx);

-- NOTE (operator): the OLD plaintext indexes from migration 864 on imei / phone / lower(customer_name)
-- now index ciphertext and are dead weight — harmless, and kept so this migration stays purely
-- additive. They can be dropped in a later cleanup once all rows are re-encrypted.
--
-- BACKFILL of existing rows (encrypt the plaintext columns + populate the *_bidx columns) is done in
-- the application, not SQL, because both the Fernet key and the HMAC key live only in the backend:
--   python -m app.scripts.backfill_receipt_import_encryption      (see backend/app/scripts/)
-- Safe to run repeatedly: already-encrypted values ('enc:v1:' prefix) are skipped.
