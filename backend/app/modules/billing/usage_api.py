"""BILLING — usage, pricing and the itemized statement (super-admin operator surface).

Owner directives 2026-09-05 (sanjot@):
  · "For every tenant ai usage counter needs to be built and a cost assigned at the super admin
     level, the cost for the tenant will be cost of the super admin / platform per token paid plus %
     or flat margin assigned by the super admin"
  · "it should bill each call on all modules, nothing is for free, and have an itemized statement for
     the tenant… the billing engine should list all the modules and an option to assign price against
     them, a drop down menu to assign what kind of plan could belong to like free, starter, premium"

I/O ONLY. Every figure, every price decision and every honesty rule lives in the pure modules
(`ai_usage`, `module_usage`, `statement`), proven DB-free by `harness_ai_usage.py` (66 checks) and
`harness_module_billing.py` (62). This file reads rows and hands them over.

GATE: `_require_super_admin` — the platform's ONE definition of super-admin (core/router.py), reused,
not re-invented. Pricing and margin are operator surfaces; fail-closed on every endpoint.

CROSS-TENANT (§19.15 incident + `harness_cross_tenant_isolation.py`): every per-tenant read is
`.eq('org_id', …)`-scoped. `GET /billing/usage-overview` is the one deliberate cross-org surface and
it carries MONEY AND COUNTS ONLY — no store, rep, period figure or commission data ever crosses it.
"""
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Header

from app.core.database import get_supabase_admin
from app.core.schemas import LaxModel
from app.modules.billing import ai_usage as au
from app.modules.billing import module_usage as mu
from app.modules.billing import statement as stmt

router = APIRouter(prefix="/billing", tags=["Billing / Usage"])
HOUSE = au.HOUSE_ORG


def sb():
    return get_supabase_admin()


def _super(authorization, active_org):
    from app.modules.core.router import _require_super_admin
    return _require_super_admin(authorization, active_org)


def _actor_email(caller):
    return (caller or {}).get("email") or (caller or {}).get("uid") or "super-admin"


def _period(year=None, month=None):
    now = datetime.now(timezone.utc)
    return au.period_bounds(int(year or now.year), int(month or now.month))


def _rows(schema, table, **eq):
    try:
        q = sb().schema(schema).table(table).select("*")
        for k, v in eq.items():
            if v is not None:
                q = q.eq(k, v)
        return q.execute().data or []
    except Exception:
        return []


def _catalog():
    from app.modules.core.entitlements import load_module_catalog
    try:
        return load_module_catalog(sb())
    except Exception:
        from app.modules.core.entitlements import MODULE_CATALOG
        return dict(MODULE_CATALOG)


def _tenants():
    try:
        return (sb().schema("storeops").table("tenants")
                .select("org_id,name,is_active,package_key")
                .order("org_id", desc=False).execute().data) or []
    except Exception:
        return []


def _plan_for(package_key):
    if not package_key:
        return None
    rows = _rows("storeops", "pricing_package", key=package_key)
    return rows[0] if rows else None


# ══════════════════════════════════════════════════════════════════════════════════════════════
# AI USAGE + MARGIN
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _ai_period_for(org_id, ps, pe):
    """The priced AI period for one tenant, returning a CLOSED snapshot when one exists."""
    frozen = None
    for r in _rows("core", "ai_usage_period", org_id=org_id, period_start=ps, period_end=pe):
        if r.get("status") == "closed":
            frozen = {**r, "margin": r.get("margin_snapshot") or {},
                      **(r.get("breakdown_snapshot") or {})}
    audit = [r for r in _rows("core", "ai_call_audit", org_id=org_id)
             if au.in_period(r, ps, pe)]
    return au.price_period(audit, _rows("core", "token_rates"), _rows("core", "ai_margin_config"),
                           org_id=org_id, period_start=ps, period_end=pe, frozen=frozen)


@router.get("/ai-usage")
def ai_usage(org_id: str = "", year: int = 0, month: int = 0,
             authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """One tenant's AI usage for a period: tokens, what it cost US, the margin, and what they are
    billed. A CLOSED period is returned from its frozen snapshot and is never recomputed."""
    caller = _super(authorization, x_active_org)
    org = (org_id or "").strip() or caller.get("org_id") or HOUSE
    ps, pe = _period(year or None, month or None)
    return {"ok": True, **_ai_period_for(org, ps, pe)}


class MarginIn(LaxModel):
    org_id: str = ""
    mode: str = "percent"
    percent: float = 0
    flat_usd: float = 0
    flat_basis: str = "period"
    effective_date: str = ""
    note: str = ""


@router.put("/ai-margin")
def set_ai_margin(body: MarginIn, authorization: str = Header(default=""),
                  x_active_org: str = Header(default="")):
    """Assign a tenant's AI margin. Super-admin only, audited, and APPEND-ONLY.

    A margin change INSERTS a row with a new effective_date rather than editing the old one, so
    (a) history cannot be rewritten — an already-billed period keeps the margin it was billed with —
    and (b) the row history IS the audit trail the owner asked for: who set what, when, and why."""
    caller = _super(authorization, x_active_org)
    org = (body.org_id or "").strip()
    if not org:
        raise HTTPException(400, "org_id is required — a margin is always assigned to a tenant.")
    mode = (body.mode or "percent").strip().lower()
    if mode not in au.MARGIN_MODES:
        raise HTTPException(400, "mode must be one of %s" % (au.MARGIN_MODES,))
    basis = (body.flat_basis or "period").strip().lower()
    if basis not in au.FLAT_BASES:
        raise HTTPException(400, "flat_basis must be 'period' or 'call'")
    if float(body.percent or 0) < 0 or float(body.flat_usd or 0) < 0:
        raise HTTPException(400, "A negative margin would bill below platform cost — not allowed.")
    eff = (body.effective_date or "").strip()[:10] or date.today().isoformat()
    row = {"org_id": org, "mode": mode, "percent": float(body.percent or 0),
           "flat_usd": float(body.flat_usd or 0), "flat_basis": basis, "effective_date": eff,
           "changed_by": _actor_email(caller), "note": (body.note or "")[:500] or None}
    try:
        sb().schema("core").table("ai_margin_config").upsert(
            row, on_conflict="org_id,effective_date").execute()
    except Exception as e:
        raise HTTPException(503, "Could not save the margin: %s" % str(e)[:200])
    return {"ok": True, "margin": row,
            "note": "Applies to calls from %s onward. Periods already CLOSED are unaffected." % eff}


@router.get("/ai-margin")
def get_ai_margin(org_id: str = "", authorization: str = Header(default=""),
                  x_active_org: str = Header(default="")):
    """A tenant's margin history — newest first. Append-only rows, so this IS the change audit."""
    _super(authorization, x_active_org)
    org = (org_id or "").strip()
    rows = sorted(_rows("core", "ai_margin_config", org_id=org or None),
                  key=lambda r: str(r.get("effective_date") or ""), reverse=True)
    return {"ok": True, "org_id": org, "history": rows,
            "effective_now": au.margin_for(rows, org)["mode"] if rows else "default (no margin)"}


@router.post("/ai-usage/close")
def close_ai_period(org_id: str = "", year: int = 0, month: int = 0,
                    authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """FREEZE a tenant's AI period. After this, editing a rate or a margin cannot move the figures."""
    caller = _super(authorization, x_active_org)
    org = (org_id or "").strip()
    if not org:
        raise HTTPException(400, "org_id is required.")
    ps, pe = _period(year or None, month or None)
    priced = _ai_period_for(org, ps, pe)
    if priced.get("recomputed") is False:
        return {"ok": True, "already_closed": True, **priced}
    snap = au.snapshot_for_close(priced, closed_by=_actor_email(caller))
    try:
        sb().schema("core").table("ai_usage_period").upsert(
            snap, on_conflict="org_id,period_start,period_end").execute()
    except Exception as e:
        raise HTTPException(503, "Could not close the period: %s" % str(e)[:200])
    return {"ok": True, "closed": True, **snap}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# MODULE PRICING — the operator grid
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/module-pricing")
def module_pricing(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """THE PRICING GRID: every module x every plan, with the price against each.

    DERIVED from the entitlement module catalog, so a module added to the platform appears here
    automatically as an explicit UNPRICED cell — never absent, and therefore never silently unbilled.
    (`main.py`'s /health already shipped the hardcoded-list bug once; this is the same class.)"""
    _super(authorization, x_active_org)
    plans = sorted(_rows("storeops", "pricing_package"), key=lambda p: (p.get("sort_order") or 0))
    grid = stmt.pricing_grid(_catalog(), _rows("core", "module_price"), plans)
    return {"ok": True, "modes": list(stmt.PRICE_MODES),
            "plans": [{"key": p.get("key"), "name": p.get("name"), "price": p.get("price"),
                       "cycle": p.get("cycle"), "currency": p.get("currency"),
                       "is_public": p.get("is_public")} for p in plans],
            **grid}


class ModulePriceIn(LaxModel):
    plan_key: str = ""
    module_key: str = ""
    mode: str = "per_call"
    unit_price: float = None
    effective_date: str = ""
    note: str = ""


@router.put("/module-pricing")
def set_module_price(body: ModulePriceIn, authorization: str = Header(default=""),
                     x_active_org: str = Header(default="")):
    """Assign a price to (plan x module). Super-admin only, audited, APPEND-ONLY / effective-dated.

    `mode='included'` means the plan's monthly fee covers it (no per-call charge). There is no
    'unpriced' mode to set: unpriced is the ABSENCE of a row, so there is exactly one representation
    of "nobody has priced this" and it cannot drift."""
    caller = _super(authorization, x_active_org)
    plan = (body.plan_key or "").strip().lower()
    module = (body.module_key or "").strip()
    mode = (body.mode or "per_call").strip().lower()
    if not plan or not module:
        raise HTTPException(400, "plan_key and module_key are required.")
    if mode not in ("per_call", "flat", "included"):
        raise HTTPException(400, "mode must be per_call, flat or included.")
    if module not in _catalog():
        raise HTTPException(400, "Unknown module %r — it is not in the entitlement catalog." % module)
    price = body.unit_price
    if mode == "included":
        price = None
    elif price is None:
        raise HTTPException(400, "A %s price needs an amount. Leave the module unpriced instead of "
                                 "setting 0 unless 0 is what you mean." % mode)
    elif float(price) < 0:
        raise HTTPException(400, "A price cannot be negative.")
    eff = (body.effective_date or "").strip()[:10] or date.today().isoformat()
    row = {"plan_key": plan, "module_key": module, "mode": mode,
           "unit_price": None if price is None else float(price),
           "effective_date": eff, "changed_by": _actor_email(caller),
           "note": (body.note or "")[:500] or None}
    try:
        sb().schema("core").table("module_price").upsert(
            row, on_conflict="plan_key,module_key,effective_date").execute()
    except Exception as e:
        raise HTTPException(503, "Could not save the price: %s" % str(e)[:200])
    return {"ok": True, "price": row,
            "note": "Applies from %s onward. Statements already CLOSED are unaffected." % eff}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# THE ITEMIZED STATEMENT
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _statement_for(org_id, ps, pe, tenant=None):
    frozen = None
    for r in _rows("core", "billing_statement", org_id=org_id, period_start=ps, period_end=pe):
        if r.get("status") == "closed":
            frozen = r
    if frozen:
        return stmt.build_statement(org_id=org_id, period_start=ps, period_end=pe, catalog={},
                                    frozen=frozen)
    t = tenant or next((x for x in _tenants() if x.get("org_id") == org_id), None) or {}
    usage = [r for r in _rows("core", "module_usage_daily", org_id=org_id)
             if ps <= str(r.get("usage_date") or "")[:10] <= pe]
    return stmt.build_statement(
        org_id=org_id, period_start=ps, period_end=pe, catalog=_catalog(), usage_rows=usage,
        price_rows=_rows("core", "module_price"), plan=_plan_for(t.get("package_key")),
        ai_period=_ai_period_for(org_id, ps, pe), tenant_name=t.get("name"))


@router.get("/statement")
def get_statement(org_id: str = "", year: int = 0, month: int = 0,
                  authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The tenant's ITEMIZED statement: monthly fee, per-module usage, AI usage, total.

    Every line traces to what generated it, unpriced lines are shown and excluded from the total
    (never billed as $0), and platform-initiated calls are visible but not charged. A CLOSED
    statement is returned from its frozen document and is never recomputed."""
    caller = _super(authorization, x_active_org)
    org = (org_id or "").strip() or caller.get("org_id") or HOUSE
    ps, pe = _period(year or None, month or None)
    return {"ok": True, **_statement_for(org, ps, pe)}


@router.post("/statement/close")
def close_statement(org_id: str = "", year: int = 0, month: int = 0,
                    authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """FREEZE the statement. After this, changing a module price or the plan's monthly fee cannot
    move a figure the tenant was already billed. An INCOMPLETE statement can be closed knowingly —
    the incompleteness is frozen with it, so nobody can later read it and not know."""
    caller = _super(authorization, x_active_org)
    org = (org_id or "").strip()
    if not org:
        raise HTTPException(400, "org_id is required.")
    ps, pe = _period(year or None, month or None)
    s = _statement_for(org, ps, pe)
    if s.get("recomputed") is False:
        return {"ok": True, "already_closed": True, **s}
    frozen = stmt.freeze_statement(s, closed_by=_actor_email(caller))
    row = {"org_id": org, "period_start": ps, "period_end": pe, "status": "closed",
           "plan_key": frozen.get("plan_key"), "plan_name": frozen.get("plan_name"),
           "currency": frozen.get("currency") or "USD", "total_usd": frozen.get("total_usd"),
           "total_calls": frozen.get("total_calls") or 0,
           "billable_calls": frozen.get("billable_calls") or 0,
           "lines": frozen.get("lines") or [], "complete": bool(frozen.get("complete")),
           "unpriced": frozen.get("unpriced") or [],
           "closed_by": frozen.get("closed_by"), "closed_at": frozen.get("closed_at")}
    try:
        sb().schema("core").table("billing_statement").upsert(
            row, on_conflict="org_id,period_start,period_end").execute()
    except Exception as e:
        raise HTTPException(503, "Could not close the statement: %s" % str(e)[:200])
    return {"ok": True, "closed": True, **frozen,
            "warning": None if frozen.get("complete") else frozen.get("complete_note")}


@router.get("/usage-overview")
def usage_overview(year: int = 0, month: int = 0, authorization: str = Header(default=""),
                   x_active_org: str = Header(default="")):
    """THE OPERATOR'S CROSS-TENANT VIEW — every tenant's billable total for the period.

    NARROWED ON PURPOSE (§19.15 cross-tenant incident): org id, tenant name, plan, call counts and
    MONEY. No store, rep, period figure, commission number or any other tenant business data crosses
    this boundary. Super-admin only, fail-closed."""
    _super(authorization, x_active_org)
    ps, pe = _period(year or None, month or None)
    out, ai_list = [], []
    for t in _tenants():
        if t.get("is_active") is False:
            continue
        org = t.get("org_id")
        if not org:
            continue
        try:
            s = _statement_for(org, ps, pe, tenant=t)
            ai = _ai_period_for(org, ps, pe)
            ai_list.append(ai)
            out.append({"org_id": org, "name": t.get("name"), "plan_key": s.get("plan_key"),
                        "total_usd": s.get("total_usd"), "billable_calls": s.get("billable_calls"),
                        "ai_billable_usd": ai.get("billable_usd"),
                        "ai_platform_cost_usd": ai.get("platform_cost_usd"),
                        "complete": s.get("complete"),
                        "unpriced_lines": len(s.get("unpriced") or [])})
        except Exception as e:
            out.append({"org_id": org, "name": t.get("name"), "error": str(e)[:160]})
    incomplete = [o for o in out if o.get("complete") is False]
    return {"ok": True, "period_start": ps, "period_end": pe, "tenants": out,
            "ai_totals": au.summarize_tenants(ai_list),
            "billable_total_usd": round(sum(float(o.get("total_usd") or 0) for o in out), 2),
            "incomplete_tenants": len(incomplete),
            "note": ("Every tenant's statement is fully priced." if not incomplete else
                     "%d tenant(s) have UNPRICED lines — their statements are incomplete and must "
                     "not be sent as final invoices until every module is priced."
                     % len(incomplete)),
            "coverage": au.coverage()}


@router.get("/module-usage")
def module_usage_detail(org_id: str = "", year: int = 0, month: int = 0,
                        authorization: str = Header(default=""),
                        x_active_org: str = Header(default="")):
    """Raw per-module call counts for one tenant and period — what the statement was built from.

    Shows billable vs platform-initiated side by side, so the operator can see exactly what we
    charged for and what we did on the tenant's behalf for free."""
    caller = _super(authorization, x_active_org)
    org = (org_id or "").strip() or caller.get("org_id") or HOUSE
    ps, pe = _period(year or None, month or None)
    rows = [r for r in _rows("core", "module_usage_daily", org_id=org)
            if ps <= str(r.get("usage_date") or "")[:10] <= pe]
    roll = mu.rollup_by_module(rows)
    catalog = _catalog()
    return {"ok": True, "org_id": org, "period_start": ps, "period_end": pe,
            "modules": [{"module": k, "label": catalog.get(k, k), **v} for k, v in roll.items()],
            "totals": {"calls": sum(v["calls"] for v in roll.values()),
                       "billable_calls": sum(v["billable_calls"] for v in roll.values()),
                       "system_calls": sum(v["system_calls"] for v in roll.values())},
            "unmapped_prefixes": mu.unmapped_prefixes(_mounted()),
            "note": "Platform-initiated calls (crons, sweeps, webhooks) are counted but never billed."}


def _mounted():
    try:
        from app.main import _mounted_modules
        return _mounted_modules()
    except Exception:
        return []
