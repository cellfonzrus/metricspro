"""Public price list + free-trial settings — the storefront half of billing.

TWO AUDIENCES, deliberately split:

  · GET /billing/public-pricing is ANONYMOUS. It is the only endpoint in this file with no auth, it
    is read by the marketing site (/welcome), and it is allowlisted GET-only in tenant_middleware.
    It serves ONLY published packages (is_public = true) and ONLY the display columns — the internal
    `notes` column never leaves this function. It is also the reason prices are DATA: the website
    renders whatever the operator published here, and has no price of its own to go stale.

  · Everything else is SUPER-ADMIN, gated by the same `_require_super_admin` as the rest of billing.
    This is where the operator sets prices and the trial length.

Nothing is published by default (migration 907 seeds the three packages with is_public = false and
price 0). A price the operator did not type must never reach the public internet, so an empty public
feed is the correct out-of-the-box state — the site falls back to a trial-led "pricing on request"
card rather than inventing a number.

If migration 907 has not been applied, every read degrades to {"ready": false} with the code
defaults instead of 500ing, matching the mig-064 behaviour in router.py.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Header

from app.core.database import get_supabase
from app.core.schemas import LaxModel
from app.modules.core.router import _require_super_admin
from app.modules.billing.trial import VALID_PLAN_STATUS, load_settings, trial_view

router = APIRouter(prefix="/billing", tags=["Pricing & Trial"])

VALID_CYCLE = {"monthly", "annual"}
# The columns the ANONYMOUS feed is allowed to serve. `notes` is internal and absent by construction.
PUBLIC_FIELDS = ("key", "name", "tagline", "price", "cycle", "currency", "unit_label",
                 "price_note", "features", "cta_label", "is_featured", "sort_order")


def sb():
    return get_supabase()


def _public_row(row: dict) -> dict:
    """Project a package row down to the public display fields (allow-list, not deny-list)."""
    return {k: row.get(k) for k in PUBLIC_FIELDS}


# ── PUBLIC: the price list the marketing site renders ─────────────────────────────────────────
@router.get("/public-pricing")
async def public_pricing():
    """PUBLIC (no auth): published packages + the free-trial terms.

    Allowlisted GET-only in tenant_middleware, exactly like /core/signup-status. Returns
    {ready, trial_enabled, trial_days, trial_note, show_pricing, headline, subhead, packages[]}.
    `packages` is empty when nothing is published yet — a valid state the site handles, NOT an error.
    """
    client = sb()
    s = load_settings(client)
    packages: list[dict] = []
    ready = True
    try:
        rows = (client.schema("storeops").table("pricing_package").select("*")
                .eq("is_public", True).order("sort_order").execute().data) or []
        packages = [_public_row(r) for r in rows]
    except Exception:
        ready = False  # migration 907 not applied — trial defaults still answer, price list is empty
    return {
        "ready": ready,
        "trial_enabled": bool(s["trial_enabled"]),
        "trial_days": int(s["trial_days"]),
        "trial_note": s["trial_note"],
        "show_pricing": bool(s["show_pricing"]),
        "currency": s["currency"],
        "headline": s["pricing_headline"],
        "subhead": s["pricing_subhead"],
        "packages": packages if s["show_pricing"] else [],
    }


# ── SUPER-ADMIN: read everything (published or not) ───────────────────────────────────────────
@router.get("/pricing")
async def get_pricing(authorization: str = Header(default="")):
    """Super-admin: the trial settings + EVERY package, including unpublished drafts."""
    _require_super_admin(authorization)
    client = sb()
    try:
        rows = (client.schema("storeops").table("pricing_package").select("*")
                .order("sort_order").execute().data) or []
    except Exception:
        return {"ready": False, "settings": load_settings(client), "packages": []}
    return {"ready": True, "settings": load_settings(client), "packages": rows}


class PricingSettingsIn(LaxModel):
    trial_enabled: Any = None
    trial_days: Any = None
    currency: Any = None
    show_pricing: Any = None
    pricing_headline: Any = None
    pricing_subhead: Any = None
    trial_note: Any = None


@router.post("/pricing/settings")
async def save_pricing_settings(body: PricingSettingsIn, authorization: str = Header(default="")):
    """Super-admin: set the free-trial length and the public pricing copy.

    PATCH semantics — only the keys actually sent are written (model_fields_set), so saving the
    trial length can never blank the headline. Changing trial_days affects companies signing up
    from now on; it does NOT move the end date of a trial already running.
    """
    _require_super_admin(authorization)
    sent = body.model_fields_set
    upd: dict = {}
    if "trial_days" in sent:
        try:
            days = int(body.trial_days)
        except (TypeError, ValueError):
            raise HTTPException(400, "trial_days must be a whole number of days")
        if days < 0 or days > 365:
            raise HTTPException(400, "trial_days must be between 0 and 365")
        upd["trial_days"] = days
    for key in ("trial_enabled", "show_pricing"):
        if key in sent:
            upd[key] = bool(getattr(body, key))
    for key in ("currency", "pricing_headline", "pricing_subhead", "trial_note"):
        if key in sent:
            val = getattr(body, key)
            upd[key] = (str(val).strip() or None) if val is not None else None
    if not upd:
        raise HTTPException(400, "nothing to update")
    upd["id"] = 1
    upd["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        sb().schema("storeops").table("pricing_settings").upsert(upd, on_conflict="id").execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 907 first: {e}")
    return {"ok": True, "settings": load_settings(sb())}


class PackageIn(LaxModel):
    id: Any = None
    key: Any = None
    name: Any = None
    tagline: Any = None
    price: Any = None
    cycle: Any = None
    currency: Any = None
    unit_label: Any = None
    price_note: Any = None
    features: Any = None
    cta_label: Any = None
    is_featured: Any = None
    is_public: Any = None
    sort_order: Any = None
    notes: Any = None


@router.post("/pricing/packages")
async def upsert_package(body: PackageIn, authorization: str = Header(default="")):
    """Super-admin: create or update one package. Keyed on `key` (a stable slug), so saving the same
    key twice edits the card rather than creating a second one. Publishing is an explicit
    is_public = true on this call — a package the operator has not published stays off the site."""
    _require_super_admin(authorization)
    key = str(body.key or "").strip().lower()
    if not key:
        raise HTTPException(400, "key required (a short slug, e.g. 'growth')")
    name = str(body.name or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    cycle = str(body.cycle or "monthly").strip() or "monthly"
    if cycle not in VALID_CYCLE:
        raise HTTPException(400, f"cycle must be one of {sorted(VALID_CYCLE)}")
    try:
        price = float(body.price or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "price must be a number")
    if price < 0:
        raise HTTPException(400, "price cannot be negative")
    features = body.features
    if isinstance(features, str):
        # The editor sends one bullet per line; blank lines are dropped.
        features = [ln.strip() for ln in features.splitlines() if ln.strip()]
    row = {
        "key": key,
        "name": name,
        "tagline": (str(body.tagline).strip() or None) if body.tagline is not None else None,
        "price": price,
        "cycle": cycle,
        "currency": str(body.currency or "USD").strip() or "USD",
        "unit_label": (str(body.unit_label).strip() or None) if body.unit_label is not None else None,
        "price_note": (str(body.price_note).strip() or None) if body.price_note is not None else None,
        "features": features or None,
        "cta_label": (str(body.cta_label).strip() or None) if body.cta_label is not None else None,
        "is_featured": bool(body.is_featured),
        "is_public": bool(body.is_public),
        "sort_order": int(body.sort_order or 0),
        "notes": (str(body.notes).strip() or None) if body.notes is not None else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        sb().schema("storeops").table("pricing_package").upsert(row, on_conflict="key").execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 907 first: {e}")
    return {"ok": True, "key": key}


@router.delete("/pricing/packages/{key}")
async def delete_package(key: str, authorization: str = Header(default="")):
    """Super-admin: remove a package from the price list entirely."""
    _require_super_admin(authorization)
    try:
        sb().schema("storeops").table("pricing_package").delete().eq("key", (key or "").strip().lower()).execute()
    except Exception as e:
        raise HTTPException(500, f"delete failed: {e}")
    return {"ok": True}


# ── SUPER-ADMIN: the trials themselves ────────────────────────────────────────────────────────
@router.get("/trials")
async def list_trials(authorization: str = Header(default="")):
    """Super-admin: every tenant's plan state — who is trialing, how long is left, who has lapsed.

    Computed per-tenant by trial_view() off the stored stamp + the clock, so an expiry shows up the
    moment it happens without anything having to run on a schedule.
    """
    _require_super_admin(authorization)
    try:
        tenants = (sb().schema("storeops").table("tenants").select("*")
                   .order("created_at").execute().data) or []
    except Exception:
        return {"ready": False, "tenants": []}
    return {"ready": True, "tenants": [
        {"org_id": t.get("org_id"), "name": t.get("name"), "is_active": t.get("is_active"),
         "created_at": t.get("created_at"), "package_key": t.get("package_key"),
         "trial": trial_view(t)} for t in tenants]}


class TenantPlanIn(LaxModel):
    org_id: Any = None
    plan_status: Any = None
    extend_days: Any = None
    package_key: Any = None


@router.post("/pricing/tenant-plan")
async def set_tenant_plan(body: TenantPlanIn, authorization: str = Header(default="")):
    """Super-admin: convert a trial, or give a company more trial time.

    · plan_status  — 'active' converts the trial into a paying customer (and, per trial_view, stops
                     the clock mattering for them). 'cancelled' records a churn.
    · extend_days  — pushes trial_ends_at out by N days from whichever is LATER: the current end
                     date, or now. Extending an ALREADY-lapsed trial therefore gives the full N days
                     rather than a window that is already half gone.
    """
    _require_super_admin(authorization)
    org_id = str(body.org_id or "").strip()
    if not org_id:
        raise HTTPException(400, "org_id required")
    client = sb()
    upd: dict = {}
    sent = body.model_fields_set
    if "plan_status" in sent and body.plan_status is not None:
        status = str(body.plan_status).strip()
        if status not in VALID_PLAN_STATUS:
            raise HTTPException(400, f"plan_status must be one of {sorted(VALID_PLAN_STATUS)}")
        upd["plan_status"] = status
    if "package_key" in sent:
        upd["package_key"] = (str(body.package_key).strip() or None) if body.package_key is not None else None
    if "extend_days" in sent and body.extend_days is not None:
        try:
            days = int(body.extend_days)
        except (TypeError, ValueError):
            raise HTTPException(400, "extend_days must be a whole number of days")
        if days <= 0 or days > 365:
            raise HTTPException(400, "extend_days must be between 1 and 365")
        try:
            rows = (client.schema("storeops").table("tenants").select("*")
                    .eq("org_id", org_id).limit(1).execute().data) or []
        except Exception as e:
            raise HTTPException(500, f"tenant read failed: {e}")
        if not rows:
            raise HTTPException(404, "tenant not found")
        now = datetime.now(timezone.utc)
        current = None
        raw = rows[0].get("trial_ends_at")
        if raw:
            try:
                current = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if not current.tzinfo:
                    current = current.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                current = None
        base = max(current, now) if current else now
        upd["trial_ends_at"] = (base + timedelta(days=days)).isoformat()
        # Extending puts them back ON trial — otherwise the extra days would be invisible to a tenant
        # already recorded as lapsed.
        upd.setdefault("plan_status", "trialing")
        if not rows[0].get("trial_started_at"):
            upd["trial_started_at"] = now.isoformat()
    if not upd:
        raise HTTPException(400, "nothing to update")
    try:
        client.schema("storeops").table("tenants").update(upd).eq("org_id", org_id).execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 907 first: {e}")
    try:
        row = (client.schema("storeops").table("tenants").select("*")
               .eq("org_id", org_id).limit(1).execute().data or [{}])[0]
    except Exception:
        row = {}
    return {"ok": True, "org_id": org_id, "trial": trial_view(row)}
