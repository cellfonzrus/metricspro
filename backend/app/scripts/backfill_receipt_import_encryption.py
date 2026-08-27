"""Backfill: encrypt existing pos.receipt_imports rows and populate their blind-index columns.

Migration 865 adds the *_bidx columns and the application now writes the PII columns as ciphertext,
but rows created BEFORE the key was set are still plaintext with empty *_bidx. This one-off pass
re-encrypts them and computes their blind indexes so old imports are protected AND searchable.

Idempotent & safe to re-run: a row is only rewritten when at least one PII column is still plaintext
(no 'enc:v1:' prefix) OR a blind-index column is missing. Already-encrypted rows are skipped.

Run on the backend host (needs FIELD_ENCRYPTION_KEY + SUPABASE_SERVICE_KEY in the environment):

    cd backend && python -m app.scripts.backfill_receipt_import_encryption          # apply
    cd backend && python -m app.scripts.backfill_receipt_import_encryption --dry-run # report only
"""
from __future__ import annotations

import sys

from app.core import crypto
from app.core.database import get_supabase
from app.modules.pos import receipt_import as R

_PII = R._ENCRYPTED_IMPORT_COLUMNS
_PAGE = 500


def _needs_work(row: dict) -> bool:
    """A row still needs backfilling if any PII column is plaintext, a blob is unwrapped, or a
    blind-index column that should exist is empty."""
    for col in _PII:
        v = row.get(col)
        if isinstance(v, str) and v.strip() and not crypto.is_encrypted(v):
            return True
    for blob in ("parsed", "raw_ocr"):
        v = row.get(blob)
        if isinstance(v, dict) and set(v.keys()) != {"enc"} and v:
            return True
    if row.get("phone") and not row.get("phone_bidx"):
        return True
    if row.get("imei") and not row.get("imei_bidx"):
        return True
    if (row.get("customer_name") or row.get("device_name")) and not row.get("search_bidx"):
        return True
    return False


def _plain_row(row: dict) -> dict:
    """Decrypt back to plaintext PII (so re-encryption is uniform whether the row is plain or already
    partially encrypted), then re-run the standard encrypt+index path."""
    plain = R.decrypt_receipt_row(row) or {}
    imp = {c: plain.get(c) for c in _PII}
    imp["parsed"] = plain.get("parsed")
    imp["raw_ocr"] = plain.get("raw_ocr")
    return R._encrypt_import_row(imp)


def main(dry_run: bool = False) -> int:
    if not crypto.is_enabled():
        print("FIELD_ENCRYPTION_KEY is not configured — refusing to backfill (would write plaintext).")
        return 2
    sb = get_supabase()
    tbl = sb.schema("pos").table("receipt_imports")
    scanned = updated = 0
    offset = 0
    while True:
        rows = (tbl.select("*").order("created_at", desc=False)
                .range(offset, offset + _PAGE - 1).execute().data) or []
        if not rows:
            break
        for row in rows:
            scanned += 1
            if not _needs_work(row):
                continue
            patch = _plain_row(row)
            updated += 1
            if dry_run:
                continue
            tbl.update(patch).eq("id", row["id"]).execute()
        offset += len(rows)
        if len(rows) < _PAGE:
            break
    verb = "would re-encrypt" if dry_run else "re-encrypted"
    print(f"scanned={scanned} {verb}={updated}")
    return 0


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv))
