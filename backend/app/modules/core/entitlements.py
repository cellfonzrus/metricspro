"""Tenant entitlement + auto-provisioning engine (multi-tenant SaaS).

POLICY (product owner, confirmed): ALL-ACCESS BY DEFAULT. A tenant with no billing_plan — or a
plan whose `modules` list is empty/NULL — is all-access and automatically receives EVERY module,
including every module that ships in the future. Only a tenant on an explicit pay-per-module plan
(storeops.billing_plan.modules = a non-empty list) is limited to just those modules.

Two layers, both reconciled by sync_tenant():
  1. ENTITLEMENT — storeops.tenant_modules rows are upserted to match effective_modules().
  2. CONTENT — the SQL function commcalc.seed_tenant_defaults(org_id) (migration 076) seeds all
     tenant-safe, CARRIER-NEUTRAL default template content (HR onboarding, store-visit checklist,
     default company, ticket counter). It is the single source of truth for default content and is
     idempotent (ON CONFLICT DO NOTHING). It deliberately seeds NO default carrier.

HOW NEW FEATURES AUTO-PROPAGATE: bump SEED_VERSION whenever the default module set or
seed_tenant_defaults() changes. /core/me compares the tenant's storeops.tenants.seed_version to
SEED_VERSION and re-runs sync_tenant() when it is behind — so every existing tenant self-provisions
the new feature on its next login, with no further migration or manual step.
"""
from app.core.database import get_supabase

# Bump whenever ALL_MODULES changes OR commcalc.seed_tenant_defaults() gains new content, so every
# tenant re-syncs on its next /core/me. (1 = initial tenant-provisioning engine, mig 076; 2 = mig 077
# folded the configurable HR intake-capture form into seed_tenant_defaults(); 3 = mig 079 expanded
# seed_intake_fields() into the comprehensive HR packet — work eligibility, W-4, policies.)
SEED_VERSION = 3

# Canonical module registry. tenant_modules + billing_plan.modules key off these module_keys.
MODULE_CATALOG = {
    "commissions": "Commissions",
    "targets": "Daily Targets",
    "asset": "Asset & Inventory",
    "vip": "VIP Wireless",
    "storeops": "StoreOps",
    "closing": "Daily Closing",
    "notify": "Notifications",
    "helpdesk": "Helpdesk",
    "hr": "HR / People",
    "account": "Accounting",
}
ALL_MODULES = list(MODULE_CATALOG.keys())


def effective_modules(client, org_id: str) -> set:
    """Modules a tenant is ENTITLED to. All-access unless an explicit pay-per-module plan restricts.
    A billing_plan with modules NULL/empty (or no plan at all) = all-access = every module."""
    try:
        rows = (client.schema("storeops").table("billing_plan")
                .select("modules").eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        rows = []
    if rows:
        mods = rows[0].get("modules")
        if mods:  # a non-empty list = pay-per-module → only these (ignore any unknown keys)
            return {m for m in mods if m in MODULE_CATALOG}
    return set(ALL_MODULES)  # no plan / NULL / empty modules = all-access


def sync_tenant(client, org_id: str) -> dict:
    """Reconcile ONE tenant to its entitlement + seed tenant-safe default content. Idempotent and
    safe to re-run. Only stamps seed_version once the content seed actually succeeded, so a tenant
    keeps retrying on each /core/me until migration 076 has been run."""
    mods = effective_modules(client, org_id)
    rows = [{"org_id": org_id, "module_key": m, "is_enabled": (m in mods)} for m in ALL_MODULES]
    try:
        client.schema("storeops").table("tenant_modules").upsert(
            rows, on_conflict="org_id,module_key").execute()
    except Exception:
        pass  # tenant_modules (mig 053) absent in some envs — non-fatal
    seeded = False
    try:
        client.schema("commcalc").rpc("seed_tenant_defaults", {"p_org": org_id}).execute()
        seeded = True
    except Exception:
        pass  # seed fn (mig 076) not run yet — non-fatal; retried on the next sync
    if seeded:
        try:
            client.schema("storeops").table("tenants").update(
                {"seed_version": SEED_VERSION}).eq("org_id", org_id).execute()
        except Exception:
            pass
    return {"org_id": org_id, "enabled_modules": sorted(mods), "content_seeded": seeded}


def sync_all_tenants(client=None) -> dict:
    """Reconcile every registered tenant. Used by the post-deploy backfill + super-admin action."""
    client = client or get_supabase()
    try:
        tens = client.schema("storeops").table("tenants").select("org_id").execute().data or []
    except Exception:
        tens = []
    results = [sync_tenant(client, t["org_id"]) for t in tens if t.get("org_id")]
    return {"synced": len(results), "detail": results}


def needs_sync(client, org_id: str) -> bool:
    """True when this tenant's stamped seed_version is behind the code's SEED_VERSION (or unset)."""
    try:
        rows = (client.schema("storeops").table("tenants")
                .select("seed_version").eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return False
    if not rows:
        return False  # not a registered tenant (or tenants table absent) — nothing to sync
    sv = rows[0].get("seed_version")
    return sv is None or sv < SEED_VERSION
