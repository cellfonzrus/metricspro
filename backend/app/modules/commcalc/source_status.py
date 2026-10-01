"""What a data-source login row SAYS about itself (index §41a, owner 2026-10-01). Stdlib only.

The row carries two kinds of status: `auth_status` (the state) and `auth_message` (the sentence the Vendors /
Data Sources pages show). Success paths stamped auth_status='authenticated' and left auth_message alone, so the
last FAILURE's sentence stayed on screen beside a green state ("The vendor portal did not accept the saved
login…" on a vendor that had just priced 194 items). A status row is a record of what happened LAST — so one
rule, applied in the one status writer (commcalc/router._source_stamp): a patch that declares the login good
also replaces the sentence, unless it brings its own.
"""

SIGNED_IN = "Signed in — the last read used the saved login."


def with_fresh_message(patch):
    """The patch as written: when it sets auth_status='authenticated' without an auth_message, the stale
    message is replaced. Any other patch is returned unchanged (a failure brings its own reason)."""
    upd = dict(patch or {})
    if upd.get("auth_status") == "authenticated" and not upd.get("auth_message"):
        upd["auth_message"] = SIGNED_IN
    return upd
