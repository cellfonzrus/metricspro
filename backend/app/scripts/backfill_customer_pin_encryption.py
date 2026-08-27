"""Backfill: encrypt existing plaintext carrier account PINs in pos.customers.password.

The app now writes pos.customers.password (the carrier account PIN — the SIM-swap / account-takeover
credential) as application-layer ciphertext ('enc:v1:'). Rows created before that are still plaintext.
This one-off pass seals them. Reads keep working throughout: crypto.decrypt returns legacy plaintext
(no prefix) unchanged, so nothing breaks before or during the backfill. Idempotent (already-sealed
values are skipped).

Two ways to run — use whichever you have access to:
  • Shell on the backend host (needs FIELD_ENCRYPTION_KEY + SUPABASE_SERVICE_KEY in the env):
        cd backend && python -m app.scripts.backfill_customer_pin_encryption            # apply
        cd backend && python -m app.scripts.backfill_customer_pin_encryption --dry-run   # report
  • NO shell: set env ENCRYPTION_BACKFILL_ON_BOOT=1 and redeploy — the app runs this same sweep once
    at startup and logs the counts (see app/core/encryption_backfill.py). Clear the flag afterward.
"""
from __future__ import annotations

import sys

from app.core import crypto
from app.core.database import get_supabase
from app.core.encryption_backfill import backfill_customer_pins


def main(dry_run: bool = False) -> int:
    if not crypto.is_enabled():
        print("FIELD_ENCRYPTION_KEY is not configured — refusing to backfill (would write plaintext).")
        return 2
    res = backfill_customer_pins(get_supabase(), dry_run=dry_run)
    verb = "would seal" if dry_run else "sealed"
    print(f"scanned={res['scanned']} {verb}={res['sealed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv))
