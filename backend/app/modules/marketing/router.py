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
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from app.core.database import get_supabase
from app.core.schemas import LaxModel
from app.modules.core.entitlements import require_module
from app.modules.core import geo
from app.modules.marketing import actuals as A
from app.modules.marketing import event_logic as L
from app.modules.marketing import event_sales as ES
from app.modules.commcalc import sales_register as _reg

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
    # Sales from Events (mig 995) — RULE TWO: the event REGISTER value, which classifier buckets the
    # headline activation number counts, and the retention windows are all DATA. Another carrier's
    # event register is an edit here, never a deploy ("the others not sure yet but provision will be
    # made" — owner 2026-09-09).
    event_sales_registers: Any = None
    event_sales_activation_classes: Any = None
    event_retention_windows_days: Any = None
    event_roi_phone_cost_from_catalog: Any = None


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
            "defaults": dict(L.DEFAULT_CONFIG),
            # The Sales-from-Events settings ride the SAME row and the SAME screen — one config
            # surface for the module, not a second settings page nobody finds.
            "event_sales": ES.resolve_event_sales_config(raw),
            "event_sales_defaults": dict(ES.DEFAULT_EVENT_SALES_CONFIG),
            "activation_classes": {c: ES.CLASS_LABELS[c] for c in ES.ACTIVATION_CLASSES}}


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
    # Sales-from-Events settings (mig 995). Validated through the PURE resolver so a typo cannot be
    # stored: an unknown activation bucket is dropped rather than saved to count nothing in silence,
    # and clearing the register list is refused for the same reason `is_event_register` refuses to
    # treat an empty list as "everything" — an org with no event register has no event sales.
    if body.event_sales_registers is not None:
        regs = _reg.normalize_registers(ES._as_list(body.event_sales_registers) or [])
        if not regs:
            raise HTTPException(400, "at least one event register is required — an empty list would "
                                     "mean every sale is an event sale, or none is")
        row["event_sales_registers"] = list(regs)
    if body.event_sales_activation_classes is not None:
        picked = [c for c in (str(x).strip().lower()
                              for x in (ES._as_list(body.event_sales_activation_classes) or []))
                  if c in ES.ACTIVATION_CLASSES]
        if not picked:
            raise HTTPException(400, "activation classes must be one or more of %s"
                                % (ES.ACTIVATION_CLASSES,))
        row["event_sales_activation_classes"] = picked
    if body.event_retention_windows_days is not None:
        row["event_retention_windows_days"] = ES._int_list(
            body.event_retention_windows_days,
            ES.DEFAULT_EVENT_SALES_CONFIG["event_retention_windows_days"])
    if body.event_roi_phone_cost_from_catalog is not None:
        row["event_roi_phone_cost_from_catalog"] = bool(body.event_roi_phone_cost_from_catalog)
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
# SALES FROM EVENTS — the three reports (owner directive 2026-09-09). See event_sales.py's header.
# ══════════════════════════════════════════════════════════════════════════════════════════════
# HTTP + I/O only. Every decision — what an activation is, what retention's three states are, what
# an unknown cost is allowed to look like — lives in the pure module and is proved DB-free by
# `backend/harness_marketing_event_sales.py`.
#
# Declared ABOVE the `/events/{event_id}/{collection}` catch-all like every other literal route in
# this file. These live under /event-sales/, so they could not be swallowed anyway — but the rule in
# this module is positional, and a future rename must not be the thing that discovers the exception.
_ES_MAX_ROWS = 200000
_ES_DEFAULT_MONTHS = 12


def _es_config(org_id: str) -> dict:
    """The event-sales config: the org's `marketing_config` row over the house defaults. ADAPTIVE —
    a database without migration 995 yields the house defaults rather than an error."""
    return ES.resolve_event_sales_config(_config_row(org_id))


def _es_range(date_from: str, date_to: str):
    """The calendar window the reports read. Defaults to the last 12 months ending today — long
    enough to cover a season of events without asking a manager to pick dates before seeing anything.
    """
    hi = str(date_to or "").strip()[:10] or _now().date().isoformat()
    lo = str(date_from or "").strip()[:10]
    if not lo:
        y, m = int(hi[:4]), int(hi[5:7])
        m -= _ES_DEFAULT_MONTHS
        while m <= 0:
            m += 12
            y -= 1
        lo = "%04d-%02d-01" % (y, m)
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _es_sales_rows(org_id: str, lo: str, hi: str, cfg: dict, stores=()):
    """The event-register sale rows for the window → `(rows, registers_seen, note)`.

    Reads `commcalc.raw_sales` with `select("*")` deliberately: report 1 is "all available fields",
    and a column added to raw_sales tomorrow must appear here without a code change. The register
    filter is applied SERVER-side so a period holding 200k sales does not travel to answer a question
    about 602 rows; the second, bounded read of the window's registers is what lets an empty result
    say WHICH registers the period does carry instead of rendering an empty table with no reason.
    """
    sc = get_supabase().schema("commcalc")
    regs = list(cfg.get("event_sales_registers") or ())
    if not regs:
        return [], {}, None
    try:
        rows = (sc.table("raw_sales").select("*").eq("org_id", org_id)
                .gte("trans_date", lo).lte("trans_date", hi).in_("register", regs)
                .limit(_ES_MAX_ROWS).execute().data) or []
    except Exception as e:
        return [], {}, ("The sales rows could not be read (%s)." % str(e)[:140])
    picked = {str(s).strip().upper() for s in (stores or []) if str(s or "").strip()}
    if picked:
        rows = [r for r in rows if str(r.get("store") or "").strip().upper() in picked]
    try:
        probe = (sc.table("raw_sales").select("register").eq("org_id", org_id)
                 .gte("trans_date", lo).lte("trans_date", hi).limit(5000).execute().data) or []
        seen = _reg.registers_present(probe)
    except Exception:
        seen = {}
    return rows, seen, None


def _es_shared_counts(org_id: str, rows):
    """The activation counts for these very rows, from THE shared per-(store, rep, day) sales pass.

    `_compute_feed_actuals_py(..., rows=<pre-built>)` is the documented seam for exactly this: the
    caller supplies the row set, the shared pass supplies every classification, the distinct-`trans_id`
    counting rule, the void/return skips and the canonical store-code resolution. Filtering by
    register BEFORE the pass is a ROW FILTER, not a second derivation — which is why this report's
    activation numbers cannot drift from the Sales Report's.
    """
    if not rows:
        return [], {}, None
    try:
        from app.modules.commcalc.router import _compute_feed_actuals_py
    except Exception as e:                                    # pragma: no cover - import environment
        return [], {}, ("The shared sales aggregation could not be loaded (%s), so only the "
                        "row-level classification is shown." % str(e)[:120])
    try:
        cells = _compute_feed_actuals_py(get_supabase(), org_id, "", rows=rows) or []
    except Exception as e:
        return [], {}, ("The shared sales aggregation failed (%s)." % str(e)[:140])
    # THE JOIN KEY IS THE STORE CODE, NOT THE STORE STRING. The shared pass returns `store` as its
    # own CANONICAL grouping key (e.g. 'b-652'), not the POS spelling on the sale row ('652
    # Communipaw Avenue') — joining on the raw string silently matches nothing and every count reads
    # empty. The raw spelling is resolved to a code through the SAME canonical resolver the shared
    # pass itself uses (`_store_code_resolver`, §13a), never a second store-matching rule.
    try:
        from app.modules.commcalc.router import _store_code_resolver
        resolve = _store_code_resolver(get_supabase(), org_id)
    except Exception:                                         # pragma: no cover - import environment
        resolve = None
    code_by_store = {}
    for r in rows:
        raw = str(r.get("store") or "").strip()
        if raw and raw not in code_by_store and resolve:
            try:
                code_by_store[raw] = resolve(raw)
            except Exception:
                continue
    return cells, code_by_store, None


def _es_cells_by_key(cells):
    """The shared pass's per-(store CODE, day) totals — the numbers this report headlines."""
    out = {}
    for c in (cells or []):
        k = (str(c.get("store_code") or ""), str(c.get("trans_date") or "")[:10])
        s = out.setdefault(k, {"prem_count": 0, "byod_count": 0, "upg_count": 0, "box_count": 0,
                               "billpay_count": 0, "acc_gp": 0.0, "setup_fee": 0.0,
                               "store_code": c.get("store_code")})
        for f in ("prem_count", "byod_count", "upg_count", "box_count", "billpay_count"):
            s[f] += int(c.get(f) or 0)
        for f in ("acc_gp", "setup_fee"):
            s[f] = round(s[f] + float(c.get(f) or 0), 2)
    return out


@router.get("/event-sales")
def event_sales_report(date_from: str = "", date_to: str = "", store: str = "",
                       org_id: str = ORG_ID):
    """REPORT 1 — total sales rung on the event register, with every field the rows carry.

    The `attribution` block is not decoration: it states that this is what the event TILL rang, which
    is not the same as what the event caused, and it carries the register-not-tender correction.
    """
    cfg = _es_config(org_id)
    lo, hi = _es_range(date_from, date_to)
    stores = [s for s in (store or "").split(",") if s.strip()]
    rows, seen, note = _es_sales_rows(org_id, lo, hi, cfg, stores)
    cells, code_by_store, cell_note = _es_shared_counts(org_id, rows)
    summary = ES.sales_summary(rows, cfg)
    by_key = _es_cells_by_key(cells)
    for k in summary["event_keys"]:
        k["store_code"] = code_by_store.get(k["store"])
        c = by_key.get((str(k["store_code"] or ""), k["trans_date"])) or {}
        k["shared_pass"] = {f: v for f, v in c.items() if f != "store_code"}
    return {
        "window": {"from": lo, "to": hi},
        "config": cfg,
        "summary": summary,
        "rows": ES.annotate_rows(rows, cfg),
        "shared_pass_cells": cells,
        "empty_reason": (ES.no_register_note(cfg, seen) if not rows else None),
        "attribution": ES.attribution(cfg, seen, source_note=(note or cell_note)),
    }


# ── report 2: SUBSCRIBER retention (NOT the GDPR check-in retention on /checkin-retention) ────────
def _es_period_labels(lo: str, hi: str):
    """{'YYYY-MM': 'Month YYYY'} for every month a retention window can land in."""
    from datetime import date as _d
    out, guard = {}, 0
    y, m = int(lo[:4]), int(lo[5:7])
    ey, em = int(hi[:4]), int(hi[5:7])
    while (y, m) <= (ey, em) and guard < 120:
        out["%04d-%02d" % (y, m)] = _d(y, m, 1).strftime("%B %Y")
        m += 1
        if m > 12:
            m, y = 1, y + 1
        guard += 1
    return out


def _es_mi_snapshots(org_id: str, period_labels: dict, lines):
    """{'YYYY-MM': {'index': …, 'loaded': bool}} — the subscriber feed, per month, for THESE lines.

    Two reads per month, both org-scoped and both bounded:
      • a ONE-ROW probe answering "was this month's feed ever loaded" — the difference between "the
        line is gone" and "we never looked", which is the whole point of the third retention state;
      • the rows for the event's own numbers only, so a 42,000-row month does not travel to answer a
        question about 125 of them.
    The index is built by `sale_installment_engine._mi_index` — the commission paid gate's OWN index
    — so the key this report matches on and the key the money path matches on are the same key.
    """
    from app.modules.commcalc.sale_installment_engine import _mi_index
    from app.modules.commcalc.router import _pvariants
    sc = get_supabase().schema("commcalc")
    mdns = sorted({str(x.get("mdn") or "") for x in (lines or []) if x.get("mdn")})
    serials = sorted({str(x.get("serial_1") or "") for x in (lines or []) if x.get("serial_1")})
    cols = ("phone_number,subscriber_id,subscriber_status,mi_activation_date,"
            "mi_deactivation_date,device_serial")
    out = {}
    for key, label in sorted(period_labels.items()):
        variants = _pvariants(label)
        try:
            probe = (sc.table("raw_mi").select("id").eq("org_id", org_id)
                     .in_("period", variants).limit(1).execute().data) or []
            loaded = bool(probe)
        except Exception:
            loaded = False
        rows = []
        if loaded:
            for field, values in (("phone_number", mdns), ("device_serial", serials)):
                for i in range(0, len(values), 150):
                    chunk = values[i:i + 150]
                    if not chunk:
                        continue
                    try:
                        rows += (sc.table("raw_mi").select(cols).eq("org_id", org_id)
                                 .in_("period", variants).in_(field, chunk)
                                 .limit(5000).execute().data) or []
                    except Exception:
                        continue
        out[key] = {"loaded": loaded, "index": _mi_index(rows), "rows": len(rows), "label": label}
    return out


@router.get("/event-sales/subscriber-retention")
def event_sales_subscriber_retention(date_from: str = "", date_to: str = "", store: str = "",
                                     org_id: str = ORG_ID):
    """REPORT 2 — do the lines activated at the event STAY?

    ⚠ This is SUBSCRIBER retention. `GET /marketing/checkin-retention` is the GDPR purge schedule for
    staff check-in GPS rows and is an entirely different thing; the two share nothing but the word.

    THREE STATES, NOT TWO: active, churned, and could-not-be-matched. The third never enters a
    retention percentage's denominator and is never rendered as a loss.
    """
    cfg = _es_config(org_id)
    lo, hi = _es_range(date_from, date_to)
    stores = [s for s in (store or "").split(",") if s.strip()]
    rows, seen, note = _es_sales_rows(org_id, lo, hi, cfg, stores)
    lines = ES.activation_lines(rows, cfg)
    windows = list(cfg.get("event_retention_windows_days") or ())

    hi_scan = hi
    if lines and windows:
        try:
            last = max(str(x["trans_date"])[:10] for x in lines)
            hi_scan = max(hi, (datetime.fromisoformat(last).date()
                               + timedelta(days=max(windows))).isoformat())
        except Exception:
            hi_scan = hi
    labels = _es_period_labels(lo, min(hi_scan, _now().date().isoformat()))
    snapshots = {}
    if lines:
        try:
            snapshots = _es_mi_snapshots(org_id, labels, lines)
        except Exception as e:
            note = ((note or "") + " The subscriber feed could not be read (%s)."
                    % str(e)[:120]).strip()
    loaded_keys = sorted(k for k, v in snapshots.items() if v.get("loaded"))
    latest = loaded_keys[-1] if loaded_keys else None
    report = ES.retention_report(lines, snapshots, labels, windows, latest_key=latest)
    report.update({
        "window": {"from": lo, "to": hi},
        "config": cfg,
        "feed_coverage": [{"period": k, "label": labels.get(k), "loaded": v.get("loaded"),
                           "matched_rows": v.get("rows", 0)} for k, v in sorted(snapshots.items())],
        "latest_loaded_period": (labels.get(latest) if latest else None),
        "empty_reason": (ES.no_register_note(cfg, seen) if not rows else
                         (None if lines else
                          "No line rung on the event register in this window classifies as an "
                          "activation, so there is nothing whose retention could be measured.")),
        "attribution": ES.attribution(cfg, seen, source_note=note),
    })
    return report


# ── report 3: ROI ─────────────────────────────────────────────────────────────────────────────────
def _es_events_for(org_id: str, lo: str, hi: str):
    """The org's events whose window could overlap [lo, hi], with their store sets. ONE read of the
    EXISTING event tables — no parallel event store, no second store-attribution path."""
    try:
        events = (sb().table(EVENT_TABLE).select("*").eq("org_id", org_id).eq("is_active", True)
                  .gte("event_start", "%sT00:00:00+00:00" % lo)
                  .lte("event_start", "%sT23:59:59+00:00" % hi)
                  .limit(2000).execute().data) or []
    except Exception:
        return [], []
    ids = [e["id"] for e in events if e.get("id")]
    stores = []
    if ids:
        try:
            stores = (sb().table("marketing_event_store").select("event_id,store_code")
                      .eq("org_id", org_id).in_("event_id", ids).limit(10000).execute().data) or []
        except Exception:
            stores = []
    return events, stores


def _es_entered_costs(org_id: str, event_ids):
    """The cost figures a human typed, per event (`core.marketing_event_cost`, migration 995).

    Absent table ⇒ empty, never an error: the ROI report must still run on a database where 995 has
    not been applied — it simply has nothing entered to read.
    """
    amounts, units = {}, {}
    ids = [i for i in (event_ids or []) if i]
    if not ids:
        return amounts, units
    try:
        rows = (sb().table("marketing_event_cost")
                .select("event_id,cost_kind,amount,unit_cost,product_ref,entered_by,entered_at")
                .eq("org_id", org_id).in_("event_id", ids)
                .order("entered_at", desc=False).limit(5000).execute().data) or []
    except Exception:
        return amounts, units
    for r in rows:
        ev = str(r.get("event_id") or "")
        if r.get("product_ref") and r.get("unit_cost") is not None:
            units.setdefault(ev, {})[str(r["product_ref"]).upper()] = r["unit_cost"]
        elif r.get("amount") is not None:
            amounts.setdefault(ev, {})[str(r.get("cost_kind") or "")] = r["amount"]
    return amounts, units


def _es_catalog(org_id: str, phones):
    """`commcalc.raw_catalog` costs for the SKUs these phones carry — the owner's "sku report"."""
    pids = sorted({p["product_id"] for p in (phones or []) if p.get("product_id")})
    skus = sorted({p["sku"] for p in (phones or []) if p.get("sku")})
    sc = get_supabase().schema("commcalc")
    rows = []
    for field, values in (("product_id", pids), ("sku", skus)):
        for i in range(0, len(values), 150):
            chunk = values[i:i + 150]
            if not chunk:
                continue
            try:
                rows += (sc.table("raw_catalog").select("product_id,product_desc,cost,sku")
                         .eq("org_id", org_id).in_(field, chunk).limit(5000).execute().data) or []
            except Exception:
                continue
    return ES.catalog_index(rows)


def _es_event_payroll(org_id: str, event, day: str):
    """Event payroll HOURS at (employee, day) grain, through the EXISTING three-state contract.

    `salary_expense.day_measurement` decides the state and the measured hours; a NOT_MEASURED day
    falls back to that day's SCHEDULED hours, and a MEASURED ZERO stays zero and never falls back.
    Nothing about hours is re-decided here — this asks that contract's question for one day and one
    roster, which is why no third payroll derivation exists.
    """
    if not event or not event.get("id"):
        return None
    try:
        from app.modules.storeops import salary_expense as _salexp
    except Exception:                                         # pragma: no cover - import environment
        return None
    try:
        staff = (sb().table("marketing_event_staff").select("employee_id,employee_name")
                 .eq("org_id", org_id).eq("event_id", event["id"]).limit(200).execute().data) or []
    except Exception:
        staff = []
    ids = sorted({str(s.get("employee_id")) for s in staff if s.get("employee_id")})
    if not ids:
        return {"rows": [], "total": 0.0, "hours_measured": 0.0, "hours_scheduled": 0.0,
                "unpriced": [], "no_staff": True}
    so = get_supabase().schema("storeops")
    try:
        shifts = (so.table("shifts")
                  .select("employee_id,store_code,shift_date,scheduled_hours,actual_hours")
                  .eq("org_id", org_id).eq("is_deleted", False).eq("shift_date", day)
                  .in_("employee_id", ids).limit(2000).execute().data) or []
    except Exception:
        shifts = []
    try:
        tl = (so.table("timelog").select("employee_id,hours,clock_out,work_date,store_code")
              .eq("org_id", org_id).eq("work_date", day).in_("employee_id", ids)
              .limit(2000).execute().data) or []
    except Exception:
        tl = []
    try:
        emps = (so.table("employees").select("employee_id,name,pay_rate")
                .eq("org_id", org_id).in_("employee_id", ids).limit(2000).execute().data) or []
    except Exception:
        emps = []
    rates = {str(e["employee_id"]): e.get("pay_rate") for e in emps if e.get("employee_id")}
    names = {str(e["employee_id"]): (e.get("name") or "") for e in emps if e.get("employee_id")}

    manual_by_day, punch_by_day, sched = {}, {}, {}
    for s in shifts:
        eid = str(s.get("employee_id"))
        if float(s.get("actual_hours") or 0) > 0:
            manual_by_day.setdefault(eid, {})[day] = float(s.get("actual_hours") or 0)
        sched[eid] = sched.get(eid, 0.0) + float(s.get("scheduled_hours") or 0)
    for t in tl:
        if t.get("clock_out") and t.get("hours") is not None:
            punch_by_day.setdefault(str(t.get("employee_id")), {})[day] = float(t.get("hours") or 0)

    hours = []
    for eid in ids:
        state, measured = _salexp.day_measurement(eid, day, manual_by_day, punch_by_day)
        if state == _salexp.NOT_MEASURED:
            h, st = sched.get(eid, 0.0), "scheduled"
            if not h:
                continue          # nothing measured and nothing scheduled: no hours to cost at all
        else:
            h, st = measured, "measured"
        hours.append({"employee_id": eid, "employee_name": names.get(eid) or eid,
                      "day": day, "hours": h, "state": st})
    out = ES.payroll_from_hours(hours, rates)
    out["no_staff"] = not hours
    return out


def _es_f(v):
    """Float, blank-safe — byte-identical to `event_sales._f` / `gp_report.safe_float`."""
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _es_payment_categories(org_id: str):
    """{label: category} — the org's OWN `commcalc.payment_categories` config, which is what the
    platform's one commission-received read (`commission_received.add_label_rows`, gated on
    `category == 'Commission'`) already classifies ePay labels with. Read, never re-derived: no
    keyword list lives in this module, so bounties/SPIFFs count and promos/reimbursements do not
    because a CONFIG ROW says so (RULE TWO). A label with no row classifies as unclassified and is
    reported as such — never guessed into or out of the commission figure.
    ORG-SCOPED BY HAND (this file is outside the CI org-scope guard's scan of commcalc/router.py).
    """
    try:
        rows = (get_supabase().schema("commcalc").table("payment_categories")
                .select("description,category").eq("org_id", org_id)
                .limit(5000).execute().data) or []
    except Exception:
        return {}
    return {str(r.get("description") or "").strip(): str(r.get("category") or "").strip()
            for r in rows if str(r.get("description") or "").strip()}


def _es_chunks(values, n=150):
    vals = [v for v in sorted(set(values or ())) if v]
    return [vals[i:i + n] for i in range(0, len(vals), n)]


def _es_commission_events(org_id: str, lines, cfg=None):
    """READ the per-line commission feeds for THESE numbers/IMEIs — every period, so the figure is
    paid-to-date rather than paid-in-the-activation-month.

    (events, feeds_loaded, periods_read, notes). One shape-blind list of `ES.commission_event`s; the
    pure code does the matching, the dedup and the three states.

    Bounded by construction: every read is filtered to the event's own keys in chunks of 150, so the
    278k-row payment feed never travels to answer a question about a few dozen numbers. Each shape is
    probed with a ONE-ROW existence query first — "this org does not receive this feed" and "this
    org receives it and nothing was paid" are different facts and only the second is a zero.

    ORG-SCOPED BY HAND: every query below carries `.eq('org_id', org_id)`. This file is NOT covered
    by the CI org-scope guard (which scans commcalc/router.py only), so the scoping is asserted in
    the harness's static guard instead.
    """
    sc = get_supabase().schema("commcalc")
    # Which shapes this org reads: the config subset (mig 999) or, by default, every shape that has
    # rows. A shape the org does not receive yields nothing and is REPORTED as not loaded — never as
    # zero commission earned.
    want = (cfg or {}).get("event_roi_commission_feed_shapes") or list(ES.COMMISSION_LINE_FEED_SHAPES)
    mdns = sorted({str(x.get("mdn") or "") for x in (lines or []) if x.get("mdn")})
    imeis = sorted({str(x.get("serial_1") or "") for x in (lines or []) if x.get("serial_1")})
    events, loaded, periods, notes = [], [], set(), []
    if not mdns and not imeis:
        return events, loaded, [], notes

    def _probe(table):
        try:
            return bool((sc.table(table).select("id").eq("org_id", org_id)
                         .limit(1).execute().data) or [])
        except Exception:
            return False

    def _read(table, cols, field, values, cap=20000):
        out = []
        for chunk in _es_chunks(values):
            try:
                out += (sc.table(table).select(cols).eq("org_id", org_id)
                        .in_(field, chunk).limit(cap).execute().data) or []
            except Exception:
                continue
        return out

    # ── shape 1: the per-payment-row feed, keyed by number AND by IMEI ──────────────────────────
    shape = "payment_detail_lines"
    if shape in want and _probe("raw_payment_detail"):
        loaded.append(shape)
        cat_of = _es_payment_categories(org_id)
        cols = "id,mdn,imei,payment_type,amount,period,payment_date"
        rows = _read("raw_payment_detail", cols, "mdn", mdns) + \
            _read("raw_payment_detail", cols, "imei", imeis)
        seen = set()
        for r in rows:
            rid = str(r.get("id"))
            if rid in seen:
                continue
            seen.add(rid)
            label = str(r.get("payment_type") or "").strip()
            periods.add(str(r.get("period") or ""))
            events.append(ES.commission_event(
                shape, rid, mdn=r.get("mdn"), imei=r.get("imei"), amount=r.get("amount"),
                label=label, category=cat_of.get(label, ""), period=r.get("period"),
                paid_on=r.get("payment_date"), stream=ES.STREAM_COMMISSION))

    # ── shape 2: the per-subscriber residual feed ───────────────────────────────────────────────
    shape = "subscriber_residual"
    if shape in want and _probe("raw_mi"):
        loaded.append(shape)
        cols = "id,phone_number,device_serial,actual_mi_payout,actual_atu_payout,period"
        rows = _read("raw_mi", cols, "phone_number", mdns) + \
            _read("raw_mi", cols, "device_serial", imeis)
        seen = set()
        for r in rows:
            rid = str(r.get("id"))
            if rid in seen:
                continue
            seen.add(rid)
            periods.add(str(r.get("period") or ""))
            amt = _es_f(r.get("actual_mi_payout")) + _es_f(r.get("actual_atu_payout"))
            # Emitted even at 0.00 — a zero-payout subscriber row is what proves the line WAS
            # matched and simply has not earned yet (the second state), rather than being unlookable.
            events.append(ES.commission_event(
                shape, rid, mdn=r.get("phone_number"), imei=r.get("device_serial"), amount=amt,
                label="", category=ES.CATEGORY_COMMISSION, period=r.get("period"),
                stream=ES.STREAM_RESIDUAL))

    # ── shape 3: the per-IMEI master-agent feed ─────────────────────────────────────────────────
    shape = "master_agent_lines"
    if shape in want and imeis and _probe("raw_ma_commission"):
        loaded.append(shape)
        comps, sign_cfg = _es_ma_components(org_id)
        cols = ("id,imei,period," + ",".join(comps)) if comps else "id,imei,period"
        rows = _read("raw_ma_commission", cols, "imei", imeis)
        for r in rows:
            periods.add(str(r.get("period") or ""))
            events.append(ES.commission_event(
                shape, str(r.get("id")), imei=r.get("imei"),
                amount=_es_ma_amount(r, comps, sign_cfg), label="",
                category=ES.CATEGORY_COMMISSION, period=r.get("period"),
                stream=ES.STREAM_COMMISSION))

    if not loaded:
        notes.append("No per-line commission feed is loaded for this organisation, so no commission "
                     "could be traced to any number. Nothing here is reported as $0.00 earned.")
    return events, loaded, sorted(p for p in periods if p), notes


def _es_ma_components(org_id: str):
    """The master-agent money columns to sum, MINUS the rebate component.

    The column list is `account.residual_subs._MA_COMPONENTS` — the SAME list the residual and
    commission-received surfaces build their totals from, so this cannot drift into summing an
    identifier column (the guarded mistake mig 083 documents). `rebate` is dropped because a rebate is
    money back on a purchase, not commission earned: the rule
    `ma_store_pnl.commission_received_lines()` states for the P&L, applied here per line
    (owner 2026-09-08 "dont count any rebate received in the commission").
    """
    try:
        from app.modules.account.residual_subs import _MA_COMPONENTS
        from app.modules.commcalc.commission_legs import for_org
        comps = [c for c in _MA_COMPONENTS if c != "rebate"]
        return comps, for_org(get_supabase(), org_id)
    except Exception:
        return [], None


def _es_ma_amount(row, comps, legcls):
    """One master-agent row's commission, through `commission_legs.split_ma_components` so the sign
    convention is the org's configured one and this agrees with every other MA money surface."""
    if not comps:
        return 0.0
    try:
        from app.modules.commcalc.commission_legs import split_ma_components
        sums = {c: row.get(c) for c in comps}
        return float((split_ma_components(sums, comps,
                                          getattr(legcls, "cfg", None)) or {}).get("total") or 0.0)
    except Exception:
        return 0.0


def _es_commission(org_id: str, period_label: str, store_raw: str):
    """Commission received for ONE store in ONE month — READ, never recomputed.

    Calls `commcalc.router.commission_received_breakout`, the platform's ONE commission-received
    read. It honours `ma_store_pnl.commission_received_lines()`'s rule that a REBATE IS NOT COMMISSION
    (owner 2026-09-08 "dont count any rebate received in the commission"), and its own payload states
    that carrier money carrying no store address is EXCLUDED while a store filter is active. Computing
    commission is the commission agent's territory; this is a reader and nothing else.

    Comprehensive Comp is deliberately NOT added: that report shows it beside the commission total,
    never inside it, and this reader keeps that separation.
    """
    try:
        from app.modules.commcalc.router import commission_received_breakout
    except Exception as e:                                    # pragma: no cover - import environment
        return None, "The commission read could not be loaded (%s)." % str(e)[:120]
    try:
        out = commission_received_breakout(period=period_label, months=1, market="",
                                           store=store_raw, org_id=org_id) or {}
    except Exception as e:
        return None, "The commission figure could not be read (%s)." % str(e)[:140]
    slot = (out.get("totals_by_period") or {}).get(period_label)
    if slot is None:
        return None, "No commission was reported for %s at this store." % period_label
    total = float(slot.get("commission") or 0.0) + float(slot.get("residual") or 0.0)
    return round(total, 2), (list(out.get("notes") or []) or [None])[0]


@router.get("/event-sales/roi")
def event_sales_roi(date_from: str = "", date_to: str = "", store: str = "", trans_date: str = "",
                    org_id: str = ORG_ID):
    """REPORT 3 — ROI per event day: commission received against what the day cost.

    THE EVENT-NOT-LOADED FLOW IS THE NORMAL PATH. `core.marketing_event` holds no rows today, so most
    (store, date) pairs have no event record. The report still runs, says that no event covers the
    day, derives everything it can and names exactly what a human has to supply. Nothing is ever
    rendered as $0.00 to make a number appear, and the ROI itself is withheld while a cost is unknown.
    """
    cfg = _es_config(org_id)
    lo, hi = _es_range(date_from, date_to)
    stores = [s for s in (store or "").split(",") if s.strip()]
    rows, seen, note = _es_sales_rows(org_id, lo, hi, cfg, stores)
    if str(trans_date or "").strip():
        want = str(trans_date).strip()[:10]
        rows = [r for r in rows if str(r.get("trans_date") or "")[:10] == want]
    cells, code_by_store, cell_note = _es_shared_counts(org_id, rows)
    by_key = _es_cells_by_key(cells)
    summary = ES.sales_summary(rows, cfg)
    events, event_stores = _es_events_for(org_id, lo, hi)
    entered_by_event, units_by_event = _es_entered_costs(
        org_id, [e.get("id") for e in events])
    labels = _es_period_labels(lo, hi)
    month_totals = {}

    # ── THE PRIMARY COMMISSION BASIS, read ONCE for the whole window ────────────────────────────
    # One activation line per number (`activation_lines` already de-duplicates the device/SIM/fee
    # lines of the same activation), one bounded read of the per-line commission feeds for those
    # numbers, one index. The per-day match then costs nothing and every day in the window is
    # measured against the same as-of instant.
    all_lines = ES.activation_lines(rows, cfg)
    comm_events, feeds_loaded, periods_read, comm_feed_notes = _es_commission_events(
        org_id, all_lines, cfg)
    comm_index = ES.index_commission_events(comm_events)
    as_of = _now().date().isoformat()

    out, notes = [], [n for n in (note, cell_note) if n] + list(comm_feed_notes)
    for key in summary["event_keys"]:
        s_raw, day = key["store"], key["trans_date"]
        code = code_by_store.get(s_raw) or s_raw
        krows = [r for r in rows if str(r.get("store") or "") == s_raw
                 and str(r.get("trans_date") or "")[:10] == day]
        ev = ES.match_event(events, event_stores, code, day, store_aliases=[s_raw])
        ev_id = str((ev or {}).get("id") or "")

        lines = ES.phone_lines(krows, cfg)
        phones = ES.price_phones(lines, _es_catalog(org_id, lines),
                                 entered_unit_costs=units_by_event.get(ev_id),
                                 use_catalog=bool(cfg.get("event_roi_phone_cost_from_catalog")))
        payroll = _es_event_payroll(org_id, ev, day) if ev else None
        costs = ES.build_costs(ev, entered_by_event.get(ev_id) or {}, payroll, phones)

        period_label = labels.get(day[:7]) or day[:7]
        cell = by_key.get((str(code or ""), day)) or {}
        # NAMED `day_register_activations`, not "event activations": §23's attribution rule (enforced
        # statically by harness_marketing_event.py §G) forbids a field name that claims the event
        # CAUSED a sale. This is what the event register rang that day — a fact about the till.
        day_acts = int(cell.get("prem_count") or 0) + int(cell.get("byod_count") or 0)

        # ── THE HEADLINE: commission actually paid on THIS day's numbers/IMEIs (owner 2026-09-09).
        day_lines = [ln for ln in all_lines
                     if ln.get("store") == s_raw and ln.get("trans_date") == day]
        per_number = ES.paid_against_lines(day_lines, comm_index, as_of=as_of,
                                           periods_read=periods_read, feeds_loaded=feeds_loaded)

        # ── THE FALLBACK, and ONLY for the lines the per-number basis could not match. The store
        # month is not read at all when every line matched — the allocation is no longer on the
        # happy path, it is the estimate of last resort for named lines.
        comm_note, fallback = None, None
        if per_number["states"][ES.PAID_STATE_UNMATCHABLE]:
            store_month_comm, comm_note = _es_commission(org_id, period_label, s_raw)
            mk = (code, day[:7])
            if mk not in month_totals:
                month_totals[mk] = _es_store_month_activations(org_id, code, day[:7])
            fallback = ES.commission_fallback(per_number, store_month_comm, month_totals[mk])

        fb_amount = (fallback or {}).get("amount")
        comm = round(float(per_number["total"]) + float(fb_amount or 0.0), 2)
        # Nothing matched AND nothing to estimate from ⇒ the commission is UNKNOWN, not zero, and
        # `roi_compute` withholds the ROI rather than reporting a return on money nobody measured.
        if not per_number["matched_lines"] and fb_amount is None:
            comm = None
        exact = bool(per_number["exact"]) and fallback is None
        roi = ES.roi_compute(comm, costs, ES.COMMISSION_BASIS_PER_NUMBER, commission_exact=exact)
        roi.update({
            "store": s_raw, "store_code": code, "trans_date": day,
            "event": ({"id": ev.get("id"), "title": ev.get("title"), "status": ev.get("status"),
                       "planned_spend": ev.get("planned_spend")} if ev else None),
            "event_linked": bool(ev),
            "event_prompt": (None if ev else ES.minimal_event_payload(code, day)),
            "sales": key, "phones": phones, "payroll": payroll,
            "commission_per_number": per_number,
            "commission_fallback": fallback,
            "day_register_activations": day_acts,
            "commission_source_note": comm_note,
        })
        out.append(roi)
        if comm_note:
            notes.append(comm_note)

    return {
        "window": {"from": lo, "to": hi},
        "config": cfg,
        "days": out,
        "commission_basis": ES.COMMISSION_BASIS_PER_NUMBER,
        "commission_bases": dict(ES.COMMISSION_BASIS_NOTES),
        "commission_bases_available": [ES.COMMISSION_BASIS_PER_NUMBER,
                                       ES.COMMISSION_BASIS_ALLOCATED],
        "commission_as_of": as_of,
        "commission_periods_read": periods_read,
        "commission_feeds_loaded": feeds_loaded,
        "commission_feed_shapes": dict(ES.COMMISSION_LINE_FEED_SHAPES),
        "commission_basis_note": (
            "The headline is the commission ACTUALLY PAID against the numbers and IMEIs each day "
            "activated, summed per line — not a share of the store's month. It is a floor as of %s "
            "and it keeps growing: commission on a new line arrives for months, so every day here "
            "carries its own paid-to-date split by period. The store-month allocation survives only "
            "as a clearly-labelled fallback for lines that could not be matched, and any day that "
            "uses it is marked not exact." % as_of),
        "empty_reason": (ES.no_register_note(cfg, seen) if not rows else None),
        "notes": sorted({n for n in notes if n}),
        "attribution": ES.attribution(cfg, seen, source_note=(note or cell_note)),
    }


def _es_store_month_activations(org_id: str, store_code: str, month_key: str):
    """The store's countable activations for the WHOLE month, from THE shared pass — the denominator
    of the commission allocation. Same pass as the numerator, so the ratio is internally consistent.

    Keyed on the CANONICAL store code the shared pass emits, never the POS store string: the pass
    groups on its own canonical key, and joining on the raw spelling matches nothing.
    """
    try:
        from app.modules.commcalc.router import _compute_feed_actuals_py
    except Exception:                                         # pragma: no cover - import environment
        return 0
    try:
        cells = _compute_feed_actuals_py(get_supabase(), org_id, month_key) or []
    except Exception:
        return 0
    return sum(int(c.get("prem_count") or 0) + int(c.get("byod_count") or 0)
               for c in cells if str(c.get("store_code") or "") == str(store_code or ""))


class EventSalesLinkIn(LaxModel):
    """The ROI screen's "I will tell you what it cost" body. `event_id` links an EXISTING event;
    otherwise the minimum needed to CREATE one is accepted and handed to the existing creator."""
    store_code: Any = None
    trans_date: Any = None
    event_id: Any = None
    title: Any = None
    market: Any = None
    planned_spend: Any = None
    payroll_cost: Any = None
    phone_unit_costs: Any = None      # {product_id or sku: unit cost}
    note: Any = None


@router.post("/event-sales/roi/link-event")
def event_sales_link_event(body: EventSalesLinkIn, authorization: str = Header(default=""),
                           x_active_org: str = Header(default=""), org_id: str = ORG_ID):
    """Link a (store, date) to an event — or CREATE the event from the minimum needed to cost it.

    The owner: *"if teh event was not loaded previously it will still run a report with the roi and
    ask the user to input teh cost details or link it to the event created in the system if the user
    inputs teh details it will create the event in the system with the minimal information which is
    required to compute the cost"*.

    It creates through `create_event` — the module's ONE event creator, with its own approval
    decision, its own store-set write and its own permission check. No second creation path exists,
    and no parallel event table was invented to hold a cost.
    """
    caller = _caller(authorization, x_active_org)
    _require_manager(caller, "record what an event cost")
    code = str(body.store_code or "").strip()
    day = str(body.trans_date or "").strip()[:10]
    if not code or not day:
        raise HTTPException(400, "store_code and trans_date are required")

    entered = {}
    if body.planned_spend is not None and str(body.planned_spend) != "":
        entered[ES.COST_EVENT_SPEND] = L._num(body.planned_spend)
    if body.payroll_cost is not None and str(body.payroll_cost) != "":
        entered[ES.COST_PAYROLL] = L._num(body.payroll_cost)

    if body.event_id:
        event = _event(org_id, str(body.event_id))
        if entered.get(ES.COST_EVENT_SPEND) is not None:
            upd = {"planned_spend": entered[ES.COST_EVENT_SPEND], "updated_at": _now_iso(),
                   "updated_by": _who(caller)}
            decision = L.approval_decision(_config(org_id), upd["planned_spend"])
            upd["approval_state"], upd["approval_reason"] = decision["state"], decision["reason"]
            try:
                sb().table(EVENT_TABLE).update(upd) \
                    .eq("org_id", org_id).eq("id", event["id"]).execute()
            except Exception as e:
                raise HTTPException(400, "could not record the cost (%s)" % str(e)[:140])
        _set_event_stores(org_id, event["id"],
                          sorted(set(_event_store_codes(org_id, event["id"])) | {code}))
        created = False
    else:
        payload = ES.minimal_event_payload(code, day, title=(str(body.title or "").strip() or None),
                                           market=(str(body.market or "").strip() or None),
                                           planned_spend=entered.get(ES.COST_EVENT_SPEND))
        result = create_event(EventIn(**payload), authorization=authorization,
                              x_active_org=x_active_org, org_id=org_id)
        event = result["event"]
        created = True

    saved = _es_save_costs(org_id, event.get("id"), entered, body.phone_unit_costs, _who(caller),
                           str(body.note or "").strip() or None)
    return {"ok": True, "created": created, "event": event, "costs": saved,
            "note": ("The event now carries the day's cost. Re-run the ROI report and it will "
                     "compute — nothing was assumed on your behalf.")}


def _es_save_costs(org_id: str, event_id, entered, phone_unit_costs, who, note):
    """Persist the typed cost figures on `core.marketing_event_cost` (migration 995).

    ═══ THE GIVEAWAY BOUNDARY — DECIDED, IMPLEMENTED, AND SAID OUT LOUD ══════════════════════════
    `core.marketing_event_giveaway.unit_cost` already exists, and migration 986 says of it:
    *"INFORMATIONAL money … Never seeded, never read by a money path."* Feeding it into an ROI would
    reverse that decision silently, and would change what an existing column MEANS for every tenant
    that has already typed a number into it as a note-to-self.

    So it is NOT promoted. The ROI's phone cost lives on its own explicitly-declared row here, and
    the giveaway row keeps its contract untouched. What the ROI takes from the giveaway side is the
    COUNT (`qty_given`) — never money — and only ever beside the count the sales rows already give.
    The recommendation and its reasoning are registered in the index (§23s) so the owner can overrule
    it in exactly one place if they would rather promote the column.
    """
    if not event_id:
        return []
    rows = []
    for kind, amount in (entered or {}).items():
        if amount is None:
            continue
        rows.append({"org_id": org_id, "event_id": event_id, "cost_kind": kind,
                     "amount": float(amount), "basis": ES.BASIS_ENTERED, "note": note,
                     "entered_by": who, "entered_at": _now_iso()})
    for key, unit in (phone_unit_costs or {}).items():
        n = L._num(unit)
        if n is None:
            continue
        rows.append({"org_id": org_id, "event_id": event_id, "cost_kind": ES.COST_PHONES,
                     "product_ref": str(key)[:200], "unit_cost": float(n),
                     "basis": ES.BASIS_ENTERED, "note": note,
                     "entered_by": who, "entered_at": _now_iso()})
    if not rows:
        return []
    try:
        r = sb().table("marketing_event_cost").insert(rows).execute()
        return r.data or rows
    except Exception as e:
        raise HTTPException(400, "the cost figures could not be saved — run migration 995 first (%s)"
                            % str(e)[:140])


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
