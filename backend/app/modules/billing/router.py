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

from fastapi import APIRouter, HTTPException, Header

from app.core.database import get_supabase
# Reuse the core super-admin gate (token-verified + house-admin bootstrap fallback).
from app.modules.core.router import _require_super_admin

router = APIRouter(prefix="/billing", tags=["Billing (Tenants)"])
ORG_ID = "00000000-0000-0000-0000-000000000001"

VALID_BASIS = {"flat", "per_store", "per_entity", "per_user", "per_module"}
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
    return {"per_store": per_store, "per_user": per_user,
            "per_entity": per_entity, "per_module": per_module, "flat": 1}


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
async def list_plans(authorization: str = Header(default="")):
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
async def get_plan(org_id: str, authorization: str = Header(default="")):
    """One tenant's plan + its live drivers."""
    _require_super_admin(authorization)
    try:
        rows = sb().schema("storeops").table("billing_plan").select("*").eq("org_id", org_id).limit(1).execute().data or []
    except Exception:
        return {"ready": False, "plan": None, "drivers": _quantity_drivers(org_id)}
    return {"ready": True, "plan": rows[0] if rows else None, "drivers": _quantity_drivers(org_id)}


@router.post("/plan")
async def upsert_plan(body: dict, authorization: str = Header(default="")):
    """Create/update a tenant's plan (keyed by org_id). Body: {org_id, basis, unit_price, cycle,
    currency?, modules?, is_active?, notes?}."""
    _require_super_admin(authorization)
    org_id = (body.get("org_id") or "").strip()
    if not org_id:
        raise HTTPException(400, "org_id required")
    basis = (body.get("basis") or "flat").strip()
    cycle = (body.get("cycle") or "monthly").strip()
    if basis not in VALID_BASIS:
        raise HTTPException(400, f"basis must be one of {sorted(VALID_BASIS)}")
    if cycle not in VALID_CYCLE:
        raise HTTPException(400, f"cycle must be one of {sorted(VALID_CYCLE)}")
    row = {
        "org_id": org_id,
        "basis": basis,
        "unit_price": float(body.get("unit_price") or 0),
        "cycle": cycle,
        "currency": (body.get("currency") or "USD").strip() or "USD",
        "modules": body.get("modules") if body.get("modules") else None,
        "is_active": bool(body.get("is_active", True)),
        "notes": body.get("notes"),
    }
    try:
        sb().schema("storeops").table("billing_plan").upsert(row, on_conflict="org_id").execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 064 first: {e}")
    return {"ok": True, "org_id": org_id}


@router.delete("/plan")
async def delete_plan(org_id: str, authorization: str = Header(default="")):
    """Remove a tenant's plan."""
    _require_super_admin(authorization)
    try:
        sb().schema("storeops").table("billing_plan").delete().eq("org_id", org_id).execute()
    except Exception as e:
        raise HTTPException(500, f"delete failed: {e}")
    return {"ok": True}


# ── invoices ──────────────────────────────────────────────────────────────────────────────────
@router.post("/invoices/generate")
async def generate_invoice(body: dict, authorization: str = Header(default="")):
    """Generate a DRAFT invoice for a tenant + period. Quantity is computed from the plan's basis
    against the LIVE drivers; amount = quantity × unit_price. The cycle's unit_price is taken as-is
    (monthly plan → monthly price, annual plan → annual price); v1 does NOT auto-prorate the period.
    Body: {org_id, period_start, period_end, due_date?, notes?}."""
    _require_super_admin(authorization)
    org_id = (body.get("org_id") or "").strip()
    if not org_id:
        raise HTTPException(400, "org_id required")
    period_start = body.get("period_start")
    period_end = body.get("period_end")
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
        "due_date": body.get("due_date"),
        "notes": body.get("notes"),
    }
    try:
        res = client.schema("storeops").table("billing_invoice").insert(inv).execute()
    except Exception as e:
        raise HTTPException(500, f"could not create invoice — run migration 064 first: {e}")
    return {"ok": True, "invoice": (res.data or [inv])[0]}


@router.get("/invoices")
async def list_invoices(org_id: str = "", authorization: str = Header(default="")):
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
async def update_invoice(invoice_id: str, body: dict, authorization: str = Header(default="")):
    """Update an invoice's lifecycle: status (sent|paid|void), payment_ref, issued_at, due_date, notes.
    Setting status=sent stamps issued_at if not already set."""
    _require_super_admin(authorization)
    upd: dict = {}
    if "status" in body:
        st = (body.get("status") or "").strip()
        if st not in VALID_STATUS:
            raise HTTPException(400, f"status must be one of {sorted(VALID_STATUS)}")
        upd["status"] = st
        if st == "sent" and not body.get("issued_at"):
            upd["issued_at"] = datetime.now(timezone.utc).isoformat()
    for k in ("payment_ref", "due_date", "notes"):
        if k in body:
            upd[k] = body[k]
    if "issued_at" in body:
        upd["issued_at"] = body["issued_at"]
    if not upd:
        raise HTTPException(400, "nothing to update")
    try:
        sb().schema("storeops").table("billing_invoice").update(upd).eq("id", invoice_id).execute()
    except Exception as e:
        raise HTTPException(500, f"update failed: {e}")
    return {"ok": True, "id": invoice_id}


@router.post("/invoices/{invoice_id}/pay")
async def mark_paid(invoice_id: str, body: dict = None, authorization: str = Header(default="")):
    """Mark an invoice PAID and store a payment reference.

    # TODO payment gateway: this is the SEAM for a real payment processor. A later phase will
    # charge the customer (Stripe/etc.), receive the provider's charge id, and call back into this
    # endpoint with that id as payment_ref. For now it is a manual stub — the super-admin records a
    # payment that happened out-of-band (wire/check/Zelle) and pastes the reference. DO NOT integrate
    # Stripe here yet.
    """
    _require_super_admin(authorization)
    body = body or {}
    upd = {"status": "paid",
           "payment_ref": body.get("payment_ref") or f"manual-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
           "issued_at": body.get("issued_at") or datetime.now(timezone.utc).isoformat()}
    try:
        sb().schema("storeops").table("billing_invoice").update(upd).eq("id", invoice_id).execute()
    except Exception as e:
        raise HTTPException(500, f"mark-paid failed: {e}")
    return {"ok": True, "id": invoice_id, "payment_ref": upd["payment_ref"]}


# ── super-admin overview (MRR / ARR) ────────────────────────────────────────────────────────────
@router.get("/summary")
async def summary(authorization: str = Header(default="")):
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
