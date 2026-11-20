"""ONE home for *"a stored credential shown to a person is an audited event"*.

Index §CORE-46. Lock: §F of `backend/harness_feed_day_coverage.py`. Audit table: migration 1128.

WHY THIS EXISTS. Owner, 2026-11-20, asked that the per-store download screen SHOW each store's back
office password, because a person has to type it into a portal this platform is not allowed to drive
(§COMMCALC-74/75). That is a legitimate ask and it is also the first time this platform hands a stored
credential back to a browser: every other read strips it (`router._strip_source_pw`,
`core/portal_secret`). Granting it in the screen's own endpoint would have made the decision a
property of one screen, and the next screen that needs it would decide again.

So the decision lives here, once, with three rules that do not bend per screen:

  1. **A reveal that cannot be RECORDED does not happen.** The audit row is written BEFORE the secret
     is returned, and a failed write refuses the reveal. An unauditable reveal is exactly the thing an
     audit trail exists to prevent, so "best effort" is the wrong posture — it would silently become
     the normal one the first time the migration lagged the code.
  2. **Only a column the credential registry calls a credential can be revealed**, named explicitly by
     the caller. A caller cannot reach an arbitrary column through this door.
  3. **The row must belong to the asking organisation**, and the check is on the row the caller
     fetched, not on a filter the caller promises it applied.

WHAT IS NEVER DONE HERE. The secret is never logged, never put in a URL, never written to the audit
row, and never returned for a row of another tenant. The audit row records WHO asked, FOR WHAT, and
WHEN — never the value.

NO TENANT, CARRIER OR VENDOR NAME APPEARS HERE (RULE TWO), and nothing is per-screen: the caller
passes the relation, the row and who is asking.
"""
SCHEMA = "core"
TABLE = "credential_reveal_audit"

#: The sentence a screen shows when the audit table is not there yet. A reveal is refused rather than
#: served unrecorded, so the operator is told what to run — not left wondering why nothing appeared.
UNAUDITABLE = ("This password cannot be shown yet: the record of who viewed it could not be written, "
               "and a credential is never shown without that record. Apply migration "
               "1128_credential_reveal_audit.sql, then try again.")
NOT_A_CREDENTIAL = ("That field is not a stored credential, so there is nothing to show.")
NOT_FOUND = ("That login was not found for this company.")
EMPTY = ("No password is stored for this login yet, so there is nothing to show.")


def is_credential(relation, column):
    """Is `column` a credential of `relation`, according to the ONE registry of that fact?

    Dereferenced from `core/portal_secret`, never listed again here — a second list is how a column
    stops being treated as a secret in one place and keeps being one in another.
    """
    try:
        from app.modules.core import portal_secret as _psec
        return bool(_psec.is_secret_column(relation, str(column or "")))
    except Exception:
        return False


def audit_row(org_id, *, relation, column, row_id, who, context=""):
    """The row written for ONE reveal. PURE, and it carries NO secret — by construction, not by care:
    the value is not an argument of this function."""
    return {"org_id": str(org_id or ""), "relation": str(relation or ""),
            "column": str(column or ""), "row_id": str(row_id or ""),
            "revealed_to": str(who or "")[:200], "context": str(context or "")[:200]}


def record(client, org_id, *, relation, column, row_id, who, context=""):
    """Write the audit row. True only when it is actually STORED — the caller refuses on False."""
    try:
        res = (client.schema(SCHEMA).table(TABLE)
               .insert(audit_row(org_id, relation=relation, column=column, row_id=row_id, who=who,
                                 context=context)).execute())
        return bool(getattr(res, "data", None) or res)
    except Exception:
        return False


def reveal(client, org_id, *, relation, column, row, who, context=""):
    """The decision and the recording, together. Returns `{"ok": bool, "value": str, "note": str}`.

    `row` is the ALREADY-FETCHED, already-decrypted row — this module does not read the table, so it
    cannot widen anybody's query. Failure modes each carry the one sentence a screen can show.
    """
    if not is_credential(relation, column):
        return {"ok": False, "value": "", "note": NOT_A_CREDENTIAL}
    row = row or {}
    if not row or str(row.get("org_id") or "") != str(org_id or ""):
        return {"ok": False, "value": "", "note": NOT_FOUND}
    value = str(row.get(column) or "")
    if not value:
        return {"ok": False, "value": "", "note": EMPTY}
    if not record(client, org_id, relation=relation, column=column, row_id=row.get("id"),
                  who=who, context=context):
        return {"ok": False, "value": "", "note": UNAUDITABLE}
    return {"ok": True, "value": value, "note": ""}
