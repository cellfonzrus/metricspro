"""DM VISIT PLAN API — /api/v1/storevisit/visit-* (the daily quota, the market manager's assignments,
the tenant-configurable priority order, and the Friday-evening auto-fill).

Why this is a SECOND router file rather than more of `storevisit/router.py`: that file is the DM's
visit-logging flow (check in, checklist, photos, submit) and is being extended in parallel for visit
alerts. This is the planning surface above it. Same `/storevisit` prefix, so the API reads as one
module; FastAPI mounts both.

Every number here comes from `storevisit/visit_plan` (pure, DB-free, proven by
backend/harness_dm_visit_plan.py). This file only reads rows, writes rows and dereferences the
existing homes — it decides nothing:

  which stores a DM owns  → storeops/org_chain.dm_by_store (index §48.7), inverted
  a store's performance   → commcalc/targets_engine via GET /commcalc/targets/{period}/summary
  attainment %            → targets_engine.attainment_pct
  the carrier deliverables→ commcalc.carrier_kpi_metric (mig 060) for the org's own carrier

RULE TWO: no carrier, tenant, store or product name appears in this file.
"""
from typing import Any, Optional, List

from fastapi import APIRouter, HTTPException, Header, Query
from app.core.database import get_supabase
from app.core.schemas import LaxModel
from app.core.run_secret import verify_notify_secret
from datetime import datetime, timezone, date, timedelta

from app.modules.storevisit import visit_plan as _vp

router = APIRouter(prefix="/storevisit", tags=["Store Visits"])

ORG_ID = "00000000-0000-0000-0000-000000000001"
# The nil carrier_id = "the org's carrier-neutral default set", the convention mig 060 already uses
# for commcalc.carrier_kpi_metric. Spelled once here so this file and that registry agree.
NIL_CARRIER = "00000000-0000-0000-0000-000000000000"


def sb():
    """storeops.* — the schema the visit tables live in (mig 027, extended by mig 1050)."""
    return get_supabase().schema("storeops")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _s(v) -> str:
    return str(v or "").strip()


# ── Request bodies (lax, this module's convention) ────────────────────────────────────────────────
class PutVisitQuotaConfigIn(LaxModel):
    dm_visit_quota_enabled: Any = None
    dm_visit_quota_per_day: Any = None
    dm_visit_quota_days: Any = None
    dm_visit_assign_auto_enabled: Any = None
    dm_visit_assign_deadline_dow: Any = None
    dm_visit_assign_deadline_time: Any = None
    dm_visit_assign_horizon_days: Any = None


class PutPriorityRuleIn(LaxModel):
    id: Any = None
    carrier_id: Any = None
    sort: Any = None
    basis: Any = None
    metric_key: Any = None
    weight: Any = None
    direction: Any = None
    label: Any = None
    is_active: Any = True


class CreateAssignmentIn(LaxModel):
    visit_date: Any = None
    dm_employee_id: Any = None
    dm_email: Any = None
    dm_name: Any = None
    store_code: Any = None
    note: Any = None


class UpdateAssignmentIn(LaxModel):
    status: Any = None
    note: Any = None
    visit_date: Any = None


# ── Tenant config ─────────────────────────────────────────────────────────────────────────────────
def _tenant_row(client, org_id):
    """The tenant's row, or {} — its own defensive read (the mig-313 posture), so a pre-1050 schema
    resolves to the house defaults instead of failing the board."""
    try:
        rows = (client.table("tenants").select("*").eq("org_id", org_id).limit(1).execute().data) or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def _config(client, org_id):
    return _vp.resolve_config(_tenant_row(client, org_id))


@router.get("/visit-quota-config")
def get_visit_quota_config(org_id: str = ORG_ID):
    """The tenant's DM visit quota + auto-assignment settings, with the house defaults underneath.
    `is_default` says nothing has been configured yet, so a screen can show "using the house 2/day"
    rather than implying somebody chose it."""
    cfg = _config(sb(), org_id)
    return {"config": {k: (list(v) if isinstance(v, tuple) else v) for k, v in cfg.items()},
            "defaults": {k: (list(v) if isinstance(v, tuple) else v)
                         for k, v in _vp.HOUSE_CONFIG.items()},
            "is_default": all(cfg[k] == _vp.HOUSE_CONFIG[k] for k in _vp.HOUSE_CONFIG)}


@router.put("/visit-quota-config")
def put_visit_quota_config(body: PutVisitQuotaConfigIn, org_id: str = ORG_ID,
                           authorization: str = Header(default="")):
    """Edit the quota and the deadline. Permission-gated the same way the rest of this module's
    settings are — the server check, not just the nav restriction."""
    from app.modules.storevisit.router import _caller_perms, _can_edit_visit_setting
    if not _can_edit_visit_setting(_caller_perms(authorization)):
        raise HTTPException(403, "Editing the DM visit quota is permission-restricted.")
    row = {"org_id": org_id, "updated_at": _now()}
    for f in ("dm_visit_quota_enabled", "dm_visit_assign_auto_enabled"):
        if f in body.model_fields_set:
            row[f] = bool(getattr(body, f))
    if "dm_visit_quota_per_day" in body.model_fields_set:
        v = _vp._i(body.dm_visit_quota_per_day)
        if v is None or v < 0 or v > 50:
            raise HTTPException(400, "dm_visit_quota_per_day must be 0–50 (0 = no daily visit required)")
        row["dm_visit_quota_per_day"] = v
    if "dm_visit_quota_days" in body.model_fields_set:
        row["dm_visit_quota_days"] = list(_vp.normalize_days(body.dm_visit_quota_days))
    if "dm_visit_assign_deadline_dow" in body.model_fields_set:
        v = _vp._i(body.dm_visit_assign_deadline_dow)
        if v is None or not (1 <= v <= 7):
            raise HTTPException(400, "dm_visit_assign_deadline_dow must be 1 (Monday) – 7 (Sunday)")
        row["dm_visit_assign_deadline_dow"] = v
    if "dm_visit_assign_deadline_time" in body.model_fields_set:
        row["dm_visit_assign_deadline_time"] = _vp.normalize_time(body.dm_visit_assign_deadline_time)
    if "dm_visit_assign_horizon_days" in body.model_fields_set:
        v = _vp._i(body.dm_visit_assign_horizon_days)
        if v is None or not (1 <= v <= 31):
            raise HTTPException(400, "dm_visit_assign_horizon_days must be 1–31")
        row["dm_visit_assign_horizon_days"] = v
    try:
        sb().table("tenants").upsert(row, on_conflict="org_id").execute()
    except Exception:
        raise HTTPException(400, "run migration 1050 first (storeops.tenants DM visit quota columns)")
    return get_visit_quota_config(org_id)


# ── The priority order, and the carrier-specific dropdown that feeds it ───────────────────────────
def _org_carrier_id(client, org_id):
    """The org's chosen carrier, DEREFERENCED from commcalc.commission_org_config — the same read
    `commcalc/router._kpi_carrier_id` does, so the dropdown offers the carrier the KPI registry is
    keyed on and not a second idea of who the tenant's carrier is. None when unset."""
    try:
        r = (get_supabase().schema("commcalc").table("commission_org_config")
             .select("carrier_id").eq("org_id", org_id).limit(1).execute().data) or []
        return (r[0].get("carrier_id") if r else None) or None
    except Exception:
        return None


def _priority_rule_rows(client, org_id, carrier_id=None):
    """The org's rules narrowed to its own carrier plus the carrier-neutral set. [] pre-1050."""
    try:
        rows = (client.table("dm_visit_priority_rule").select("*")
                .eq("org_id", org_id).order("sort").execute().data) or []
    except Exception:
        return []
    keep = []
    for r in rows:
        cid = r.get("carrier_id")
        if cid in (None, "", NIL_CARRIER) or (carrier_id and _s(cid) == _s(carrier_id)):
            keep.append(r)
    return keep


def _rules(client, org_id):
    cid = _org_carrier_id(client, org_id)
    rules, rejected = _vp.normalize_rules(_priority_rule_rows(client, org_id, cid))
    return rules, rejected, cid


@router.get("/visit-priority-rules")
def list_visit_priority_rules(org_id: str = ORG_ID):
    """The order stores are offered in when nobody assigned a DM's week. `is_default` = this tenant
    has written none, so the house order (activations → accessories → KPI) is in force. `rejected`
    names any stored rule that could not be read — never silently scored on something else."""
    client = sb()
    rules, rejected, cid = _rules(client, org_id)
    stored = _priority_rule_rows(client, org_id, cid)
    return {"rules": rules, "rejected": rejected, "carrier_id": cid,
            "is_default": not stored,
            "defaults": [dict(r) for r in _vp.HOUSE_PRIORITY_RULES]}


@router.get("/visit-priority-options")
def visit_priority_options(org_id: str = ORG_ID):
    """THE DROPDOWN the owner asked for — "options to assign other deliverables … carrier specific".

    It is not a list this module keeps. It is two existing registries, read:
      • the Daily Targets categories (`commcalc/targets_engine.CATEGORIES`) — activations, upgrades,
        BYOD, accessory $;
      • this org's KPI metric registry (`commcalc.carrier_kpi_metric`, mig 060) for ITS OWN carrier
        plus the carrier-neutral default set. A tenant whose carrier has an extra deliverable adds it
        on the KPI-metrics screen it already has, and it appears here with no code change.
    Plus the whole-registry roll-up (`kpi_all`), which is the owner's "then kpi" without this module
    choosing which KPIs count.

    `already_used` marks the options the tenant has a rule for, so the dropdown never offers a
    duplicate — two rules on one metric would be two voices on one question."""
    client = sb()
    cid = _org_carrier_id(client, org_id)
    opts = []
    try:
        from app.modules.commcalc import targets_engine as _te
        cats = list(_te.CATEGORIES)
        units = dict(_te.UNITS)
    except Exception:
        cats, units = [], {}
    for c in cats:
        opts.append({"basis": "target_category", "metric_key": c,
                     "label": c.replace("_", " ").title(),
                     "unit": units.get(c), "group": "Daily Targets",
                     "source": "commcalc/targets_engine.CATEGORIES"})
    opts.append({"basis": "kpi_all", "metric_key": "", "label": "KPI attainment (all metrics)",
                 "unit": "percent", "group": "KPI",
                 "source": "mean of commcalc.carrier_kpi_metric active metrics"})
    kpi_ready = True
    try:
        rows = (get_supabase().schema("commcalc").table("carrier_kpi_metric").select("*")
                .eq("org_id", org_id).eq("is_active", True).order("sort").execute().data) or []
    except Exception:
        rows, kpi_ready = [], False
    seen = set()
    for r in rows:
        rc = r.get("carrier_id")
        if not (rc in (None, "", NIL_CARRIER) or (cid and _s(rc) == _s(cid))):
            continue
        k = _s(r.get("metric_key"))
        if not k or k in seen:
            continue
        seen.add(k)
        opts.append({"basis": "kpi_metric", "metric_key": k,
                     "label": _s(r.get("label")) or k, "unit": "percent",
                     "group": "KPI", "carrier_specific": rc not in (None, "", NIL_CARRIER),
                     "source": "commcalc.carrier_kpi_metric"})
    used = {f"{_s(r.get('basis'))}:{_s(r.get('metric_key'))}"
            for r in _priority_rule_rows(client, org_id, cid)}
    for o in opts:
        o["already_used"] = f"{o['basis']}:{o['metric_key']}" in used
    return {"options": opts, "carrier_id": cid, "kpi_registry_ready": kpi_ready,
            "directions": list(_vp.DIRECTIONS),
            "note": ("Add a deliverable your carrier pushes on the KPI metrics screen "
                     "(Commission → KPI metrics) and it appears in this list.")}


@router.put("/visit-priority-rules")
def save_visit_priority_rule(body: PutPriorityRuleIn, org_id: str = ORG_ID,
                             authorization: str = Header(default="")):
    """Add or edit one priority rule. `basis` + `metric_key` must be an option
    `GET /visit-priority-options` offers — a rule scoring on a metric no registry carries would rank
    every store 'unmeasured' and read as a working priority list."""
    from app.modules.storevisit.router import _caller_perms, _can_edit_visit_setting
    if not _can_edit_visit_setting(_caller_perms(authorization)):
        raise HTTPException(403, "Editing the visit priority order is permission-restricted.")
    basis = _s(body.basis).lower() or "target_category"
    key = "" if basis == "kpi_all" else _s(body.metric_key)
    if basis not in _vp.BASES:
        raise HTTPException(400, f"basis must be one of {', '.join(_vp.BASES)}")
    direction = _s(body.direction).lower() or "low_first"
    if direction not in _vp.DIRECTIONS:
        raise HTTPException(400, f"direction must be one of {', '.join(_vp.DIRECTIONS)}")
    offered = {f"{o['basis']}:{o['metric_key']}"
               for o in visit_priority_options(org_id=org_id)["options"]}
    if f"{basis}:{key}" not in offered:
        raise HTTPException(400, "that metric is not in this tenant's target categories or carrier KPI "
                                 "registry — add it on the KPI metrics screen first")
    w = _vp._f(body.weight, 1.0)
    if w is None or w <= 0:
        raise HTTPException(400, "weight must be greater than 0")
    row = {"org_id": org_id, "carrier_id": body.carrier_id or None,
           "sort": _vp._i(body.sort, 100) or 100, "basis": basis, "metric_key": key,
           "weight": float(w), "direction": direction,
           "label": _s(body.label) or None, "is_active": bool(body.is_active),
           "updated_at": _now()}
    try:
        if body.id:
            r = (sb().table("dm_visit_priority_rule").update(row)
                 .eq("id", body.id).eq("org_id", org_id).execute())
        else:
            r = (sb().table("dm_visit_priority_rule")
                 .upsert(row, on_conflict="org_id,carrier_id,basis,metric_key").execute())
        return (r.data or [row])[0]
    except Exception as e:
        raise HTTPException(400, f"save priority rule failed (is migration 1050 applied?): {e}")


@router.delete("/visit-priority-rules/{rule_id}")
def delete_visit_priority_rule(rule_id: str, org_id: str = ORG_ID,
                               authorization: str = Header(default="")):
    """Deactivate a rule. Soft, so an auto-filled assignment's `priority_detail` still resolves to a
    rule somebody can look up."""
    from app.modules.storevisit.router import _caller_perms, _can_edit_visit_setting
    if not _can_edit_visit_setting(_caller_perms(authorization)):
        raise HTTPException(403, "Editing the visit priority order is permission-restricted.")
    try:
        sb().table("dm_visit_priority_rule").update({"is_active": False, "updated_at": _now()}) \
            .eq("id", rule_id).eq("org_id", org_id).execute()
    except Exception as e:
        raise HTTPException(400, f"deactivate failed (is migration 1050 applied?): {e}")
    return {"deactivated": rule_id}


# ── The DM spans, from THE one org-tree walk ──────────────────────────────────────────────────────
def _dm_spans(org_id):
    """{dm_key: {name, email, store_codes}} + the stores the org tree gives no DM.

    DEREFERENCES `storeops/org_chain.dm_by_store` (index §48.7) — the ONE walk — read inverted. No
    second walk, no second idea of which stores a DM owns.

    THE SIBLING THIS CHANGE DOES NOT FIX, STATED RATHER THAN HIDDEN: a second mechanism already
    answers a nearby question — `storeops/target_attribution.dm_roster_from_app_users` attributes
    accessory targets to a DM by the MARKETS granted on their login, not by the org tree. The two can
    disagree, and the Daily-Targets-attribution one is on a money path this change must not move. So
    this surface uses the org tree (the designated home for store→DM) and REPORTS the disagreement in
    `span_divergence` instead of silently picking a winner. Unifying them is its own change."""
    from app.modules.storeops.router import org_chain_inputs
    from app.modules.storeops import org_chain
    chain = org_chain.dm_by_store(**org_chain_inputs(org_id))
    return _vp.invert_dm_by_store(chain)


def _span_divergence(org_id, spans):
    """Stores whose org-tree DM span disagrees with the market-grant DM roster the accessory-target
    attribution uses. Read-only, best-effort, never raises — it is a FINDING on the board, not a gate."""
    try:
        # DEREFERENCES `storeops.router._dm_roster` — the existing reader of the market-grant roster.
        # Reading app_users and roles again here would be a third copy of that resolution.
        from app.modules.storeops.router import _dm_roster
        grants = _dm_roster(org_id)
        tree_dms = len(spans or {})
        grant_dms = len(grants or {})
        return {"org_tree_dms": tree_dms, "market_grant_dms": grant_dms,
                "disagrees": tree_dms != grant_dms,
                "meaning": ("Two mechanisms answer 'which stores does this DM own': the org tree "
                            "(used here) and the markets granted on a DM's login (used by accessory "
                            "target attribution). A different count means they disagree for at least "
                            "one DM. Reported, not resolved — see the note in _dm_spans.")}
    except Exception as e:
        return {"unavailable": str(e)[:200]}


# ── Store performance, DEREFERENCED from the Daily Targets summary ────────────────────────────────
async def _store_metrics(org_id, period=None, authorization=""):
    """{store_code: {"categories": {...}, "kpi": {...}}} for `rank_stores`.

    Activations / accessory $ / upgrades / BYOD come from the Daily Targets store summary — the SAME
    rows the Targets pages render — and attainment is `targets_engine.attainment_pct`, the one
    formula. Nothing is recounted from sales here; a second count of a store's activations is exactly
    the duplicate the index rules forbid.

    KPI values come from `commcalc.kpi_actual` at store grain against each metric's configured target,
    through the same registry the pay engine reads.

    Returns (metrics_by_code, meta). `meta.unmeasured` names every (store, rule) with no target and
    no value — never folded into a score."""
    from app.modules.commcalc import targets_engine as _te
    from app.modules.commcalc.router import get_targets_summary, _kpi_defs, _kpi_actuals_by_store
    client = get_supabase()
    period = _s(period) or datetime.now(timezone.utc).strftime("%Y-%m")
    out, meta = {}, {"period": period, "targets_read": 0, "kpi_read": 0, "errors": []}
    try:
        # include_untargeted so a store with sales but no target still appears — with attainment None,
        # which is the honest answer and is reported, not scored.
        # `stores`/`markets`/`reps` are passed explicitly as None: this function is a FastAPI
        # handler, so its defaults are Query(...) objects, and calling it in-process without them
        # would hand the filter logic a Query marker instead of "no filter".
        summary = await get_targets_summary(period=period, today="", include_untargeted=True,
                                            include_inactive=False, stores=None, markets=None,
                                            reps=None, authorization="", org_id=org_id)
        rows = summary.get("stores") or []
    except Exception as e:
        rows, _ = [], meta["errors"].append(f"targets summary unavailable: {str(e)[:200]}")
    meta["targets_read"] = len(rows)
    for r in rows:
        code = _s(r.get("store_code")).upper()
        if not code:
            continue
        cats = {}
        for cat, m in (r.get("categories") or {}).items():
            cats[cat] = {"monthly": m.get("monthly"), "achieved": m.get("achieved_mtd"),
                         "attainment": _te.attainment_fraction(m.get("monthly"), m.get("achieved_mtd"))}
        out[code] = {"categories": cats, "kpi": {},
                     "address": r.get("address"), "market": r.get("market")}
    # KPI: the registry's targets vs the measured store-grain actuals.
    try:
        defs = _kpi_defs(org_id)
        actuals = _kpi_actuals_by_store(client, org_id, period)
        meta["kpi_read"] = len(actuals)
        targets = {k: d for (k, _l, _c, d) in defs}
        for ent, byk in (actuals or {}).items():
            code = _s(ent).upper()
            if code not in out:
                out[code] = {"categories": {}, "kpi": {}}
            for k, v in (byk or {}).items():
                tgt = targets.get(k)
                out[code]["kpi"][k] = {
                    "value": v, "target": tgt,
                    "attainment": _te.attainment_fraction(tgt, v)}
    except Exception as e:
        meta["errors"].append(f"kpi actuals unavailable: {str(e)[:200]}")
    return out, meta


# ── Assignments ───────────────────────────────────────────────────────────────────────────────────
def _read_assignments(client, org_id, date_from, date_to, dm_employee_id=None):
    try:
        q = (client.table("dm_visit_assignment").select("*").eq("org_id", org_id)
             .gte("visit_date", date_from).lte("visit_date", date_to))
        if dm_employee_id:
            q = q.eq("dm_employee_id", dm_employee_id)
        return q.order("visit_date").limit(5000).execute().data or []
    except Exception:
        return []


def _read_submitted_visits(client, org_id, date_from, date_to):
    """Visits actually logged in the window, keyed to the DM who logged them. Reads the EXISTING
    `storeops.store_visits` (mig 027) — the real record of work done — never a second log."""
    rows = []
    try:
        rows = (client.table("store_visits").select("store_code,dm_email,dm_name,check_in_at,status")
                .eq("org_id", org_id)
                .gte("check_in_at", f"{date_from}T00:00:00")
                .lte("check_in_at", f"{date_to}T23:59:59.999999")
                .limit(5000).execute().data) or []
    except Exception:
        return []
    out = []
    for r in rows:
        out.append({"dm_email": _s(r.get("dm_email")).lower(), "dm_name": r.get("dm_name"),
                    "store_code": r.get("store_code"),
                    "visit_date": _s(r.get("check_in_at"))[:10], "status": r.get("status")})
    return out


def _dm_key_by_email(spans):
    return {_s(v.get("email")).lower(): k for k, v in (spans or {}).items() if _s(v.get("email"))}


@router.get("/visit-assignments")
def list_visit_assignments(date_from: str = "", date_to: str = "", dm_employee_id: str = "",
                           org_id: str = ORG_ID, authorization: str = Header(default="")):
    """The assignments on the calendar. Span-scoped through the SAME keyset every other manager
    surface uses, so a market-scoped caller sees their own stores."""
    client = sb()
    today = datetime.now(timezone.utc).date()
    df = _s(date_from)[:10] or today.isoformat()
    dt = _s(date_to)[:10] or (today + timedelta(days=13)).isoformat()
    rows = _read_assignments(client, org_id, df, dt, _s(dm_employee_id) or None)
    try:
        from app.modules.storeops.router import scope_keyset, in_keyset
        ks = scope_keyset(authorization, org_id)
        if ks is not None:
            rows = [r for r in rows if in_keyset(ks, r.get("store_code"))]
    except Exception:
        pass
    return {"date_from": df, "date_to": dt, "assignments": rows, "count": len(rows)}


@router.post("/visit-assignments")
def create_visit_assignment(body: CreateAssignmentIn, org_id: str = ORG_ID,
                            authorization: str = Header(default="")):
    """The market manager assigns one store to one DM for one day. Refuses a store outside that DM's
    span — sending a DM to a store they do not own is not an assignment — and is idempotent on
    (date, DM, store)."""
    from app.modules.storeops.router import _require_manager
    mgr = _require_manager(authorization, org_id)
    org_id = _s(mgr.get("org_id")) or org_id
    d = _s(body.visit_date)[:10]
    dm = _s(body.dm_employee_id)
    code = _s(body.store_code)
    if not (d and dm and code):
        raise HTTPException(400, "visit_date, dm_employee_id and store_code are required")
    spans, _unowned = _dm_spans(org_id)
    span = spans.get(dm) or {}
    if span and code.upper() not in (span.get("store_codes") or set()):
        raise HTTPException(400, "that store is not in this DM's span — assign it to the DM whose "
                                 "district owns it, or fix the org tree")
    row = {"org_id": org_id, "visit_date": d, "dm_employee_id": dm,
           "dm_email": _s(body.dm_email) or span.get("email"),
           "dm_name": _s(body.dm_name) or span.get("name"),
           "store_code": code, "source": "manual", "status": "open",
           "note": _s(body.note) or None,
           "assigned_by": _s(mgr.get("email")) or _s(mgr.get("employee_id")),
           "updated_at": _now()}
    try:
        r = (sb().table("dm_visit_assignment")
             .upsert(row, on_conflict="org_id,visit_date,dm_employee_id,store_code").execute())
        return (r.data or [row])[0]
    except Exception as e:
        raise HTTPException(400, f"assign failed (is migration 1050 applied?): {e}")


@router.patch("/visit-assignments/{assignment_id}")
def update_visit_assignment(assignment_id: str, body: UpdateAssignmentIn, org_id: str = ORG_ID,
                            authorization: str = Header(default="")):
    """Move an assignment's day, or mark it visited / skipped."""
    from app.modules.storeops.router import _require_manager
    mgr = _require_manager(authorization, org_id)
    org_id = _s(mgr.get("org_id")) or org_id
    upd = {"updated_at": _now()}
    if "status" in body.model_fields_set:
        st = _s(body.status).lower()
        if st not in _vp.ASSIGN_STATUSES:
            raise HTTPException(400, f"status must be one of {', '.join(_vp.ASSIGN_STATUSES)}")
        upd["status"] = st
    if "note" in body.model_fields_set:
        upd["note"] = _s(body.note) or None
    if "visit_date" in body.model_fields_set:
        d = _s(body.visit_date)[:10]
        if not d:
            raise HTTPException(400, "visit_date cannot be blank")
        upd["visit_date"] = d
    try:
        r = (sb().table("dm_visit_assignment").update(upd)
             .eq("id", assignment_id).eq("org_id", org_id).execute())
        return (r.data or [upd])[0]
    except Exception as e:
        raise HTTPException(400, f"update failed: {e}")


@router.delete("/visit-assignments/{assignment_id}")
def delete_visit_assignment(assignment_id: str, org_id: str = ORG_ID,
                            authorization: str = Header(default="")):
    from app.modules.storeops.router import _require_manager
    mgr = _require_manager(authorization, org_id)
    org_id = _s(mgr.get("org_id")) or org_id
    sb().table("dm_visit_assignment").delete().eq("id", assignment_id).eq("org_id", org_id).execute()
    return {"deleted": assignment_id}


# ── The market manager's monitor ──────────────────────────────────────────────────────────────────
@router.get("/dm-visit-performance")
async def dm_visit_performance(date_from: str = "", days: int = 14, period: str = "",
                               org_id: str = ORG_ID, authorization: str = Header(default="")):
    """THE MARKET MANAGER'S BOARD — per DM per quota day: required, assigned, completed, shortfall,
    and which stores are next in the priority order.

    A DM with nothing assigned and nothing done still appears as a real present zero: a DM missing
    from the board is the one nobody chased. A visit the DM did to a store nobody assigned still
    counts toward the quota and is named in `unassigned_visits` — the work happened.

    `unowned_stores` names every store the org tree gives no DM, and `unmeasured` every store with no
    target on any priority rule. Neither is folded into a total. READ-ONLY."""
    client = sb()
    cfg = _config(client, org_id)
    today = datetime.now(timezone.utc).date()
    try:
        start = date.fromisoformat(_s(date_from)[:10]) if _s(date_from) else today
    except ValueError:
        raise HTTPException(400, "date_from must be YYYY-MM-DD")
    n = max(1, min(int(days or 14), 62))
    dates = _vp.quota_dates(start, n, cfg)
    df, dt = start.isoformat(), (start + timedelta(days=n - 1)).isoformat()

    spans, unowned = _dm_spans(org_id)
    by_email = _dm_key_by_email(spans)
    assigns = [{"dm_key": _s(a.get("dm_employee_id")), "visit_date": _s(a.get("visit_date"))[:10],
                "store_code": a.get("store_code"), "source": a.get("source"),
                "status": a.get("status")}
               for a in _read_assignments(client, org_id, df, dt)]
    visits = []
    for v in _read_submitted_visits(client, org_id, df, dt):
        k = by_email.get(v["dm_email"])
        if k:
            visits.append({"dm_key": k, "visit_date": v["visit_date"], "store_code": v["store_code"]})
    dms = [{"key": k, "name": v.get("name"), "email": v.get("email")} for k, v in spans.items()]
    status = _vp.quota_status(assigns, visits, cfg, dms, dates)

    rules, rejected, carrier_id = _rules(client, org_id)
    metrics, mmeta = await _store_metrics(org_id, period=period, authorization=authorization)
    ranked = _vp.rank_stores(metrics, rules)
    # The next few stores in the order, per DM — what the manager would pick if they picked now.
    suggestions = {}
    for k, span in spans.items():
        codes = {_s(c).upper() for c in (span.get("store_codes") or ())}
        picks = [r for r in ranked if _s(r.get("store_code")).upper() in codes][:5]
        suggestions[k] = [{"store_code": p["store_code"], "rank": p["rank"], "score": p["score"],
                           "reason": _vp.reason_text(p["detail"])} for p in picks]
    unmeasured = [r["store_code"] for r in ranked if r["detail"]["measured_rules"] == 0]
    try:
        from app.modules.storeops.router import scope_keyset, in_keyset
        ks = scope_keyset(authorization, org_id)
    except Exception:
        ks = None
    if ks is not None:
        allowed = {k for k, sp in spans.items()
                   if any(in_keyset(ks, c) for c in (sp.get("store_codes") or ()))}
        status["rows"] = [r for r in status["rows"] if r["dm_key"] in allowed]
        suggestions = {k: v for k, v in suggestions.items() if k in allowed}
    return {"date_from": df, "date_to": dt, "quota_dates": [d.isoformat() for d in dates],
            "config": {k: (list(v) if isinstance(v, tuple) else v) for k, v in cfg.items()},
            "quota": status, "suggestions": suggestions,
            "rules": rules, "rules_rejected": rejected, "carrier_id": carrier_id,
            "deadline_reached": _vp.deadline_reached(datetime.now(_biz_tz(org_id)), cfg),
            "unowned_stores": unowned, "unmeasured_stores": unmeasured,
            "span_divergence": _span_divergence(org_id, spans),
            "metrics_meta": mmeta,
            "restricted": ks is not None}


def _biz_tz(org_id):
    """The tenant's own business timezone — `storeops.router._biz_tz_for`, the one home. The deadline
    is 'Friday evening' where the tenant is, not where the server is."""
    try:
        from app.modules.storeops.router import _biz_tz_for
        return _biz_tz_for(org_id)
    except Exception:
        return timezone.utc


# ── The Friday-evening auto-fill ──────────────────────────────────────────────────────────────────
async def _do_auto_fill(org_id, *, respect_deadline=True, respect_enabled=True, dry_run=True,
                        start_from=None, period=""):
    """Top up every under-quota DM-day in the horizon from the tenant's priority order. NEVER raises.

    What it will not do: replace a store a manager chose, assign a store outside a DM's span, assign
    the same store twice in the window, or write anything at all when `dry_run`. What it reports
    rather than hides: DM-days it could not fill (`short`), stores the org tree gives no DM
    (`unowned`), and stores no rule could measure (`unmeasured`)."""
    client = sb()
    cfg = _config(client, org_id)
    tz = _biz_tz(org_id)
    now_local = datetime.now(tz)
    out = {"org_id": org_id, "dry_run": bool(dry_run), "written": 0,
           "config": {k: (list(v) if isinstance(v, tuple) else v) for k, v in cfg.items()}}
    if respect_enabled and not cfg.get("auto_enabled", True):
        return {**out, "skipped": "auto-assignment is off for this tenant"}
    if respect_enabled and not cfg.get("quota_enabled", True):
        return {**out, "skipped": "the visit quota is off for this tenant"}
    if respect_deadline and not _vp.deadline_reached(now_local, cfg):
        return {**out, "skipped": f"before this tenant's assignment deadline "
                                  f"(day {cfg['deadline_dow']} {cfg['deadline_time']} local)"}
    # The window starts the day AFTER the deadline day, so the fill plans the week ahead rather than
    # back-filling days that have already been worked.
    start = start_from or (now_local.date() + timedelta(days=1))
    dates = _vp.quota_dates(start, cfg["horizon_days"], cfg)
    if not dates:
        return {**out, "skipped": "no quota day falls in the horizon"}
    spans, unowned = _dm_spans(org_id)
    if not spans:
        return {**out, "skipped": "the org tree assigns no DM to any store — nothing to assign to",
                "unowned_stores": unowned}
    rules, rejected, carrier_id = _rules(client, org_id)
    metrics, mmeta = await _store_metrics(org_id, period=period)
    ranked = _vp.rank_stores(metrics, rules)
    existing = [{"dm_key": _s(a.get("dm_employee_id")), "visit_date": _s(a.get("visit_date"))[:10],
                 "store_code": a.get("store_code")}
                for a in _read_assignments(client, org_id, dates[0].isoformat(),
                                           dates[-1].isoformat())]
    plan = _vp.plan_auto_assignments(dm_spans=spans, ranked=ranked, config=cfg,
                                     existing=existing, dates=dates)
    rows = []
    for a in plan["assignments"]:
        rows.append({"org_id": org_id, "visit_date": a["visit_date"],
                     "dm_employee_id": a["dm_key"], "dm_email": a.get("dm_email"),
                     "dm_name": a.get("dm_name"), "store_code": a["store_code"],
                     "market": (metrics.get(_s(a["store_code"]).upper()) or {}).get("market"),
                     "source": "auto", "status": "open",
                     "priority_rank": a.get("priority_rank"), "priority_score": a.get("score"),
                     "priority_reason": a.get("priority_reason"),
                     "priority_detail": a.get("priority_detail"),
                     "assigned_by": "system (priority rules)", "updated_at": _now()})
    detail = {"planned": len(rows), "short": plan["short"], "skipped_days": plan["skipped_days"],
              "unowned_stores": unowned,
              "unmeasured_stores": [r["store_code"] for r in ranked
                                    if r["detail"]["measured_rules"] == 0],
              "rules": rules, "rules_rejected": rejected, "carrier_id": carrier_id,
              "metrics_meta": mmeta,
              "dates": [d.isoformat() for d in dates]}
    if dry_run or not rows:
        return {**out, "plan": rows, **detail}
    try:
        # The unique index on (org, date, DM, store) is what makes this safe on an hourly tick: a
        # second run collides on every row it would re-add and changes nothing.
        sb().table("dm_visit_assignment").upsert(
            rows, on_conflict="org_id,visit_date,dm_employee_id,store_code",
            ignore_duplicates=True).execute()
        out["written"] = len(rows)
    except Exception as e:
        detail["error"] = f"write failed (is migration 1050 applied?): {str(e)[:300]}"
        return {**out, "plan": rows, **detail}
    try:
        sb().table("tenants").upsert(
            {"org_id": org_id, "dm_visit_assign_last_run": _now(),
             "dm_visit_assign_last_detail": (f"{len(rows)} assigned across {len(dates)} day(s); "
                                             f"{len(plan['short'])} slot group(s) short")},
            on_conflict="org_id").execute()
    except Exception:
        pass
    return {**out, "plan": rows, **detail}


@router.post("/visit-assignments/auto-fill")
async def auto_fill_now(send: bool = False, force: bool = False, period: str = "",
                        org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Run the fill for THIS tenant. `send=0` (the default) is a dry run — the plan with every reason,
    nothing written — so a manager can see what the rules would do before the deadline does it.
    `force=1` ignores the deadline and the tenant switches (for a preview out of hours)."""
    from app.modules.storeops.router import _require_manager
    mgr = _require_manager(authorization, org_id)
    org_id = _s(mgr.get("org_id")) or org_id
    return await _do_auto_fill(org_id, respect_deadline=not force, respect_enabled=not force,
                               dry_run=not send, period=period)


@router.post("/visit-assignments/auto-fill/run-due")
async def auto_fill_run_due(x_notify_secret: str = Header(default="")):
    """pg_cron entrypoint (guarded by NOTIFY_RUN_SECRET) — hourly across ALL tenants, the mig-433
    convention: each tenant's own deadline weekday + local time is compared inside the handler, so one
    job serves every timezone. Idempotent on the unique index."""
    if not verify_notify_secret(x_notify_secret):
        raise HTTPException(403, "forbidden")
    root = get_supabase().schema("storeops")
    try:
        tenants = root.table("tenants").select("org_id").execute().data or []
    except Exception:
        tenants = []
    results = []
    for t in tenants:
        oid = _s(t.get("org_id"))
        if not oid:
            continue
        try:
            r = await _do_auto_fill(oid, respect_deadline=True, respect_enabled=True, dry_run=False)
        except Exception as e:
            r = {"org_id": oid, "error": str(e)[:300]}
        results.append({k: v for k, v in r.items()
                        if k in ("org_id", "written", "skipped", "error")})
    return {"tenants": len(results), "results": results}


@router.get("/visit-plan/health")
def visit_plan_health(org_id: str = ORG_ID):
    """Is migration 1050 applied, and what is in force. Read-only, never raises."""
    client = sb()
    ready = {"tenants_columns": False, "priority_rule": False, "assignment": False}
    try:
        client.table("dm_visit_priority_rule").select("id").limit(1).execute()
        ready["priority_rule"] = True
    except Exception:
        pass
    try:
        client.table("dm_visit_assignment").select("id").limit(1).execute()
        ready["assignment"] = True
    except Exception:
        pass
    row = _tenant_row(client, org_id)
    ready["tenants_columns"] = "dm_visit_quota_per_day" in (row or {})
    cfg = _vp.resolve_config(row)
    return {"ok": True, "migration_1050": ready,
            "config": {k: (list(v) if isinstance(v, tuple) else v) for k, v in cfg.items()}}
