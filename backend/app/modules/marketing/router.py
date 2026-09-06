"""Marketing API — /api/v1/marketing/*  (Phase 1: outside-store event management).

OWNER DIRECTIVE 2026-09-06 (sanjot@): an event management module for outside-store events, GPS
enabled — theme, location/venue, goals, a user-created checklist, planned creative links, times
(including separately what time employees have to get there), the outside party, planned employees,
a backup employee, how everyone is getting there, who is picking up whom, and giveaways. "Again none
of the options I mentioned above are hard coded but options pre added with plus sign to add more as
per user discretion."

Tables: core.marketing_* (migration 986) + house vocabulary (migration 987). See 986's header for
the duplicate check, the schema choice, and the GPS privacy posture.

DESIGN
  • Every decision lives in `event_logic` (pure) or `core/geo` (pure). This file is HTTP and I/O.
    That is what makes the module provable in harness_marketing_event.py rather than "verified" by
    clicking around.
  • ACTUALS ARE NEVER STORED. `actuals.py` derives them from commcalc's ONE shared sales pass.
  • org_id is a QUERY PARAM on every endpoint (AGENT_CONTRACT §2) — the tenant middleware rewrites
    it from the caller's JWT. EVERY read filters it; EVERY insert stamps it. The static guard in
    harness_marketing_event.py §J fails the build if one is missed.
  • Child collections go through ONE generic CRUD layer (`_CHILD`), so there is a single place where
    ownership and org-scoping are enforced instead of eight near-identical handlers that drift.
  • A missing migration degrades to an empty list or a named 400 — never a 500 that takes an
    unrelated page down.
"""
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from app.core.database import get_supabase
from app.core.schemas import LaxModel
from app.modules.core.entitlements import require_module
from app.modules.core import geo
from app.modules.marketing import actuals as A
from app.modules.marketing import event_logic as L

router = APIRouter(prefix="/marketing", tags=["Marketing & Events"],
                   dependencies=[Depends(require_module("marketing"))])

ORG_ID = "00000000-0000-0000-0000-000000000001"   # house org; middleware rewrites the query param
HOUSE_ORG = "00000000-0000-0000-0000-000000000001"

EVENT_TABLE = "marketing_event"


def sb():
    """Marketing tables live in core.* (migration 986) — a schema PostgREST already serves."""
    return get_supabase().schema("core")


def _now():
    return datetime.now(timezone.utc)


def _now_iso():
    return _now().isoformat()


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Request bodies (LaxModel — the house Pydantic posture, so a legacy caller never breaks)
# ══════════════════════════════════════════════════════════════════════════════════════════════
class OptionIn(LaxModel):
    list_key: Any = None
    key: Any = None
    label: Any = None
    sort_order: Any = None
    is_active: Any = None
    extra: Any = None


class ConfigIn(LaxModel):
    approval_required: Any = None
    approval_spend_threshold: Any = None
    default_checkin_radius_m: Any = None
    max_checkin_accuracy_m: Any = None
    block_checkin_outside_fence: Any = None
    checkin_geo_retention_days: Any = None
    staffing_alert_lead_hours: Any = None


class EventIn(LaxModel):
    title: Any = None
    description: Any = None
    theme_key: Any = None
    market: Any = None
    primary_store_code: Any = None
    store_codes: Any = None
    venue_name: Any = None
    venue_type_key: Any = None
    address: Any = None
    city: Any = None
    state: Any = None
    postal_code: Any = None
    geo_lat: Any = None
    geo_lng: Any = None
    checkin_radius_m: Any = None
    setup_notes: Any = None
    parking_notes: Any = None
    event_start: Any = None
    event_end: Any = None
    staff_call_at: Any = None
    setup_start_at: Any = None
    teardown_end_at: Any = None
    planned_spend: Any = None
    debrief_what_worked: Any = None
    debrief_what_didnt: Any = None
    debrief_notes: Any = None


class StatusIn(LaxModel):
    status: Any = None
    note: Any = None


class ApprovalIn(LaxModel):
    action: Any = None          # submit | approve | reject
    note: Any = None


class ChildIn(LaxModel):
    """Generic child-row body. Fields are whitelisted per collection by `_CHILD`, so an unknown or
    protected key (org_id, id, event_id) is dropped rather than written."""
    data: Any = None


class CheckinIn(LaxModel):
    staff_id: Any = None
    employee_id: Any = None
    employee_name: Any = None
    check_in_lat: Any = None
    check_in_lng: Any = None
    check_in_accuracy: Any = None


class ApplyTemplateIn(LaxModel):
    template_id: Any = None
    replace: Any = False


class DocIn(LaxModel):
    doc_kind: Any = None
    file_name: Any = None
    data: Any = None
    uploaded_by: Any = None


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Caller identity + permissions
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _caller(authorization: str, x_active_org: str = ""):
    """{org_id, role, super_admin, perms, employee_id, store_code, market, full_name} or None.
    Same resolution CRM and closing use — marketing introduces no second identity path."""
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
        return {**c, "employee_id": u.get("employee_id"), "store_code": u.get("store_code"),
                "market": u.get("market"), "full_name": u.get("full_name"), "email": u.get("email")}
    except Exception:
        return None


def _who(caller) -> str:
    if not caller:
        return "unknown"
    return str(caller.get("full_name") or caller.get("email") or caller.get("employee_id") or "unknown")[:120]


def _is_manager(caller) -> bool:
    """Market-level and above. Managers plan and approve events; a store-scoped rep works one."""
    return bool(caller and (caller.get("super_admin")
                            or (caller.get("perms") or {}).get("scope") in ("all", "market")))


def _can_edit_settings(caller) -> bool:
    """Who may change the option vocabulary and the module switches. Company-wide scope, an explicit
    `settings.marketing` grant, or super-admin. An unresolved caller is DENIED, never defaulted
    open — the same fail-closed posture the 2026-07-26 settings audit established."""
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    s = perms.get("settings") or {}
    if "marketing" in s:
        return bool(s["marketing"])
    return (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin")


def _require_settings(caller):
    if not _can_edit_settings(caller):
        raise HTTPException(403, "Changing marketing setup is permission-restricted — you need the "
                                 "'marketing' settings permission or a company-wide role.")


def _require_manager(caller, what="manage events"):
    if not _is_manager(caller):
        raise HTTPException(403, "You need a market-wide or company-wide role to %s." % what)


def _require_approver(caller):
    """Approving is a manager act. Deliberately the same gate as managing rather than a new
    permission key: the approval switch is OFF by default, and inventing a permission nobody has
    granted would make turning the switch on look broken."""
    if not _is_manager(caller):
        raise HTTPException(403, "Approving an event requires a market-wide or company-wide role.")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Config + the option registry (RULE TWO)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _config_row(org_id: str) -> dict:
    try:
        rows = (sb().table("marketing_config").select("*")
                .eq("org_id", org_id).limit(1).execute().data) or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def _config(org_id: str) -> dict:
    """The effective config — the org's row over the house defaults. A missing table or row yields
    the house posture (approval OFF), never a crash and never approval silently ON."""
    return L.resolve_config(_config_row(org_id))


def _option_rows(org_id: str):
    """(house_rows, tenant_rows). The house rows are read with an explicit `.eq('org_id', HOUSE_ORG)`
    — a deliberate, bounded cross-org read of the platform's own starting vocabulary, which is the
    same tenant∪house pattern nav labels and report labels already use. It exposes no tenant data:
    the house org holds only platform seed rows."""
    def _q(oid):
        try:
            return (sb().table("marketing_option")
                    .select("list_key,key,label,sort_order,is_active,extra")
                    .eq("org_id", oid).limit(2000).execute().data) or []
        except Exception:
            return []
    house = _q(HOUSE_ORG)                    # org-guard-ok: platform seed vocabulary, not tenant data
    tenant = _q(org_id) if str(org_id) != HOUSE_ORG else []
    return house, tenant


def _options(org_id: str, list_key: str, include_inactive=False):
    house, tenant = _option_rows(org_id)
    return L.resolve_options(house, tenant, list_key, include_inactive=include_inactive)


def _all_options(org_id: str, include_inactive=False):
    house, tenant = _option_rows(org_id)
    return {lk: L.resolve_options(house, tenant, lk, include_inactive=include_inactive)
            for lk in L.LIST_KEYS}


@router.get("/options")
def get_options(list_key: str = "", include_inactive: bool = False, org_id: str = ORG_ID):
    """The effective pickers. `list_key` empty = every list, which is what the settings screen and
    the event form both load in one call."""
    lk = (list_key or "").strip()
    if lk and lk not in L.LIST_KEYS:
        raise HTTPException(400, "unknown list_key %r (expected one of %s)" % (lk, ", ".join(L.LIST_KEYS)))
    if lk:
        return {"list_key": lk, "label": L.LIST_LABELS.get(lk, lk),
                "options": _options(org_id, lk, include_inactive)}
    return {"lists": [{"list_key": k, "label": L.LIST_LABELS.get(k, k),
                       "options": _options(org_id, k, include_inactive)} for k in L.LIST_KEYS]}


@router.post("/options")
def upsert_option(body: OptionIn, authorization: str = Header(default=""),
                  x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """THE "+" the owner asked for. Adds a value to any list, or overrides/renames/deactivates a
    house one — as a ROW on this org. No deploy, no migration, no code change: that is the whole
    requirement, and it is why nothing in this module branches on an option value."""
    caller = _caller(authorization, x_active_org)
    _require_settings(caller)
    lk = str(body.list_key or "").strip()
    if lk not in L.LIST_KEYS:
        raise HTTPException(400, "unknown list_key %r (expected one of %s)" % (lk, ", ".join(L.LIST_KEYS)))
    label = str(body.label or "").strip()
    key = L.normalize_option_key(body.key or label)
    if not key:
        raise HTTPException(400, "a label (or key) is required")
    row = {"org_id": org_id, "list_key": lk, "key": key, "label": label or key,
           "updated_at": _now_iso(), "updated_by": _who(caller)}
    if body.sort_order is not None:
        row["sort_order"] = L._int(body.sort_order, 100)
    if body.is_active is not None:
        row["is_active"] = bool(body.is_active)
    if isinstance(body.extra, dict):
        row["extra"] = body.extra
    try:
        sb().table("marketing_option").upsert(row, on_conflict="org_id,list_key,key").execute()
    except Exception as e:
        raise HTTPException(400, "could not save the option — run migration 986 first (%s)"
                            % str(e)[:120])
    return {"ok": True, "list_key": lk, "key": key,
            "options": _options(org_id, lk, include_inactive=True)}


@router.delete("/options")
def deactivate_option(list_key: str = "", key: str = "", authorization: str = Header(default=""),
                      x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """Deactivate, never delete. An event booked last season keeps rendering the label it was booked
    with; a deactivated option simply stops appearing in new pickers. Deactivating a HOUSE option
    writes a tenant row with is_active false — the tenant row wins, and the house seed is untouched
    for every other org."""
    caller = _caller(authorization, x_active_org)
    _require_settings(caller)
    lk = (list_key or "").strip()
    k = (key or "").strip()
    if lk not in L.LIST_KEYS or not k:
        raise HTTPException(400, "list_key and key are required")
    existing = [o for o in _options(org_id, lk, include_inactive=True) if o["key"] == k]
    label = existing[0]["label"] if existing else k
    row = {"org_id": org_id, "list_key": lk, "key": k, "label": label, "is_active": False,
           "updated_at": _now_iso(), "updated_by": _who(caller)}
    try:
        sb().table("marketing_option").upsert(row, on_conflict="org_id,list_key,key").execute()
    except Exception as e:
        raise HTTPException(400, "could not update the option (%s)" % str(e)[:120])
    return {"ok": True, "options": _options(org_id, lk, include_inactive=True)}


@router.get("/config")
def get_config(org_id: str = ORG_ID):
    """The module switches. `is_default` tells the settings screen whether anyone has ever changed
    anything, so it can say "approval is off (default)" rather than implying a decision was made."""
    raw = _config_row(org_id)
    return {"config": _config(org_id), "is_default": not bool(raw),
            "defaults": dict(L.DEFAULT_CONFIG)}


@router.put("/config")
def put_config(body: ConfigIn, authorization: str = Header(default=""),
               x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """Change the switches. Note what is NOT here: no way to enable approval implicitly. Setting a
    spend threshold while approval is off changes nothing (see `event_logic.approval_decision`), so
    an org cannot accidentally gate its events by filling in a number."""
    caller = _caller(authorization, x_active_org)
    _require_settings(caller)
    row = {"org_id": org_id, "updated_at": _now_iso(), "updated_by": _who(caller)}
    for f in ("approval_required", "block_checkin_outside_fence"):
        if getattr(body, f) is not None:
            row[f] = bool(getattr(body, f))
    for f in ("default_checkin_radius_m", "max_checkin_accuracy_m",
              "checkin_geo_retention_days", "staffing_alert_lead_hours"):
        if getattr(body, f) is not None:
            row[f] = L._int(getattr(body, f), L.DEFAULT_CONFIG[f])
    if body.approval_spend_threshold is not None:
        row["approval_spend_threshold"] = L._num(body.approval_spend_threshold)
    try:
        sb().table("marketing_config").upsert(row, on_conflict="org_id").execute()
    except Exception as e:
        raise HTTPException(400, "could not save settings — run migration 986 first (%s)" % str(e)[:120])
    return get_config(org_id)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Events
# ══════════════════════════════════════════════════════════════════════════════════════════════
_EVENT_FIELDS = (
    "title", "description", "theme_key", "market", "primary_store_code",
    "venue_name", "venue_type_key", "address", "city", "state", "postal_code",
    "setup_notes", "parking_notes",
    "event_start", "event_end", "staff_call_at", "setup_start_at", "teardown_end_at",
    "debrief_what_worked", "debrief_what_didnt", "debrief_notes",
)


def _event_payload(body: EventIn) -> dict:
    """Body → column payload. Coordinates and money go through the parsers rather than straight in:
    a 'lat' of "abc" must become NULL (no pin), not a 500 and not a 0.0 that would place the event
    in the Gulf of Guinea and fail every check-in."""
    row = {}
    for f in _EVENT_FIELDS:
        v = getattr(body, f, None)
        if v is not None:
            row[f] = (str(v) if isinstance(v, str) else v) or None
    if body.geo_lat is not None:
        row["geo_lat"] = geo.parse_lat(body.geo_lat)
    if body.geo_lng is not None:
        row["geo_lng"] = geo.parse_lng(body.geo_lng)
    if body.checkin_radius_m is not None:
        row["checkin_radius_m"] = (geo.clamp_radius(body.checkin_radius_m)
                                   if str(body.checkin_radius_m).strip() != "" else None)
    if body.planned_spend is not None:
        row["planned_spend"] = L._num(body.planned_spend)
    return row


def _event(org_id: str, event_id: str) -> dict:
    """ONE event, org-scoped. This is the ONLY way an event row is fetched in this module, so no
    handler can accidentally reach across tenants by forgetting the filter."""
    try:
        rows = (sb().table(EVENT_TABLE).select("*")
                .eq("org_id", org_id).eq("id", event_id).limit(1).execute().data) or []
    except Exception as e:
        raise HTTPException(400, "could not read the event — run migration 986 first (%s)" % str(e)[:120])
    if not rows:
        raise HTTPException(404, "event not found")
    return rows[0]


def _event_store_codes(org_id: str, event_id: str):
    try:
        rows = (sb().table("marketing_event_store").select("store_code")
                .eq("org_id", org_id).eq("event_id", event_id).limit(500).execute().data) or []
    except Exception:
        return []
    return [r["store_code"] for r in rows if r.get("store_code")]


def _set_event_stores(org_id: str, event_id: str, codes):
    """Replace the store set. Delete-then-insert (both org-scoped) because the set is small and a
    diff would be more code for no benefit."""
    want = sorted({str(c).strip() for c in (codes or []) if str(c or "").strip()})
    try:
        sb().table("marketing_event_store").delete() \
            .eq("org_id", org_id).eq("event_id", event_id).execute()
        if want:
            sb().table("marketing_event_store").insert(
                [{"org_id": org_id, "event_id": event_id, "store_code": c} for c in want]).execute()
    except Exception as e:
        raise HTTPException(400, "could not save the event's stores (%s)" % str(e)[:120])
    return want


@router.get("/events")
def list_events(status: str = "", market: str = "", store_code: str = "", theme_key: str = "",
                start: str = "", end: str = "", limit: int = 200, org_id: str = ORG_ID):
    """The event list. Filters are all optional and all applied server-side so the page never pulls
    a tenant's whole history to filter it in the browser."""
    try:
        q = (sb().table(EVENT_TABLE).select("*").eq("org_id", org_id).eq("is_active", True))
        if status:
            q = q.eq("status", status)
        if market:
            q = q.eq("market", market)
        if theme_key:
            q = q.eq("theme_key", theme_key)
        if start:
            q = q.gte("event_start", start)
        if end:
            q = q.lte("event_start", end)
        rows = (q.order("event_start", desc=True).limit(max(1, min(int(limit or 200), 1000)))
                .execute().data) or []
    except Exception as e:
        return {"events": [], "count": 0,
                "error": "Marketing tables are not available yet — run migration 986. (%s)" % str(e)[:120]}

    ids = [r["id"] for r in rows if r.get("id")]
    stores_by_event = {}
    if ids:
        try:
            srows = (sb().table("marketing_event_store").select("event_id,store_code")
                     .eq("org_id", org_id).in_("event_id", ids).limit(5000).execute().data) or []
            for s in srows:
                stores_by_event.setdefault(s["event_id"], []).append(s.get("store_code"))
        except Exception:
            pass
    if store_code:
        want = str(store_code).strip().upper()
        rows = [r for r in rows
                if want in {str(c).upper() for c in stores_by_event.get(r["id"], [])}
                or str(r.get("primary_store_code") or "").upper() == want]

    opts = _all_options(org_id, include_inactive=True)
    for r in rows:
        r["store_codes"] = stores_by_event.get(r["id"], [])
        r["theme_label"] = L.option_label(opts[L.LIST_THEME], r.get("theme_key"), fallback="")
        r["venue_type_label"] = L.option_label(opts[L.LIST_VENUE_TYPE], r.get("venue_type_key"), fallback="")
    return {"events": rows, "count": len(rows)}


@router.post("/events")
def create_event(body: EventIn, authorization: str = Header(default=""),
                 x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """Create an event. It starts as a DRAFT and its approval state is computed immediately, so the
    form can tell the planner "this will need approval" before they have finished typing rather than
    surprising them at go-live."""
    caller = _caller(authorization, x_active_org)
    _require_manager(caller, "create events")
    title = str(body.title or "").strip()
    if not title:
        raise HTTPException(400, "a title is required")
    row = _event_payload(body)
    row.update({"org_id": org_id, "title": title, "status": L.STATUS_DRAFT,
                "created_at": _now_iso(), "created_by": _who(caller),
                "updated_at": _now_iso(), "updated_by": _who(caller)})
    decision = L.approval_decision(_config(org_id), row.get("planned_spend"))
    row["approval_state"] = decision["state"]
    row["approval_reason"] = decision["reason"]
    try:
        r = sb().table(EVENT_TABLE).insert(row).execute()
    except Exception as e:
        raise HTTPException(400, "could not create the event — run migration 986 first (%s)" % str(e)[:120])
    saved = (r.data[0] if r.data else row)
    if body.store_codes is not None and saved.get("id"):
        saved["store_codes"] = _set_event_stores(org_id, saved["id"], body.store_codes)
    elif saved.get("primary_store_code") and saved.get("id"):
        saved["store_codes"] = _set_event_stores(org_id, saved["id"], [saved["primary_store_code"]])
    return {"ok": True, "event": saved, "approval": decision}


@router.patch("/events/{event_id}")
def update_event(event_id: str, body: EventIn, authorization: str = Header(default=""),
                 x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """Edit an event. Two rules worth naming:

    · A CLOSED or CANCELLED event's PLAN is frozen; only the debrief fields stay writable, because
      the debrief is written after the event is closed and rewriting the plan afterwards would make
      the actuals report describe an event that never happened.
    · Changing planned spend RE-EVALUATES approval, but only from `not_required`/`pending`. An
      already-APPROVED event whose spend is edited upward goes back to pending — an approval is for
      a plan, not for a row id.
    """
    caller = _caller(authorization, x_active_org)
    _require_manager(caller, "edit events")
    existing = _event(org_id, event_id)
    row = _event_payload(body)
    status = existing.get("status") or L.STATUS_DRAFT
    if status not in L.EDITABLE_STATUSES:
        debrief_only = {k: v for k, v in row.items() if k.startswith("debrief_")}
        if len(debrief_only) != len(row):
            raise HTTPException(400, "This event is %s — only the debrief can still be edited." % status)
        row = debrief_only
        if row:
            row["debrief_at"] = _now_iso()
            row["debrief_by"] = _who(caller)

    if "planned_spend" in row and status in L.EDITABLE_STATUSES:
        decision = L.approval_decision(_config(org_id), row.get("planned_spend"))
        current = existing.get("approval_state") or L.APPROVAL_NOT_REQUIRED
        if current in (L.APPROVAL_NOT_REQUIRED, L.APPROVAL_PENDING) or decision["required"]:
            row["approval_state"] = decision["state"]
            row["approval_reason"] = decision["reason"]
            if decision["state"] == L.APPROVAL_PENDING and current == L.APPROVAL_APPROVED:
                row["approval_reason"] = (decision["reason"]
                                          + " The previous approval no longer applies because the "
                                            "planned spend changed.")
                row["approved_by"] = None
                row["approved_at"] = None

    row["updated_at"] = _now_iso()
    row["updated_by"] = _who(caller)
    try:
        sb().table(EVENT_TABLE).update(row).eq("org_id", org_id).eq("id", event_id).execute()
    except Exception as e:
        raise HTTPException(400, "could not save the event (%s)" % str(e)[:120])
    if body.store_codes is not None:
        _set_event_stores(org_id, event_id, body.store_codes)
    return {"ok": True, "event": _event(org_id, event_id),
            "store_codes": _event_store_codes(org_id, event_id)}


@router.delete("/events/{event_id}")
def archive_event(event_id: str, authorization: str = Header(default=""),
                  x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """Soft-archive. A hard delete would take the GPS check-ins, the giveaway counts and the debrief
    with it — the record of what a group of people actually did on a Saturday."""
    caller = _caller(authorization, x_active_org)
    _require_manager(caller, "archive events")
    _event(org_id, event_id)
    try:
        sb().table(EVENT_TABLE).update({"is_active": False, "updated_at": _now_iso(),
                                        "updated_by": _who(caller)}) \
            .eq("org_id", org_id).eq("id", event_id).execute()
    except Exception as e:
        raise HTTPException(400, "could not archive the event (%s)" % str(e)[:120])
    return {"ok": True}


@router.post("/events/{event_id}/status")
def set_event_status(event_id: str, body: StatusIn, authorization: str = Header(default=""),
                     x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """Move the event through its lifecycle. Going LIVE is the one transition with a gate on it, and
    `event_logic.gate_go_live` is the only place that gate exists."""
    caller = _caller(authorization, x_active_org)
    _require_manager(caller, "change an event's status")
    existing = _event(org_id, event_id)
    target = str(body.status or "").strip()
    current = existing.get("status") or L.STATUS_DRAFT

    if target == L.STATUS_LIVE:
        ok, why = L.gate_go_live(current, existing.get("approval_state"))
    else:
        ok, why = L.can_transition(current, target)
    if not ok:
        raise HTTPException(400, why)

    row = {"status": target, "updated_at": _now_iso(), "updated_by": _who(caller)}
    if target == L.STATUS_CLOSED:
        row["debrief_at"] = existing.get("debrief_at") or _now_iso()
    try:
        sb().table(EVENT_TABLE).update(row).eq("org_id", org_id).eq("id", event_id).execute()
    except Exception as e:
        raise HTTPException(400, "could not change the status (%s)" % str(e)[:120])
    return {"ok": True, "status": target, "event": _event(org_id, event_id)}


@router.post("/events/{event_id}/approval")
def event_approval(event_id: str, body: ApprovalIn, authorization: str = Header(default=""),
                   x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """submit | approve | reject.

    `submit` recomputes the requirement from CURRENT config and spend rather than trusting whatever
    is stored, so an org that switched approval off after an event was submitted does not leave that
    event stuck pending forever.
    """
    caller = _caller(authorization, x_active_org)
    existing = _event(org_id, event_id)
    action = str(body.action or "").strip().lower()
    cfg = _config(org_id)

    if action == "submit":
        _require_manager(caller, "submit an event for approval")
        decision = L.approval_decision(cfg, existing.get("planned_spend"))
        row = {"approval_state": decision["state"], "approval_reason": decision["reason"]}
    elif action in ("approve", "reject"):
        _require_approver(caller)
        if (existing.get("approval_state") or L.APPROVAL_NOT_REQUIRED) == L.APPROVAL_NOT_REQUIRED:
            raise HTTPException(400, "This event does not require approval, so there is nothing to "
                                     "approve or reject.")
        row = {"approval_state": (L.APPROVAL_APPROVED if action == "approve" else L.APPROVAL_REJECTED),
               "approved_by": _who(caller), "approved_at": _now_iso(),
               "approval_note": str(body.note or "")[:2000] or None}
        decision = {"state": row["approval_state"], "reason": existing.get("approval_reason")}
    else:
        raise HTTPException(400, "action must be submit, approve or reject")

    row["updated_at"] = _now_iso()
    row["updated_by"] = _who(caller)
    try:
        sb().table(EVENT_TABLE).update(row).eq("org_id", org_id).eq("id", event_id).execute()
    except Exception as e:
        raise HTTPException(400, "could not update approval (%s)" % str(e)[:120])
    return {"ok": True, "approval": decision, "event": _event(org_id, event_id)}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Child collections — ONE generic CRUD layer
# ══════════════════════════════════════════════════════════════════════════════════════════════
# Eight near-identical handler triples would drift; more importantly, org-scoping and parent
# ownership would have to be re-proved in eight places. Here they are enforced once. The whitelist
# per collection is what stops a client writing org_id, id or event_id — the three fields that
# decide who a row belongs to.
_CHILD = {
    "staff": ("marketing_event_staff",
              ("employee_id", "employee_name", "role_key", "is_backup", "backup_for_staff_id",
               "confirm_state", "confirmed_at", "transport_mode_key", "pickup_by_staff_id",
               "pickup_at", "pickup_location", "call_time_override", "notes")),
    "vendors": ("marketing_event_vendor",
                ("party_type_key", "vendor_name", "contact_name", "contact_phone", "contact_email",
                 "cost", "confirm_state", "confirmed_at", "arrival_at", "contract_document_id",
                 "notes")),
    "checklist": ("marketing_event_checklist_item",
                  ("label", "category", "qty", "owner_staff_id", "owner_employee_id",
                   "is_returnable", "is_packed", "packed_at", "packed_by", "is_returned",
                   "returned_at", "returned_by", "sort_order", "notes")),
    "links": ("marketing_event_link",
              ("channel_key", "label", "url", "planned_post_at", "posted_at", "status", "notes",
               "sort_order")),
    "giveaways": ("marketing_event_giveaway",
                  ("giveaway_type_key", "item_label", "qty_out", "qty_returned", "qty_given",
                   "unit_cost", "notes")),
    "goals": ("marketing_event_goal", ("metric_key", "target_value", "note", "sort_order")),
}
#: Collections whose rows carry an audit stamp. `goals` does not (it is a target, not an act).
_CHILD_STAMPED = ("staff", "vendors", "checklist", "links", "giveaways")

#: Booleans that must be coerced rather than stored as the strings a JSON body may carry.
_CHILD_BOOL = ("is_backup", "is_returnable", "is_packed", "is_returned")
_CHILD_NUM = ("cost", "qty", "qty_out", "qty_returned", "qty_given", "unit_cost", "target_value")


def _child_spec(collection: str):
    spec = _CHILD.get(collection)
    if not spec:
        raise HTTPException(404, "unknown collection %r" % collection)
    return spec


def _child_payload(collection: str, data) -> dict:
    """Whitelist + coerce. Anything not on the collection's field list is DROPPED silently rather
    than rejected: a frontend that sends back a whole row it just read (including id, org_id and
    joined display labels) is normal, and 400-ing it would be hostile."""
    _, fields = _child_spec(collection)
    src = data if isinstance(data, dict) else {}
    row = {}
    for f in fields:
        if f not in src:
            continue
        v = src[f]
        if f in _CHILD_BOOL:
            row[f] = bool(v)
        elif f in _CHILD_NUM:
            row[f] = L._num(v)
        elif isinstance(v, str):
            row[f] = v.strip() or None
        else:
            row[f] = v
    return row


def _child_rows(org_id: str, event_id: str, collection: str):
    table, _ = _child_spec(collection)
    try:
        return (sb().table(table).select("*")
                .eq("org_id", org_id).eq("event_id", event_id).limit(2000).execute().data) or []
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Checklist templates
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/checklist-templates")
def list_checklist_templates(org_id: str = ORG_ID):
    """Templates this org may use: its own, plus the house starting template. Same tenant∪house
    resolution as the option lists, for the same reason — a new tenant's first event should not be
    a blank page, and a tenant that builds its own should not see ours."""
    def _q(oid):
        try:
            return (sb().table("marketing_checklist_template").select("*")
                    .eq("org_id", oid).eq("is_active", True).limit(200).execute().data) or []
        except Exception:
            return []
    house = _q(HOUSE_ORG)                 # org-guard-ok: platform starting template, not tenant data
    tenant = _q(org_id) if str(org_id) != HOUSE_ORG else []
    for t in house:
        t["source"] = "house"
    for t in tenant:
        t["source"] = "tenant"
    return {"templates": tenant + house}


def _template_items(template_org_id: str, template_id: str):
    try:
        return (sb().table("marketing_checklist_template_item").select("*")
                .eq("org_id", template_org_id).eq("template_id", template_id)
                .order("sort_order").limit(500).execute().data) or []
    except Exception:
        return []


@router.post("/events/{event_id}/apply-checklist-template")
def apply_checklist_template(event_id: str, body: ApplyTemplateIn,
                             authorization: str = Header(default=""),
                             x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """Copy a template onto an event. A COPY, deliberately: once instantiated the list is the
    EVENT's, so editing the template next month never rewrites the history of an event that already
    ran. `replace` clears what is there first; the default APPENDS, because the common case is
    adding the standard kit to a list someone has already started."""
    caller = _caller(authorization, x_active_org)
    _require_manager(caller, "edit an event's plan")
    event = _event(org_id, event_id)
    if (event.get("status") or L.STATUS_DRAFT) not in L.EDITABLE_STATUSES:
        raise HTTPException(400, "This event is %s — its checklist can no longer be built."
                            % event.get("status"))
    tpl_id = str(body.template_id or "").strip()
    if not tpl_id:
        raise HTTPException(400, "template_id required")

    items = _template_items(org_id, tpl_id)
    if not items:
        # The template may be the platform's. Only the HOUSE org is consulted as a fallback — never
        # another tenant — so this can read platform content and nothing else.
        items = _template_items(HOUSE_ORG, tpl_id)   # org-guard-ok: platform starting template
    if not items:
        raise HTTPException(404, "template not found (or it has no items)")

    if body.replace:
        try:
            sb().table("marketing_event_checklist_item").delete() \
                .eq("org_id", org_id).eq("event_id", event_id).execute()
        except Exception as e:
            raise HTTPException(400, "could not clear the existing checklist (%s)" % str(e)[:120])
    rows = L.instantiate_template(items, event_id, org_id)
    for r in rows:
        r["created_at"] = _now_iso()
        r["created_by"] = _who(caller)
    try:
        sb().table("marketing_event_checklist_item").insert(rows).execute()
    except Exception as e:
        raise HTTPException(400, "could not add the checklist items (%s)" % str(e)[:160])
    return {"ok": True, "added": len(rows)}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# GPS check-in / check-out  (SENSITIVE — read migration 986's privacy header first)
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.post("/events/{event_id}/checkin")
def event_checkin(event_id: str, body: CheckinIn, authorization: str = Header(default=""),
                  x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """"I'm here." ONE position, taken now, judged once, stored with its verdict.

    This is NOT manager-gated: the person checking in is a rep, and a rep must be able to say they
    arrived. What a rep cannot do is check someone else in — `employee_id` is taken from the CALLER's
    identity whenever we can resolve one, and only a manager may name a different person (for the
    teammate whose phone is dead, which is a real thing that happens at 8am in a parking lot).

    The geofence verdict comes from `core/geo.evaluate_checkin` — the single shared decision — and
    the row records the distance, the radius and the verdict as evidence. An out-of-fence check-in is
    RECORDED AND FLAGGED, not refused, unless the org explicitly turned on hard blocking.
    """
    caller = _caller(authorization, x_active_org)
    event = _event(org_id, event_id)
    cfg = _config(org_id)

    caller_emp = str((caller or {}).get("employee_id") or "").strip()
    asked_emp = str(body.employee_id or "").strip()
    if asked_emp and asked_emp != caller_emp and not _is_manager(caller):
        raise HTTPException(403, "You can only check yourself in. A manager can check in a teammate.")
    employee_id = asked_emp or caller_emp
    if not employee_id:
        raise HTTPException(400, "We could not tell who is checking in. Sign in again, or ask a "
                                 "manager to check you in.")

    verdict = geo.evaluate_checkin(
        fix_lat=body.check_in_lat, fix_lng=body.check_in_lng,
        fix_accuracy_m=body.check_in_accuracy,
        target_lat=event.get("geo_lat"), target_lng=event.get("geo_lng"),
        radius_m=(event.get("checkin_radius_m") or cfg["default_checkin_radius_m"]),
        max_accuracy_m=cfg["max_checkin_accuracy_m"],
        block_outside=cfg["block_checkin_outside_fence"])
    if not verdict["accepted"]:
        raise HTTPException(400, verdict["note"])

    row = {
        "org_id": org_id, "event_id": event_id,
        "staff_id": (str(body.staff_id).strip() or None) if body.staff_id else None,
        "employee_id": employee_id,
        "employee_name": (str(body.employee_name or "").strip()
                          or (caller or {}).get("full_name") or None),
        "checked_in_at": _now_iso(),
        # Only stored when there was a real fix; a rejected coordinate is stored as NULL rather than
        # as a plausible-looking 0.
        "check_in_lat": geo.parse_lat(body.check_in_lat),
        "check_in_lng": geo.parse_lng(body.check_in_lng),
        "check_in_accuracy": geo.parse_accuracy(body.check_in_accuracy),
        "distance_m": verdict["distance_m"], "radius_m": verdict["radius_m"],
        "within_geofence": verdict["within_geofence"], "decision": verdict["decision"],
        "decision_note": verdict["note"],
        # The retention promise, stamped onto the row itself.
        "purge_after_date": L.purge_after_date(_now(), cfg["checkin_geo_retention_days"]),
    }
    try:
        r = sb().table("marketing_event_checkin").insert(row).execute()
    except Exception as e:
        raise HTTPException(400, "could not record the check-in (%s)" % str(e)[:160])
    return {"ok": True, "checkin": (r.data[0] if r.data else row), "verdict": verdict,
            "retention_note": ("Your location was recorded once, now, to confirm you are at the "
                               "event. It is kept until %s and you can see it any time under "
                               "'My check-ins'." % row["purge_after_date"])}


@router.post("/events/{event_id}/checkout")
def event_checkout(event_id: str, body: CheckinIn, authorization: str = Header(default=""),
                   x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """"I'm done." A TIMESTAMP ONLY — no second coordinate is taken, requested or stored. Knowing
    when someone left does not require knowing where they were when they left."""
    caller = _caller(authorization, x_active_org)
    _event(org_id, event_id)
    caller_emp = str((caller or {}).get("employee_id") or "").strip()
    asked_emp = str(body.employee_id or "").strip()
    if asked_emp and asked_emp != caller_emp and not _is_manager(caller):
        raise HTTPException(403, "You can only check yourself out. A manager can check out a teammate.")
    employee_id = asked_emp or caller_emp
    if not employee_id:
        raise HTTPException(400, "We could not tell who is checking out.")
    try:
        rows = (sb().table("marketing_event_checkin").select("id,checked_out_at")
                .eq("org_id", org_id).eq("event_id", event_id).eq("employee_id", employee_id)
                .order("checked_in_at", desc=True).limit(1).execute().data) or []
    except Exception as e:
        raise HTTPException(400, "could not read the check-in (%s)" % str(e)[:120])
    if not rows:
        raise HTTPException(404, "There is no check-in to close for you on this event.")
    try:
        sb().table("marketing_event_checkin").update({"checked_out_at": _now_iso()}) \
            .eq("org_id", org_id).eq("id", rows[0]["id"]).execute()
    except Exception as e:
        raise HTTPException(400, "could not record the check-out (%s)" % str(e)[:120])
    return {"ok": True}


@router.get("/my-checkins")
def my_checkins(limit: int = 100, authorization: str = Header(default=""),
                x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """TRANSPARENCY (owner constraint, and basic decency): every location record the platform holds
    about the CALLER, in plain terms, with the retention date on each one. Filtered to the caller's
    own employee_id — this endpoint cannot be used to look at anybody else, by anybody, including a
    super-admin."""
    caller = _caller(authorization, x_active_org)
    emp = str((caller or {}).get("employee_id") or "").strip()
    if not emp:
        raise HTTPException(403, "Sign in to see your own check-ins.")
    try:
        rows = (sb().table("marketing_event_checkin").select("*")
                .eq("org_id", org_id).eq("employee_id", emp)
                .order("checked_in_at", desc=True)
                .limit(max(1, min(int(limit or 100), 500))).execute().data) or []
    except Exception:
        rows = []
    ids = sorted({r.get("event_id") for r in rows if r.get("event_id")})
    titles = {}
    if ids:
        try:
            evs = (sb().table(EVENT_TABLE).select("id,title,event_start")
                   .eq("org_id", org_id).in_("id", ids).limit(500).execute().data) or []
            titles = {e["id"]: e for e in evs}
        except Exception:
            pass
    for r in rows:
        ev = titles.get(r.get("event_id")) or {}
        r["event_title"] = ev.get("title")
        r["event_start"] = ev.get("event_start")
    return {
        "checkins": rows, "count": len(rows),
        "explanation": ("Each row is one location reading, taken at the moment you pressed "
                        "check-in. Nothing tracks you between check-ins, and checking out records "
                        "only the time. Each row shows the date its location data is scheduled to "
                        "be removed."),
    }


@router.get("/checkin-retention")
def checkin_retention(org_id: str = ORG_ID, authorization: str = Header(default=""),
                      x_active_org: str = Header(default="")):
    """What is past its retention date. Phase 1 does NOT delete automatically — that gap is declared
    on the control box (mig 987) rather than implied away — so this is what makes the promise
    measurable instead of theoretical."""
    caller = _caller(authorization, x_active_org)
    _require_settings(caller)
    try:
        rows = (sb().table("marketing_event_checkin").select("id,purge_after_date")
                .eq("org_id", org_id).limit(20000).execute().data) or []
    except Exception:
        rows = []
    return L.retention_summary(rows, now=_now())


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Documents — storeops.store_document + the private store-docs bucket, REUSED unchanged
# ══════════════════════════════════════════════════════════════════════════════════════════════
EVENT_DOC_KINDS = ("event_vendor_contract", "event_photo", "event_permit")


@router.post("/events/{event_id}/doc")
def upload_event_doc(event_id: str, body: DocIn, authorization: str = Header(default=""),
                     x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """Upload a vendor contract, an event photo or a permit.

    Reuses `storeops.store_lease.upload_store_doc` and the private `store-docs` bucket verbatim (the
    second argument is only a storage path segment) so this adds NO new storage path, NO new bucket
    and NO new way to reach a file. Append-only, like leases: a re-upload is a new version.
    """
    caller = _caller(authorization, x_active_org)
    _require_manager(caller, "attach documents to an event")
    _event(org_id, event_id)
    kind = str(body.doc_kind or "").strip().lower()
    if kind not in EVENT_DOC_KINDS:
        raise HTTPException(400, "doc_kind must be one of %s" % (EVENT_DOC_KINDS,))
    try:
        from app.modules.storeops import store_lease as _lease
        path, size, ctype = _lease.upload_store_doc(org_id, "event-%s" % event_id, kind,
                                                    body.file_name, body.data,
                                                    client=get_supabase())
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, "The document couldn't be saved to storage — please try again. "
                                 "(storage error: %s)" % str(e)[:160])
    row = {"org_id": org_id, "event_id": event_id, "doc_kind": kind, "storage_path": path,
           "file_name": str(body.file_name or "")[:200] or None, "content_type": ctype,
           "size_bytes": size, "uploaded_by": _who(caller)}
    try:
        r = get_supabase().schema("storeops").table("store_document").insert(row).execute()
    except Exception as e:
        raise HTTPException(400, "could not record the document — run migration 986 first (%s)"
                            % str(e)[:160])
    saved = (r.data[0] if r.data else dict(row))
    saved.pop("storage_path", None)      # never echoed — downloads go by id, exactly as leases do
    return {"ok": True, "document": saved}


@router.get("/events/{event_id}/docs")
def list_event_docs(event_id: str, org_id: str = ORG_ID):
    """Document versions on this event, newest first. `storage_path` is deliberately NOT selected."""
    _event(org_id, event_id)
    try:
        rows = (get_supabase().schema("storeops").table("store_document")
                .select("id,doc_kind,file_name,content_type,size_bytes,uploaded_by,uploaded_at")
                .eq("org_id", org_id).eq("event_id", event_id)
                .order("uploaded_at", desc=True).limit(500).execute().data) or []
    except Exception:
        rows = []
    return {"documents": rows}


@router.get("/doc-url")
def event_doc_url(doc_id: str = "", org_id: str = ORG_ID, authorization: str = Header(default=""),
                  x_active_org: str = Header(default="")):
    """Sign ONE event document on demand. The path comes from an ORG-SCOPED row lookup by id and is
    additionally required to be an EVENT document — so this endpoint can never be used to sign a
    lease, a COI or another tenant's file, whatever id is passed."""
    caller = _caller(authorization, x_active_org)
    _require_manager(caller, "open event documents")
    if not doc_id:
        raise HTTPException(400, "doc_id required")
    try:
        rows = (get_supabase().schema("storeops").table("store_document")
                .select("storage_path,file_name,content_type,doc_kind,event_id")
                .eq("org_id", org_id).eq("id", doc_id).limit(1).execute().data) or []
    except Exception:
        rows = []
    if not rows or not rows[0].get("event_id") or rows[0].get("doc_kind") not in EVENT_DOC_KINDS:
        raise HTTPException(404, "document not found")
    _event(org_id, rows[0]["event_id"])          # and the caller must be able to see that event
    from app.modules.storeops import store_lease as _lease
    url = _lease.signed_doc_url(rows[0].get("storage_path"), client=get_supabase())
    if not url:
        raise HTTPException(502, "The document could not be signed — try again.")
    return {"url": url, "file_name": rows[0].get("file_name"),
            "content_type": rows[0].get("content_type")}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# The event workspace + planned-vs-actual
# ══════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/events/{event_id}")
def get_event(event_id: str, org_id: str = ORG_ID):
    """Everything one event screen needs, in one call: the event, its stores, every child
    collection, the resolved option lists, the staffing/backup/transport analysis, checklist
    readiness, giveaway reconciliation and the readiness issue list.

    One call rather than nine because the analyses have to agree with each other — computing the
    staffing summary in a second request against a second snapshot is how a screen ends up telling a
    manager two different things about the same event.
    """
    event = _event(org_id, event_id)
    opts = _all_options(org_id, include_inactive=True)
    staff = _child_rows(org_id, event_id, "staff")
    checklist = sorted(_child_rows(org_id, event_id, "checklist"),
                       key=lambda r: (r.get("sort_order") or 100, str(r.get("label") or "")))
    vendors = _child_rows(org_id, event_id, "vendors")
    links = sorted(_child_rows(org_id, event_id, "links"),
                   key=lambda r: (r.get("sort_order") or 100, str(r.get("label") or "")))
    giveaways = _child_rows(org_id, event_id, "giveaways")
    goals = sorted(_child_rows(org_id, event_id, "goals"),
                   key=lambda r: (r.get("sort_order") or 100, str(r.get("metric_key") or "")))
    try:
        checkins = (sb().table("marketing_event_checkin").select("*")
                    .eq("org_id", org_id).eq("event_id", event_id)
                    .order("checked_in_at", desc=True).limit(500).execute().data) or []
    except Exception:
        checkins = []

    cfg = _config(org_id)
    staffing = L.resolve_staffing(staff, checkins)
    transport = L.resolve_transport(staff, opts[L.LIST_TRANSPORT_MODE])
    readiness = L.event_readiness(event, staff, checklist, vendors, now=_now(),
                                  lead_hours=cfg["staffing_alert_lead_hours"],
                                  transport_options=opts[L.LIST_TRANSPORT_MODE])
    # Per-person call time, resolved once here so the screen never re-implements the fallback chain.
    for s in staff:
        when, src = L.call_time_for(s, event)
        s["resolved_call_time"] = when
        s["call_time_source"] = src

    event["store_codes"] = _event_store_codes(org_id, event_id)
    event["theme_label"] = L.option_label(opts[L.LIST_THEME], event.get("theme_key"), fallback="")
    event["venue_type_label"] = L.option_label(opts[L.LIST_VENUE_TYPE], event.get("venue_type_key"),
                                               fallback="")
    return {
        "event": event,
        "options": opts,
        "config": cfg,
        "staff": staff,
        "staffing": {"counts": staffing["counts"], "roster": staffing["roster"],
                     "uncovered": staffing["uncovered"],
                     "unassigned_backups": staffing["unassigned_backups"]},
        "transport": transport,
        "checkins": checkins,
        "checklist": checklist,
        "checklist_readiness": L.checklist_readiness(checklist),
        "vendors": vendors,
        "links": links,
        "giveaways": giveaways,
        "giveaway_reconciliation": L.giveaway_reconciliation(giveaways),
        "goals": goals,
        "readiness": readiness,
        "allowed_transitions": list(L.TRANSITIONS.get(event.get("status") or L.STATUS_DRAFT, ())),
    }


@router.get("/events/{event_id}/actuals")
def get_event_actuals(event_id: str, org_id: str = ORG_ID):
    """Planned vs actual — DERIVED from commcalc's shared sales pass, never stored.

    Read `actuals.py`'s header before changing anything here: the response deliberately reports
    STORE PERFORMANCE OVER THE EVENT WINDOW against a same-weekday baseline, and says so in an
    `attribution` block that the UI renders as a visible caption, not a tooltip.
    """
    event = _event(org_id, event_id)
    goals = _child_rows(org_id, event_id, "goals")
    codes = _event_store_codes(org_id, event_id)
    if not codes and event.get("primary_store_code"):
        codes = [event["primary_store_code"]]
    opts = _options(org_id, L.LIST_GOAL_METRIC, include_inactive=True)
    return A.event_actuals(get_supabase(), org_id, event, codes, goals, opts)


@router.get("/summary")
def marketing_summary(days_ahead: int = 30, days_back: int = 30, org_id: str = ORG_ID):
    """The dashboard: what is coming up, what needs a human, and what just finished.

    `needs_attention` is computed with the SAME `event_readiness` the event page and the attention
    providers use, so the dashboard count, the event page banner and the admin notification can
    never disagree.
    """
    now = _now()
    cfg = _config(org_id)
    lo = (now.date().toordinal() - max(0, int(days_back or 0)))
    hi = (now.date().toordinal() + max(0, int(days_ahead or 0)))
    from datetime import date as _date
    start_s, end_s = _date.fromordinal(lo).isoformat(), _date.fromordinal(hi).isoformat()
    try:
        events = (sb().table(EVENT_TABLE).select("*")
                  .eq("org_id", org_id).eq("is_active", True)
                  .gte("event_start", start_s).lte("event_start", end_s + "T23:59:59+00:00")
                  .order("event_start").limit(500).execute().data) or []
    except Exception as e:
        return {"upcoming": [], "needs_attention": [], "recent": [], "counts": {},
                "error": "Marketing tables are not available yet — run migration 986. (%s)" % str(e)[:120]}

    ids = [e["id"] for e in events if e.get("id")]
    staff_by, check_by, vend_by = {}, {}, {}
    if ids:
        for table, bucket in (("marketing_event_staff", staff_by),
                              ("marketing_event_checklist_item", check_by),
                              ("marketing_event_vendor", vend_by)):
            try:
                rows = (sb().table(table).select("*")
                        .eq("org_id", org_id).in_("event_id", ids).limit(5000).execute().data) or []
            except Exception:
                rows = []
            for r in rows:
                bucket.setdefault(r.get("event_id"), []).append(r)

    opts = _all_options(org_id, include_inactive=True)
    upcoming, attention, recent = [], [], []
    for e in events:
        start = L.parse_dt(e.get("event_start"))
        ready = L.event_readiness(e, staff_by.get(e["id"], []), check_by.get(e["id"], []),
                                  vend_by.get(e["id"], []), now=now,
                                  lead_hours=cfg["staffing_alert_lead_hours"],
                                  transport_options=opts[L.LIST_TRANSPORT_MODE])
        e["theme_label"] = L.option_label(opts[L.LIST_THEME], e.get("theme_key"), fallback="")
        e["issues"] = ready["issues"]
        e["staff_count"] = len([s for s in staff_by.get(e["id"], []) if not s.get("is_backup")])
        if ready["issues"]:
            attention.append(e)
        if start and start >= now and e.get("status") not in (L.STATUS_CLOSED, L.STATUS_CANCELLED):
            upcoming.append(e)
        elif start and start < now:
            recent.append(e)
    recent.reverse()
    return {
        "upcoming": upcoming[:50], "needs_attention": attention[:50], "recent": recent[:50],
        "counts": {
            "upcoming": len(upcoming), "needs_attention": len(attention), "recent": len(recent),
            "pending_approval": sum(1 for e in events
                                    if e.get("approval_state") == L.APPROVAL_PENDING),
        },
        "config": cfg,
        "window": {"from": start_s, "to": end_s},
    }


# ══════════════════════════════════════════════════════════════════════════════════════════════
# The generic child CRUD handlers — REGISTERED LAST, DELIBERATELY
# ══════════════════════════════════════════════════════════════════════════════════════════════
# `/events/{event_id}/{collection}` is a CATCH-ALL: `{collection}` matches any single path segment,
# including "checkin", "checkout", "doc" and "apply-checklist-template". FastAPI matches routes in
# REGISTRATION ORDER, first match wins — so if these were declared above the literal-segment routes,
# every one of those would be swallowed here and answered with `404 unknown collection 'checkin'`.
# That is exactly what happened during this module's build: GPS check-in, check-out, document upload
# and apply-template were all shadowed and would have 404'd in production. Moving these three to the
# bottom is the fix.
#
# So: ANY new literal-segment route under /events/{event_id}/ must be added ABOVE this block.
# `harness_marketing_event.py` §M resolves each of those paths through the real router and fails the
# build if one of them ever reaches `create_child` again.

@router.post("/events/{event_id}/{collection}")
def create_child(event_id: str, collection: str, body: ChildIn,
                 authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                 org_id: str = ORG_ID):
    """Add one child row. The parent event is fetched org-scoped FIRST, which is what makes it
    impossible to attach a row to another tenant's event by guessing an id."""
    caller = _caller(authorization, x_active_org)
    _require_manager(caller, "edit an event's plan")
    table, _ = _child_spec(collection)
    event = _event(org_id, event_id)
    if (event.get("status") or L.STATUS_DRAFT) not in L.EDITABLE_STATUSES:
        raise HTTPException(400, "This event is %s — its plan can no longer be changed."
                            % event.get("status"))
    row = _child_payload(collection, body.data)
    row.update({"org_id": org_id, "event_id": event_id, "created_at": _now_iso()})
    if collection in _CHILD_STAMPED:
        row["created_by"] = _who(caller)
    try:
        r = sb().table(table).insert(row).execute()
    except Exception as e:
        raise HTTPException(400, "could not add the row (%s)" % str(e)[:160])
    return {"ok": True, "row": (r.data[0] if r.data else row)}


@router.patch("/events/{event_id}/{collection}/{row_id}")
def update_child(event_id: str, collection: str, row_id: str, body: ChildIn,
                 authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                 org_id: str = ORG_ID):
    """Update one child row. Scoped by org AND event AND row id together, so a row id from another
    event — or another tenant — matches nothing.

    Packing and returning stamp themselves: a checklist is only trustworthy if "who packed it" is
    recorded by the act, not typed by whoever is looking at the screen afterwards.
    """
    caller = _caller(authorization, x_active_org)
    _require_manager(caller, "edit an event's plan")
    table, _ = _child_spec(collection)
    event = _event(org_id, event_id)
    row = _child_payload(collection, body.data)
    if not row:
        raise HTTPException(400, "nothing to update")
    if collection == "checklist":
        if row.get("is_packed"):
            row.setdefault("packed_at", _now_iso())
            row["packed_by"] = _who(caller)
        elif "is_packed" in row:
            row["packed_at"], row["packed_by"] = None, None
        if row.get("is_returned"):
            row.setdefault("returned_at", _now_iso())
            row["returned_by"] = _who(caller)
        elif "is_returned" in row:
            row["returned_at"], row["returned_by"] = None, None
    if collection == "staff" and row.get("confirm_state") == L.CONFIRM_CONFIRMED:
        row.setdefault("confirmed_at", _now_iso())
    if collection in _CHILD_STAMPED:
        row["updated_at"] = _now_iso()
        row["updated_by"] = _who(caller)
    # The debrief-era exception: a CLOSED event may still record what came back and what was given
    # away, because those are counted after everyone gets home.
    if (event.get("status") or L.STATUS_DRAFT) not in L.EDITABLE_STATUSES \
            and collection not in ("checklist", "giveaways"):
        raise HTTPException(400, "This event is %s — its plan can no longer be changed."
                            % event.get("status"))
    try:
        sb().table(table).update(row) \
            .eq("org_id", org_id).eq("event_id", event_id).eq("id", row_id).execute()
    except Exception as e:
        raise HTTPException(400, "could not update the row (%s)" % str(e)[:160])
    return {"ok": True}


@router.delete("/events/{event_id}/{collection}/{row_id}")
def delete_child(event_id: str, collection: str, row_id: str,
                 authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                 org_id: str = ORG_ID):
    caller = _caller(authorization, x_active_org)
    _require_manager(caller, "edit an event's plan")
    table, _ = _child_spec(collection)
    event = _event(org_id, event_id)
    if (event.get("status") or L.STATUS_DRAFT) not in L.EDITABLE_STATUSES:
        raise HTTPException(400, "This event is %s — its plan can no longer be changed."
                            % event.get("status"))
    try:
        sb().table(table).delete() \
            .eq("org_id", org_id).eq("event_id", event_id).eq("id", row_id).execute()
    except Exception as e:
        raise HTTPException(400, "could not remove the row (%s)" % str(e)[:160])
    return {"ok": True}


# Attention providers register themselves on import (the storevisit precedent: a guarded
# bottom-of-file import, no main.py change, no core edit).
try:                                              # pragma: no cover - registration side effect
    from app.modules.marketing import attention_providers as _mkt_attention   # noqa: F401
except Exception:
    pass
