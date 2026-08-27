"""Backfill: encrypt existing plaintext carrier account PINs in pos.customers.password.

The app now writes pos.customers.password (the carrier account PIN — the SIM-swap / account-takeover
credential) as application-layer ciphertext ('enc:v1:'). Rows created before that are still plaintext.
This one-off pass seals them. Reads keep working throughout: crypto.decrypt returns legacy plaintext
(no prefix) unchanged, so nothing breaks before or during the backfill.

Idempotent & safe to re-run: an already-sealed value ('enc:v1:' prefix) is skipped.

Run on the backend host (needs FIELD_ENCRYPTION_KEY + SUPABASE_SERVICE_KEY in the environment):

    cd backend && python -m app.scripts.backfill_customer_pin_encryption           # apply
    cd backend && python -m app.scripts.backfill_customer_pin_encryption --dry-run  # report only
"""
from __future__ import annotations

import sys

from app.core import crypto
from app.core.database import get_supabase

_PAGE = 500


def main(dry_run: bool = False) -> int:
    if not crypto.is_enabled():
        print("FIELD_ENCRYPTION_KEY is not configured — refusing to backfill (would write plaintext).")
        return 2
    sb = get_supabase()
    tbl = sb.schema("pos").table("customers")
    scanned = sealed = 0
    offset = 0
    while True:
        # Narrow select — never pull the whole customer book; just the id + the one field we seal.
        rows = (tbl.select("id,password").order("created_at", desc=False)
                .range(offset, offset + _PAGE - 1).execute().data) or []
        if not rows:
            break
        for row in rows:
            scanned += 1
            pin = row.get("password")
            if not pin or crypto.is_encrypted(pin):
                continue
            sealed += 1
            if dry_run:
                continue
            tbl.update({"password": crypto.encrypt(pin)}).eq("id", row["id"]).execute()
        offset += len(rows)
        if len(rows) < _PAGE:
            break
    verb = "would seal" if dry_run else "sealed"
    print(f"scanned={scanned} {verb}={sealed}")
    return 0


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv))
