"""PLATFORM-CORE attention providers — tenant provisioning + system-error backlog.

Registered into the shared attention registry (`core.import_health.register_provider`) exactly the way a
module is supposed to contribute an item: no edit to the aggregator, no new endpoint, no new gate. Imported
for its side effect from core/router.py (next to the import-health sub-router include).

OWNER ASK 2026-07-26 (item 5 of the dispatch): audit platform-core's OWN settings the way the module agents
are being audited — anything REQUIRED-but-unconfigured, or wiring that is silently dead, must surface as an
attention item with plain-language fix instructions and a working deep link.

CONTRACT (see import_health's module docstring): every item below reports LIVE state only and clears on the
next GET /core/attention once the fix is done on the page it links to. Every read is org-scoped on the
ACTING org (the middleware-rewritten query param, clamped for non-super-admins), CHEAP (config-table reads),
and exception-guarded — a provider may never break the admin popup.

  tenant_unregistered  — this org has no storeops.tenants row, so entitlements resolve to "no modules" and
                         nothing seeds. Clears when a super-admin registers the tenant at /admin/tenants.
  tenant_seed_behind   — the tenant's stamped seed_version is behind the code's SEED_VERSION, which means
                         sync_tenant() ran but commcalc.seed_tenant_defaults() FAILED (usually a migration
                         that has not been run) — default content for newer features is missing for this
                         tenant. Clears on the next login after the seed succeeds.
  failures_unreviewed  — error/critical rows in core.failure_log that nobody has reviewed. Clears the moment
                         they are cleared on /failures (mig 716's soft `reviewed` state — rows are kept).
"""
from datetime import timedelta

from app.modules.core.import_health import register_provider, _item, _now

# How far back the unreviewed-error backlog looks, and the severities worth interrupting an admin for.
# Deliberately NOT every failure row: face-mismatch style warnings are high-volume and self-healing, and an
# item nobody can act on is exactly what makes people ignore the popup.
_FAILURE_WINDOW_DAYS = 14
_FAILURE_SEVERITIES = ("error", "critical")
_FAILURE_MAX = 500          # bound the read; the page itself paginates


@register_provider("tenant_provisioning", label="Tenant provisioning / seeding",
                   group="config", cost="cheap")
def _p_tenant_provisioning(client, org_id, ctx):
    """Is the ACTING org actually provisioned? One config read (`select *` deliberately: the columns differ
    by how many migrations an environment has run, and a missing column must not blank the whole check)."""
    try:
        rows = (client.schema("storeops").table("tenants").select("*")
                .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return []          # tenants table unreachable (very early env) → say nothing
    if not rows:
        return [_item("config", "tenant_unregistered", "error", "This organization is not registered",
                      "No tenant record exists for the organization you are signed in to, so module "
                      "entitlements resolve to nothing and no default content can be installed. A "
                      "super-admin needs to register it on the Tenants page (that also runs provisioning).",
                      1, "/admin/tenants", "Open Tenants")]
    row = rows[0]
    if "seed_version" not in row:
        return []          # pre-mig-076 environment: nothing to compare against
    try:
        from app.modules.core.entitlements import SEED_VERSION
    except Exception:
        return []
    sv = row.get("seed_version")
    if sv is not None and int(sv) >= int(SEED_VERSION):
        return []
    return [_item("config", "tenant_seed_behind", "warning", "Default setup content is missing",
                  f"This tenant is provisioned at setup version {sv if sv is not None else 'none'} but the "
                  f"app ships version {SEED_VERSION}, which means the automatic seeding step is failing — "
                  f"usually a database migration has not been run yet. Newer features can be missing their "
                  f"starter templates/catalogues for this tenant. It retries on every sign-in; if it keeps "
                  f"reporting, run the pending migrations and re-sync the tenant.",
                  1, "/admin/tenants", "Open Tenants")]


def _recent_failures(client, org_id, since_iso):
    """Unreviewed failure rows since `since_iso`. mig 716 added the `reviewed` column; if it is absent the
    filter 400s, so we retry WITHOUT it and fall back to the pre-716 concept (status='open')."""
    tbl = lambda: client.schema("core").table("failure_log")          # noqa: E731
    cols = "id,category,severity,status,created_at,reviewed"
    try:
        return (tbl().select(cols).eq("org_id", org_id).eq("reviewed", False)
                .gte("created_at", since_iso).order("created_at", desc=True)
                .limit(_FAILURE_MAX).execute().data) or []
    except Exception:
        pass
    try:
        rows = (tbl().select("id,category,severity,status,created_at").eq("org_id", org_id)
                .gte("created_at", since_iso).order("created_at", desc=True)
                .limit(_FAILURE_MAX).execute().data) or []
    except Exception:
        return []
    return [r for r in rows if (r.get("status") or "open") == "open"]


@register_provider("failure_backlog", label="System errors awaiting review",
                   group="system", cost="cheap")
def _p_failure_backlog(client, org_id, ctx):
    """Errors the app logged for THIS tenant that nobody has looked at. Severity filtering happens in
    Python (one indexed read, no `in_` round-trip) so an unexpected severity value can never hide a row."""
    now = ctx.get("now") or _now()
    since = (now - timedelta(days=_FAILURE_WINDOW_DAYS)).isoformat()
    rows = _recent_failures(client, org_id, since)
    hot = [r for r in rows if (r.get("severity") or "").strip().lower() in _FAILURE_SEVERITIES]
    if not hot:
        return []
    kinds = sorted({(r.get("category") or "other") for r in hot})
    return [_item("system", "failures_unreviewed", "warning", "System errors need review",
                  f"{len(hot)} error(s) logged in the last {_FAILURE_WINDOW_DAYS} days have not been "
                  f"reviewed ({', '.join(kinds[:3])}{'…' if len(kinds) > 3 else ''}). Failure Logs explains "
                  f"each one in plain English with how to fix it; clearing a group marks it reviewed (the "
                  f"rows are kept for the audit trail) and removes this notice.",
                  len(hot), "/failures", "Open Failure Logs")]
