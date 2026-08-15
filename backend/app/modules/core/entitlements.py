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
from fastapi import HTTPException

from app.core.database import get_supabase

ORG_ID = "00000000-0000-0000-0000-000000000001"   # house org (middleware rewrites the query param)

# Bump whenever ALL_MODULES changes OR commcalc.seed_tenant_defaults() gains new content, so every
# tenant re-syncs on its next /core/me. (1 = initial tenant-provisioning engine, mig 076; 2 = mig 077
# folded the configurable HR intake-capture form into seed_tenant_defaults(); 3 = mig 079 expanded
# seed_intake_fields() into the comprehensive HR packet — work eligibility, W-4, policies.)
SEED_VERSION = 12  # bumped: 12 = the 2026-08-14 training pack (v3 of app/data/training_tours_seed.json)
                   #              adds the two Time Clock walk-throughs — the rep's "Clocking out — and
                   #              when you need permission" (auto clock-out at shift end + 5 min grace,
                   #              late-clock-out and second-session approval) and the manager's "Approve
                   #              late clock-outs & second sessions" board tour. Seeded onto the HOUSE
                   #              org (never-clobber) so every tenant READS them; a bump re-runs the
                   #              house seed pass so the new tours land without a manual re-seed.
                   # 11 = mig 800 registered the "crm" module (sales pipeline + follow-up +
                   #              Customer 360), so every EXISTING tenant self-provisions a crm
                   #              tenant_modules entitlement row on its next login. The CRM's own
                   #              default CONTENT (pipeline, stages, dispositions, cadence) is seeded
                   #              by core.seed_crm_defaults(), which migration 800 ran for every
                   #              tenant that existed and crm/router.py re-runs lazily on first touch
                   #              — so a tenant created later still lands on a working pipeline. No
                   #              money code, no payout column: leads are not money.
                   #         10 = the 2026-08-10 training pack (v2 of app/data/training_tours_seed.json)
                   #              adds the Point of Sale walk-throughs — setup wizard, sales tax, the
                   #              register, stock + activations, who can use it — plus imports/daily
                   #              uploads. The tours live on the HOUSE org and every tenant READS them,
                   #              so only the house org's sync pass has to run; this bump is what makes
                   #              it run. Without it the new tours reach a brand-new tenant and nobody
                   #              else, which is the exact silent-miss this counter exists to prevent.
                   #              Never-clobber still applies: a tour edited in /admin/training is
                   #              skipped by the reseed. No new entitlement module, no new permission
                   #              key, no money code.
                   #          9 = mig 721 (What's New) added the bundled platform release-note pack, which
                   #             loads into the HOUSE org on its sync pass so every tenant's ADMIN STAFF see
                   #             the new-features / improvements feed beside the login warnings. No new
                   #             entitlement module: it is an admin surface gated by the SAME gate as the
                   #             warnings (import_health.can_view_attention), not a billable module.
                   #         8 = mig 720 (Training Center) registered the "training" module — so every
                   #             existing tenant self-provisions a training tenant_modules entitlement row
                   #             on its next login — AND added the bundled platform-default walk-through
                   #             pack, which loads into the HOUSE org on its sync pass (never clobbering an
                   #             edited tour). The Training Center page itself is NOT entitlement-gated:
                   #             help/training is universal like the "?" panel, and gating a brand-new
                   #             module key would silently hide it from every EXISTING role (seeded role
                   #             modules are forward-only). The entitlement row exists for billing hygiene
                   #             so the module is never "missing" for a tenant.
                   #         7 = mig 718 (Auto-Fix Pipeline) added core.seed_token_rates(), which sync_tenant
                   #             now calls on the HOUSE org's pass, so the AI token-rate table self-seeds on
                   #             the next login instead of depending on the migration's own seed line having
                   #             been run. Idempotent (ON CONFLICT DO NOTHING) — it never clobbers an
                   #             owner-edited rate. No new entitlement module: the fix-request board is a
                   #             PLATFORM (super-admin) surface like /admin/tenants, not a billable module.
                   #         6 = mig 715 registered the "support" (Tech Support) module, so every existing
                   #             tenant self-provisions a support tenant_modules entitlement row on its next
                   #             login (the cross-tenant console is HOUSE-gated regardless; this is billing
                   #             hygiene so the module isn't "missing" for any tenant).
                   #         5 = mig 708 folded the carrier-neutral remediation playbook catalog into
                   #             seed_tenant_defaults(), so every tenant (not just the house org) gets the
                   #             Auto-Remediation starter catalog on its next login.
                   #         4 = added "ai_assistant" capability (auto-provisions to every tenant on next login)

# ── ONE canonical module registry (platform-core-3) ────────────────────────────────────────────
# The DB table core.module_catalog (migration 700) is the SINGLE SOURCE OF TRUTH. This in-code dict
# is (a) the seed source that migration mirrors and (b) the FALLBACK used whenever the table is
# absent/empty — so an unrun migration is a no-op (the app is byte-identical either way). Every other
# module list (seeded role perms, org-level role perms, rbac.ts nav tags) reconciles to these keys.
# tenant_modules + billing_plan.modules key off these CANONICAL module_keys.
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
    "ai_assistant": "AI Assistant",
    "support": "Tech Support",
    "training": "Training Center",
    "crm": "CRM / Sales Pipeline",
}
ALL_MODULES = list(MODULE_CATALOG.keys())

# Legacy / frontend key → CANONICAL module_key. The frontend historically tagged Finance nav + roles
# with `accounts`; the backend canonical entitlement key is `account`. Any inbound alias normalizes to
# canonical (mirrors core.module_alias, mig 700). Keep the backend key as the winner.
MODULE_ALIASES = {"accounts": "account"}

# Role-permission GATE keys that are NOT tenant-entitlement modules (they gate RBAC visibility, not
# billing). `admin` = the super-admin/role-management gate (isSuperAdmin in rbac.ts). Kept OUT of the
# entitlement catalog so effective_modules() / tenant_modules never treat `admin` as a billable module.
ROLE_GATE_KEYS = ("admin",)


def canonical_module_key(key: str) -> str:
    """Normalize a possibly-aliased module key to its canonical form (e.g. 'accounts' → 'account')."""
    return MODULE_ALIASES.get(key, key)


def load_module_catalog(client=None) -> dict:
    """Canonical module registry {key: label}. Reads core.module_catalog (mig 700) when present;
    falls back to the in-code MODULE_CATALOG so an unrun migration is a no-op. Best-effort, never
    raises. Aliases are collapsed to canonical."""
    try:
        client = client or get_supabase()
        rows = (client.schema("core").table("module_catalog")
                .select("key,label,sort_order").order("sort_order").execute().data) or []
        cat = {r["key"]: (r.get("label") or r["key"]) for r in rows if r.get("key")}
        if cat:
            return cat
    except Exception:
        pass
    return dict(MODULE_CATALOG)


def all_modules(client=None) -> list:
    """Canonical module_keys in registry order (DB-backed with in-code fallback)."""
    return list(load_module_catalog(client).keys())


# ── Entitlement enforcement (shared, adoptable by every module) ─────────────────────────────────
def module_enabled(org_id: str, key: str, client=None) -> bool:
    """True if `key` (canonical or aliased) is enabled for the tenant per storeops.tenant_modules.
    Fails OPEN if tenant_modules is unreachable (mig 053 unrun) — a missing migration must never
    black-hole a whole module. A tenant with no row for the module = NOT enabled (entitlement gate)."""
    key = canonical_module_key(key)
    try:
        client = client or get_supabase()
        rows = (client.schema("storeops").table("tenant_modules").select("is_enabled")
                .eq("org_id", org_id).eq("module_key", key).limit(1).execute().data or [])
    except Exception:
        return True   # tenant_modules table/infra unreachable → degrade gracefully (fail open)
    return bool(rows and rows[0].get("is_enabled"))


def assert_module_enabled(org_id: str, key: str, client=None) -> None:
    """Imperative gate: raise 403 unless `key` is enabled for the tenant. Drop-in for a module's own
    _require_module()."""
    if not module_enabled(org_id, key, client):
        raise HTTPException(403, f"{canonical_module_key(key)} not enabled for this tenant")


def require_module(key: str):
    """FastAPI dependency factory — the shared entitlement gate modules can adopt. Usage:

        from app.modules.core.entitlements import require_module
        router = APIRouter(prefix="/account", dependencies=[Depends(require_module("account"))])

    or per-endpoint: `dependencies=[Depends(require_module("closing"))]`. It resolves org_id from the
    (tenant-middleware-rewritten) `org_id` query param and 403s if the module is not enabled for that
    tenant. Carrier-/tenant-neutral: it only reads the entitlement, never branches on a tenant name."""
    canon = canonical_module_key(key)

    async def _guard(org_id: str = ORG_ID):
        assert_module_enabled(org_id, canon)

    _guard.__name__ = f"require_module_{canon}"
    return _guard


def effective_modules(client, org_id: str) -> set:
    """Modules a tenant is ENTITLED to. All-access unless an explicit pay-per-module plan restricts.
    A billing_plan with modules NULL/empty (or no plan at all) = all-access = every module."""
    catalog = load_module_catalog(client)
    try:
        rows = (client.schema("storeops").table("billing_plan")
                .select("modules").eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        rows = []
    if rows:
        mods = rows[0].get("modules")
        if mods:  # a non-empty list = pay-per-module → only these (aliases normalized; unknown dropped)
            return {canonical_module_key(m) for m in mods if canonical_module_key(m) in catalog}
    return set(catalog.keys())  # no plan / NULL / empty modules = all-access


def sync_tenant(client, org_id: str) -> dict:
    """Reconcile ONE tenant to its entitlement + seed tenant-safe default content. Idempotent and
    safe to re-run. Only stamps seed_version once the content seed actually succeeded, so a tenant
    keeps retrying on each /core/me until migration 076 has been run."""
    mods = effective_modules(client, org_id)
    rows = [{"org_id": org_id, "module_key": m, "is_enabled": (m in mods)} for m in all_modules(client)]
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
    # HOUSE org only: load the bundled tech-support help-doc packs (mig 715). Zero manual import — they
    # land on the house org's sync pass (triggered by the SEED_VERSION 6 bump). NEVER clobbers a
    # human-edited row (only inserts missing page_keys / refreshes rows whose updated_by is NULL or 'seed').
    # Best-effort: an un-run mig 715 (support_doc absent) or a missing bundle file is a silent no-op.
    if org_id == ORG_ID:
        try:
            from app.modules.core.support_seed import seed_support_docs
            seed_support_docs(client, org_id)
        except Exception:
            pass
        # HOUSE org only: seed the AI token-rate table (mig 718). Rates are PLATFORM config — the house
        # rows are the default every tenant prices against (a tenant row overrides for that tenant), so
        # this runs once on the house pass, not per tenant. Idempotent inside the SQL function
        # (ON CONFLICT DO NOTHING) ⇒ an owner-edited rate is never clobbered. An un-run mig 718 is a
        # silent no-op, so the fix-pipeline board simply shows no $ until the migration lands.
        try:
            client.schema("core").rpc("seed_token_rates", {"p_org": org_id}).execute()
        except Exception:
            pass
        # HOUSE org only: load the bundled PLATFORM-DEFAULT training walk-throughs (mig 720). Same shape
        # and rationale as the support-doc pack above — the house rows ARE the platform defaults every
        # tenant reads, so this runs once on the house pass, not per tenant. NEVER clobbers a tour a
        # human has edited (updated_by not NULL/'seed'). An un-run mig 720 or a missing bundle file is a
        # silent no-op, so the Training Center simply shows its empty state until the migration lands.
        try:
            from app.modules.core.training_seed import seed_training_tours
            seed_training_tours(client, org_id)
        except Exception:
            pass
        # HOUSE org only: load the bundled platform What's New entries (mig 721). Same never-clobber
        # shape as the two seeds above; an un-run mig 721 is a silent no-op, so the popup simply shows
        # its existing Warnings tab until the migration lands.
        try:
            from app.modules.core.whats_new_seed import seed_release_notes
            seed_release_notes(client, org_id)
        except Exception:
            pass
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
