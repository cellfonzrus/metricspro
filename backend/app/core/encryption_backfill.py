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


def backfill_portal_secrets(client, *, dry_run: bool = False, page: int = _PAGE) -> dict:
    """Encrypt the portal credentials, API keys and authenticator secrets already stored in plain
    text, for EVERY table the credential registry names (index §CORE-03).

    This sweep has no table list of its own on purpose. It reads
    `app.modules.core.portal_secret.SECRET_COLUMNS` — the same fact the readers and writers
    dereference — so a sweep that registers a new credential column tomorrow is backfilled by this
    function without anyone editing it. A copy of the list here would be exactly the second home the
    registry exists to prevent.

    Nothing here is required for correctness: `crypto.decrypt` returns an unprefixed value unchanged,
    so a plaintext row reads fine forever. This is how the OLD rows stop being readable from a
    database dump. Idempotent (an already-sealed value is skipped) and paged, so re-running it — on
    every boot while ENCRYPTION_BACKFILL_ON_BOOT is set, or on several replicas at once — is safe.

    Returns {tables: {<table>: {scanned, sealed, columns, error?}}, sealed, enabled}. A table whose
    migration is not applied in this tenant's database is reported as an error and SKIPPED, never
    raised: one tenant missing mig 411 must not stop the other eight tables being sealed.
    """
    from app.modules.core import portal_secret
    if not crypto.is_enabled():
        return {"tables": {}, "sealed": 0, "enabled": False}
    out: dict = {}
    total = 0
    for table, cols in sorted(portal_secret.SECRET_COLUMNS.items()):
        schema = portal_secret.table_schema(table)
        key = portal_secret.table_key(table)
        res = {"scanned": 0, "sealed": 0, "columns": list(cols), "key": list(key)}
        try:
            # The registry says where the table is AND how to address one of its rows. Only
            # data_source has a surrogate `id`; the eight config tables are keyed on the tenant (and
            # the mailbox, mig 075), so a hard-coded .eq("id", …) here would have failed on all of
            # them. One fact, read — not assumed.
            def _tbl():
                return (client.schema(schema).table(table) if schema else client.table(table))
            offset = 0
            while True:
                rows = (_tbl().select(",".join(tuple(key) + tuple(cols)))
                        .range(offset, offset + page - 1).execute().data) or []
                if not rows:
                    break
                for row in rows:
                    res["scanned"] += 1
                    patch = {c: crypto.encrypt(row[c]) for c in cols
                             if isinstance(row.get(c), str) and row[c]
                             and not crypto.is_encrypted(row[c])}
                    if not patch:
                        continue
                    res["sealed"] += 1
                    total += 1
                    if not dry_run:
                        q = _tbl().update(patch)
                        for k in key:
                            q = q.eq(k, row.get(k))
                        q.execute()
                offset += len(rows)
                if len(rows) < page:
                    break
        except Exception as e:                      # noqa: BLE001 — a missing table is a RESULT
            res["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        out[table] = res
    return {"tables": out, "sealed": total, "enabled": True}


def run_all(client, *, dry_run: bool = False) -> dict:
    """Run every backfill sweep. Add future sweeps here as more fields become encrypted."""
    return {"customer_pins": backfill_customer_pins(client, dry_run=dry_run),
            "portal_secrets": backfill_portal_secrets(client, dry_run=dry_run)}
