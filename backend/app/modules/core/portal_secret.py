"""WHICH STORED COLUMNS ARE A CREDENTIAL — the one home, and the only place that decides it.

Owner directive 2026-10-12, before a UPS Store portal login is entered: a portal password should not
sit in the database in plain text.

THE CLASS, not the instance. The defect was never "epay's password is plaintext". It was that a
portal credential had NO home: nine tables each carried their own `portal_pass` / `password` /
`api_key` / `totp_secret`, each sweep read and wrote its own, and whether a value was protected
depended on which module happened to own the table. The `vision` module got it right on its own
(mig 900 stores `client_secret_enc` / `secret_enc` as `enc:v1:` ciphertext) — and because that
decision lived inside that module, nothing carried it to the other eight. One module's good
decision that its siblings cannot inherit is the same defect as a bad one.

So the FACT "this column holds a credential" lives HERE, once, as `SECRET_COLUMNS`, and every reader
and writer dereferences it. A new sweep that adds a credential column registers it here and is
sealed by construction; `harness_portal_secret_lock.py` fails the build if a credential column
appears on a table this registry does not name, or if a config reader or writer stops going through
`open_row` / `seal_row`.

NO MIGRATION, AND NO BACKFILL IS REQUIRED TO BE SAFE. `crypto`'s stored form is `enc:v1:<token>` and
its `decrypt` returns anything WITHOUT that prefix unchanged, so:

  · a row written before this shipped still reads correctly (it is legacy plaintext, passed through);
  · a row written after this ships is ciphertext;
  · the two coexist in one column, for as long as they like.

`encryption_backfill.backfill_portal_secrets` seals what is already stored, idempotently — it is how
the old rows catch up, not how this becomes correct.

WITH NO KEY CONFIGURED this is a transparent no-op: `crypto.encrypt` returns its input and
`is_enabled()` is False. That is deliberate — a platform without `FIELD_ENCRYPTION_KEY` keeps
working exactly as it does today rather than failing to log into a portal — and it is why
`protection_state()` exists: a surface can SAY the credentials are unprotected instead of implying a
safety that is not there. Silence about an absent control is how a control gets forgotten.

WHAT THIS IS NOT. It is not a secrets manager and does not pretend to be: the key lives in the
backend's environment, so this protects against someone reading the DATABASE (a leaked backup, raw
service-role access, a support engineer), not against someone who already has the backend's
environment. That is the same bar `app.core.crypto` was built to and states for employee bank
details. It is also not a substitute for the columns being stripped from API reads — they already
are, by `router._SOURCE_SECRETS` and each `_*_public_cfg`, and that stays the first line.

PURE-ISH: no DB, no network. Takes dicts, returns dicts, and asks `app.core.crypto` for the
primitive. Every function is safe on a row that is None or missing the column.
"""
from app.core import crypto

# ── THE REGISTRY ──────────────────────────────────────────────────────────────────────────────────
# table name (unqualified, as the sweep's own code names it) → the columns on it that hold a secret.
# A column here is ENCRYPTED AT REST and must never be returned by an API read; those are two
# different controls and this one does not replace the other.
#
# Everything in this map is a credential for a system OUTSIDE the platform — a portal login, an API
# key, an authenticator secret. A platform password (an employee signing in to MetricsPro) is NOT
# here and must never be: those are one-way HASHED by `core.auth_security`, and hashing beats
# encryption for anything that never has to be replayed. This registry is for secrets we have to be
# able to USE, which is why they are reversible.
# Each entry is EVERYTHING a caller could need to know about one credential-holding table, in one
# place: where the table lives, how to address a single row of it, and which of its columns hold a
# secret. Three facts about the same table in three separate maps is three chances to drift — the
# exact defect this module was written to end — so they are one record.
#
#   schema   the Postgres schema. '' means the client is already scoped to it by the caller
#            (storeops's `sb()` does this), so the backfill addresses it without a .schema() hop.
#   key      the primary key, as columns. Every sweep config is keyed on the TENANT (and the mailbox
#            for the multi-mailbox one, mig 075); only `data_source` has a surrogate `id`. A backfill
#            that assumed `id` would have crashed on eight of the nine tables.
#   secrets  the TEXT columns that are ENCRYPTED AT REST and must never be returned by an API read.
#            Encrypted-at-rest and stripped-from-API-reads are two different controls and this one
#            does not replace the other.
#   json      the JSONB columns that hold a secret. They are a SEPARATE field because they need a
#            different primitive, not a different policy: `enc:v1:<token>` is not valid JSON, so a
#            JSONB column takes crypto.encrypt_json's `{"enc": …}` envelope instead. Getting this
#            wrong would not have been a silent weakening — it would have been a 400 from Postgres
#            the first time a portal login tried to save its session. §C of the lock reads the
#            declared column TYPE so the two can never be swapped by hand.
#
# Everything here is a credential for a system OUTSIDE the platform — a portal login, an API key, an
# authenticator secret. A platform password (an employee signing in to MetricsPro) is NOT here and
# must never be: those are one-way HASHED by `core.auth_security`, and hashing beats encryption for
# anything that never has to be replayed. This registry is for secrets we have to be able to USE,
# which is why they are reversible.
TABLES = {
    # portal logins — a username and a password typed into someone else's website by a sweep
    "vip_sweep_config":   {"schema": "commcalc", "key": ("org_id",), "secrets": ("portal_pass",)},
    "dlar_sweep_config":  {"schema": "commcalc", "key": ("org_id",), "secrets": ("portal_pass",)},
    "epay_sweep_config":  {"schema": "commcalc", "key": ("org_id",), "secrets": ("portal_pass",)},
    "b2b_sweep_config":   {"schema": "commcalc", "key": ("org_id",), "secrets": ("portal_pass",)},
    "ftp_sweep_config":   {"schema": "commcalc", "key": ("org_id",), "secrets": ("password",)},
    # mig 075 re-keyed the mailbox table on (org_id, account) so a tenant can have several inboxes
    "email_sweep_config": {"schema": "commcalc", "key": ("org_id", "account"),
                           "secrets": ("password",)},
    # the generic portal-login row (mig 083 + 955): password, the authenticator-app shared secret,
    # and the two browser-session blobs, which carry live session cookies and are a credential in
    # every sense that matters — a stolen session is a stolen login.
    "data_source":        {"schema": "commcalc", "key": ("id",),
                           "secrets": ("password", "totp_secret"),
                           "json": ("session_state", "pending_state")},
    # third-party API keys. storeops.google_review_config (mig 411) — SINGULAR "review", and the one
    # table here the caller's client is already scoped to. The name is spelled once, here, so no
    # caller has to remember which way round it goes.
    "google_review_config": {"schema": "storeops", "key": ("org_id",), "secrets": ("api_key",)},
    # the platform's OWN vendor API tokens (mig 090) — the same class exactly: a token we store so we
    # can replay it to someone else's API. Found by §D's migration scan while this shipped, which is
    # the point of the scan: "find the siblings before you ship".
    "platform_billing_connector": {"schema": "storeops", "key": ("id",),
                                   "secrets": ("credential",)},
}

# DERIVED, never a second source of truth: `table → secret columns`, the shape every reader and
# writer actually asks for. Computed from TABLES so a column cannot be registered in one and missed
# in the other.
SECRET_COLUMNS = {t: tuple(v["secrets"]) + tuple(v.get("json", ())) for t, v in TABLES.items()}

# The JSONB half, same derivation. A column in here is in SECRET_COLUMNS too — this says only which
# primitive opens it.
JSON_COLUMNS = {t: tuple(v.get("json", ())) for t, v in TABLES.items()}

# Columns the VISION module already stores as ciphertext under its own `_enc` naming (migs 900+).
# They are NOT in TABLES because nothing is gained by double-sealing them — but §D of
# harness_portal_secret_lock.py asserts they are STILL `_enc`, so this registry's arrival can never
# be read as permission to stop.
ALREADY_SEALED = {
    "vision_credential":     ("client_secret_enc", "refresh_token_enc"),
    "vision_edge_agent":     ("secret_enc",),
    "vision_stream_session": ("extension_token_enc",),
}

# Credential-LOOKING columns that are deliberately NOT encrypted, each with the reason. §D of the
# lock reads this: a column that matches its migration scan must be registered above, already
# sealed, or excused HERE — so a new secret column cannot be added to the schema and quietly skipped,
# and an excuse cannot be given without writing down why.
#
# The recurring reason is LOOKUP BY VALUE. An encrypted column is opaque, so a token the server has
# to find a row BY (`WHERE access_token = …`) cannot be encrypted without breaking the lookup; the
# right tool for those is `crypto.blind_index`, which is a separate piece of work and is named here
# rather than left implied.
NOT_A_SECRET = {
    ("expense_apply_config", "token"):
        "not a credential — a case-insensitive substring matched against an expense name (mig 205).",
    ("tenants", "password_policy"):
        "a POLICY (length / expiry rules), not a password.",
    ("customers", "password"):
        "the carrier PIN, already encrypted at its own one home by "
        "encryption_backfill.backfill_customer_pins (pos schema).",
    ("vendor_connector", "credential_ref"):
        "a REFERENCE to a credential held elsewhere, not the credential.",
    ("vendor_connector", "inbound_token_hash"):
        "one-way HASHED, which is stronger than encryption for a value only ever compared.",
    ("employee_onboarding_profile", "access_token"):
        "a revocable per-employee QR token the server must look up BY VALUE (indexed, mig 073). "
        "Encrypting it would break the lookup; the right tool is crypto.blind_index — open work, "
        "deliberately not pretended to be done here.",
    ("chat_push_tokens", "token"):
        "a device push token, looked up by value and revoked by the device, same shape as above.",
}


def json_columns(table):
    """Which of `table`'s secret columns are JSONB, and so take the `{"enc": …}` envelope."""
    return JSON_COLUMNS.get((table or "").strip(), ())


def table_schema(table):
    """The schema `table` lives in, or '' when the caller's client is already scoped to it."""
    return (TABLES.get((table or "").strip()) or {}).get("schema", "")


def table_key(table):
    """The primary-key columns of `table` — what a backfill addresses one row by."""
    return (TABLES.get((table or "").strip()) or {}).get("key", ())


def secret_columns(table):
    """The secret columns on `table`, or () for a table that holds none. THE lookup — a caller that
    decides for itself which of its columns are secret is the defect this module exists to remove."""
    return SECRET_COLUMNS.get((table or "").strip(), ())


def is_secret_column(table, column):
    """Is this one column a credential? Used by the API-read strippers and by the lock."""
    return (column or "") in secret_columns(table)


def seal_row(table, row):
    """A row ABOUT TO BE WRITTEN, with its registered secret columns encrypted. Returns a NEW dict;
    the caller's row is never mutated, because a caller that logs or returns the dict it passed in
    would otherwise be logging ciphertext or — worse, if this were ever reordered — plaintext.

    A column that is absent stays absent, so a partial update (the write-only password pattern: send
    the field to change it, omit it to keep the stored one) keeps working untouched. A value already
    encrypted is passed through rather than sealed twice.
    """
    cols = secret_columns(table)
    if not row or not cols:
        return dict(row or {})
    jsn = json_columns(table)
    out = dict(row)
    for c in cols:
        if c not in out:
            continue
        v = out[c]
        if c in jsn:
            if v is not None:
                out[c] = crypto.encrypt_json(v)
        elif isinstance(v, str) and v and not crypto.is_encrypted(v):
            out[c] = crypto.encrypt(v)
    return out


def open_row(table, row):
    """A row JUST READ, with its registered secret columns decrypted. Returns a NEW dict.

    Legacy plaintext passes through unchanged (`crypto.decrypt` keys off the `enc:v1:` prefix), which
    is what lets this ship with no migration and no backfill window: a row written yesterday and a
    row written tomorrow both read correctly, from the same column, at the same time.
    """
    cols = secret_columns(table)
    if not row or not cols:
        return dict(row or {})
    jsn = json_columns(table)
    out = dict(row)
    for c in cols:
        if c not in out:
            continue
        v = out[c]
        if c in jsn:
            out[c] = crypto.decrypt_json(v)
        elif isinstance(v, str) and v:
            out[c] = crypto.decrypt(v)
    return out


def open_rows(table, rows):
    """`open_row` over a list — the shape a sweep's due-list read returns."""
    return [open_row(table, r) for r in (rows or [])]


def protection_state():
    """Whether stored credentials are actually protected right now, for a surface to SAY so.

    'protected'   — a key is configured, so anything written from now on is ciphertext.
    'unprotected' — no key, so credentials are stored as typed. Not an error (the platform is meant
                    to run without one), but never something to leave implied.

    A screen that takes a portal password should show this rather than let an operator assume.
    """
    return "protected" if crypto.is_enabled() else "unprotected"


def describe():
    """What this registry covers, for an operator page or a status payload. Names TABLES AND COLUMNS
    and never a value — there is no function in this module that returns a stored secret to a
    caller that did not already read it from the database."""
    return {
        "state": protection_state(),
        "tables": {t: list(c) for t, c in sorted(SECRET_COLUMNS.items())},
        "json_columns": {t: list(c) for t, c in sorted(JSON_COLUMNS.items()) if c},
        "already_sealed": {t: list(c) for t, c in sorted(ALREADY_SEALED.items())},
        "column_count": sum(len(c) for c in SECRET_COLUMNS.values()),
    }
