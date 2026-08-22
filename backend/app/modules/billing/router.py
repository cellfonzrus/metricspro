"""Tenant Billing router (SaaS) — SUPER-ADMIN only.

Phase 1: per-tenant billing PLANS + generated INVOICES. The super-admin prices each tenant by
choosing a BASIS (what the unit_price is multiplied by) and a CYCLE (monthly | annual):

    basis ∈ {flat, per_store, per_entity, per_user, per_module}
      flat       → quantity = 1
      per_store  → count of active storeops.stores       for the org
      per_user   → count of storeops.app_users           for the org
      per_entity → count of commcalc.companies (legal entities) for the org
      per_module → count of enabled storeops.tenant_modules for the org

amount = quantity × unit_price (unit_price is per CYCLE; v1 does NOT auto-prorate). Generating an
invoice FREEZES basis/quantity/unit_price/amount so later driver changes don't rewrite history.

Tables live in storeops.* (migration 064). Every endpoint is gated by `_require_super_admin`
(reused from the core router — token-verified, with the house-admin bootstrap fallback). If
migration 064 hasn't been applied yet, reads degrade to {"ready": false} instead of 500ing.

PAYMENT GATEWAY is a LATER phase — see the `# TODO payment gateway` seam on POST /invoices/{id}/pay.
"""
from datetime import datetime, timezone

from typing import Any

from fastapi import APIRouter, HTTPException, Header

from app.core.database import get_supabase
from app.core.schemas import LaxModel
# Reuse the core super-admin gate (token-verified + house-admin bootstrap fallback).
from app.modules.core.router import _require_super_admin

router = APIRouter(prefix="/billing", tags=["Billing (Tenants)"])
ORG_ID = "00000000-0000-0000-0000-000000000001"


# ── Request bodies (Item 15 Pydantic rollout — lax so legacy callers never break) ──────────────
class UpsertPlanIn(LaxModel):
    org_id: Any = None
    basis: Any = None
    cycle: Any = None
    unit_price: Any = None
    currency: Any = None
    modules: Any = None
    is_active: Any = True
    notes: Any = None


class GenerateInvoiceIn(LaxModel):
    org_id: Any = None
    period_start: Any = None
    period_end: Any = None
    due_date: Any = None
    notes: Any = None


class UpdateInvoiceIn(LaxModel):
    status: Any = None
    issued_at: Any = None
    payment_ref: Any = None
    due_date: Any = None
    notes: Any = None


class MarkPaidIn(LaxModel):
    payment_ref: Any = None
    issued_at: Any = None


class UpsertPlatformConnectorIn(LaxModel):
    id: Any = None
    provider: Any = None
    display_name: Any = None
    is_enabled: Any = True
    credential: Any = None
    config: Any = None
    flat_monthly_cost: Any = None
    sort_order: Any = None
    notes: Any = None


class RefreshPlatformCostsIn(LaxModel):
    id: Any = None

VALID_BASIS = {"flat", "per_store", "per_entity", "per_user", "per_module", "per_carrier"}
VALID_CYCLE = {"monthly", "annual"}
VALID_STATUS = {"draft", "sent", "paid", "void"}


def sb():
    return get_supabase()


# ── quantity drivers ──────────────────────────────────────────────────────────────────────────
def _count(query) -> int:
    """Run a supabase count='exact' query and return the integer count (0 on any error)."""
    try:
        resp = query.execute()
        return int(resp.count or 0)
    except Exception:
        return 0


def _quantity_drivers(org_id: str) -> dict:
    """The LIVE quantity drivers for an org — one count per basis. Surfaced in the UI so it's clear
    what would drive the bill, and used by /invoices/generate to compute quantity from basis."""
    client = sb()
    per_store = _count(client.schema("storeops").table("stores")
                       .select("id", count="exact").eq("org_id", org_id).eq("is_active", True))
    # Fallback: some store tables predate is_active — count all rows if the filtered count is 0.
    if per_store == 0:
        per_store = _count(client.schema("storeops").table("stores")
                           .select("id", count="exact").eq("org_id", org_id))
    per_user = _count(client.schema("storeops").table("app_users")
                      .select("id", count="exact").eq("org_id", org_id))
    per_entity = _count(client.schema("commcalc").table("companies")
                        .select("id", count="exact").eq("org_id", org_id))
    per_module = _count(client.schema("storeops").table("tenant_modules")
                        .select("module_key", count="exact").eq("org_id", org_id).eq("is_enabled", True))
    per_carrier = _count(client.schema("commcalc").table("carrier")
                         .select("id", count="exact").eq("org_id", org_id))
    return {"per_store": per_store, "per_user": per_user, "per_entity": per_entity,
            "per_module": per_module, "per_carrier": per_carrier, "flat": 1}


def _quantity_for_basis(org_id: str, basis: str) -> int:
    """The single quantity number that a given basis resolves to for this org."""
    drivers = _quantity_drivers(org_id)
    return int(drivers.get(basis, 1))


def _monthly_equiv(amount: float, cycle: str) -> float:
    """Normalise a per-cycle amount to a monthly figure (annual → /12) for MRR rollups."""
    amount = float(amount or 0)
    return round(amount / 12.0, 2) if cycle == "annual" else amount


# ── plans ─────────────────────────────────────────────────────────────────────────────────────
@router.get("/plans")
def list_plans(authorization: str = Header(default="")):
    """Every tenant's plan (joined to the tenant name) + that tenant's LIVE quantity drivers."""
    _require_super_admin(authorization)
    client = sb()
    try:
        tenants = client.schema("storeops").table("tenants").select("*").order("created_at").execute().data or []
    except Exception:
        return {"ready": False, "plans": []}
    try:
        plans = client.schema("storeops").table("billing_plan").select("*").execute().data or []
    except Exception:
        # migration 064 not applied yet — still list tenants (with no plan) so the UI renders.
        return {"ready": False,
                "plans": [{"org_id": t["org_id"], "name": t.get("name"), "is_active_tenant": t.get("is_active"),
                           "plan": None, "drivers": _quantity_drivers(t["org_id"])} for t in tenants]}
    by_org = {p["org_id"]: p for p in plans}
    out = []
    for t in tenants:
        org = t["org_id"]
        out.append({"org_id": org, "name": t.get("name"), "is_active_tenant": t.get("is_active"),
                    "plan": by_org.get(org), "drivers": _quantity_drivers(org)})
    return {"ready": True, "plans": out}


@router.get("/plan")
def get_plan(org_id: str, authorization: str = Header(default="")):
    """One tenant's plan + its live drivers."""
    _require_super_admin(authorization)
    try:
        rows = sb().schema("storeops").table("billing_plan").select("*").eq("org_id", org_id).limit(1).execute().data or []
    except Exception:
        return {"ready": False, "plan": None, "drivers": _quantity_drivers(org_id)}
    return {"ready": True, "plan": rows[0] if rows else None, "drivers": _quantity_drivers(org_id)}


@router.post("/plan")
def upsert_plan(body: UpsertPlanIn, authorization: str = Header(default="")):
    """Create/update a tenant's plan (keyed by org_id). Body: {org_id, basis, unit_price, cycle,
    currency?, modules?, is_active?, notes?}."""
    _require_super_admin(authorization)
    org_id = (body.org_id or "").strip()
    if not org_id:
        raise HTTPException(400, "org_id required")
    basis = (body.basis or "flat").strip()
    cycle = (body.cycle or "monthly").strip()
    if basis not in VALID_BASIS:
        raise HTTPException(400, f"basis must be one of {sorted(VALID_BASIS)}")
    if cycle not in VALID_CYCLE:
        raise HTTPException(400, f"cycle must be one of {sorted(VALID_CYCLE)}")
    row = {
        "org_id": org_id,
        "basis": basis,
        "unit_price": float(body.unit_price or 0),
        "cycle": cycle,
        "currency": (body.currency or "USD").strip() or "USD",
        "modules": body.modules if body.modules else None,
        "is_active": bool(body.is_active),
        "notes": body.notes,
    }
    try:
        sb().schema("storeops").table("billing_plan").upsert(row, on_conflict="org_id").execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 064 first: {e}")
    # Picking modules ASSIGNS them: reconcile the tenant's entitlement now so the customer gets
    # exactly the picked modules (empty/blank = all-access). Non-fatal if the engine isn't present.
    entitled = None
    try:
        from app.modules.core.entitlements import sync_tenant
        entitled = sync_tenant(sb(), org_id).get("enabled_modules")
    except Exception:
        pass
    return {"ok": True, "org_id": org_id, "enabled_modules": entitled}


@router.delete("/plan")
def delete_plan(org_id: str, authorization: str = Header(default="")):
    """Remove a tenant's plan."""
    _require_super_admin(authorization)
    try:
        sb().schema("storeops").table("billing_plan").delete().eq("org_id", org_id).execute()
    except Exception as e:
        raise HTTPException(500, f"delete failed: {e}")
    return {"ok": True}


# ── invoices ──────────────────────────────────────────────────────────────────────────────────
@router.post("/invoices/generate")
def generate_invoice(body: GenerateInvoiceIn, authorization: str = Header(default="")):
    """Generate a DRAFT invoice for a tenant + period. Quantity is computed from the plan's basis
    against the LIVE drivers; amount = quantity × unit_price. The cycle's unit_price is taken as-is
    (monthly plan → monthly price, annual plan → annual price); v1 does NOT auto-prorate the period.
    Body: {org_id, period_start, period_end, due_date?, notes?}."""
    _require_super_admin(authorization)
    org_id = (body.org_id or "").strip()
    if not org_id:
        raise HTTPException(400, "org_id required")
    period_start = body.period_start
    period_end = body.period_end
    if not period_start or not period_end:
        raise HTTPException(400, "period_start and period_end required (YYYY-MM-DD)")
    client = sb()
    try:
        plans = client.schema("storeops").table("billing_plan").select("*").eq("org_id", org_id).limit(1).execute().data or []
    except Exception as e:
        raise HTTPException(500, f"cannot read plan — run migration 064 first: {e}")
    if not plans:
        raise HTTPException(400, "this tenant has no billing plan yet — set one first")
    plan = plans[0]
    basis = plan.get("basis") or "flat"
    quantity = _quantity_for_basis(org_id, basis)
    unit_price = float(plan.get("unit_price") or 0)
    amount = round(quantity * unit_price, 2)
    inv = {
        "org_id": org_id,
        "period_start": period_start,
        "period_end": period_end,
        "basis": basis,
        "quantity": quantity,
        "unit_price": unit_price,
        "amount": amount,
        "currency": plan.get("currency") or "USD",
        "status": "draft",
        "due_date": body.due_date,
        "notes": body.notes,
    }
    try:
        res = client.schema("storeops").table("billing_invoice").insert(inv).execute()
    except Exception as e:
        raise HTTPException(500, f"could not create invoice — run migration 064 first: {e}")
    return {"ok": True, "invoice": (res.data or [inv])[0]}


@router.get("/invoices")
def list_invoices(org_id: str = "", authorization: str = Header(default="")):
    """Invoices for one tenant (org_id) — or, if org_id is omitted, all tenants' invoices."""
    _require_super_admin(authorization)
    try:
        q = sb().schema("storeops").table("billing_invoice").select("*")
        if org_id:
            q = q.eq("org_id", org_id)
        rows = q.order("created_at", desc=True).execute().data or []
    except Exception:
        return {"ready": False, "invoices": []}
    return {"ready": True, "invoices": rows}


@router.patch("/invoices/{invoice_id}")
def update_invoice(invoice_id: str, body: UpdateInvoiceIn, authorization: str = Header(default="")):
    """Update an invoice's lifecycle: status (sent|paid|void), payment_ref, issued_at, due_date, notes.
    Setting status=sent stamps issued_at if not already set."""
    _require_super_admin(authorization)
    upd: dict = {}
    if "status" in body.model_fields_set:
        st = (body.status or "").strip()
        if st not in VALID_STATUS:
            raise HTTPException(400, f"status must be one of {sorted(VALID_STATUS)}")
        upd["status"] = st
        if st == "sent" and not body.issued_at:
            upd["issued_at"] = datetime.now(timezone.utc).isoformat()
    for k in ("payment_ref", "due_date", "notes"):
        if k in body.model_fields_set:
            upd[k] = getattr(body, k)
    if "issued_at" in body.model_fields_set:
        upd["issued_at"] = body.issued_at
    if not upd:
        raise HTTPException(400, "nothing to update")
    try:
        sb().schema("storeops").table("billing_invoice").update(upd).eq("id", invoice_id).execute()
    except Exception as e:
        raise HTTPException(500, f"update failed: {e}")
    return {"ok": True, "id": invoice_id}


@router.post("/invoices/{invoice_id}/pay")
def mark_paid(invoice_id: str, body: MarkPaidIn = None, authorization: str = Header(default="")):
    """Mark an invoice PAID and store a payment reference.

    # TODO payment gateway: this is the SEAM for a real payment processor. A later phase will
    # charge the customer (Stripe/etc.), receive the provider's charge id, and call back into this
    # endpoint with that id as payment_ref. For now it is a manual stub — the super-admin records a
    # payment that happened out-of-band (wire/check/Zelle) and pastes the reference. DO NOT integrate
    # Stripe here yet.
    """
    _require_super_admin(authorization)
    body = body or MarkPaidIn()
    upd = {"status": "paid",
           "payment_ref": body.payment_ref or f"manual-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
           "issued_at": body.issued_at or datetime.now(timezone.utc).isoformat()}
    try:
        sb().schema("storeops").table("billing_invoice").update(upd).eq("id", invoice_id).execute()
    except Exception as e:
        raise HTTPException(500, f"mark-paid failed: {e}")
    return {"ok": True, "id": invoice_id, "payment_ref": upd["payment_ref"]}


# ── super-admin overview (MRR / ARR) ────────────────────────────────────────────────────────────
@router.get("/summary")
def summary(authorization: str = Header(default="")):
    """Super-admin overview across ALL tenants: per tenant {name, plan, quantity, monthly_amount,
    latest_invoice}, plus MRR (sum of monthly-equivalent recurring) + ARR (MRR × 12)."""
    _require_super_admin(authorization)
    client = sb()
    try:
        tenants = client.schema("storeops").table("tenants").select("*").order("created_at").execute().data or []
    except Exception:
        return {"ready": False, "tenants": [], "mrr": 0, "arr": 0}
    try:
        plans = client.schema("storeops").table("billing_plan").select("*").execute().data or []
        invoices = client.schema("storeops").table("billing_invoice").select("*").order("created_at", desc=True).execute().data or []
    except Exception:
        return {"ready": False, "tenants": [], "mrr": 0, "arr": 0}
    plan_by_org = {p["org_id"]: p for p in plans}
    latest_by_org: dict = {}
    for inv in invoices:  # invoices already sorted newest-first
        latest_by_org.setdefault(inv["org_id"], inv)

    rows = []
    mrr = 0.0
    for t in tenants:
        org = t["org_id"]
        plan = plan_by_org.get(org)
        quantity = monthly_amount = 0.0
        if plan and plan.get("is_active", True):
            quantity = _quantity_for_basis(org, plan.get("basis") or "flat")
            amount = round(quantity * float(plan.get("unit_price") or 0), 2)
            monthly_amount = _monthly_equiv(amount, plan.get("cycle") or "monthly")
            mrr += monthly_amount
        rows.append({
            "org_id": org, "name": t.get("name"), "is_active_tenant": t.get("is_active"),
            "plan": plan, "quantity": quantity, "monthly_amount": round(monthly_amount, 2),
            "latest_invoice": latest_by_org.get(org),
        })
    mrr = round(mrr, 2)
    return {"ready": True, "tenants": rows, "mrr": mrr, "arr": round(mrr * 12, 2)}


# ════════════════════════════════════════════════════════════════════════════════════════════════
# PLATFORM COSTS — the OPERATOR's own spend to run MetricsPro (vendor/infra bills), plus the derived
# break-even COST PER TENANT so pricing/billing is easy. Super-admin only. (migration 090)
# ════════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.billing import platform_costs as _pc


def _pcstore():
    return sb().schema("storeops").table("platform_billing_connector")


def _pc_out(row: dict) -> dict:
    return {
        "id": row.get("id"), "provider": row.get("provider"), "display_name": row.get("display_name"),
        "credential_masked": _pc.mask(row.get("credential")), "has_credential": bool(row.get("credential")),
        "config": row.get("config") or {}, "flat_monthly_cost": row.get("flat_monthly_cost"),
        "is_enabled": row.get("is_enabled", True), "last_cost": row.get("last_cost"),
        "last_currency": row.get("last_currency"), "last_synced_at": row.get("last_synced_at"),
        "last_status": row.get("last_status"), "last_detail": row.get("last_detail"),
        "sort_order": row.get("sort_order", 0), "notes": row.get("notes"),
    }


@router.get("/platform-providers")
def platform_providers(authorization: str = Header(default="")):
    """The provider registry for the connector dropdown (which have a live cost API vs manual)."""
    _require_super_admin(authorization)
    return {"providers": _pc.PROVIDERS}


@router.get("/platform-connectors")
def list_platform_connectors(authorization: str = Header(default="")):
    _require_super_admin(authorization)
    try:
        rows = _pcstore().select("*").order("sort_order").order("provider").execute().data or []
    except Exception:
        return {"ready": False, "connectors": []}
    return {"ready": True, "connectors": [_pc_out(r) for r in rows]}


@router.post("/platform-connectors")
def upsert_platform_connector(body: UpsertPlatformConnectorIn, authorization: str = Header(default="")):
    """Add or update a platform connector. The credential is only overwritten when a NEW (non-masked)
    value is supplied — re-saving with the masked placeholder keeps the stored secret."""
    _require_super_admin(authorization)
    provider = (body.provider or "").strip().lower()
    if not provider:
        raise HTTPException(400, "provider required")
    row = {"org_id": ORG_ID, "provider": provider,
           "display_name": (body.display_name or "").strip() or provider,
           "is_enabled": bool(body.is_enabled)}
    cred = (body.credential or "").strip()
    if cred and "…" not in cred and "•" not in cred:   # a real new secret, not the masked echo
        row["credential"] = cred
    if "config" in body.model_fields_set:
        row["config"] = body.config or {}
    if "flat_monthly_cost" in body.model_fields_set:
        row["flat_monthly_cost"] = _pc._num(body.flat_monthly_cost)
    if "sort_order" in body.model_fields_set:
        try: row["sort_order"] = int(body.sort_order or 0)
        except (TypeError, ValueError): pass
    if "notes" in body.model_fields_set:
        row["notes"] = body.notes
    try:
        cid = body.id
        if cid:
            _pcstore().update(row).eq("id", cid).execute()
            return {"ok": True, "id": cid}
        res = _pcstore().insert(row).execute()
        return {"ok": True, "id": (res.data[0]["id"] if res.data else None)}
    except Exception as e:
        raise HTTPException(500, f"save failed: {e} (run migration 090?)")


@router.delete("/platform-connectors/{cid}")
def delete_platform_connector(cid: str, authorization: str = Header(default="")):
    _require_super_admin(authorization)
    try:
        _pcstore().delete().eq("id", cid).execute()
    except Exception as e:
        raise HTTPException(500, f"delete failed: {e}")
    return {"ok": True}


def _active_tenant_count(client) -> int:
    try:
        tenants = client.schema("storeops").table("tenants").select("org_id,is_active").execute().data or []
        return sum(1 for t in tenants if t.get("is_active") is not False)
    except Exception:
        return 0


@router.get("/platform-costs")
def platform_costs_summary(authorization: str = Header(default="")):
    """Total monthly cost to run MetricsPro (sum of the last synced connector costs) + the derived
    break-even COST PER TENANT (total ÷ active tenants). Pair it with /summary's MRR to see margin."""
    _require_super_admin(authorization)
    client = sb()
    try:
        rows = _pcstore().select("*").order("sort_order").order("provider").execute().data or []
    except Exception:
        return {"ready": False, "total_monthly": 0, "active_tenants": 0, "cost_per_tenant": 0, "connectors": []}
    total = round(sum((_pc._num(r.get("last_cost")) or 0) for r in rows if r.get("is_enabled", True)), 2)
    active = _active_tenant_count(client)
    return {"ready": True, "total_monthly": total, "active_tenants": active,
            "cost_per_tenant": round(total / active, 2) if active else 0.0,
            "connectors": [_pc_out(r) for r in rows]}


@router.post("/platform-costs/refresh")
async def refresh_platform_costs(body: RefreshPlatformCostsIn = None, authorization: str = Header(default="")):
    """Pull live cost for every enabled connector (or one, if {id} is passed), persist the result, and
    return the new total + per-connector status. Providers without a live fetcher use their flat figure."""
    _require_super_admin(authorization)
    body = body or RefreshPlatformCostsIn()
    only = body.id
    try:
        rows = _pcstore().select("*").execute().data or []
    except Exception as e:
        raise HTTPException(500, f"not ready: {e} (run migration 090?)")
    results = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for r in rows:
        if only and r.get("id") != only:
            continue
        if not r.get("is_enabled", True):
            continue
        out = await _pc.fetch_cost(r)
        try:
            _pcstore().update({
                "last_cost": out.get("cost"), "last_currency": out.get("currency") or "USD",
                "last_status": out.get("status"), "last_detail": (out.get("detail") or "")[:300],
                "last_synced_at": now_iso,
            }).eq("id", r["id"]).execute()
        except Exception:
            pass
        results.append({"id": r["id"], "provider": r.get("provider"),
                        "display_name": r.get("display_name"), **out})
    total = round(sum((_pc._num(x.get("cost")) or 0) for x in results), 2)
    active = _active_tenant_count(sb())
    return {"ok": True, "refreshed": len(results), "total_monthly": total,
            "active_tenants": active, "cost_per_tenant": round(total / active, 2) if active else 0.0,
            "results": results}
