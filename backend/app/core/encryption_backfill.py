"""Reusable encryption-backfill sweeps — seal data that predates a field becoming encrypted.

Two ways to run these, so an operator WITHOUT shell access to the backend can still trigger them:
  • CLI (if you have a shell):  python -m app.scripts.backfill_customer_pin_encryption
  • At boot (no shell needed):   set env ENCRYPTION_BACKFILL_ON_BOOT=1 and redeploy — main.py's
    startup hook runs run_all() once in a background thread, logs the counts, then you clear the flag.

Every sweep is IDEMPOTENT (already-encrypted values are skipped) and paged, so re-running — including
on every boot while the flag is set, or across multiple replicas — is safe.

THE CLASS THIS MODULE IS THE ONE HOME FOR (index §CORE-04)
-----------------------------------------------------------
A column becomes encrypted at rest the moment someone adds a `crypto.encrypt` call to its write
path. From that moment, everything written is ciphertext — and everything ALREADY stored stays
plaintext unless somebody ALSO remembers to write a sweep here. Nothing enforced that, and on
2026-10-12 two employees' bank routing numbers were still readable in plain text in production
(`storeops.employee_onboarding_profile.intake_data`) while `run_all()`'s own docstring claimed to run
every sweep. The instance was HR intake. The CLASS is that "encrypted on write" and "sealed at rest"
were two separate facts with nothing tying them together, so a new encrypted column silently started
out half protected — and it looks fine from the application, because every new write IS sealed.

So: `SWEEPS` below is the ONE home for "which encrypted-at-rest column family has a sweep", and it is
LOCKED. `run_all()` derives its calls from it rather than listing them, and
`backend/harness_encryption_backfill_coverage.py` fails the build when any module under backend/app
encrypts a stored value and is named by no family here. A fifth encrypted column cannot ship
half-protected without the build saying so.

No sweep spells a column name of its own. Each reads the declaration the WRITER already dereferences
(`portal_secret.SECRET_COLUMNS`, `receipt_import.ENCRYPTED_IMPORT_COLUMNS`, HR's own
`_public_intake_fields`), so a column added to a writer is covered here without a second edit.
"""
from __future__ import annotations

from app.core import crypto

_PAGE = 500


def backfill_customer_pins(client, *, dry_run: bool = False, page: int = _PAGE) -> dict:
    """Encrypt existing plaintext carrier PINs in pos.customers.password. Narrow id+password select
    (never bulk-reads the customer book). Returns {scanned, sealed, enabled}.

    HOW a stored PIN is sealed is `pos.router.seal_customer_pin` — the same function the three
    writers call — not a `crypto.encrypt` of our own. It agreed by luck while there was one text
    column and one primitive; the portal sweep's identical half-dereference did not agree, and left
    every stored browser session readable. See that sweep's comment.
    """
    if not crypto.is_enabled():
        return {"scanned": 0, "sealed": 0, "enabled": False}
    from app.modules.pos import router as pos_router
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
            if not pin:
                continue
            fresh = pos_router.seal_customer_pin({"password": pin}).get("password")
            if fresh == pin:
                continue            # already sealed — the primitive passes ciphertext through
            sealed += 1
            if not dry_run:
                tbl.update({"password": fresh}).eq("id", row["id"]).execute()
        offset += len(rows)
        if len(rows) < page:
            break
    return {"scanned": scanned, "sealed": sealed, "enabled": True}


def backfill_portal_secrets(client, *, dry_run: bool = False, page: int = _PAGE) -> dict:
    """Encrypt the portal credentials, API keys and authenticator secrets already stored in plain
    text, for EVERY table the credential registry names (index §CORE-03).

    This sweep has no table list of its own on purpose, AND no sealing logic of its own. It reads
    `portal_secret.SECRET_COLUMNS` for WHICH columns hold a credential, and calls
    `portal_secret.seal_row` for WHAT sealing one means — both the same facts the readers and
    writers dereference. So a sweep that registers a new credential column tomorrow, in either the
    text half or the JSONB half, is backfilled by this function without anyone editing it.

    Reading the registry for WHICH while deciding HOW here is what broke it once (see the comment at
    the patch below): a JSONB credential column needs a different primitive, this sweep did not ask,
    and three live portal sessions stayed in plain text through every boot. One half-dereference is
    still a second home.

    Nothing here is required for correctness: `crypto.decrypt` returns an unprefixed value unchanged,
    so a plaintext row reads fine forever. This is how the OLD rows stop being readable from a
    database dump. Idempotent (an already-sealed value is passed through by both primitives, so the
    diff is empty) and paged, so re-running it — on every boot while ENCRYPTION_BACKFILL_ON_BOOT is
    set, or on several replicas at once — is safe.

    Returns {tables: {<table>: {scanned, sealed, columns, sealed_columns, error?}}, sealed, enabled}.
    `sealed_columns` names which columns actually changed, so an operator reading the boot log can
    see the JSONB half was reached and not assume it. A table whose migration is not applied in this
    tenant's database is reported as an error and SKIPPED, never raised: one tenant missing mig 411
    must not stop the other eight tables being sealed.
    """
    from app.modules.core import portal_secret
    if not crypto.is_enabled():
        return {"tables": {}, "sealed": 0, "enabled": False}
    out: dict = {}
    total = 0
    for table, cols in sorted(portal_secret.SECRET_COLUMNS.items()):
        schema = portal_secret.table_schema(table)
        key = portal_secret.table_key(table)
        res = {"scanned": 0, "sealed": 0, "columns": list(cols), "key": list(key),
               # always present, so a reader never has to tell "sealed nothing" from "did not look"
               "sealed_columns": []}
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
                    # WHAT SEALING A ROW OF THIS TABLE MEANS is portal_secret's to answer, not this
                    # sweep's. `seal_row` is the same function the WRITE path calls
                    # (router.save_data_source), and it knows what this sweep used to get wrong: a
                    # registered JSONB column takes crypto.encrypt_json's {"enc": …} envelope,
                    # because `enc:v1:<token>` is not valid JSON. This sweep built its own patch and
                    # type-guarded on `isinstance(v, str)`, so a JSONB column — which comes back
                    # from the client as a dict — was SKIPPED on every boot, silently and forever.
                    # Measured in production 2026-10-13: three stored browser sessions, carrying
                    # live portal cookies, still in plain text after the sweep had run. Reading the
                    # registry for WHICH columns while deciding HOW ourselves was the second home.
                    #
                    # Both primitives pass an already-sealed value through unchanged, so the diff
                    # below is empty on the second boot and this stays idempotent.
                    sealed_row = portal_secret.seal_row(table, row)
                    patch = {c: sealed_row[c] for c in cols
                             if c in sealed_row and sealed_row[c] != row.get(c)}
                    if not patch:
                        continue
                    res["sealed"] += 1
                    res["sealed_columns"] = sorted(set(res["sealed_columns"]) | set(patch))
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


def backfill_intake_sensitive(client, *, org_id: str | None = None, dry_run: bool = False,
                              page: int = _PAGE) -> dict:
    """Encrypt the sensitive employee-intake values (SSN, bank routing / account, A-Number) already
    stored in plain text in `storeops.employee_onboarding_profile.intake_data`.

    WHICH KEYS ARE SENSITIVE IS NOT DECIDED HERE. It is a per-tenant declaration
    (`storeops.onboarding_intake_field.sensitive`), and the one home that answers it is HR's own
    `_public_intake_fields(org_id)` — the same function the intake SAVE path asks before encrypting.
    A tenant that marks a new field sensitive tomorrow is therefore backfilled by this sweep with no
    edit here. Reading the declaration per-org also means a field one tenant treats as sensitive is
    not sealed in a tenant that does not.

    This is the sweep whose ABSENCE was the defect: sealing these values used to exist only as
    `POST /hr/onboarding/encrypt-existing`, a manual per-tenant button, so a server could boot with
    ENCRYPTION_BACKFILL_ON_BOOT set and never touch them. That endpoint now calls this function, so
    there is one implementation and the button and the boot sweep cannot diverge.

    Returns {orgs: {<org_id>: {scanned, sealed, values, keys, error?}}, sealed, enabled} — `sealed`
    counts PROFILES changed, `values` the individual fields encrypted inside them. A tenant whose
    intake-field table is missing is reported and SKIPPED, never raised.
    """
    if not crypto.is_enabled():
        return {"orgs": {}, "sealed": 0, "enabled": False}
    from app.modules.hr import router as hr_router
    tbl = lambda: client.schema("storeops").table("employee_onboarding_profile")  # noqa: E731
    orgs: list[str] = []
    if org_id:
        orgs = [org_id]
    else:
        # Every tenant that HAS an intake profile. Paged, because this is the whole table.
        seen, offset = set(), 0
        while True:
            rows = (tbl().select("org_id").range(offset, offset + page - 1).execute().data) or []
            if not rows:
                break
            for r in rows:
                if r.get("org_id"):
                    seen.add(r["org_id"])
            offset += len(rows)
            if len(rows) < page:
                break
        orgs = sorted(seen)
    out: dict = {}
    total = 0
    for oid in orgs:
        res = {"scanned": 0, "sealed": 0, "values": 0, "keys": []}
        try:
            keys = [f["key"] for f in hr_router._public_intake_fields(oid) if f.get("sensitive")]
            res["keys"] = keys
            if not keys:
                out[oid] = res
                continue
            offset = 0
            while True:
                rows = (tbl().select("employee_id,intake_data").eq("org_id", oid)
                        .range(offset, offset + page - 1).execute().data) or []
                if not rows:
                    break
                for row in rows:
                    res["scanned"] += 1
                    data = dict(row.get("intake_data") or {})
                    changed = False
                    for k in keys:
                        v = data.get(k)
                        if not v or not str(v).strip():
                            continue
                        # HOW a sensitive intake value is sealed is HR's `seal_intake_value` — the
                        # same function the intake SAVE path calls — not a crypto.encrypt of our
                        # own. See backfill_portal_secrets for what a half-dereference cost.
                        fresh = hr_router.seal_intake_value(v)
                        if fresh != v:
                            data[k] = fresh
                            changed = True
                            res["values"] += 1
                    if not changed:
                        continue
                    res["sealed"] += 1
                    total += 1
                    if not dry_run:
                        (tbl().update({"intake_data": data}).eq("org_id", oid)
                         .eq("employee_id", row["employee_id"]).execute())
                offset += len(rows)
                if len(rows) < page:
                    break
        except Exception as e:                      # noqa: BLE001 — one tenant must not stop the rest
            res["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        out[oid] = res
    return {"orgs": out, "sealed": total, "enabled": True}


def backfill_receipt_imports(client, *, dry_run: bool = False, page: int = _PAGE) -> dict:
    """Seal the IMEI, note and parsed/raw_ocr audit blobs already stored in plain text in
    `pos.receipt_imports`, and give a plaintext IMEI the blind index its search needs.

    The columns and the blind-index dependency are NOT listed here: `receipt_import.seal_stored_row`
    is the one home for "what sealing a stored import row means", declared next to the writer that
    seals a NEW one. This sweep pages the table and applies that patch.
    """
    if not crypto.is_enabled():
        return {"scanned": 0, "sealed": 0, "enabled": False}
    from app.modules.pos import receipt_import
    cols = ("id", "org_id") + tuple(receipt_import.ENCRYPTED_IMPORT_COLUMNS) \
        + tuple(receipt_import.ENCRYPTED_IMPORT_JSON_COLUMNS) \
        + tuple(receipt_import.IMPORT_BLIND_INDEX_COLUMNS)
    tbl = lambda: client.schema("pos").table("receipt_imports")  # noqa: E731
    res = {"scanned": 0, "sealed": 0, "columns": list(cols[2:])}
    try:
        offset = 0
        while True:
            rows = (tbl().select(",".join(cols)).range(offset, offset + page - 1).execute().data) or []
            if not rows:
                break
            for row in rows:
                res["scanned"] += 1
                patch = receipt_import.seal_stored_row(row)
                if not patch:
                    continue
                res["sealed"] += 1
                if not dry_run:
                    tbl().update(patch).eq("id", row["id"]).execute()
            offset += len(rows)
            if len(rows) < page:
                break
    except Exception as e:                          # noqa: BLE001 — a missing table is a RESULT
        res["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    res["enabled"] = True
    return res


# ── The registry: every encrypted-at-rest column family, and the sweep that seals its old rows ─────
# READ THE MODULE DOCSTRING FIRST — this dict is the lock's subject, not bookkeeping.
#
#   writer  the module whose code calls crypto.encrypt / encrypt_map / encrypt_json on a value it
#           STORES. The coverage harness scans backend/app for exactly those calls and requires
#           every module it finds to appear here (or in NO_STORED_WRITE below, with a reason).
#   stores  what is sealed, for a human reading a failure.
#   sweep   the function in THIS module that seals the rows written before the column was encrypted.
#   sealer  the WRITER-SIDE function that answers "what does sealing a row of this family MEAN", as
#           `module.function`. The sweep APPLIES it and holds no primitive of its own.
#
#           This field exists because of what its absence cost (2026-10-13). Every sweep already
#           read its writer's declaration of WHICH columns hold a secret — the registry, the column
#           tuples, the per-tenant sensitive-field list — and every sweep then decided HOW to seal
#           them itself, with an inlined `crypto.encrypt`. Three of the four agreed with their
#           writer by luck, because one text column takes one primitive. `backfill_portal_secrets`
#           did not: a registered JSONB credential column needs crypto.encrypt_json's {"enc": …}
#           envelope, the sweep's patch type-guarded on `isinstance(v, str)`, and a JSONB column
#           comes back from the client as a dict. So it was SKIPPED on every single boot. Measured
#           in production: three `commcalc.data_source.session_state` blobs — live portal session
#           cookies, a stolen session being a stolen login — still in plain text long after
#           ENCRYPTION_BACKFILL_ON_BOOT had run and reported success.
#
#           Reading a registry for WHICH while deciding HOW yourself is a HALF-dereference, and a
#           half-dereference is a whole second home. §I of the lock holds every sweep to its sealer
#           and fails the build on an encrypt call anywhere in this module.
def backfill_receipt_remit(client, *, dry_run: bool = False, page: int = _PAGE) -> dict:
    """Seal any pay-by-bank number still stored as plaintext in pos.pos_settings (§POS-06).

    WHICH ROWS: the ones whose `key` is in `pos.receipt_remit.KEYS` — the writer's own declaration of
    which two settings hold bank numbers, never a key spelled again here. Every other setting in that
    table is ordinary configuration and is left alone.

    HOW a value is sealed is `receipt_remit.seal_setting_value`, the same function the editor calls.
    Returns {scanned, sealed, enabled}.
    """
    if not crypto.is_enabled():
        return {"scanned": 0, "sealed": 0, "enabled": False}
    from app.modules.pos import receipt_remit as _remit
    tbl = client.schema("pos").table("pos_settings")
    scanned = sealed = 0
    offset = 0
    while True:
        rows = (tbl.select("id,key,value").in_("key", list(_remit.KEYS))
                .order("id", desc=False).range(offset, offset + page - 1).execute().data) or []
        if not rows:
            break
        for row in rows:
            scanned += 1
            value = row.get("value")
            if not value or crypto.is_encrypted(value):
                continue
            if dry_run:
                sealed += 1
                continue
            tbl.update({"value": _remit.seal_setting_value(value)}).eq("id", row["id"]).execute()
            sealed += 1
        if len(rows) < page:
            break
        offset += page
    return {"scanned": scanned, "sealed": sealed, "enabled": True}


SWEEPS: dict[str, dict] = {
    "customer_pins": {
        "writer": "app/modules/pos/router.py",
        "stores": ("pos.customers.password (the carrier account PIN)",),
        "sweep": "backfill_customer_pins",
        "sealer": "app.modules.pos.router.seal_customer_pin",
    },
    "portal_secrets": {
        "writer": "app/modules/core/portal_secret.py",            # index §CORE-03
        "stores": ("every column portal_secret.SECRET_COLUMNS names (ten tables)",),
        "sweep": "backfill_portal_secrets",
        "sealer": "app.modules.core.portal_secret.seal_row",
    },
    "intake_sensitive": {
        "writer": "app/modules/hr/router.py",                     # the intake save + the HR button
        "stores": ("storeops.employee_onboarding_profile.intake_data"
                   " (the keys the tenant declares sensitive)",),
        "sweep": "backfill_intake_sensitive",
        "sealer": "app.modules.hr.router.seal_intake_value",
    },
    "receipt_imports": {
        "writer": "app/modules/pos/receipt_import.py",            # _encrypt_import_row
        "stores": ("pos.receipt_imports.imei / notes / parsed / raw_ocr + imei_bidx",),
        "sweep": "backfill_receipt_imports",
        "sealer": "app.modules.pos.receipt_import.seal_stored_row",
    },
    "receipt_remit": {
        "writer": "app/modules/pos/receipt_remit.py",             # index §POS-06
        "stores": ("pos.pos_settings.value for the keys receipt_remit.KEYS names"
                   " (the account a customer is told to pay into)",),
        "sweep": "backfill_receipt_remit",
        "sealer": "app.modules.pos.receipt_remit.seal_setting_value",
    },
}

# Modules that call an encrypt helper but store NOTHING of their own, each with its written reason.
# §C of the coverage harness reads this; an unexplained module fails the build rather than being
# assumed harmless.
# THIS MODULE IS NO LONGER IN HERE, and its absence is the signal. It used to be excused with "the
# sweeps themselves encrypt, which is the point" — and that excuse was the defect in one sentence: a
# sweep holding its own primitive is a second home for "what sealing this family means". Every sweep
# now applies the sealer its family's WRITER uses (portal_secret.seal_row,
# pos.router.seal_customer_pin, hr.router.seal_intake_value, receipt_import.seal_stored_row), so no
# encrypt call is left here to excuse and §C's staleness check removes the entry on its own. If one
# ever comes back, the build fails for the right reason.
NO_STORED_WRITE: dict[str, str] = {
    "app/core/crypto.py":
        "the primitive itself. encrypt_map / encrypt_json call encrypt; crypto stores no row and "
        "knows no table.",
}

# Modules whose encrypted columns were created ENCRYPTED and have never held a plaintext value, so
# there is no old row for a sweep to seal. This is NOT an excuse the lock takes on trust: §C holds it
# true by refusing any write to a named column that is not an `encrypt(...)` call, so the day someone
# writes one in the clear the build fails and the module needs a real sweep.
BORN_SEALED: dict[str, dict] = {
    # WHY this one is exempt, in full. The specifics live in this COMMENT rather than in the `why`
    # string below, and that is not cosmetic: `reason` is a registered customer-message key
    # (`core/setup_notice.MESSAGE_KEYS`), so internals written under one are treated as copy a tenant
    # could read — the carrier-vocab guard's infra-names and setup-internals locks both flagged an
    # earlier draft of this entry for exactly that, correctly. Developer documentation belongs in a
    # comment; the field keeps business words.
    #
    #   The camera module's credential columns were created ENCRYPTED — the two migrations that add
    #   them name every one `*_enc` and store ciphertext — and every writer in that module has always
    #   passed its value through encrypt(). Counted against the live database on 2026-10-12: 3 of 3
    #   stored camera-vendor credentials and the 1 stored edge-agent secret all carry the `enc:v1:`
    #   prefix, and neither table holds a single plaintext row. There is therefore no old row for a
    #   sweep to seal — which is a fact about history, not a property of the code, so §C of the lock
    #   re-establishes it on every build by refusing any write to these columns that skips encrypt().
    "app/modules/vision/router.py": {
        "why": "created encrypted, written through encrypt() by every writer since, and counted "
               "clean against the live database on 2026-10-12 — no plaintext row has ever existed, "
               "so no sweep is owed. See the comment above for the measurement; the lock re-proves "
               "the claim on every build rather than trusting it.",
        "columns": ("client_secret_enc", "refresh_token_enc", "extension_token_enc", "secret_enc"),
    },
}


def run_all(client, *, dry_run: bool = False) -> dict:
    """Run every backfill sweep, DERIVED from SWEEPS — never a list of calls that can fall behind it.

    A sweep that raises is reported under its own key and the rest still run: one tenant missing a
    migration must not leave the other families plaintext.
    """
    out: dict = {}
    for name in sorted(SWEEPS):
        fn = globals().get(SWEEPS[name]["sweep"])
        if not callable(fn):                        # the harness makes this unreachable; be honest anyway
            out[name] = {"error": f"sweep {SWEEPS[name]['sweep']!r} is not defined"}
            continue
        try:
            out[name] = fn(client, dry_run=dry_run)
        except Exception as e:                      # noqa: BLE001
            out[name] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
    return out
