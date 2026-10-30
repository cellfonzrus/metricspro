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


# ── What a human PICKS this company by ───────────────────────────────────────────────────────────
# The second question this fact answers, added 2026-10-30 (owner, with a screenshot of the mobile
# "Choose a company" screen listing four raw UUIDs): *"How do I know which company is which — it
# should show the company name on the app instead of company id"*.
#
# A LABEL IS NOT A NAME. `display_name` is deliberately allowed to be blank: a receipt must not
# claim to be from a business that has not named itself. A PICKER cannot work that way — a row with
# no label is a button nobody can choose, and the previous fallbacks (`"Tenant"` server-side for
# every unnamed org, `org_id` client-side) were each a different wrong answer to the same question.
# So the label is the name when there is one, the slug read back as words when there is not, and a
# plain generic word otherwise — and it is NEVER a UUID, whatever the stored row holds.
#
# It lives beside `display_name` rather than in the picker because the picker is not the only asker:
# the post-login chooser, the Settings switcher and the operator's facet list all want it, and that
# is three places for the fallback to drift apart again.

import re as _re

#: What the id looks like. A label of this shape is the defect, not a label.
_UUID = _re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", _re.I)

#: The last resort. Generic by RULE TWO — it names no tenant, and it is a word, not an id.
GENERIC_LABEL = "Company"

#: The column the slug lives in, named once for the same reason `NAME_COLUMN` is.
SLUG_COLUMN = "slug"


def looks_like_uuid(value) -> bool:
    """Is this text the shape of an org id? The one test for "this is an id, not a label"."""
    return bool(_UUID.match(str(value or "").strip()))


def _words_from_slug(slug) -> str:
    """`ny-logistics` → `Ny Logistics`. A slug is the name the tenant typed, machine-folded; read
    back it is far closer to what its people call the business than any stand-in we could invent.

    An id stored in the slug column reads as nothing, not as an id with its hyphens knocked out.
    Only the FIRST letter of each word is raised (not `str.title()`, which also lowercases the rest
    and would turn `LLC` into `Llc`) — the same rule the app half applies, pinned by
    `harness_company_label_lock.py` §L3, which runs both and compares them row for row.
    """
    if looks_like_uuid(slug):
        return ""
    text = " ".join(str(slug or "").replace("-", " ").replace("_", " ").split())
    return _re.sub(r"\b\w", lambda m: m.group(0).upper(), text)


def picker_label_from(row, org_id=None) -> str:
    """What to PRINT on a button that chooses this company. Never blank, never a UUID.

    `org_id` is accepted so a caller passes the whole membership without having to decide anything,
    and is used only to be ignored: it is here to make explicit that the id is NOT a fallback. That
    is the defect this function exists to make impossible.
    """
    name = display_name_from(row)
    if name and not looks_like_uuid(name):
        return name
    slug = _words_from_slug((row or {}).get(SLUG_COLUMN))
    if slug and not looks_like_uuid(slug):
        return slug
    return GENERIC_LABEL


class LabelMap(dict):
    """`{org_id: label}` that answers for an org it was never asked about, too.

    A caller that looks up an id outside the batch it requested used to get `None` and spell its own
    `or "Tenant"` — seven times over in the helpdesk alone. A map that cannot miss removes the
    reason to spell one: `.get()` and `[]` both answer the generic label rather than nothing.
    """

    def __missing__(self, _key):
        return GENERIC_LABEL

    def get(self, key, default=None):  # noqa: A003 - dict's own name
        got = super().get(key, None)
        return got if got else (default if default is not None else GENERIC_LABEL)


def picker_labels(client, org_ids=None) -> "LabelMap":
    """`{org_id: label}` for a list of companies — the SAME fact as `picker_label_from`, read once.

    An org with no row, or a read that fails outright, still gets a label: a login must be able to
    pick a company even when its tenants row is missing, which is precisely when the old code showed
    an id. Any failure degrades to the generic label, never to an exception and never to the id.
    """
    ids = [str(i) for i in (org_ids or []) if i]
    rows = {}
    if ids:
        try:
            got = (_so(client).table("tenants")
                   .select(f"org_id,{NAME_COLUMN},{SLUG_COLUMN}")
                   .in_("org_id", ids).order("org_id").execute().data) or []
            rows = {str(r.get("org_id")): r for r in got if r.get("org_id")}
        except Exception:
            rows = {}
    return LabelMap((i, picker_label_from(rows.get(i), i)) for i in ids)
