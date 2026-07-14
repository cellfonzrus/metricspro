"""Shared tenant-membership primitives — the ONE rule for "which tenant is this login acting as".

A single Supabase login (one `auth_id`) may belong to more than one tenant: mig 706 relaxes
`storeops.app_users.auth_id` from a GLOBAL unique to a per-`(auth_id, org_id)` unique, so a shared
cross-tenant login has one `app_users` row per tenant. Any module that resolves a caller's tenant from
their JWT must therefore use the SAME selection rule, or two modules disagree about which tenant a
request acts as (a cross-tenant leak). This module is that single source of truth.

The rule (mirrors `app.core.tenant_middleware` exactly):
  - the client declares the acting tenant via the `x-active-org` header — UNTRUSTED, honored ONLY when
    it names one of the login's own memberships;
  - otherwise the login's DEFAULT membership is used (the row flagged `is_default_org`, else the
    earliest by `created_at`).

Everything is tolerant of mig 706 being un-run: pre-706 every login has exactly one `app_users` row,
so `list_memberships` returns a single row and `pick_membership` returns it regardless of `active_org`
— byte-identical to the single-tenant behaviour that predates the switcher.

Import surface (safe for any module — no side effects, stdlib only):
    from app.modules.core.membership import list_memberships, pick_membership
"""


def list_memberships(client, uid):
    """All `storeops.app_users` rows for this Supabase `auth_id`, earliest-first.

    `client` is a supabase-py client (e.g. `get_supabase()`). Tolerant of mig 706 being un-run:
    `is_default_org` / `created_at` are post-706 columns, so if the ordered select raises we fall back
    to an unordered select (pre-706 there is at most one row anyway). Returns `[]` when `uid` is falsy
    or the lookup fails, so a caller never has to guard for None."""
    if not uid:
        return []
    tbl = client.schema("storeops").table("app_users")
    try:
        return (tbl.select("*").eq("auth_id", uid).order("created_at").execute().data) or []
    except Exception:
        try:
            return (tbl.select("*").eq("auth_id", uid).execute().data) or []
        except Exception:
            return []


def pick_membership(rows, active_org=None):
    """The single membership row for the tenant the request acts as, from `rows`
    (`list_memberships(...)` output).

    `active_org` (the `x-active-org` header value) wins ONLY when it names one of the memberships;
    otherwise the row flagged `is_default_org` wins, else the first (earliest). Returns None when the
    login has no memberships (unprovisioned). This is deliberately identical to the middleware's
    `_pick_active_org` so backend handlers and the middleware agree on the acting tenant."""
    if not rows:
        return None
    if active_org:
        for r in rows:
            if r.get("org_id") == active_org:
                return r
    return next((r for r in rows if r.get("is_default_org")), rows[0])
