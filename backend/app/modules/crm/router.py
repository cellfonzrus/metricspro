"""CRM API — /api/v1/crm/*  (sales pipeline, follow-up engine, agencies, Customer 360).

OWNER DIRECTIVE 2026-08-12 (sanjot@): a Salesforce-class sales pipeline + follow-up system with
reminders for employees to log their leads, dispose them, and assign them to teammates and to outside
agencies; plus a phone-number lookup that returns everything known about a customer, permission-gated.

Spec: docs/specs/CRM_SALES_PIPELINE_SPEC.md (in the commcalc docs repo).
Tables: core.crm_* (migration 800). See that migration's header for why `core` and not a `crm` schema.

Design notes
  • Every decision (scoring, assignment, cadence, reminders, disposition) lives in `pipeline_core`
    as a pure function; this file is I/O and HTTP only. That is what makes the sweep provable
    offline in harness_crm_pipeline.py instead of "verified" by watching production.
  • org_id is a QUERY PARAM on every endpoint (AGENT_CONTRACT §2) — the tenant middleware rewrites
    it from the caller's JWT. Every read filters it and every insert stamps it.
  • Every table read is wrapped: a missing migration degrades to an empty list / a named 400, never
    a 500 that takes an unrelated page down with it.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException

from app.core.database import get_supabase
from app.core.config import settings
from app.core.run_secret import verify_notify_secret
from app.modules.crm import customer360, pipeline_core as core

router = APIRouter(prefix="/crm", tags=["CRM"])

ORG_ID = "00000000-0000-0000-0000-000000000001"   # house org; middleware rewrites the query param


def sb():
    """CRM tables live in core.* (migration 800) — the schema PostgREST already serves."""
    return get_supabase().schema("core")


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat() if hasattr(dt, "isoformat") else dt


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Caller identity, permissions, scope
# ══════════════════════════════════════════════════════════════════════════════════════════════

def _caller(authorization: str, x_active_org: str = ""):
    """{org_id, role, super_admin, perms, id, employee_id, store_code, market} or None.

    `_resolve_caller` gives the tenant/role/permissions; the app_users row adds the identity fields
    the CRM needs to say "this is YOUR lead" (employee_id) and to default a new lead's store."""
    try:
        from app.modules.core.router import _uid_from_token, _resolve_caller
        uid = _uid_from_token(authorization)
        if not uid:
            return None
        c = _resolve_caller(get_supabase(), uid, (x_active_org or "").strip() or None)
        if not c:
            return None
        try:
            from app.core.tenant_middleware import caller_app_user
            u = caller_app_user(uid, "id,org_id,employee_id,store_code,market,full_name,email") or {}
        except Exception:
            u = {}
        return {**c, "id": u.get("id"), "employee_id": u.get("employee_id"),
                "store_code": u.get("store_code"), "market": u.get("market"),
                "full_name": u.get("full_name"), "email": u.get("email")}
    except Exception:
        return None


def _keyset(authorization: str, org_id: str):
    """None = unrestricted; else the UPPER store keyset the caller may see. Same helper closing and
    pos already use, so CRM introduces no second scoping vocabulary."""
    try:
        from app.modules.storeops.router import scope_keyset
        return scope_keyset(authorization, org_id)
    except Exception:
        return None


def _in_keyset(keyset, *vals) -> bool:
    if keyset is None:
        return True
    return any(str(v or "").strip().upper() in keyset for v in vals)


def _can_edit_settings(caller) -> bool:
    """Who may change pipelines, stages, dispositions, cadences, routing rules. Company-wide scope or
    an explicit `settings.crm` grant. A caller we could not resolve is DENIED, never defaulted open."""
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    s = perms.get("settings") or {}
    if "crm" in s:
        return bool(s["crm"])
    return (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin")


def _require_settings(caller):
    if not _can_edit_settings(caller):
        raise HTTPException(403, "Changing CRM setup is permission-restricted — you need the "
                                 "'crm' settings permission or a company-wide role.")


def _is_manager(caller) -> bool:
    return bool(caller and (caller.get("super_admin")
                            or (caller.get("perms") or {}).get("scope") in ("all", "market")))


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Config + lazy tenant seeding
# ══════════════════════════════════════════════════════════════════════════════════════════════

def _seed(org_id: str) -> None:
    """Self-provision this tenant's default pipeline/stages/dispositions/cadence on first touch.

    The migration seeds every tenant that existed when it ran; this covers everyone created after.
    `core.seed_crm_defaults` is ON CONFLICT DO NOTHING throughout, so it never clobbers an edited
    pipeline — calling it on every config read is safe and keeps new tenants from landing on an
    empty CRM. Best-effort: an un-run migration must not 500 the page."""
    try:
        get_supabase().schema("core").rpc("seed_crm_defaults", {"p_org": org_id}).execute()
    except Exception:
        pass


def _config_row(org_id: str) -> dict:
    try:
        rows = (sb().table("crm_config").select("*").eq("org_id", org_id).limit(1).execute().data) or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def _cfg(org_id: str) -> dict:
    return core.resolve_config(_config_row(org_id))


@router.get("/config")
def get_config(org_id: str = ORG_ID, authorization: str = Header(default=""),
               x_active_org: str = Header(default="")):
    _seed(org_id)
    caller = _caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    cfg["can_edit"] = _can_edit_settings(caller)
    cfg["can_lookup"] = customer360.customer_360_allowed(caller, cfg)
    cfg["can_lookup_money"] = customer360.customer_360_financial_allowed(caller)
    cfg["me"] = {"employee_id": (caller or {}).get("employee_id"),
                 "store_code": (caller or {}).get("store_code"),
                 "market": (caller or {}).get("market"),
                 "is_manager": _is_manager(caller)}
    return cfg


@router.put("/config")
def put_config(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
               x_active_org: str = Header(default="")):
    _require_settings(_caller(authorization, x_active_org))
    allowed = {"default_pipeline_id", "timezone", "business_hours", "stale_lead_hours",
               "escalate_after_hours", "miss_grace_hours", "require_disposition_on_close",
               "duplicate_match", "reminder_channels", "auto_convert_on_won",
               "max_open_leads_per_rep", "daily_logging_reminder_hour", "intake_key",
               "lookup_requires_grant"}
    # Whitelist the payload but REJECT unknown keys loudly rather than dropping them silently — a
    # config POST that answers "Saved ✓" while the database never sees the field has bitten this
    # codebase twice ([[config-write-whitelist-silent-drop]]).
    unknown = [k for k in body.keys() if k not in allowed and k != "org_id"]
    if unknown:
        raise HTTPException(400, f"Unknown CRM setting(s): {', '.join(sorted(unknown))}. "
                                 f"Nothing was saved.")
    row = {k: v for k, v in body.items() if k in allowed}
    row["org_id"] = org_id
    row["updated_at"] = _iso(_now())
    try:
        sb().table("crm_config").upsert(row, on_conflict="org_id").execute()
    except Exception:
        raise HTTPException(400, "run migration 800 first (core.crm_config)")
    return get_config(org_id, authorization, x_active_org)


# ── generic config-list CRUD ──────────────────────────────────────────────────────────────────
# The nine reference tables (pipelines, stages, sources, ...) are all the same shape: org-scoped
# rows an admin edits in a grid. One implementation, one whitelist per table — a per-table copy of
# the same 30 lines is how field drift starts.
_CONFIG_TABLES = {
    "pipelines":        ("crm_pipeline", {"key", "name", "description", "is_default", "is_active", "sort_order"}),
    "stages":           ("crm_stage", {"pipeline_id", "key", "name", "sort_order", "probability",
                                       "is_won", "is_lost", "sla_hours", "requires_disposition", "is_active"}),
    "sources":          ("crm_source", {"key", "name", "category", "is_active", "sort_order"}),
    "interests":        ("crm_interest", {"key", "name", "category", "is_active", "sort_order"}),
    "dispositions":     ("crm_disposition", {"key", "name", "outcome", "requires_followup",
                                             "default_followup_hours", "requires_reason", "closes_lead",
                                             "sets_stage_id", "is_active", "sort_order"}),
    "reason-codes":     ("crm_reason_code", {"disposition_id", "key", "name", "is_active", "sort_order"}),
    "queues":           ("crm_queue", {"key", "name", "is_active"}),
    "queue-members":    ("crm_queue_member", {"queue_id", "employee_id", "sort_order", "is_active"}),
    "assignment-rules": ("crm_assignment_rule", {"name", "priority", "match", "strategy",
                                                 "target_employee_id", "target_queue_id",
                                                 "target_agency_id", "is_active"}),
    "cadences":         ("crm_cadence", {"name", "pipeline_id", "stage_id", "trigger", "idle_hours", "is_active"}),
    "cadence-steps":    ("crm_cadence_step", {"cadence_id", "step_no", "offset_hours", "channel",
                                              "task_type", "title", "body", "assign_to", "is_active"}),
    "score-rules":      ("crm_score_rule", {"name", "field", "op", "value", "points", "is_active"}),
    "agencies":         ("crm_agency", {"name", "type", "contact_name", "email", "phone",
                                        "commission_note", "portal_enabled", "is_active"}),
    "agency-contacts":  ("crm_agency_contact", {"agency_id", "name", "email", "phone", "is_primary"}),
}
_CONFIG_ORDER = {
    "crm_pipeline": "sort_order", "crm_stage": "sort_order", "crm_source": "sort_order",
    "crm_interest": "sort_order", "crm_disposition": "sort_order", "crm_reason_code": "sort_order",
    "crm_assignment_rule": "priority", "crm_cadence_step": "step_no", "crm_agency": "name",
}


def _table_for(name: str):
    entry = _CONFIG_TABLES.get(name)
    if not entry:
        raise HTTPException(404, f"Unknown CRM configuration list '{name}'.")
    return entry


@router.get("/lists/{name}")
def list_config(name: str, org_id: str = ORG_ID, include_inactive: bool = False):
    table, _ = _table_for(name)
    _seed(org_id)
    try:
        q = sb().table(table).select("*").eq("org_id", org_id)
        order_col = _CONFIG_ORDER.get(table)
        if order_col:
            q = q.order(order_col)
        rows = q.limit(1000).execute().data or []
    except Exception:
        return []
    if not include_inactive:
        rows = [r for r in rows if r.get("is_active", True)]
    return rows


@router.post("/lists/{name}")
def create_config(name: str, body: dict, org_id: str = ORG_ID,
                  authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    _require_settings(_caller(authorization, x_active_org))
    table, allowed = _table_for(name)
    unknown = [k for k in body.keys() if k not in allowed and k not in ("org_id", "id")]
    if unknown:
        raise HTTPException(400, f"Unknown field(s) for {name}: {', '.join(sorted(unknown))}. "
                                 f"Nothing was saved.")
    row = {k: v for k, v in body.items() if k in allowed}
    row["org_id"] = org_id                       # ALWAYS stamped, never taken from the body
    try:
        r = sb().table(table).insert(row).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save: {e}")
    return (r.data or [{}])[0]


@router.put("/lists/{name}/{row_id}")
def update_config(name: str, row_id: str, body: dict, org_id: str = ORG_ID,
                  authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    _require_settings(_caller(authorization, x_active_org))
    table, allowed = _table_for(name)
    unknown = [k for k in body.keys() if k not in allowed and k not in ("org_id", "id")]
    if unknown:
        raise HTTPException(400, f"Unknown field(s) for {name}: {', '.join(sorted(unknown))}. "
                                 f"Nothing was saved.")
    row = {k: v for k, v in body.items() if k in allowed}
    if not row:
        raise HTTPException(400, "Nothing to update.")
    try:
        r = sb().table(table).update(row).eq("org_id", org_id).eq("id", row_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save: {e}")
    return (r.data or [{}])[0]


@router.delete("/lists/{name}/{row_id}")
def delete_config(name: str, row_id: str, org_id: str = ORG_ID,
                  authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Deactivate, not delete, for anything a lead can point at — a disposition that vanishes turns
    every historical lead that used it into an unreadable row. Hard-delete only where nothing
    references it (queue members, agency contacts, cadence steps)."""
    _require_settings(_caller(authorization, x_active_org))
    table, allowed = _table_for(name)
    hard = table in ("crm_queue_member", "crm_agency_contact", "crm_cadence_step",
                     "crm_assignment_rule", "crm_score_rule")
    try:
        if hard:
            sb().table(table).delete().eq("org_id", org_id).eq("id", row_id).execute()
            return {"deleted": row_id}
        sb().table(table).update({"is_active": False}).eq("org_id", org_id).eq("id", row_id).execute()
        return {"deactivated": row_id}
    except Exception as e:
        raise HTTPException(400, f"Could not remove: {e}")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Lookup helpers shared by the lead endpoints
# ══════════════════════════════════════════════════════════════════════════════════════════════

def _fetch(table: str, org_id: str, select: str = "*", limit: int = 1000, **eq):
    try:
        q = sb().table(table).select(select).eq("org_id", org_id)
        for k, v in eq.items():
            q = q.eq(k, v)
        return q.limit(limit).execute().data or []
    except Exception:
        return []


def _by_id(rows):
    return {r.get("id"): r for r in rows or []}


def _vocab(org_id: str) -> dict:
    """The reference data every lead read needs: stages, sources, interests, dispositions. Fetched
    once per request and threaded through, rather than an N+1 per lead."""
    return {
        "stages": _fetch("crm_stage", org_id),
        "sources": _fetch("crm_source", org_id),
        "interests": _fetch("crm_interest", org_id),
        "dispositions": _fetch("crm_disposition", org_id),
        "pipelines": _fetch("crm_pipeline", org_id),
        "agencies": _fetch("crm_agency", org_id),
    }


def _decorate(lead: dict, vocab: dict) -> dict:
    stage = _by_id(vocab["stages"]).get(lead.get("stage_id")) or {}
    source = _by_id(vocab["sources"]).get(lead.get("source_id")) or {}
    interest = _by_id(vocab["interests"]).get(lead.get("interest_id")) or {}
    disp = _by_id(vocab["dispositions"]).get(lead.get("disposition_id")) or {}
    agency = _by_id(vocab["agencies"]).get(lead.get("agency_id")) or {}
    return {
        **lead,
        "display_name": core.display_name(lead),
        "stage_name": stage.get("name"), "stage_key": stage.get("key"),
        "stage_sort": stage.get("sort_order"), "stage_probability": stage.get("probability"),
        "source_name": source.get("name"), "source_key": source.get("key"),
        "interest_name": interest.get("name"), "interest_key": interest.get("key"),
        "disposition_name": disp.get("name"),
        "agency_name": agency.get("name"),
    }


def _log_activity(org_id: str, lead_id: str, kind: str, body: str = "", meta: dict = None,
                  caller=None, direction=None) -> None:
    """Append to the immutable timeline. Best-effort by design: the business action already
    succeeded, and losing the audit line is strictly better than rolling back a rep's work."""
    try:
        sb().table("crm_activity").insert({
            "org_id": org_id, "lead_id": lead_id, "kind": kind, "body": (body or "")[:4000],
            "meta": meta or {}, "direction": direction,
            "actor_employee_id": (caller or {}).get("employee_id"),
            "actor_app_user_id": (caller or {}).get("id"),
        }).execute()
    except Exception:
        pass


def _touch(org_id: str, lead_id: str, extra: dict = None) -> None:
    upd = {"last_activity_at": _iso(_now()), "updated_at": _iso(_now())}
    upd.update(extra or {})
    try:
        sb().table("crm_lead").update(upd).eq("org_id", org_id).eq("id", lead_id).execute()
    except Exception:
        pass


def _get_lead(org_id: str, lead_id: str) -> dict:
    rows = _fetch("crm_lead", org_id, limit=1, id=lead_id)
    if not rows:
        raise HTTPException(404, "Lead not found.")
    return rows[0]


def _rescore(org_id: str, lead: dict, vocab: dict) -> dict:
    rules = _fetch("crm_score_rule", org_id)
    enriched = _decorate(lead, vocab)
    enriched["has_email"] = bool(lead.get("email"))
    score = core.score_lead(enriched, rules)
    return {"score": score, "priority": core.priority_from_score(score)}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Leads
# ══════════════════════════════════════════════════════════════════════════════════════════════

@router.get("/leads")
def list_leads(org_id: str = ORG_ID, status: str = "", stage_id: str = "", source_id: str = "",
               agency_id: str = "", owner: str = "", store_code: str = "", market: str = "",
               q: str = "", mine: bool = False, overdue_only: bool = False,
               start: str = "", end: str = "", limit: int = 500,
               authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The lead list. Carries the standard filter set (period / store / market / rep) plus the CRM
    ones, and the SAME filters drive the exports — RULE FIVE."""
    _seed(org_id)
    caller = _caller(authorization, x_active_org)
    ks = _keyset(authorization, org_id)
    vocab = _vocab(org_id)
    try:
        query = sb().table("crm_lead").select("*").eq("org_id", org_id)
        if status:
            query = query.eq("status", status)
        if stage_id:
            query = query.eq("stage_id", stage_id)
        if source_id:
            query = query.eq("source_id", source_id)
        if agency_id:
            query = query.eq("agency_id", agency_id)
        if owner:
            query = query.eq("owner_employee_id", owner)
        if store_code:
            query = query.eq("store_code", store_code)
        if market:
            query = query.eq("market", market)
        if start:
            query = query.gte("created_at", start)
        if end:
            query = query.lte("created_at", f"{end}T23:59:59+00:00" if len(end) == 10 else end)
        rows = query.order("created_at", desc=True).limit(min(max(limit, 1), 2000)).execute().data or []
    except Exception:
        return {"rows": [], "total": 0, "note": "run migration 800 first (core.crm_lead)"}

    if mine and (caller or {}).get("employee_id"):
        rows = [r for r in rows if r.get("owner_employee_id") == caller["employee_id"]]
    # Span narrowing. A lead with NO store yet (a phone-in nobody has routed) stays visible to
    # scoped users — otherwise brand-new leads would be invisible to exactly the people meant to
    # grab them, which is worse than the small over-share of an unrouted record.
    rows = [r for r in rows if not r.get("store_code") or _in_keyset(ks, r.get("store_code"))]
    if q:
        needle = q.strip().lower()
        digits = core.normalize_phone(q)
        rows = [r for r in rows
                if needle in core.display_name(r).lower()
                or needle in str(r.get("email") or "").lower()
                or (digits and core.normalize_phone(r.get("phone")) == digits)
                or needle in str(r.get("lead_no") or "")]
    if overdue_only:
        now = _now()
        rows = [r for r in rows
                if (r.get("status") or "open") == "open"
                and core._dt(r.get("next_action_at")) is not None
                and core._dt(r.get("next_action_at")) < now]
    decorated = [_decorate(r, vocab) for r in rows]
    return {"rows": decorated, "total": len(decorated),
            "stages": sorted(vocab["stages"], key=lambda s: s.get("sort_order") or 0)}


@router.get("/leads/{lead_id}")
def get_lead(lead_id: str, org_id: str = ORG_ID, authorization: str = Header(default=""),
             x_active_org: str = Header(default="")):
    lead = _get_lead(org_id, lead_id)
    ks = _keyset(authorization, org_id)
    if lead.get("store_code") and not _in_keyset(ks, lead.get("store_code")):
        raise HTTPException(403, "This lead belongs to a store outside your access.")
    vocab = _vocab(org_id)
    try:
        activity = (sb().table("crm_activity").select("*").eq("org_id", org_id)
                    .eq("lead_id", lead_id).order("created_at", desc=True).limit(300)
                    .execute().data) or []
    except Exception:
        activity = []
    tasks = _fetch("crm_task", org_id, lead_id=lead_id)
    assignments = _fetch("crm_assignment", org_id, lead_id=lead_id)
    return {
        "lead": _decorate(lead, vocab),
        "activity": activity,
        "tasks": sorted(tasks, key=lambda t: str(t.get("due_at") or "")),
        "assignments": sorted(assignments, key=lambda a: str(a.get("created_at") or ""), reverse=True),
        "vocab": vocab,
    }


def _resolve_ref(rows, ref, org_id):
    """Accept either an id or a `key` for a reference field, so the intake API and the UI can both
    speak in keys ('walk_in') while storage stays id-based."""
    if not ref:
        return None
    for r in rows or []:
        if r.get("id") == ref or r.get("key") == ref:
            return r.get("id")
    return None


def _assignment_ctx(org_id: str) -> dict:
    queues = _fetch("crm_queue", org_id)
    members = _fetch("crm_queue_member", org_id)
    by_queue = {}
    for m in members:
        by_queue.setdefault(m.get("queue_id"), []).append(m)
    store_owner = {}
    try:
        rows = (get_supabase().schema("storeops").table("app_users")
                .select("employee_id,store_code,role").eq("org_id", org_id)
                .eq("is_active", True).limit(2000).execute().data) or []
        for r in rows:
            code = str(r.get("store_code") or "").strip().upper()
            if code and r.get("employee_id") and code not in store_owner:
                store_owner[code] = r["employee_id"]
    except Exception:
        pass
    return {"queues": _by_id(queues), "queue_members": by_queue, "store_owner": store_owner}


def _apply_cadences_on_create(org_id: str, lead: dict, cfg: dict, caller=None) -> int:
    """Materialize any `on_create` cadence step already due (offset 0 usually is). Later steps are
    picked up by the sweep. Returns how many tasks were booked."""
    cadences = [c for c in _fetch("crm_cadence", org_id)
                if c.get("is_active", True) and (c.get("trigger") or "") == "on_create"
                and (not c.get("pipeline_id") or c.get("pipeline_id") == lead.get("pipeline_id"))]
    if not cadences:
        return 0
    all_steps = _fetch("crm_cadence_step", org_id)
    booked = 0
    for cad in cadences:
        steps = [s for s in all_steps if s.get("cadence_id") == cad.get("id")]
        due = core.due_cadence_steps(lead, cad, steps, set(), cfg, _now())
        for d in due:
            booked += 1 if _book_task(org_id, lead, d, caller) else 0
    return booked


def _book_task(org_id: str, lead: dict, spec: dict, caller=None):
    """Insert one task. Idempotent against the cadence unique index — a duplicate is a no-op, which
    is what makes a re-run of the sweep harmless."""
    assign_to = (spec.get("assign_to") or "owner").lower()
    employee = lead.get("owner_employee_id")
    if assign_to == "agency" and lead.get("agency_id"):
        employee = None
    row = {
        "org_id": org_id, "lead_id": lead.get("id"),
        "title": spec.get("title") or "Follow up", "body": spec.get("body"),
        "type": spec.get("type") or "call",
        "due_at": spec.get("due_at"), "remind_at": spec.get("remind_at") or spec.get("due_at"),
        "assigned_employee_id": employee,
        "assigned_agency_id": lead.get("agency_id") if assign_to == "agency" else None,
        "queue_id": lead.get("queue_id") if assign_to == "queue" else None,
        "cadence_id": spec.get("cadence_id"), "cadence_step_no": spec.get("cadence_step_no"),
        "created_by": (caller or {}).get("employee_id"),
    }
    try:
        r = sb().table("crm_task").insert(row).execute()
        return (r.data or [None])[0]
    except Exception:
        return None       # unique-index collision = already booked; that is success, not an error


@router.post("/leads")
def create_lead(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                x_active_org: str = Header(default="")):
    """Log a lead. Name is optional; a phone number alone is a valid lead — the whole point is that
    capture must be faster than not capturing."""
    _seed(org_id)
    caller = _caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    vocab = _vocab(org_id)

    phone = str(body.get("phone") or "").strip()
    if not phone and not str(body.get("email") or "").strip():
        raise HTTPException(400, "A lead needs at least a phone number or an email address.")

    pipeline_id = body.get("pipeline_id") or cfg.get("default_pipeline_id")
    if not pipeline_id and vocab["pipelines"]:
        default = next((p for p in vocab["pipelines"] if p.get("is_default")), vocab["pipelines"][0])
        pipeline_id = default.get("id")
    stage_id = body.get("stage_id")
    if not stage_id:
        stages = [s for s in vocab["stages"] if s.get("pipeline_id") == pipeline_id
                  and not s.get("is_won") and not s.get("is_lost")]
        stages.sort(key=lambda s: s.get("sort_order") or 0)
        stage_id = stages[0].get("id") if stages else None

    row = {
        "org_id": org_id,
        "first_name": body.get("first_name"), "last_name": body.get("last_name"),
        "company_name": body.get("company_name"),
        "phone": phone or None, "email": (body.get("email") or "").strip() or None,
        "address_1": body.get("address_1"), "city": body.get("city"),
        "state": body.get("state"), "zip": body.get("zip"),
        "store_code": body.get("store_code") or (caller or {}).get("store_code"),
        "market": body.get("market") or (caller or {}).get("market"),
        "source_id": _resolve_ref(vocab["sources"], body.get("source_id") or body.get("source_key"), org_id),
        "interest_id": _resolve_ref(vocab["interests"], body.get("interest_id") or body.get("interest_key"), org_id),
        "campaign": body.get("campaign"),
        "notes": body.get("notes"),
        "pipeline_id": pipeline_id, "stage_id": stage_id,
        "stage_entered_at": _iso(_now()),
        "value_estimate": body.get("value_estimate") or 0,
        "lines_estimate": body.get("lines_estimate") or 0,
        "expected_close_date": body.get("expected_close_date"),
        "matched_customer_id": body.get("matched_customer_id"),
        "do_not_call": bool(body.get("do_not_call")),
        "sms_opt_in": bool(body.get("sms_opt_in")),
        "created_by": (caller or {}).get("employee_id"),
        "last_activity_at": _iso(_now()),
    }

    # Routing: an explicit owner wins; otherwise the assignment rules decide.
    owner = body.get("owner_employee_id")
    rule_id = None
    if owner:
        row["owner_employee_id"] = owner
    else:
        ctx = _assignment_ctx(org_id)
        enriched = _decorate({**row, "id": None}, vocab)
        pick = core.pick_assignee(enriched, _fetch("crm_assignment_rule", org_id), ctx)
        row["owner_employee_id"] = pick.get("employee_id")
        row["queue_id"] = pick.get("queue_id")
        row["agency_id"] = pick.get("agency_id")
        rule_id = pick.get("rule_id")
        if pick.get("agency_id"):
            row["agency_assigned_at"] = _iso(_now())
        if pick.get("rr_cursor_update"):
            qid, cursor = pick["rr_cursor_update"]
            try:
                sb().table("crm_queue").update({"rr_cursor": cursor}) \
                    .eq("org_id", org_id).eq("id", qid).execute()
            except Exception:
                pass

    scored = _rescore(org_id, row, vocab)
    row.update(scored)

    try:
        created = (sb().table("crm_lead").insert(row).execute().data or [{}])[0]
    except Exception as e:
        raise HTTPException(400, f"Could not save the lead: {e}")

    lead_id = created.get("id")
    _log_activity(org_id, lead_id, "system", "Lead created", {"source": "ui"}, caller)
    if row.get("owner_employee_id") or row.get("agency_id") or row.get("queue_id"):
        try:
            sb().table("crm_assignment").insert({
                "org_id": org_id, "lead_id": lead_id,
                "to_employee_id": row.get("owner_employee_id"),
                "to_queue_id": row.get("queue_id"), "to_agency_id": row.get("agency_id"),
                "by_employee_id": (caller or {}).get("employee_id"),
                "by_app_user_id": (caller or {}).get("id"),
                "rule_id": rule_id, "reason": "auto-routed on create" if rule_id else "assigned on create",
            }).execute()
        except Exception:
            pass
    booked = _apply_cadences_on_create(org_id, created, cfg, caller)
    return {"lead": _decorate(created, vocab), "tasks_booked": booked}


@router.post("/leads/dedupe-check")
def dedupe_check(body: dict, org_id: str = ORG_ID):
    """Live duplicate warning for the capture form. Advisory, not a block: a real second lead for the
    same number happens (a customer coming back), and refusing it just teaches reps to fake a digit."""
    cfg = _cfg(org_id)
    phone = core.normalize_phone(body.get("phone"))
    email = core.normalize_email(body.get("email"))
    if not phone and not email:
        return {"duplicates": []}
    rows = []
    try:
        if phone:
            rows += (sb().table("crm_lead").select("*").eq("org_id", org_id)
                     .eq("phone_norm", phone).limit(20).execute().data) or []
        if email and (cfg.get("duplicate_match") in ("email", "both")):
            rows += (sb().table("crm_lead").select("*").eq("org_id", org_id)
                     .eq("email_norm", email).limit(20).execute().data) or []
    except Exception:
        return {"duplicates": []}
    seen, dupes = set(), []
    for r in rows:
        if r.get("id") in seen:
            continue
        seen.add(r.get("id"))
        if core.is_duplicate(body, r, cfg.get("duplicate_match")):
            dupes.append({"id": r.get("id"), "lead_no": r.get("lead_no"),
                          "name": core.display_name(r), "status": r.get("status"),
                          "owner_employee_id": r.get("owner_employee_id"),
                          "created_at": r.get("created_at")})
    return {"duplicates": dupes, "mode": cfg.get("duplicate_match")}


@router.patch("/leads/{lead_id}")
def update_lead(lead_id: str, body: dict, org_id: str = ORG_ID,
                authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    caller = _caller(authorization, x_active_org)
    lead = _get_lead(org_id, lead_id)
    vocab = _vocab(org_id)
    allowed = {"first_name", "last_name", "company_name", "phone", "email", "address_1", "city",
               "state", "zip", "store_code", "market", "source_id", "interest_id", "campaign",
               "notes", "value_estimate", "lines_estimate", "expected_close_date", "priority",
               "do_not_call", "sms_opt_in", "next_action_at", "matched_customer_id"}
    row = {k: v for k, v in body.items() if k in allowed}
    if not row:
        raise HTTPException(400, "Nothing to update.")
    row["updated_at"] = _iso(_now())
    merged = {**lead, **row}
    row.update(_rescore(org_id, merged, vocab))
    try:
        r = sb().table("crm_lead").update(row).eq("org_id", org_id).eq("id", lead_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save: {e}")
    _log_activity(org_id, lead_id, "system", "Lead details updated",
                  {"fields": sorted(row.keys())}, caller)
    return _decorate((r.data or [merged])[0], vocab)


@router.post("/leads/{lead_id}/stage")
def move_stage(lead_id: str, body: dict, org_id: str = ORG_ID,
               authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Move a lead to another stage. A closing stage the tenant marked `requires_disposition` is
    REFUSED without an outcome — that refusal is the whole reason the pipeline knows why deals die."""
    caller = _caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    lead = _get_lead(org_id, lead_id)
    vocab = _vocab(org_id)
    stage = _by_id(vocab["stages"]).get(body.get("stage_id"))
    if not stage:
        raise HTTPException(400, "Unknown stage.")
    if core.stage_close_requires_disposition(stage, cfg) and not body.get("disposition_id"):
        raise HTTPException(400, f"'{stage.get('name')}' needs an outcome — pick a disposition.")

    upd = {"stage_id": stage.get("id"), "stage_entered_at": _iso(_now()),
           "last_activity_at": _iso(_now()), "updated_at": _iso(_now())}
    if stage.get("is_won"):
        upd["status"], upd["closed_at"] = "won", _iso(_now())
    elif stage.get("is_lost"):
        upd["status"], upd["closed_at"] = "lost", _iso(_now())
    else:
        upd["status"], upd["closed_at"] = "open", None
    try:
        sb().table("crm_lead").update(upd).eq("org_id", org_id).eq("id", lead_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not move the lead: {e}")
    old = _by_id(vocab["stages"]).get(lead.get("stage_id")) or {}
    _log_activity(org_id, lead_id, "stage_change",
                  f"{old.get('name') or '—'} → {stage.get('name')}",
                  {"from": lead.get("stage_id"), "to": stage.get("id")}, caller)
    if body.get("disposition_id"):
        return dispose_lead(lead_id, {"disposition_id": body["disposition_id"],
                                      "reason_code_id": body.get("reason_code_id"),
                                      "note": body.get("note") or ""},
                            org_id, authorization, x_active_org)
    return {"ok": True, "stage": stage.get("name"), "status": upd["status"]}


@router.post("/leads/{lead_id}/dispose")
def dispose_lead(lead_id: str, body: dict, org_id: str = ORG_ID,
                 authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """"Dispose" a lead — record what happened on this touch. This is the single most important write
    in the module: it is what turns activity into a pipeline, and it is what books the next step."""
    caller = _caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    lead = _get_lead(org_id, lead_id)
    vocab = _vocab(org_id)
    disp = _by_id(vocab["dispositions"]).get(body.get("disposition_id"))
    if not disp:
        disp = next((d for d in vocab["dispositions"] if d.get("key") == body.get("disposition_id")), None)
    result = core.apply_disposition(lead, disp, cfg, _now(),
                                    reason_code_id=body.get("reason_code_id"),
                                    note=body.get("note") or "",
                                    followup_at=core._dt(body.get("followup_at")))
    if result["errors"]:
        raise HTTPException(400, " ".join(result["errors"]))
    try:
        sb().table("crm_lead").update(result["lead_updates"]) \
            .eq("org_id", org_id).eq("id", lead_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not record the outcome: {e}")
    act = result["activity"]
    _log_activity(org_id, lead_id, act["kind"], act["body"], act["meta"], caller)

    task = None
    if result["followup"]:
        merged = {**lead, **result["lead_updates"]}
        task = _book_task(org_id, merged, result["followup"], caller)
    # Close out whatever open task prompted this touch, so the rep's inbox actually empties.
    if body.get("task_id"):
        _complete_task_row(org_id, body["task_id"], caller, disp.get("id") if disp else None)
    return {"ok": True, "closed": bool(disp.get("closes_lead")), "followup": task}


@router.post("/leads/{lead_id}/assign")
def assign_lead(lead_id: str, body: dict, org_id: str = ORG_ID,
                authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Hand a lead to a teammate, a queue, or an outside agency. Exactly one target."""
    caller = _caller(authorization, x_active_org)
    lead = _get_lead(org_id, lead_id)
    vocab = _vocab(org_id)
    emp, queue, agency = (body.get("employee_id") or None, body.get("queue_id") or None,
                          body.get("agency_id") or None)
    targets = [t for t in (emp, queue, agency) if t]
    if len(targets) != 1:
        raise HTTPException(400, "Pick exactly one: a teammate, a queue, or an agency.")
    if agency:
        ag = _by_id(vocab["agencies"]).get(agency)
        if not ag or not ag.get("is_active", True):
            raise HTTPException(400, "Unknown or inactive agency.")

    upd = {"owner_employee_id": emp, "queue_id": queue, "agency_id": agency,
           "agency_assigned_at": _iso(_now()) if agency else None,
           "agency_accepted_at": None,
           "last_activity_at": _iso(_now()), "updated_at": _iso(_now())}
    try:
        sb().table("crm_lead").update(upd).eq("org_id", org_id).eq("id", lead_id).execute()
        sb().table("crm_assignment").insert({
            "org_id": org_id, "lead_id": lead_id,
            "from_employee_id": lead.get("owner_employee_id"),
            "from_queue_id": lead.get("queue_id"), "from_agency_id": lead.get("agency_id"),
            "to_employee_id": emp, "to_queue_id": queue, "to_agency_id": agency,
            "by_employee_id": (caller or {}).get("employee_id"),
            "by_app_user_id": (caller or {}).get("id"),
            "reason": body.get("reason") or "",
        }).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not reassign: {e}")

    label = emp or (f"agency {(_by_id(vocab['agencies']).get(agency) or {}).get('name')}" if agency
                    else "a queue")
    _log_activity(org_id, lead_id, "assignment", f"Assigned to {label}",
                  {"employee_id": emp, "queue_id": queue, "agency_id": agency,
                   "reason": body.get("reason") or ""}, caller)
    # Open tasks follow the lead — a follow-up left pointing at the previous owner is a follow-up
    # nobody does.
    try:
        sb().table("crm_task").update({"assigned_employee_id": emp,
                                       "assigned_agency_id": agency, "queue_id": queue}) \
            .eq("org_id", org_id).eq("lead_id", lead_id).eq("status", "open").execute()
    except Exception:
        pass
    if agency:
        _notify_agency(org_id, lead, _by_id(vocab["agencies"]).get(agency))
    return {"ok": True}


@router.post("/leads/bulk-assign")
def bulk_assign(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                x_active_org: str = Header(default="")):
    ids = [i for i in (body.get("lead_ids") or []) if i][:500]
    if not ids:
        raise HTTPException(400, "No leads selected.")
    done, failed = 0, []
    for lead_id in ids:
        try:
            assign_lead(lead_id, body, org_id, authorization, x_active_org)
            done += 1
        except HTTPException as e:
            failed.append({"lead_id": lead_id, "error": e.detail})
    return {"assigned": done, "failed": failed}


@router.post("/leads/bulk-dispose")
def bulk_dispose(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                 x_active_org: str = Header(default="")):
    ids = [i for i in (body.get("lead_ids") or []) if i][:500]
    if not ids:
        raise HTTPException(400, "No leads selected.")
    done, failed = 0, []
    for lead_id in ids:
        try:
            dispose_lead(lead_id, body, org_id, authorization, x_active_org)
            done += 1
        except HTTPException as e:
            failed.append({"lead_id": lead_id, "error": e.detail})
    return {"disposed": done, "failed": failed}


@router.post("/leads/{lead_id}/agency-response")
def agency_response(lead_id: str, body: dict, org_id: str = ORG_ID,
                    authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """An agency accepts or declines the lead. A decline returns it to the previous owner with the
    reason attached — an un-answered agency assignment ages into the escalation path like anything
    else, which is the point: work handed outside is still work you are tracking."""
    caller = _caller(authorization, x_active_org)
    lead = _get_lead(org_id, lead_id)
    if not lead.get("agency_id"):
        raise HTTPException(400, "This lead is not assigned to an agency.")
    accepted = bool(body.get("accepted"))
    now = _iso(_now())
    if accepted:
        upd = {"agency_accepted_at": now, "last_activity_at": now, "updated_at": now}
    else:
        prev = None
        for a in sorted(_fetch("crm_assignment", org_id, lead_id=lead_id),
                        key=lambda r: str(r.get("created_at") or ""), reverse=True):
            if a.get("from_employee_id"):
                prev = a["from_employee_id"]
                break
        upd = {"agency_id": None, "agency_assigned_at": None, "agency_accepted_at": None,
               "owner_employee_id": prev, "last_activity_at": now, "updated_at": now}
    try:
        sb().table("crm_lead").update(upd).eq("org_id", org_id).eq("id", lead_id).execute()
        latest = sorted(_fetch("crm_assignment", org_id, lead_id=lead_id),
                        key=lambda r: str(r.get("created_at") or ""), reverse=True)
        if latest:
            sb().table("crm_assignment").update(
                {"accepted_at": now} if accepted
                else {"declined_at": now, "declined_reason": body.get("reason") or ""}
            ).eq("org_id", org_id).eq("id", latest[0]["id"]).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not record the response: {e}")
    _log_activity(org_id, lead_id, "assignment",
                  "Agency accepted the lead" if accepted
                  else f"Agency declined: {body.get('reason') or 'no reason given'}",
                  {"accepted": accepted}, caller)
    return {"ok": True, "accepted": accepted}


@router.post("/leads/{lead_id}/convert")
def convert_lead(lead_id: str, body: dict, org_id: str = ORG_ID,
                 authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Turn a won lead into a POS customer. If a customer with this phone already exists we LINK to
    it rather than creating a second one — a duplicate customer master is how a CRM stops being
    trustworthy."""
    caller = _caller(authorization, x_active_org)
    lead = _get_lead(org_id, lead_id)
    phone = core.normalize_phone(lead.get("phone"))
    customer_id = body.get("customer_id") or lead.get("matched_customer_id")
    if not customer_id and phone:
        try:
            rows = (get_supabase().schema("pos").table("customers")
                    .select("id,phone_primary,phone_secondary").eq("org_id", org_id)
                    .ilike("phone_primary", f"%{phone[-7:]}").limit(10).execute().data) or []
            for r in rows:
                if core.normalize_phone(r.get("phone_primary")) == phone:
                    customer_id = r["id"]
                    break
        except Exception:
            pass
    if not customer_id:
        try:
            created = (get_supabase().schema("pos").table("customers").insert({
                "org_id": org_id,
                "account_type": "Business" if lead.get("company_name") else "Personal",
                "company_name": lead.get("company_name"),
                "first_name": lead.get("first_name"), "last_name": lead.get("last_name"),
                "email": lead.get("email"), "phone_primary": lead.get("phone"),
                "address_1": lead.get("address_1"), "city": lead.get("city"),
                "state": lead.get("state"), "zip": lead.get("zip"),
                "referral_source": "CRM lead",
            }).execute().data or [{}])[0]
            customer_id = created.get("id")
        except Exception as e:
            raise HTTPException(400, f"Could not create the customer record: {e}")
    upd = {"converted_customer_id": customer_id, "matched_customer_id": customer_id,
           "converted_at": _iso(_now()), "updated_at": _iso(_now()),
           "last_activity_at": _iso(_now())}
    try:
        sb().table("crm_lead").update(upd).eq("org_id", org_id).eq("id", lead_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not link the customer: {e}")
    _log_activity(org_id, lead_id, "conversion", "Converted to a customer",
                  {"customer_id": customer_id}, caller)
    return {"ok": True, "customer_id": customer_id}


@router.post("/leads/intake")
def intake_lead(body: dict, org_id: str = ORG_ID):
    """Web-to-Lead. Authenticated by the tenant's `crm_config.intake_key` instead of a JWT, so a
    website form or a partner can post here. Disabled (401) until a tenant sets a key — an open
    intake endpoint is a spam funnel."""
    cfg = _cfg(org_id)
    key = (cfg.get("intake_key") or "").strip()
    if not key or str(body.get("intake_key") or "").strip() != key:
        raise HTTPException(401, "Lead intake is not enabled for this account.")
    payload = {k: v for k, v in body.items() if k != "intake_key"}
    payload.setdefault("source_key", "website")
    return create_lead(payload, org_id, "", "")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Activity + tasks
# ══════════════════════════════════════════════════════════════════════════════════════════════

@router.post("/leads/{lead_id}/activity")
def add_activity(lead_id: str, body: dict, org_id: str = ORG_ID,
                 authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    caller = _caller(authorization, x_active_org)
    _get_lead(org_id, lead_id)
    kind = (body.get("kind") or "note").lower()
    if kind not in ("note", "call", "sms", "email", "whatsapp", "visit"):
        raise HTTPException(400, f"'{kind}' is not something you can log by hand.")
    _log_activity(org_id, lead_id, kind, body.get("body") or "", body.get("meta") or {},
                  caller, body.get("direction"))
    _touch(org_id, lead_id, {"first_contacted_at": _iso(_now())} if kind != "note" else None)
    return {"ok": True}


@router.get("/tasks")
def list_tasks(org_id: str = ORG_ID, scope: str = "mine", status: str = "open",
               employee_id: str = "", days: int = 14, limit: int = 500,
               authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The follow-up inbox. `scope`: mine | team | all. `team` is the manager view and is refused to
    a caller who is not one, rather than silently narrowed to `mine` — a manager who has lost a
    permission should see a clear refusal, not a quietly wrong list."""
    _seed(org_id)
    caller = _caller(authorization, x_active_org)
    ks = _keyset(authorization, org_id)
    if scope in ("team", "all") and not _is_manager(caller):
        raise HTTPException(403, "Seeing the team's follow-ups needs a manager role.")
    try:
        q = sb().table("crm_task").select("*").eq("org_id", org_id)
        if status:
            q = q.eq("status", status)
        if scope == "mine":
            me = employee_id or (caller or {}).get("employee_id")
            if not me:
                return {"rows": [], "note": "Your login is not linked to an employee record yet."}
            q = q.eq("assigned_employee_id", me)
        elif employee_id:
            q = q.eq("assigned_employee_id", employee_id)
        if days:
            q = q.lte("due_at", _iso(_now() + timedelta(days=max(1, days))))
        tasks = q.order("due_at").limit(min(max(limit, 1), 2000)).execute().data or []
    except Exception:
        return {"rows": [], "note": "run migration 800 first (core.crm_task)"}

    lead_ids = [t.get("lead_id") for t in tasks if t.get("lead_id")]
    leads = {}
    if lead_ids:
        try:
            for chunk in [lead_ids[i:i + 100] for i in range(0, len(lead_ids), 100)]:
                for l in (sb().table("crm_lead").select(
                        "id,lead_no,first_name,last_name,company_name,phone,store_code,stage_id,"
                        "status,owner_employee_id").eq("org_id", org_id)
                        .in_("id", chunk).execute().data) or []:
                    leads[l["id"]] = l
        except Exception:
            pass
    now = _now()
    out = []
    for t in tasks:
        lead = leads.get(t.get("lead_id")) or {}
        if lead.get("store_code") and not _in_keyset(ks, lead.get("store_code")):
            continue
        due = core._dt(t.get("due_at"))
        out.append({**t,
                    "lead_name": core.display_name(lead) if lead else "—",
                    "lead_no": lead.get("lead_no"), "lead_phone": lead.get("phone"),
                    "lead_store": lead.get("store_code"),
                    "is_overdue": bool(due and due < now and (t.get("status") or "open") == "open"),
                    "is_today": bool(due and due.date() == now.date())})
    return {"rows": out, "total": len(out)}


@router.post("/tasks")
def create_task(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                x_active_org: str = Header(default="")):
    caller = _caller(authorization, x_active_org)
    if not body.get("lead_id"):
        raise HTTPException(400, "A follow-up has to belong to a lead.")
    lead = _get_lead(org_id, body["lead_id"])
    due = core._dt(body.get("due_at"))
    if due is None:
        raise HTTPException(400, "Pick when this follow-up is due.")
    task = _book_task(org_id, lead, {
        "title": body.get("title") or "Follow up", "body": body.get("body"),
        "type": body.get("type") or "call",
        "due_at": _iso(due), "remind_at": _iso(core._dt(body.get("remind_at")) or due),
        "assign_to": "owner",
    }, caller)
    if body.get("assigned_employee_id") and task:
        try:
            sb().table("crm_task").update({"assigned_employee_id": body["assigned_employee_id"]}) \
                .eq("org_id", org_id).eq("id", task["id"]).execute()
        except Exception:
            pass
    _touch(org_id, lead["id"], {"next_action_at": _iso(due)})
    return task or {}


def _complete_task_row(org_id: str, task_id: str, caller, disposition_id=None):
    try:
        sb().table("crm_task").update({
            "status": "done", "completed_at": _iso(_now()),
            "completed_by": (caller or {}).get("employee_id"),
            "outcome_disposition_id": disposition_id,
        }).eq("org_id", org_id).eq("id", task_id).execute()
    except Exception:
        pass


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: str, body: dict = None, org_id: str = ORG_ID,
                  authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    body = body or {}
    caller = _caller(authorization, x_active_org)
    rows = _fetch("crm_task", org_id, limit=1, id=task_id)
    if not rows:
        raise HTTPException(404, "Follow-up not found.")
    task = rows[0]
    if body.get("disposition_id"):
        # Completing WITH an outcome is the good path: it books the next step automatically.
        return dispose_lead(task["lead_id"], {**body, "task_id": task_id},
                            org_id, authorization, x_active_org)
    _complete_task_row(org_id, task_id, caller)
    _log_activity(org_id, task["lead_id"], "task", f"Completed: {task.get('title')}",
                  {"task_id": task_id}, caller)
    _touch(org_id, task["lead_id"])
    return {"ok": True}


@router.post("/tasks/{task_id}/snooze")
def snooze_task(task_id: str, body: dict, org_id: str = ORG_ID,
                authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    caller = _caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    until = core._dt(body.get("until"))
    if until is None:
        try:
            hours = int(body.get("hours") or 24)
        except (TypeError, ValueError):
            hours = 24
        until = core.shift_to_business_hours(_now() + timedelta(hours=hours), cfg)
    rows = _fetch("crm_task", org_id, limit=1, id=task_id)
    if not rows:
        raise HTTPException(404, "Follow-up not found.")
    try:
        sb().table("crm_task").update({"snooze_until": _iso(until), "due_at": _iso(until),
                                       "remind_at": _iso(until), "status": "open"}) \
            .eq("org_id", org_id).eq("id", task_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not snooze: {e}")
    _log_activity(org_id, rows[0]["lead_id"], "task",
                  f"Snoozed '{rows[0].get('title')}' to {_iso(until)[:16]}", {"task_id": task_id}, caller)
    _touch(org_id, rows[0]["lead_id"], {"next_action_at": _iso(until)})
    return {"ok": True, "until": _iso(until)}


@router.delete("/tasks/{task_id}")
def cancel_task(task_id: str, org_id: str = ORG_ID, authorization: str = Header(default=""),
                x_active_org: str = Header(default="")):
    caller = _caller(authorization, x_active_org)
    try:
        sb().table("crm_task").update({"status": "cancelled"}) \
            .eq("org_id", org_id).eq("id", task_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not cancel: {e}")
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Dashboard + reports
# ══════════════════════════════════════════════════════════════════════════════════════════════

def _visible_leads(org_id: str, ks, *, start="", end="", store_code="", market="", owner=""):
    try:
        q = sb().table("crm_lead").select("*").eq("org_id", org_id)
        if start:
            q = q.gte("created_at", start)
        if end:
            q = q.lte("created_at", f"{end}T23:59:59+00:00" if len(end) == 10 else end)
        if store_code:
            q = q.eq("store_code", store_code)
        if market:
            q = q.eq("market", market)
        if owner:
            q = q.eq("owner_employee_id", owner)
        rows = q.order("created_at", desc=True).limit(5000).execute().data or []
    except Exception:
        return []
    return [r for r in rows if not r.get("store_code") or _in_keyset(ks, r.get("store_code"))]


@router.get("/summary")
def summary(org_id: str = ORG_ID, start: str = "", end: str = "", store_code: str = "",
            market: str = "", owner: str = "", authorization: str = Header(default=""),
            x_active_org: str = Header(default="")):
    """Everything the dashboard needs in one call — the same filters the list and the exports use."""
    _seed(org_id)
    ks = _keyset(authorization, org_id)
    cfg = _cfg(org_id)
    vocab = _vocab(org_id)
    leads = _visible_leads(org_id, ks, start=start, end=end, store_code=store_code,
                           market=market, owner=owner)
    now = _now()
    open_leads = [l for l in leads if (l.get("status") or "open") == "open"]
    stale = core.stale_leads(open_leads, cfg, now)
    try:
        tasks = (sb().table("crm_task").select("*").eq("org_id", org_id)
                 .eq("status", "open").limit(3000).execute().data) or []
    except Exception:
        tasks = []
    lead_ids = {l.get("id") for l in leads}
    tasks = [t for t in tasks if t.get("lead_id") in lead_ids]
    overdue = [t for t in tasks if (core._dt(t.get("due_at")) or now) < now]
    try:
        missed = (sb().table("crm_task").select("id,lead_id,title,due_at,assigned_employee_id")
                  .eq("org_id", org_id).eq("status", "missed").limit(500).execute().data) or []
    except Exception:
        missed = []
    missed = [t for t in missed if t.get("lead_id") in lead_ids]

    by_owner = {}
    for l in leads:
        key = l.get("owner_employee_id") or "—"
        b = by_owner.setdefault(key, {"employee_id": key, "leads": 0, "won": 0, "lost": 0,
                                      "open": 0, "value": 0.0})
        b["leads"] += 1
        b[l.get("status") if l.get("status") in ("won", "lost", "open") else "open"] += 1
        b["value"] += float(l.get("value_estimate") or 0)
    for b in by_owner.values():
        closed = b["won"] + b["lost"]
        b["win_rate"] = round(100.0 * b["won"] / closed, 1) if closed else 0.0

    by_source = {}
    src = _by_id(vocab["sources"])
    for l in leads:
        name = (src.get(l.get("source_id")) or {}).get("name") or "Unknown"
        b = by_source.setdefault(name, {"source": name, "leads": 0, "won": 0, "value": 0.0})
        b["leads"] += 1
        b["won"] += 1 if l.get("status") == "won" else 0
        b["value"] += float(l.get("value_estimate") or 0) if l.get("status") == "won" else 0.0
    for b in by_source.values():
        b["conversion"] = round(100.0 * b["won"] / b["leads"], 1) if b["leads"] else 0.0

    return {
        "totals": core.conversion_rates(leads),
        "funnel": core.funnel(open_leads, vocab["stages"]),
        "forecast": core.weighted_forecast(open_leads, vocab["stages"]),
        "pipeline_value": round(sum(float(l.get("value_estimate") or 0) for l in open_leads), 2),
        "attention": {
            "stale_leads": len(stale),
            "overdue_tasks": len(overdue),
            "missed_tasks": len(missed),
            "unassigned": sum(1 for l in open_leads
                              if not l.get("owner_employee_id") and not l.get("agency_id")),
            "agency_unanswered": sum(1 for l in open_leads
                                     if l.get("agency_id") and not l.get("agency_accepted_at")),
        },
        "leaderboard": sorted(by_owner.values(), key=lambda b: (-b["won"], -b["leads"])),
        "by_source": sorted(by_source.values(), key=lambda b: -b["leads"]),
        "stale_sample": [_decorate(l, vocab) for l in stale[:25]],
        "config": {"stale_lead_hours": cfg.get("stale_lead_hours"),
                   "escalate_after_hours": cfg.get("escalate_after_hours")},
    }


@router.get("/reports/activity")
def report_activity(org_id: str = ORG_ID, start: str = "", end: str = "", limit: int = 5000,
                    authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Who is actually working the pipeline — touches per rep per kind. This is the report that
    answers "is the team logging their leads?", which is the reason the reminders exist."""
    ks = _keyset(authorization, org_id)
    try:
        q = sb().table("crm_activity").select("kind,actor_employee_id,created_at,lead_id") \
            .eq("org_id", org_id)
        if start:
            q = q.gte("created_at", start)
        if end:
            q = q.lte("created_at", f"{end}T23:59:59+00:00" if len(end) == 10 else end)
        rows = q.order("created_at", desc=True).limit(min(max(limit, 1), 20000)).execute().data or []
    except Exception:
        return {"rows": []}
    leads = {l["id"]: l for l in _visible_leads(org_id, ks, start="", end="")}
    rows = [r for r in rows if r.get("lead_id") in leads]
    by_rep = {}
    for r in rows:
        key = r.get("actor_employee_id") or "—"
        b = by_rep.setdefault(key, {"employee_id": key, "total": 0})
        b["total"] += 1
        b[r.get("kind") or "note"] = b.get(r.get("kind") or "note", 0) + 1
    return {"rows": sorted(by_rep.values(), key=lambda b: -b["total"])}


@router.get("/reports/conversion")
def report_conversion(org_id: str = ORG_ID, start: str = "", end: str = "",
                      authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    ks = _keyset(authorization, org_id)
    vocab = _vocab(org_id)
    leads = _visible_leads(org_id, ks, start=start, end=end)
    by_stage = core.funnel(leads, vocab["stages"])
    disp = _by_id(vocab["dispositions"])
    lost_reasons = {}
    for l in leads:
        if l.get("status") != "lost":
            continue
        name = (disp.get(l.get("disposition_id")) or {}).get("name") or "Not recorded"
        lost_reasons[name] = lost_reasons.get(name, 0) + 1
    return {"totals": core.conversion_rates(leads), "by_stage": by_stage,
            "lost_reasons": sorted(({"reason": k, "count": v} for k, v in lost_reasons.items()),
                                   key=lambda r: -r["count"])}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Customer 360
# ══════════════════════════════════════════════════════════════════════════════════════════════

@router.get("/customer-360")
def customer_360(phone: str = "", org_id: str = ORG_ID, authorization: str = Header(default=""),
                 x_active_org: str = Header(default="")):
    """Enter a phone number → everything this company knows about that customer.

    Gated three ways (module, `customer_360` grant, `customer_360_financial` for the $) and audited
    on BOTH outcomes. See crm/customer360.py for the section-by-section sourcing."""
    caller = _caller(authorization, x_active_org)
    cfg = _cfg(org_id)
    client = get_supabase()
    if not customer360.customer_360_allowed(caller, cfg):
        customer360.write_audit(client, org_id, phone=phone, caller=caller, allowed=False,
                                sections={"denied": {"available": False, "count": 0}})
        raise HTTPException(403, "Customer lookup is restricted — you need the 'customer_360' "
                                 "permission. Ask an administrator to grant it on your role.")
    money_ok = customer360.customer_360_financial_allowed(caller)
    ks = _keyset(authorization, org_id)
    return customer360.build_360(client, org_id, phone, caller=caller, money_ok=money_ok, keyset=ks)


@router.get("/customer-360/audit")
def customer_360_audit(org_id: str = ORG_ID, limit: int = 200,
                       authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Who looked up whom. Admin-only — an access log readable by the people it logs is not a log."""
    caller = _caller(authorization, x_active_org)
    if not _can_edit_settings(caller):
        raise HTTPException(403, "The lookup audit trail is administrator-only.")
    try:
        return (sb().table("crm_lookup_audit").select("*").eq("org_id", org_id)
                .order("created_at", desc=True).limit(min(max(limit, 1), 1000)).execute().data) or []
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════════════════════
# The reminder sweep
# ══════════════════════════════════════════════════════════════════════════════════════════════

def _notify(org_id: str, *, emails, subject: str, body: str) -> bool:
    """Send through the notify module's Resend channel, so CRM adds no second deliverability path.

    `email_resend.send_email` is async and every caller of this helper is a SYNC endpoint (FastAPI
    runs a `def` handler in its threadpool, so there is no running event loop in this thread and
    `asyncio.run` is safe). Doing it the other way round — a sync send inside an `async def` — is the
    mistake that froze the whole backend once already ([[sync-call-in-async-endpoint-freeze]]).
    Returns True only if at least one address was accepted; a mail failure never aborts the sweep."""
    addrs = [a for a in (emails or []) if a]
    if not addrs:
        return False
    try:
        import asyncio
        import html as _h
        from app.modules.notify.channels import email_resend
        if not email_resend.is_configured():
            return False
        html = ("<pre style='font-family:system-ui,-apple-system,sans-serif;white-space:pre-wrap'>"
                + _h.escape(body or "") + "</pre>")

        async def _send_all():
            ok = False
            for addr in addrs:
                try:
                    await email_resend.send_email(addr, subject, html, [])
                    ok = True
                except Exception:
                    pass
            return ok
        return bool(asyncio.run(_send_all()))
    except Exception:
        return False


def _notify_agency(org_id: str, lead: dict, agency: dict) -> None:
    if not agency or not agency.get("email"):
        return
    _notify(org_id, emails=[agency["email"]],
            subject=f"New lead assigned to {agency.get('name')}",
            body=(f"A lead has been assigned to you.\n\n"
                  f"Name: {core.display_name(lead)}\n"
                  f"Phone: {lead.get('phone') or '—'}\n"
                  f"Store: {lead.get('store_code') or '—'}\n\n"
                  f"Please accept or decline it in the portal."))


def _employee_emails(org_id: str, employee_ids) -> dict:
    ids = [e for e in set(employee_ids or []) if e]
    if not ids:
        return {}
    out = {}
    try:
        rows = (get_supabase().schema("storeops").table("app_users")
                .select("employee_id,email,full_name").eq("org_id", org_id)
                .in_("employee_id", ids[:500]).limit(500).execute().data) or []
        for r in rows:
            if r.get("employee_id") and r.get("email"):
                out[r["employee_id"]] = r["email"]
    except Exception:
        pass
    return out


def _sweep_org(org_id: str) -> dict:
    """One tenant's pass: materialize → remind → miss → escalate. Pure decisions come from
    pipeline_core; this only does the I/O around them."""
    cfg = _cfg(org_id)
    now = _now()
    stats = {"org_id": org_id, "booked": 0, "reminded": 0, "missed": 0, "escalated": 0}

    leads = _fetch("crm_lead", org_id, limit=5000, status="open")
    if not leads:
        return stats

    # 1. materialize cadence steps
    cadences = [c for c in _fetch("crm_cadence", org_id) if c.get("is_active", True)]
    steps = _fetch("crm_cadence_step", org_id)
    existing = {}
    for t in _fetch("crm_task", org_id, limit=5000):
        if t.get("cadence_id"):
            existing.setdefault(t.get("lead_id"), set()).add((t["cadence_id"], t.get("cadence_step_no")))
    for lead in leads:
        for cad in cadences:
            if cad.get("pipeline_id") and cad["pipeline_id"] != lead.get("pipeline_id"):
                continue
            cad_steps = [s for s in steps if s.get("cadence_id") == cad.get("id")]
            due = core.due_cadence_steps(lead, cad, cad_steps,
                                         existing.get(lead.get("id"), set()), cfg, now)
            for d in due:
                if _book_task(org_id, lead, d):
                    stats["booked"] += 1

    # 2. remind
    open_tasks = [t for t in _fetch("crm_task", org_id, limit=5000) if t.get("status") == "open"]
    try:
        sent = {r.get("window_key") for r in
                (sb().table("crm_reminder_log").select("window_key").eq("org_id", org_id)
                 .eq("kind", "task_due").limit(20000).execute().data) or []}
    except Exception:
        sent = set()
    to_remind = core.tasks_to_remind(open_tasks, sent, now)
    lead_by_id = {l.get("id"): l for l in leads}
    emails = _employee_emails(org_id, [t.get("assigned_employee_id") for t in to_remind])
    for t in to_remind:
        lead = lead_by_id.get(t.get("lead_id")) or {}
        addr = emails.get(t.get("assigned_employee_id"))
        ok = _notify(org_id, emails=[addr] if addr else [],
                     subject=f"Follow-up due: {core.display_name(lead)}",
                     body=(f"{t.get('title')}\n\n"
                           f"Lead: {core.display_name(lead)} ({lead.get('phone') or 'no phone'})\n"
                           f"Due: {str(t.get('due_at'))[:16]}\n\n"
                           f"{t.get('body') or ''}"))
        try:
            sb().table("crm_reminder_log").insert({
                "org_id": org_id, "task_id": t.get("id"), "lead_id": t.get("lead_id"),
                "kind": "task_due", "channel": "email" if addr else "in_app",
                "target": addr, "window_key": t.get("window_key"),
                "status": "sent" if (ok or not addr) else "failed",
            }).execute()
            sb().table("crm_task").update({
                "reminder_sent_at": _iso(now),
                "reminder_count": int(t.get("reminder_count") or 0) + 1,
            }).eq("org_id", org_id).eq("id", t.get("id")).execute()
            stats["reminded"] += 1
        except Exception:
            pass

    # 3. miss
    for t in core.tasks_to_miss(open_tasks, cfg, now):
        try:
            sb().table("crm_task").update({"status": "missed"}) \
                .eq("org_id", org_id).eq("id", t.get("id")).execute()
            stats["missed"] += 1
        except Exception:
            pass

    # 4. escalate to the DM (manager-first, per the standing DM review-gate directive)
    try:
        escalated_keys = {r.get("window_key") for r in
                          (sb().table("crm_reminder_log").select("window_key").eq("org_id", org_id)
                           .eq("kind", "escalation").limit(20000).execute().data) or []}
    except Exception:
        escalated_keys = set()
    day = now.date().isoformat()
    for lead in core.leads_to_escalate(leads, cfg, now):
        key = f"{lead.get('id')}:{day}"       # at most one escalation per lead per day
        if key in escalated_keys:
            continue
        try:
            sb().table("crm_reminder_log").insert({
                "org_id": org_id, "lead_id": lead.get("id"), "kind": "escalation",
                "channel": "in_app", "window_key": key, "status": "sent",
            }).execute()
            _log_activity(org_id, lead.get("id"), "system",
                          f"Escalated — no activity for {lead.get('quiet_hours')} hours",
                          {"quiet_hours": lead.get("quiet_hours")})
            stats["escalated"] += 1
        except Exception:
            pass
    return stats


@router.post("/run-reminders")
def run_reminders(x_notify_secret: str = Header(default=""), org_id: str = ""):
    """Scheduler entrypoint — pg_cron via pg_net, every 15 minutes. Secret-guarded exactly like
    notify's /run-due, and each tenant runs under `core.run_for_tenant` so a deactivated tenant is
    skipped and every pass leaves a job_run audit row."""
    if not verify_notify_secret(x_notify_secret):
        raise HTTPException(403, "forbidden")
    orgs = []
    if org_id:
        orgs = [org_id]
    else:
        try:
            orgs = [r["org_id"] for r in
                    (get_supabase().schema("storeops").table("tenants").select("org_id")
                     .eq("is_active", True).limit(500).execute().data) or [] if r.get("org_id")]
        except Exception:
            orgs = [ORG_ID]
    out = []
    for oid in orgs:
        try:
            from app.modules.core.run_for_tenant import run_for_tenant
            res = run_for_tenant(oid, "crm_reminders", lambda ctx, _o=oid: _sweep_org(_o),
                                 money_scope="none")
            out.append(res if isinstance(res, dict) else {"org_id": oid, "ok": True})
        except Exception:
            try:
                out.append(_sweep_org(oid))
            except Exception as e:
                out.append({"org_id": oid, "error": str(e)})
    return {"ran": len(out), "results": out}


# ── Attention providers (registered on import; no NEEDS CORE, no main.py change) ─────────────────
# Guarded exactly like storevisit/attention_providers.py: a failure to register must never stop the
# CRM router itself from mounting.
try:
    from app.modules.crm import attention as _crm_attention   # noqa: E402,F401
except Exception:
    pass
