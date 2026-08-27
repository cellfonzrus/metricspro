"""Reusable encryption-backfill sweeps — seal data that predates a field becoming encrypted.

Two ways to run these, so an operator WITHOUT shell access to the backend can still trigger them:
  • CLI (if you have a shell):  python -m app.scripts.backfill_customer_pin_encryption
  • At boot (no shell needed):   set env ENCRYPTION_BACKFILL_ON_BOOT=1 and redeploy — main.py's
    startup hook runs run_all() once in a background thread, logs the counts, then you clear the flag.

Every sweep is IDEMPOTENT (already-encrypted values are skipped) and paged, so re-running — including
on every boot while the flag is set, or across multiple replicas — is safe.
"""
from __future__ import annotations

from app.core import crypto

_PAGE = 500


def backfill_customer_pins(client, *, dry_run: bool = False, page: int = _PAGE) -> dict:
    """Encrypt existing plaintext carrier PINs in pos.customers.password. Narrow id+password select
    (never bulk-reads the customer book). Returns {scanned, sealed, enabled}."""
    if not crypto.is_enabled():
        return {"scanned": 0, "sealed": 0, "enabled": False}
    tbl = client.schema("pos").table("customers")
    scanned = sealed = 0
    offset = 0
    while True:
        rows = (tbl.select("id,password").order("created_at", desc=False)
                .range(offset, offset + page - 1).execute().data) or []
        if not rows:
            break
        for row in rows:
            scanned += 1
            pin = row.get("password")
            if not pin or crypto.is_encrypted(pin):
                continue
            sealed += 1
            if not dry_run:
                tbl.update({"password": crypto.encrypt(pin)}).eq("id", row["id"]).execute()
        offset += len(rows)
        if len(rows) < page:
            break
    return {"scanned": scanned, "sealed": sealed, "enabled": True}


def run_all(client, *, dry_run: bool = False) -> dict:
    """Run every backfill sweep. Add future sweeps here as more fields become encrypted."""
    return {"customer_pins": backfill_customer_pins(client, dry_run=dry_run)}
