"""WHAT IS THIS TENANT CALLED — the ONE home (owner ask 2026-10-17, index §POS-02).

Owner, verbatim: *"the from email address should show the company name Vzone Wireless or whatever
name is set up during onboarding"*.

**The fact.** A tenant's name is the one it typed at onboarding and keeps in `storeops.tenants.name`.
Everything a customer or an employee reads which claims to be from the business — an emailed receipt's
From line, a statement's heading, a document's title — is naming THAT, and must name it the same way.

**Why a module.** The name was being read ad hoc, `select("name")` at a time, with each caller
spelling its own fallback (`""`, `"Boost"`, the carrier's name). A name read five ways is five names
the day one caller trims differently or one tenant leaves the field blank. So the read lives here and
callers dereference it; `backend/harness_pos_receipt_render.py` (§E6) fails the build if a second
reader of `storeops.tenants.name` appears outside this module.

PURE + one I/O wrapper: `display_name_from` takes the row and is provable offline; `display_name`
reads it, org-scoped, and degrades to "" rather than raising — a tenant with no name still gets a
receipt, it just does not claim to be from anyone.
"""
from __future__ import annotations

#: The column the name lives in, named once so a reader cannot drift onto a different one.
NAME_COLUMN = "name"


def _so(client):
    """The storeops-schema handle, whether the caller passed the root client or an already
    schema-bound one (`sb()` in storeops/* returns the latter). Dereferencing this fact must not
    depend on which of the two a module happens to hold, or the rewiring stops at the awkward
    callers — which is how the ad-hoc reads got there in the first place.
    """
    schema = getattr(client, "schema", None)
    if callable(schema):
        try:
            return schema("storeops")
        except Exception:
            return client
    return client


def display_name_from(row) -> str:
    """The tenant's name from its row — trimmed to one line, blank when there is none.

    Never a placeholder: an empty name is reported as empty so the CALLER can decide what to show
    (the receipt falls back to the store's own name; an email simply sends with no display name).
    A fabricated stand-in here would print on a customer's receipt as if the business were called it.
    """
    return " ".join(str((row or {}).get(NAME_COLUMN) or "").split())


def display_name(client, org_id) -> str:
    """The tenant's name, org-scoped. Any failure (missing table, no row, no permission) reads as
    blank, because no surface in this platform should fail because a business has not been named."""
    try:
        rows = (_so(client).table("tenants").select(NAME_COLUMN)
                .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return ""
    return display_name_from(rows[0] if rows else None)


def display_names(client, org_ids=None) -> dict:
    """Every tenant's name keyed by org id — the SAME fact as `display_name`, read once for a list.

    The second shape a caller needs: an operator's company picker, a facet list, a sweep that visits
    each tenant in turn. It is here rather than in each of those so "which column means the name"
    and "how a blank one reads" stay one answer. `org_ids` None = every tenant (operator surfaces and
    cross-tenant sweeps, which are already behind their own gates); a list scopes the read.
    Any failure reads as an empty map, never an exception.
    """
    try:
        q = _so(client).table("tenants").select(f"org_id,{NAME_COLUMN}")
        if org_ids is not None:
            ids = [str(i) for i in org_ids if i]
            if not ids:
                return {}
            q = q.in_("org_id", ids)
        rows = q.order("org_id").execute().data or []
    except Exception:
        return {}
    return {str(r["org_id"]): display_name_from(r) for r in rows if r.get("org_id")}
