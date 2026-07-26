"""Store-visit contributions to the UNIVERSAL admin-attention system (core/import_health.py).

OWNER DIRECTIVE 2026-07-26 (settings + imports audit). Registered from storevisit/router.py's own
bottom-of-file guarded import — no NEEDS CORE, no main.py change. Read-only: no visit/checklist data
is written here.
"""
try:
    from app.modules.core.import_health import register_provider
except Exception:                      # mig 717 / import_health not present in this deployment yet
    def register_provider(*_a, **_k):
        def _deco(fn):
            return fn
        return _deco


@register_provider("storevisit_checklist_template", label="Store-visit checklist template",
                   group="other", cost="cheap")
def _p_checklist_template(client, org_id, ctx):
    """A tenant with District Managers (a 'market_manager'-scope role, per storeops.roles) but ZERO
    active storeops.checklist_items rows has no template to hand its DMs at all — every store visit
    would show an empty checklist with nothing to check off. This is normally impossible (mig 076's
    seed_tenant_defaults() seeds 16 standard items for every tenant on first sync), so seeing this
    fire at all means that seed never ran for this org — a stale SEED_VERSION watermark, or a tenant
    provisioned outside the normal flow."""
    try:
        n = (client.schema("storeops").table("checklist_items").select("id", count="exact")
             .eq("org_id", org_id).eq("is_active", True).limit(1).execute()).count or 0
    except Exception:
        return []
    if n > 0:
        return []
    try:
        roles = (client.schema("storeops").table("roles").select("name,permissions")
                 .eq("org_id", org_id).limit(200).execute().data) or []
    except Exception:
        roles = []
    dm_roles = {r.get("name") for r in roles
                if isinstance(r.get("permissions"), dict) and (r["permissions"].get("scope") or "") == "market"}
    if not dm_roles:
        return []
    try:
        dm_count = (client.schema("storeops").table("app_users").select("id", count="exact")
                    .eq("org_id", org_id).in_("role", list(dm_roles)).limit(1).execute()).count or 0
    except Exception:
        dm_count = 0
    if not dm_count:
        return []
    return [{
        "group": "other", "key": "storevisit_no_checklist_template", "severity": "warning",
        "label": "No store-visit checklist template configured",
        "detail": (f"This tenant has {dm_count} District Manager account(s) but zero active checklist "
                  f"items — a store visit has nothing to check off. Add items on Visit Checklist "
                  f"(company-wide admin), or ask support to re-run the tenant's default-content sync "
                  f"if this used to have items."),
        "count": 1, "deep_link": "/storeops/visits/settings", "deep_link_label": "Open Visit Checklist",
    }]
