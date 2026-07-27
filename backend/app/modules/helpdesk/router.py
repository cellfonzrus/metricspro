"""Helpdesk / ticketing module (multi-tenant ready).

Employees (and other users) raise tickets; managers/admins (agents) triage + resolve them.
Configurable like SAP — categories / priorities / statuses / teams / custom fields live in
per-org config tables (config-as-data), so adding a category is a row insert, not a deploy.

Consistent with the rest of MetricsPro: every table lives in the PostgREST-exposed `storeops`
schema, org_id defaults to the house org (single-tenant today, tenant-ready), and every query
is org-scoped. Caller identity (requester + whether they're an agent) is supplied by the
frontend from the logged-in user — the same loose-identity pattern the other modules use;
hardening to server-verified identity plugs into the RBAC-enforcement work later.
"""
import uuid
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, UploadFile, File, Header

from app.core.config import settings
from app.core.database import get_supabase

router = APIRouter(prefix="/helpdesk", tags=["Helpdesk"])
ORG_ID = "00000000-0000-0000-0000-000000000001"
BUCKET = "ticket-attachments"


def db(name: str):
    return get_supabase().schema("storeops").table(name)


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── Module entitlement (reference consumer of the shared core gate — platform-core-3) ───────────
# The logic now lives in app.modules.core.entitlements (single source: canonical module_keys,
# alias-aware, graceful degradation). These thin wrappers preserve the "helpdesk" default key so the
# ~30 imperative call sites below stay byte-identical, while every module now enforces identically.
from app.modules.core.entitlements import (
    module_enabled as _core_module_enabled,
    assert_module_enabled as _core_require_module,
)


def _module_enabled(org_id: str, key: str = "helpdesk") -> bool:
    return _core_module_enabled(org_id, key)


def _require_module(org_id: str, key: str = "helpdesk"):
    _core_require_module(org_id, key)


# ── Defaults (seeded on first bootstrap so a freshly-enabled org is usable immediately) ─────────
_DEFAULT_STATUSES = [
    ("open", "Open", "open", "#3b82f6", 10),
    ("in_progress", "In Progress", "open", "#f59e0b", 20),
    ("waiting", "Waiting on Requester", "pending", "#6b7280", 30),
    ("resolved", "Resolved", "done", "#22c55e", 40),
    ("closed", "Closed", "done", "#475569", 50),
]
_DEFAULT_PRIORITIES = [
    ("low", "Low", "#6b7280", 10), ("normal", "Normal", "#3b82f6", 20),
    ("high", "High", "#f97316", 30), ("urgent", "Urgent", "#ef4444", 40),
]
_DEFAULT_CATEGORIES = ["IT / Systems", "Inventory", "Operations", "HR / Payroll", "Facilities"]


def _seed_defaults(org_id: str):
    """Idempotent: only seeds a config table that's currently empty for this org."""
    if not (db("ticket_statuses").select("id").eq("org_id", org_id).limit(1).execute().data or []):
        db("ticket_statuses").insert([
            {"org_id": org_id, "key": k, "label": l, "stage": s, "color": c, "sort_order": o}
            for (k, l, s, c, o) in _DEFAULT_STATUSES]).execute()
    if not (db("ticket_priorities").select("id").eq("org_id", org_id).limit(1).execute().data or []):
        db("ticket_priorities").insert([
            {"org_id": org_id, "key": k, "label": l, "color": c, "sort_order": o}
            for (k, l, c, o) in _DEFAULT_PRIORITIES]).execute()
    if not (db("ticket_categories").select("id").eq("org_id", org_id).limit(1).execute().data or []):
        db("ticket_categories").insert([
            {"org_id": org_id, "name": n, "sort_order": i * 10}
            for i, n in enumerate(_DEFAULT_CATEGORIES)]).execute()
    if not (db("ticket_settings").select("org_id").eq("org_id", org_id).limit(1).execute().data or []):
        db("ticket_settings").upsert({"org_id": org_id, "updated_at": _now()}).execute()


# ── Config: generic CRUD over the simple per-tenant config tables ───────────────────────────────
_CONFIG_COLS = {
    "ticket_categories": ["name", "description", "sort_order", "is_active", "notify_emails"],
    "ticket_priorities": ["key", "label", "color", "sort_order", "is_active"],
    "ticket_statuses": ["key", "label", "stage", "color", "sort_order", "is_active"],
    "ticket_teams": ["name", "is_active"],
    "ticket_custom_fields": ["field_key", "label", "field_type", "options", "is_required", "sort_order", "is_active"],
}


def _clean(table: str, body: dict) -> dict:
    return {k: body[k] for k in _CONFIG_COLS[table] if k in body}


def _cfg_list(table: str, org_id: str):
    order_col = "name" if table == "ticket_teams" else "sort_order"  # teams has no sort_order column
    return (db(table).select("*").eq("org_id", org_id)
            .order(order_col).execute().data or [])


def _cfg_create(table: str, org_id: str, body: dict):
    row = {**_clean(table, body), "org_id": org_id}
    return (db(table).insert(row).execute().data or [{}])[0]


def _cfg_update(table: str, row_id: str, body: dict):
    upd = _clean(table, body)
    if not upd:
        raise HTTPException(400, "nothing to update")
    res = db(table).update(upd).eq("id", row_id).execute()
    if not res.data:
        raise HTTPException(404, "not found")
    return res.data[0]


def _cfg_delete(table: str, row_id: str):
    db(table).delete().eq("id", row_id).execute()
    return {"deleted": True}


@router.get("/config/bootstrap")
def bootstrap(org_id: str = ORG_ID):
    """One call with everything the frontend needs to render forms + badges for this tenant."""
    _require_module(org_id)
    _seed_defaults(org_id)
    teams = _cfg_list("ticket_teams", org_id)
    members = db("ticket_team_members").select("*").eq("org_id", org_id).execute().data or []
    by_team: dict = {}
    for m in members:
        by_team.setdefault(m["team_id"], []).append(m["member"])
    for t in teams:
        t["members"] = by_team.get(t["id"], [])
    settings = (db("ticket_settings").select("*").eq("org_id", org_id).limit(1).execute().data or [{}])
    return {
        "categories": _cfg_list("ticket_categories", org_id),
        "priorities": _cfg_list("ticket_priorities", org_id),
        "statuses": _cfg_list("ticket_statuses", org_id),
        "teams": teams,
        "custom_fields": _cfg_list("ticket_custom_fields", org_id),
        "settings": settings[0] if settings else {"org_id": org_id},
    }


# explicit config endpoints (thin wrappers over the generic helpers)
@router.get("/config/categories")
def cat_list(org_id: str = ORG_ID): _require_module(org_id); return _cfg_list("ticket_categories", org_id)
@router.post("/config/categories")
def cat_create(body: dict, org_id: str = ORG_ID): _require_module(org_id); return _cfg_create("ticket_categories", org_id, body)
@router.patch("/config/categories/{rid}")
def cat_update(rid: str, body: dict, org_id: str = ORG_ID): _require_module(org_id); return _cfg_update("ticket_categories", rid, body)
@router.delete("/config/categories/{rid}")
def cat_delete(rid: str, org_id: str = ORG_ID): _require_module(org_id); return _cfg_delete("ticket_categories", rid)

@router.get("/config/priorities")
def pri_list(org_id: str = ORG_ID): _require_module(org_id); return _cfg_list("ticket_priorities", org_id)
@router.post("/config/priorities")
def pri_create(body: dict, org_id: str = ORG_ID): _require_module(org_id); return _cfg_create("ticket_priorities", org_id, body)
@router.patch("/config/priorities/{rid}")
def pri_update(rid: str, body: dict, org_id: str = ORG_ID): _require_module(org_id); return _cfg_update("ticket_priorities", rid, body)
@router.delete("/config/priorities/{rid}")
def pri_delete(rid: str, org_id: str = ORG_ID): _require_module(org_id); return _cfg_delete("ticket_priorities", rid)

@router.get("/config/statuses")
def st_list(org_id: str = ORG_ID): _require_module(org_id); return _cfg_list("ticket_statuses", org_id)
@router.post("/config/statuses")
def st_create(body: dict, org_id: str = ORG_ID): _require_module(org_id); return _cfg_create("ticket_statuses", org_id, body)
@router.patch("/config/statuses/{rid}")
def st_update(rid: str, body: dict, org_id: str = ORG_ID): _require_module(org_id); return _cfg_update("ticket_statuses", rid, body)
@router.delete("/config/statuses/{rid}")
def st_delete(rid: str, org_id: str = ORG_ID): _require_module(org_id); return _cfg_delete("ticket_statuses", rid)

@router.get("/config/custom-fields")
def cf_list(org_id: str = ORG_ID): _require_module(org_id); return _cfg_list("ticket_custom_fields", org_id)
@router.post("/config/custom-fields")
def cf_create(body: dict, org_id: str = ORG_ID): _require_module(org_id); return _cfg_create("ticket_custom_fields", org_id, body)
@router.patch("/config/custom-fields/{rid}")
def cf_update(rid: str, body: dict, org_id: str = ORG_ID): _require_module(org_id); return _cfg_update("ticket_custom_fields", rid, body)
@router.delete("/config/custom-fields/{rid}")
def cf_delete(rid: str, org_id: str = ORG_ID): _require_module(org_id); return _cfg_delete("ticket_custom_fields", rid)


# Teams (config table + members)
@router.get("/config/teams")
def team_list(org_id: str = ORG_ID):
    _require_module(org_id)
    teams = _cfg_list("ticket_teams", org_id)
    members = db("ticket_team_members").select("*").eq("org_id", org_id).execute().data or []
    by_team: dict = {}
    for m in members:
        by_team.setdefault(m["team_id"], []).append({"id": m["id"], "member": m["member"]})
    for t in teams:
        t["members"] = by_team.get(t["id"], [])
    return teams

@router.post("/config/teams")
def team_create(body: dict, org_id: str = ORG_ID): _require_module(org_id); return _cfg_create("ticket_teams", org_id, body)
@router.patch("/config/teams/{rid}")
def team_update(rid: str, body: dict, org_id: str = ORG_ID): _require_module(org_id); return _cfg_update("ticket_teams", rid, body)
@router.delete("/config/teams/{rid}")
def team_delete(rid: str, org_id: str = ORG_ID): _require_module(org_id); return _cfg_delete("ticket_teams", rid)

@router.post("/config/teams/{tid}/members")
def team_add_member(tid: str, body: dict, org_id: str = ORG_ID):
    _require_module(org_id)
    member = (body.get("member") or "").strip()
    if not member:
        raise HTTPException(400, "member required")
    db("ticket_team_members").upsert(
        {"org_id": org_id, "team_id": tid, "member": member},
        on_conflict="org_id,team_id,member").execute()
    return {"ok": True}

@router.delete("/config/teams/{tid}/members/{mid}")
def team_remove_member(tid: str, mid: str, org_id: str = ORG_ID):
    _require_module(org_id)
    db("ticket_team_members").delete().eq("id", mid).execute()
    return {"deleted": True}


@router.get("/config/settings")
def get_settings(org_id: str = ORG_ID):
    _require_module(org_id)
    rows = db("ticket_settings").select("*").eq("org_id", org_id).limit(1).execute().data or []
    return rows[0] if rows else {"org_id": org_id}

@router.put("/config/settings")
def put_settings(body: dict, org_id: str = ORG_ID):
    _require_module(org_id)
    row = {"org_id": org_id, "updated_at": _now()}
    for k in ("default_assignee", "notify_emails", "brand_logo_url", "brand_color", "business_hours"):
        if k in body:
            row[k] = body[k]
    db("ticket_settings").upsert(row, on_conflict="org_id").execute()
    return get_settings(org_id)


# ── Config lookup maps (for joining labels onto tickets in Python — robust, no PostgREST embeds) ──
def _maps(org_id: str):
    def by_id(table):
        return {r["id"]: r for r in (db(table).select("*").eq("org_id", org_id).execute().data or [])}
    return by_id("ticket_statuses"), by_id("ticket_priorities"), by_id("ticket_categories"), by_id("ticket_teams")


def _decorate(t: dict, st, pr, ca, te) -> dict:
    s = st.get(t.get("status_id")) or {}
    p = pr.get(t.get("priority_id")) or {}
    c = ca.get(t.get("category_id")) or {}
    tm = te.get(t.get("team_id")) or {}
    t["status"] = {"key": s.get("key"), "label": s.get("label"), "stage": s.get("stage"), "color": s.get("color")}
    t["priority"] = {"key": p.get("key"), "label": p.get("label"), "color": p.get("color")}
    t["category"] = {"name": c.get("name")}
    t["team"] = {"name": tm.get("name")}
    t["display_number"] = f"TKT-{t.get('ticket_number')}" if t.get("ticket_number") else None
    return t


# ── Tickets ──────────────────────────────────────────────────────────────────────────────────
@router.post("/tickets")
async def create_ticket(body: dict, org_id: str = ORG_ID):
    _require_module(org_id)
    subject = (body.get("subject") or "").strip()
    description = (body.get("description") or "").strip()
    if not subject or not description:
        raise HTTPException(422, "subject and description are required")

    # validate custom fields against THIS tenant's active definitions
    defs = db("ticket_custom_fields").select("*").eq("org_id", org_id).eq("is_active", True).execute().data or []
    incoming = body.get("custom_fields") or {}
    cleaned = {}
    for d in defs:
        val = incoming.get(d["field_key"])
        if d.get("is_required") and (val is None or val == "" or val == []):
            raise HTTPException(422, f"Missing required field: {d['label']}")
        if val is not None and val != "":
            cleaned[d["field_key"]] = val

    # default to the tenant's lowest-sort 'open'-stage status if none supplied
    status_id = body.get("status_id")
    if not status_id:
        opens = (db("ticket_statuses").select("id,sort_order").eq("org_id", org_id)
                 .eq("stage", "open").order("sort_order").limit(1).execute().data or [])
        status_id = opens[0]["id"] if opens else None

    row = {
        "org_id": org_id, "subject": subject, "description": description,
        "status_id": status_id, "priority_id": body.get("priority_id"),
        "category_id": body.get("category_id"), "team_id": body.get("team_id"),
        "requester_id": body.get("requester_id"), "requester_name": body.get("requester_name"),
        "requester_email": body.get("requester_email"), "store_code": body.get("store_code"),
        "assignee": body.get("assignee"), "custom_fields": cleaned,
    }
    ticket = (db("tickets").insert(row).execute().data or [{}])[0]
    db("ticket_events").insert({
        "org_id": org_id, "ticket_id": ticket.get("id"),
        "actor": body.get("requester_name") or body.get("requester_email"),
        "event_type": "created", "detail": {"subject": subject}}).execute()

    await _notify_new_ticket(org_id, ticket, body.get("requester_name") or body.get("requester_email"))
    st, pr, ca, te = _maps(org_id)
    return _decorate(ticket, st, pr, ca, te)


@router.get("/tickets")
def list_tickets(org_id: str = ORG_ID, agent: bool = False, requester: str = "",
                 status_key: str = "", priority_key: str = "", assignee: str = "",
                 view: str = "all", q: str = ""):
    _require_module(org_id)
    st, pr, ca, te = _maps(org_id)
    query = db("tickets").select("*").eq("org_id", org_id)

    if not agent:
        # employees see only their own tickets (matched on the identity the frontend passes)
        if requester:
            query = query.eq("requester_email", requester)
        else:
            return []
    else:
        if view == "mine" and requester:
            query = query.eq("assignee", requester)
        elif view == "unassigned":
            query = query.is_("assignee", "null")
    if status_key:
        ids = [s["id"] for s in st.values() if s.get("key") == status_key]
        if ids:
            query = query.in_("status_id", ids)
    if priority_key:
        ids = [p["id"] for p in pr.values() if p.get("key") == priority_key]
        if ids:
            query = query.in_("priority_id", ids)
    if assignee:
        query = query.eq("assignee", assignee)
    if q:
        query = query.ilike("subject", f"%{q}%")

    rows = query.order("created_at", desc=True).limit(500).execute().data or []
    esc = _escalated_ticket_ids(org_id, [t.get("id") for t in rows])
    out = []
    for t in rows:
        d = _decorate(t, st, pr, ca, te)
        d["escalated"] = t.get("id") in esc
        out.append(d)
    return out


@router.get("/tickets/{tid}")
def ticket_detail(tid: str, org_id: str = ORG_ID, agent: bool = False):
    _require_module(org_id)
    rows = db("tickets").select("*").eq("org_id", org_id).eq("id", tid).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "ticket not found")
    st, pr, ca, te = _maps(org_id)
    ticket = _decorate(rows[0], st, pr, ca, te)

    comments = (db("ticket_comments").select("*").eq("org_id", org_id).eq("ticket_id", tid)
                .order("created_at").execute().data or [])
    if not agent:
        comments = [c for c in comments if not c.get("is_internal")]
    events = (db("ticket_events").select("*").eq("org_id", org_id).eq("ticket_id", tid)
              .order("created_at").execute().data or [])
    atts = (db("ticket_attachments").select("*").eq("org_id", org_id).eq("ticket_id", tid)
            .order("created_at").execute().data or [])
    return {"ticket": ticket, "comments": comments, "events": events, "attachments": atts,
            "support_case": _ticket_support_case(org_id, tid)}


@router.patch("/tickets/{tid}")
def update_ticket(tid: str, body: dict, org_id: str = ORG_ID, actor: str = ""):
    _require_module(org_id)
    cur = db("tickets").select("*").eq("org_id", org_id).eq("id", tid).limit(1).execute().data or []
    if not cur:
        raise HTTPException(404, "ticket not found")
    cur = cur[0]
    upd: dict = {"updated_at": _now()}
    events = []
    for f in ("status_id", "priority_id", "category_id", "team_id", "assignee"):
        if f in body and body[f] != cur.get(f):
            upd[f] = body[f]
            events.append((f, cur.get(f), body[f]))

    # lifecycle timestamps off the new status's stage (skip if status is being cleared → no id to look up)
    if upd.get("status_id"):
        s = (db("ticket_statuses").select("stage").eq("org_id", org_id).eq("id", upd["status_id"]).limit(1).execute().data or [{}])[0]
        stage = s.get("stage")
        if stage == "done":
            if not cur.get("resolved_at"):
                upd["resolved_at"] = _now()
            upd["closed_at"] = _now()
        elif stage in ("open", "pending"):
            upd["closed_at"] = None

    if len(upd) > 1:
        db("tickets").update(upd).eq("id", tid).execute()
    for (f, old, new) in events:
        db("ticket_events").insert({
            "org_id": org_id, "ticket_id": tid, "actor": actor or "agent",
            "event_type": f"set_{f}", "detail": {"from": old, "to": new}}).execute()

    rows = db("tickets").select("*").eq("org_id", org_id).eq("id", tid).limit(1).execute().data or []
    st, pr, ca, te = _maps(org_id)
    return _decorate(rows[0], st, pr, ca, te)


@router.delete("/tickets/{tid}")
def delete_ticket(tid: str, org_id: str = ORG_ID):
    """Agent-only in the UI. Comments/events/attachments cascade via FK ON DELETE CASCADE."""
    _require_module(org_id)
    db("tickets").delete().eq("org_id", org_id).eq("id", tid).execute()
    return {"deleted": True}


@router.post("/tickets/{tid}/comments")
def add_comment(tid: str, body: dict, org_id: str = ORG_ID, agent: bool = False):
    _require_module(org_id)
    text = (body.get("body") or "").strip()
    if not text:
        raise HTTPException(422, "comment body required")
    is_internal = bool(body.get("is_internal")) and agent   # employees can't post internal notes
    row = {"org_id": org_id, "ticket_id": tid, "author": body.get("author"),
           "author_name": body.get("author_name"), "body": text, "is_internal": is_internal}
    c = (db("ticket_comments").insert(row).execute().data or [{}])[0]
    db("tickets").update({"updated_at": _now()}).eq("id", tid).execute()
    db("ticket_events").insert({
        "org_id": org_id, "ticket_id": tid, "actor": body.get("author_name") or body.get("author"),
        "event_type": "internal_note" if is_internal else "comment"}).execute()
    return c


# ── Attachments (Supabase Storage, tenant-scoped path) ─────────────────────────────────────────
def _ensure_bucket():
    c = get_supabase()
    try:
        c.storage.get_bucket(BUCKET)
    except Exception:
        try:
            c.storage.create_bucket(BUCKET)   # private by default (matches the closing-envelope bucket)
        except Exception:
            pass
    return c


@router.post("/tickets/{tid}/attachments")
async def upload_attachment(tid: str, file: UploadFile = File(...), org_id: str = ORG_ID,
                            uploader: str = "", comment_id: str = ""):
    _require_module(org_id)
    data = await file.read()
    safe = (file.filename or "file").replace("/", "_")
    path = f"{org_id}/{tid}/{uuid.uuid4().hex}_{safe}"
    c = _ensure_bucket()
    try:
        c.storage.from_(BUCKET).upload(path, data, {"content-type": file.content_type or "application/octet-stream"})
    except Exception as e:
        raise HTTPException(500, f"upload failed: {e}")
    row = {"org_id": org_id, "ticket_id": tid, "comment_id": comment_id or None,
           "uploader": uploader or None, "file_name": safe, "storage_path": path,
           "file_size": len(data), "mime_type": file.content_type}
    att = (db("ticket_attachments").insert(row).execute().data or [{}])[0]
    return att


@router.get("/tickets/{tid}/attachments/{aid}/url")
def attachment_url(tid: str, aid: str, org_id: str = ORG_ID):
    _require_module(org_id)
    rows = db("ticket_attachments").select("storage_path").eq("org_id", org_id).eq("id", aid).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "attachment not found")
    try:
        res = get_supabase().storage.from_(BUCKET).create_signed_url(rows[0]["storage_path"], 3600)
        url = (res.get("signedURL") or res.get("signed_url") or res.get("signedUrl")) if isinstance(res, dict) else res
        return {"url": url}
    except Exception as e:
        raise HTTPException(500, f"could not sign url: {e}")


# ── Dashboard (this tenant only; uses the fixed lifecycle stage so renamed labels still group) ──
@router.get("/stats/dashboard")
def dashboard(org_id: str = ORG_ID, date_from: str = "", date_to: str = ""):
    """Aggregate tiles. `date_from`/`date_to` (YYYY-MM-DD, optional) = the RULE FIVE §3d period range over
    created_at; omitted → all tickets (backward-compatible). Tiles are aggregates with no store/market/rep
    dimension, so only the period core-filter applies here (documented deviation)."""
    _require_module(org_id)
    st, _, _, _ = _maps(org_id)
    tickets = db("tickets").select("status_id,created_at,resolved_at").eq("org_id", org_id).limit(5000).execute().data or []
    df, dt = (date_from or "").strip()[:10], (date_to or "").strip()[:10]
    if df or dt:
        def _in_range(t):
            d = str(t.get("created_at") or "")[:10]
            if not d:
                return False
            return (not df or d >= df) and (not dt or d <= dt)
        tickets = [t for t in tickets if _in_range(t)]
    by_stage = {"open": 0, "pending": 0, "done": 0}
    open_count = 0
    res_hours = []
    now = datetime.now(timezone.utc)
    aging = {"0-1d": 0, "1-3d": 0, "3-7d": 0, "7d+": 0}
    for t in tickets:
        stage = (st.get(t.get("status_id")) or {}).get("stage") or "open"
        by_stage[stage] = by_stage.get(stage, 0) + 1
        if t.get("resolved_at") and t.get("created_at"):
            try:
                d = (datetime.fromisoformat(t["resolved_at"].replace("Z", "+00:00"))
                     - datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")))
                res_hours.append(d.total_seconds() / 3600)
            except Exception:
                pass
        if stage in ("open", "pending"):
            open_count += 1
            try:
                age = (now - datetime.fromisoformat(t["created_at"].replace("Z", "+00:00"))).total_seconds() / 86400
                aging["0-1d" if age < 1 else "1-3d" if age < 3 else "3-7d" if age < 7 else "7d+"] += 1
            except Exception:
                pass
    avg_res = round(sum(res_hours) / len(res_hours), 1) if res_hours else None
    return {"total": len(tickets), "open": open_count, "by_stage": by_stage,
            "avg_resolution_hours": avg_res, "aging": aging}


# ── AI support assistant (Phase 2) — tenant-scoped, READ-ONLY ───────────────────────────────────
_AI_SUPPORT_SYSTEM = """You are the in-app support assistant for MetricsPro, a multi-tenant SaaS for
cellular-retail commission and store operations. You are helping a user at the tenant "{tenant_name}".
Enabled modules for this tenant: {modules}.

SCOPE & ISOLATION
- Only ever discuss THIS tenant ("{tenant_name}"). Never reference or imply another company's data.
- You do NOT have live database access. Answer from product knowledge and what the user tells you; when a
  specific number is needed, tell them exactly which page or report shows it.

WHAT METRICSPRO DOES (use this model to frame answers)
A store sells a phone at a carrier-defined discount on a new activation or an upgrade; the carrier pays the
store back the discount plus commission per its commission addendum. The app then reconciles expected vs
paid, bills the phones and other purchases, reconciles that, and characterizes expenses into the P&L.
Commission rules are configured per tenant/carrier in the onboarding and mapping menus — never hard-coded.

COMMON QUESTIONS — answer specifically
- Upload sales: use Data Imports with the 78-column "Sales Transaction Details" export (it has Contract
  Type). The old 25-column daily format produces $0 commissions.
- Commissions $0 / no reps: usually the wrong sales file (missing Contract Type), dates stored as Excel
  serial numbers instead of real dates, or — for a Total/VidaPay carrier — commissions live on the Total
  Processor page, not the Boost commissions dashboard.
- Discrepancy or commission report is empty: check the selected period (not a future month with no data)
  and that the month's data was uploaded, then run/recompute.
- Cash recon flags everything: the tender (cash vs card) split comes from the POS X-Report, not the sales
  feed — make sure the X-Report is being ingested for that store and day.
- Payout looks wrong: point to the commissions/payout page for the period; if a rule itself looks wrong,
  that is an admin configuration question (Mapping / commission plans).

GUARDRAILS
- You are READ-ONLY: you cannot change data, run uploads, edit records, or move money. When asked to DO
  such a thing, explain the exact screen where the user does it themselves, or offer to help them raise a
  support ticket for a person.
- Never assert a specific dollar figure (owed, payout, correction) as fact — say where to see it and who
  can change it.
- If you don't know, say so and suggest raising a ticket. Never invent features, prices, or numbers.

STYLE: concise, friendly, practical. Lead with the answer, then the exact page or steps. A few sentences
is usually enough."""


def _tenant_ai_context(org_id: str) -> dict:
    name = "your company"
    try:
        t = db("tenants").select("name").eq("org_id", org_id).limit(1).execute().data or []
        if t and t[0].get("name"):
            name = t[0]["name"]
    except Exception:
        pass
    mods = []
    try:
        rows = db("tenant_modules").select("module_key,is_enabled").eq("org_id", org_id).execute().data or []
        mods = sorted(r["module_key"] for r in rows if r.get("is_enabled") and r.get("module_key"))
    except Exception:
        pass
    return {"tenant_name": name, "modules": ", ".join(mods) or "the core modules"}


@router.get("/ai-assist/status")
def ai_assist_status(org_id: str = ORG_ID):
    """Whether the AI assistant is usable for this tenant (module enabled + API key configured)."""
    return {"module_enabled": _module_enabled(org_id, "ai_assistant"),
            "configured": bool(settings.ANTHROPIC_API_KEY),
            "model": settings.ACCOUNT_ENGINE_MODEL}


@router.post("/ai-assist")
async def ai_assist(body: dict, org_id: str = ORG_ID):
    """Tenant-scoped AI support assistant (Phase 2). Answers product / how-to / 'why is X empty' questions
    grounded in THIS tenant's context. READ-ONLY by construction — no data-mutation path, and the model is
    told it may only speak about the caller's tenant. Multi-tenant safe: org_id is the caller's tenant
    (enforcement rewrites it from the JWT). Body: {message, history?: [{role, content}]}."""
    _require_module(org_id, "ai_assistant")
    question = (body.get("message") or body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "message required")
    question = question[:4000]
    if not settings.ANTHROPIC_API_KEY:
        return {"reply": "The AI assistant isn't configured yet. Ask an admin to set the API key, or raise "
                         "a ticket and a person will help.", "configured": False}
    ctx = _tenant_ai_context(org_id)
    system = _AI_SUPPORT_SYSTEM.replace("{tenant_name}", ctx["tenant_name"]).replace("{modules}", ctx["modules"])
    msgs = []
    for h in (body.get("history") or [])[-10:]:
        role = (h.get("role") or "").lower()
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content[:4000]})
    msgs.append({"role": "user", "content": question})
    try:
        from anthropic import Anthropic
        cli = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = cli.messages.create(
            model=settings.ACCOUNT_ENGINE_MODEL, max_tokens=1024,
            system=system, messages=msgs,
        )
        reply = "".join(getattr(b, "text", "") for b in resp.content
                        if getattr(b, "type", None) == "text").strip()
        return {"reply": reply or "I couldn't produce an answer — try rephrasing, or raise a ticket.",
                "configured": True}
    except Exception as e:
        return {"reply": "The assistant hit an error. You can raise a ticket and a person will help.",
                "configured": True, "error": str(e)[:200]}


# ── Notify (best-effort; never blocks ticket creation) ─────────────────────────────────────────
async def _notify_new_ticket(org_id: str, ticket: dict, requester: str):
    try:
        # Route by category first (e.g. IT → IT lead), then fall back to the global recipient list.
        emails = []
        cat_id = ticket.get("category_id")
        if cat_id:
            try:
                c = db("ticket_categories").select("notify_emails").eq("org_id", org_id).eq("id", cat_id).limit(1).execute().data or []
                emails = (c[0].get("notify_emails") if c else None) or []
            except Exception:
                emails = []   # column may not exist yet (migration 054 not run) → fall back
        if not emails:
            s = db("ticket_settings").select("notify_emails").eq("org_id", org_id).limit(1).execute().data or []
            emails = (s[0].get("notify_emails") if s else None) or []
        if not emails:
            return
        from app.modules.notify.channels import email_resend
        if not email_resend.is_configured():
            return
        num = f"TKT-{ticket.get('ticket_number')}"
        subject = f"[Helpdesk] New ticket {num}: {ticket.get('subject')}"
        html = (f"<p>A new helpdesk ticket was raised{(' by ' + requester) if requester else ''}.</p>"
                f"<p><b>{num}</b> — {ticket.get('subject')}</p>"
                f"<p>{(ticket.get('description') or '')[:500]}</p>")
        for addr in emails:
            try:
                await email_resend.send_email(addr, subject, html, [])
            except Exception:
                pass
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# TECH-SUPPORT PLATFORM (mig 715) — cross-tenant support console + ticket escalation.
#
# The HOUSE org's tech-support staff handle escalated tickets from EVERY tenant in ONE console. The
# console endpoints below are CROSS-TENANT BY DESIGN (they read support_case rows across all org_ids and
# show each tenant's name) — this is the ONE sanctioned cross-tenant read surface in the app, and it is
# SERVER-GATED by `_require_support`: only a super_admin, or a HOUSE-org membership whose role grants the
# `support` module (or is scope-all / admin), passes. A tenant user has NO house membership, so nothing
# here is reachable by tenant users (anti-enumeration). The gate resolves identity from the JWT
# (Authorization header) via the existing core helpers — NOT from any org_id query param — so the
# tenant-middleware org_id rewrite never narrows a cross-tenant read.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
HOUSE_ORG = "00000000-0000-0000-0000-000000000001"
_SUPPORT_STATUSES = ("new", "in_progress", "waiting_user", "resolved", "closed")
_SUPPORT_PRIORITIES = ("low", "normal", "high", "urgent")


def _support_ctx(authorization: str, x_active_org: str = ""):
    """Resolve the caller and decide if they may use the cross-tenant support console. Returns
    {"email", "super_admin", "org_id"} when ALLOWED, else None. ALLOWED = super_admin (login-level
    bypass) OR a HOUSE-org membership whose role grants modules.support (or scope 'all' / role 'admin').
    Reuses core.router._uid_from_token (60s token-verify cache) + core.membership primitives — lazy
    imports to avoid any import cycle (core never imports helpdesk)."""
    from app.modules.core.router import _uid_from_token
    from app.modules.core.membership import list_memberships, pick_membership
    uid = _uid_from_token(authorization or "")
    if not uid:
        return None
    client = get_supabase()
    rows = list_memberships(client, uid)
    if not rows:
        return None
    if any(r.get("super_admin") for r in rows):
        active = pick_membership(rows, (x_active_org or "").strip() or None) or rows[0]
        return {"email": active.get("email"), "super_admin": True, "org_id": active.get("org_id")}
    house = next((r for r in rows if r.get("org_id") == HOUSE_ORG), None)
    if not house:
        return None
    role = house.get("role")
    perms = {}
    if role:
        try:
            rr = (client.schema("storeops").table("roles").select("permissions")
                  .eq("org_id", HOUSE_ORG).eq("name", role).limit(1).execute().data) or []
            if rr:
                perms = rr[0].get("permissions") or {}
        except Exception:
            perms = {}
    mods = perms.get("modules") or {}
    if mods.get("support") or perms.get("scope") == "all" or (role or "").lower() == "admin":
        return {"email": house.get("email"), "super_admin": False, "org_id": HOUSE_ORG}
    return None


def _require_support(authorization: str, x_active_org: str = ""):
    ctx = _support_ctx(authorization, x_active_org)
    if not ctx:
        raise HTTPException(403, "The tech-support console is restricted to house support staff.")
    return ctx


# ── Pure helpers (unit-proven in harness_tech_support.py) ───────────────────────────────────────
def _sla_due_at(policy_rows, priority, created_iso):
    """created_at + response_hours from the SLA policy row matching `priority`. Returns an ISO string,
    or None when no policy row exists for that priority (NO hard-coded hours — values live in rows)."""
    hours = None
    for r in (policy_rows or []):
        if str(r.get("priority", "")).strip().lower() == str(priority or "").strip().lower():
            hours = r.get("response_hours")
            break
    if hours is None:
        return None
    try:
        base = datetime.fromisoformat(str(created_iso).replace("Z", "+00:00"))
    except Exception:
        base = datetime.now(timezone.utc)
    return (base + timedelta(hours=int(hours))).isoformat()


def _support_priority_from_ticket(priority_label_or_key):
    """Map a helpdesk priority (key or label, e.g. 'urgent'/'High') to a support priority. Defaults to
    'normal' when unknown, so an escalation always gets a valid SLA-eligible priority."""
    p = str(priority_label_or_key or "").strip().lower()
    for cand in _SUPPORT_PRIORITIES:
        if cand in p:
            return cand
    return "normal"


# ── support_case DB helpers (storeops schema; org-scoped where tenant-facing) ───────────────────
def _house_sla_policy():
    try:
        return db("support_sla_policy").select("*").eq("org_id", HOUSE_ORG).execute().data or []
    except Exception:
        return []


def _escalated_ticket_ids(org_id: str, ticket_ids):
    """Set of ticket ids (from `ticket_ids`) that have an open/any support_case for this tenant.
    Best-effort — returns an empty set if mig 715 is un-run (never breaks the ticket list)."""
    ids = [t for t in (ticket_ids or []) if t]
    if not ids:
        return set()
    try:
        rows = (db("support_case").select("ticket_id").eq("org_id", org_id)
                .in_("ticket_id", ids).execute().data) or []
        return {r.get("ticket_id") for r in rows if r.get("ticket_id")}
    except Exception:
        return set()


def _ticket_support_case(org_id: str, tid: str):
    """The support_case (status/priority) for a tenant ticket, or None. Best-effort (mig un-run → None)."""
    try:
        rows = (db("support_case").select("id,status,priority,sla_due_at,created_at,assignee_email")
                .eq("org_id", org_id).eq("ticket_id", tid).limit(1).execute().data) or []
        return rows[0] if rows else None
    except Exception:
        return None


def _case_event(org_id, case_id, kind, body=None, author_email=None, visible_to_user=False):
    try:
        db("support_case_event").insert({
            "org_id": org_id, "case_id": case_id, "kind": kind, "body": body,
            "author_email": author_email, "visible_to_user": bool(visible_to_user)}).execute()
    except Exception:
        pass


# ── Escalation (TENANT-facing: a helpdesk agent escalates one of THEIR tickets) ─────────────────
@router.post("/tickets/{tid}/escalate")
async def escalate_ticket(tid: str, body: dict = None, org_id: str = ORG_ID, actor: str = ""):
    """Escalate a tenant helpdesk ticket to the house tech-support console. Idempotent per ticket
    (UNIQUE org_id,ticket_id): a second call returns the existing case. Stamps sla_due_at from the HOUSE
    SLA policy for the ticket's priority, records a VISIBLE event on the tenant ticket ('Escalated to
    tech support' — so the requester sees it in their existing helpdesk thread) and best-effort emails
    the tenant's helpdesk notify list. org_id is the (middleware-rewritten) tenant query param."""
    _require_module(org_id)
    body = body or {}
    rows = db("tickets").select("*").eq("org_id", org_id).eq("id", tid).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "ticket not found")
    ticket = rows[0]
    # already escalated? (idempotent)
    existing = _ticket_support_case(org_id, tid)
    if existing:
        return {"ok": True, "already_escalated": True, "case": existing}
    # priority from the ticket's priority row (fall back to 'normal')
    plabel = ""
    if ticket.get("priority_id"):
        pr = (db("ticket_priorities").select("key,label").eq("org_id", org_id)
              .eq("id", ticket["priority_id"]).limit(1).execute().data or [{}])[0]
        plabel = f"{pr.get('key') or ''} {pr.get('label') or ''}"
    priority = _support_priority_from_ticket(plabel)
    now = _now()
    sla = _sla_due_at(_house_sla_policy(), priority, now)
    row = {"org_id": org_id, "ticket_id": tid, "page_key": (body.get("page_key") or None),
           "status": "new", "priority": priority, "sla_due_at": sla,
           "created_at": now, "updated_at": now}
    try:
        case = (db("support_case").insert(row).execute().data or [{}])[0]
    except Exception as e:
        # unique-violation race → return the now-existing case; other errors → surface
        again = _ticket_support_case(org_id, tid)
        if again:
            return {"ok": True, "already_escalated": True, "case": again}
        raise HTTPException(500, f"could not escalate (run migration 715?): {e}")
    # Visible marker on the tenant ticket thread (the requester sees this in their helpdesk UI).
    try:
        db("ticket_comments").insert({
            "org_id": org_id, "ticket_id": tid, "author": actor or "support",
            "author_name": actor or "Tech Support",
            "body": "Escalated to tech support — a specialist will follow up here.",
            "is_internal": False}).execute()
    except Exception:
        pass
    try:
        db("ticket_events").insert({
            "org_id": org_id, "ticket_id": tid, "actor": actor or "agent",
            "event_type": "escalated", "detail": {"priority": priority}}).execute()
    except Exception:
        pass
    _case_event(org_id, case.get("id"), "status", body="Escalated to tech support",
                author_email=actor or None, visible_to_user=True)
    await _notify_escalation(org_id, ticket, actor)
    return {"ok": True, "already_escalated": False, "case": case}


async def _notify_escalation(org_id: str, ticket: dict, actor: str):
    """Best-effort email to the tenant's helpdesk notify list (never blocks the escalation)."""
    try:
        s = db("ticket_settings").select("notify_emails").eq("org_id", org_id).limit(1).execute().data or []
        emails = (s[0].get("notify_emails") if s else None) or []
        if not emails:
            return
        from app.modules.notify.channels import email_resend
        if not email_resend.is_configured():
            return
        num = f"TKT-{ticket.get('ticket_number')}"
        subject = f"[Tech Support] {num} escalated: {ticket.get('subject')}"
        html = (f"<p>Ticket <b>{num}</b> — {ticket.get('subject')} — was escalated to tech support"
                f"{(' by ' + actor) if actor else ''}.</p>"
                f"<p>Track it in the support console.</p>")
        for addr in emails:
            try:
                await email_resend.send_email(addr, subject, html, [])
            except Exception:
                pass
    except Exception:
        pass


# ── Support console (CROSS-TENANT, house-gated) ─────────────────────────────────────────────────
def _tenant_names(org_ids):
    out = {}
    ids = [o for o in set(org_ids) if o]
    if not ids:
        return out
    try:
        rows = db("tenants").select("org_id,name").in_("org_id", ids).execute().data or []
        out = {r["org_id"]: r.get("name") for r in rows if r.get("org_id")}
    except Exception:
        pass
    return out


@router.get("/support/cases")
async def support_cases(authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                        status: str = "", priority: str = "", org: str = "", assignee: str = "",
                        page_key: str = "", limit: int = 300):
    """CROSS-TENANT queue: every escalated case across all tenants (house-gated). Filters: status,
    priority, org (tenant), assignee, page_key. Each row carries its tenant name + ticket subject/number.
    RULE FIVE core-set applies where meaningful (period is created_at-ordered; store/market/rep are not
    dimensions of a support case)."""
    _require_support(authorization, x_active_org)
    q = db("support_case").select("*")            # NO org filter — cross-tenant by design (house-gated)
    if status:
        q = q.eq("status", status)
    if priority:
        q = q.eq("priority", priority)
    if org:
        q = q.eq("org_id", org)
    if assignee:
        q = q.eq("assignee_email", assignee)
    if page_key:
        q = q.eq("page_key", page_key)
    try:
        cases = q.order("created_at", desc=True).limit(min(max(limit, 1), 1000)).execute().data or []
    except Exception as e:
        raise HTTPException(500, f"support cases unavailable (run migration 715?): {e}")
    names = _tenant_names([c.get("org_id") for c in cases])
    # ticket subjects/numbers (grouped by org so we never cross tenants in the lookup)
    tmap = {}
    by_org: dict = {}
    for c in cases:
        if c.get("ticket_id"):
            by_org.setdefault(c["org_id"], []).append(c["ticket_id"])
    for oid, tids in by_org.items():
        try:
            trows = (db("tickets").select("id,subject,ticket_number,status_id,requester_name,requester_email")
                     .eq("org_id", oid).in_("id", tids).execute().data) or []
            for t in trows:
                tmap[(oid, t["id"])] = t
        except Exception:
            pass
    out = []
    for c in cases:
        t = tmap.get((c.get("org_id"), c.get("ticket_id"))) or {}
        out.append({**c, "tenant_name": names.get(c.get("org_id")) or "Tenant",
                    "ticket_subject": t.get("subject"),
                    "ticket_number": (f"TKT-{t.get('ticket_number')}" if t.get("ticket_number") else None),
                    "requester": t.get("requester_name") or t.get("requester_email")})
    return {"cases": out, "count": len(out),
            "statuses": list(_SUPPORT_STATUSES), "priorities": list(_SUPPORT_PRIORITIES)}


def _load_case(cid: str):
    rows = db("support_case").select("*").eq("id", cid).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "case not found")
    return rows[0]


@router.get("/support/cases/{cid}")
async def support_case_detail(cid: str, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Case detail: the case + its timeline + the origin ticket (subject/description/thread) + the tenant
    name + recent core.failure_log rows for the case's org within ±24h of ticket creation. House-gated."""
    _require_support(authorization, x_active_org)
    case = _load_case(cid)
    org_id = case.get("org_id")
    events = (db("support_case_event").select("*").eq("org_id", org_id).eq("case_id", cid)
              .order("created_at").execute().data or [])
    ticket, comments = None, []
    if case.get("ticket_id"):
        trows = db("tickets").select("*").eq("org_id", org_id).eq("id", case["ticket_id"]).limit(1).execute().data or []
        if trows:
            ticket = trows[0]
            ticket["display_number"] = f"TKT-{ticket.get('ticket_number')}" if ticket.get("ticket_number") else None
            comments = (db("ticket_comments").select("*").eq("org_id", org_id)
                        .eq("ticket_id", case["ticket_id"]).order("created_at").execute().data or [])
    names = _tenant_names([org_id])
    return {"case": {**case, "tenant_name": names.get(org_id) or "Tenant"},
            "events": events, "ticket": ticket, "ticket_comments": comments,
            "failures": _case_failures(org_id, (ticket or {}).get("created_at")),
            "statuses": list(_SUPPORT_STATUSES), "priorities": list(_SUPPORT_PRIORITIES)}


def _case_failures(org_id, ticket_created_at):
    """Recent core.failure_log rows for this tenant within ±24h of the ticket's creation (best-effort)."""
    if not org_id:
        return []
    try:
        base = datetime.fromisoformat(str(ticket_created_at).replace("Z", "+00:00")) if ticket_created_at \
            else datetime.now(timezone.utc)
    except Exception:
        base = datetime.now(timezone.utc)
    lo = (base - timedelta(hours=24)).isoformat()
    hi = (base + timedelta(hours=24)).isoformat()
    try:
        return (get_supabase().schema("core").table("failure_log").select("*")
                .eq("org_id", org_id).gte("created_at", lo).lte("created_at", hi)
                .order("created_at", desc=True).limit(50).execute().data) or []
    except Exception:
        return []


def _touch_case(cid, patch):
    patch = {**patch, "updated_at": _now()}
    db("support_case").update(patch).eq("id", cid).execute()


@router.post("/support/cases/{cid}/reply")
async def support_case_reply(cid: str, body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Post a reply visible to the tenant user. Records a case event (kind='reply', visible_to_user=true)
    AND fans the reply into the tenant's helpdesk ticket thread (storeops.ticket_comments + a ticket_event)
    so the user sees it in their existing helpdesk UI, plus a best-effort notify email. House-gated."""
    ctx = _require_support(authorization, x_active_org)
    text = (body.get("body") or "").strip()
    if not text:
        raise HTTPException(422, "reply body required")
    case = _load_case(cid)
    org_id = case.get("org_id")
    author = ctx.get("email") or "support"
    _case_event(org_id, cid, "reply", body=text, author_email=author, visible_to_user=True)
    # Fan-out into the tenant ticket thread (visible to the requester).
    if case.get("ticket_id"):
        try:
            db("ticket_comments").insert({
                "org_id": org_id, "ticket_id": case["ticket_id"], "author": author,
                "author_name": "Tech Support", "body": text, "is_internal": False}).execute()
            db("tickets").update({"updated_at": _now()}).eq("id", case["ticket_id"]).execute()
            db("ticket_events").insert({
                "org_id": org_id, "ticket_id": case["ticket_id"], "actor": author,
                "event_type": "support_reply"}).execute()
        except Exception:
            pass
        await _notify_ticket_reply(org_id, case["ticket_id"])
    _touch_case(cid, {})
    return {"ok": True}


async def _notify_ticket_reply(org_id, ticket_id):
    try:
        trows = db("tickets").select("subject,ticket_number,requester_email").eq("org_id", org_id).eq("id", ticket_id).limit(1).execute().data or []
        if not trows:
            return
        t = trows[0]
        addr = t.get("requester_email")
        if not addr:
            return
        from app.modules.notify.channels import email_resend
        if not email_resend.is_configured():
            return
        num = f"TKT-{t.get('ticket_number')}"
        subject = f"[Support] Update on {num}: {t.get('subject')}"
        html = f"<p>Tech support replied to your ticket <b>{num}</b>. Open the helpdesk to view the reply.</p>"
        try:
            await email_resend.send_email(addr, subject, html, [])
        except Exception:
            pass
    except Exception:
        pass


@router.post("/support/cases/{cid}/note")
async def support_case_note(cid: str, body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Internal note — NEVER fanned to the tenant (visible_to_user=false). House-gated."""
    ctx = _require_support(authorization, x_active_org)
    text = (body.get("body") or "").strip()
    if not text:
        raise HTTPException(422, "note body required")
    case = _load_case(cid)
    _case_event(case.get("org_id"), cid, "internal_note", body=text,
                author_email=ctx.get("email"), visible_to_user=False)
    _touch_case(cid, {})
    return {"ok": True}


@router.post("/support/cases/{cid}/assign")
async def support_case_assign(cid: str, body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Assign the case to a support agent (assignee_email). House-gated."""
    ctx = _require_support(authorization, x_active_org)
    assignee = (body.get("assignee_email") or "").strip() or None
    case = _load_case(cid)
    _touch_case(cid, {"assignee_email": assignee})
    _case_event(case.get("org_id"), cid, "assign",
                body=(f"Assigned to {assignee}" if assignee else "Unassigned"),
                author_email=ctx.get("email"), visible_to_user=False)
    return {"ok": True, "assignee_email": assignee}


@router.post("/support/cases/{cid}/status")
async def support_case_status(cid: str, body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Change case status/priority. Resolving (status='resolved') REQUIRES resolution text. House-gated."""
    ctx = _require_support(authorization, x_active_org)
    case = _load_case(cid)
    patch = {}
    new_status = (body.get("status") or "").strip().lower()
    if new_status:
        if new_status not in _SUPPORT_STATUSES:
            raise HTTPException(400, f"invalid status; use one of {list(_SUPPORT_STATUSES)}")
        resolution = (body.get("resolution") or "").strip()
        if new_status == "resolved" and not resolution and not (case.get("resolution") or "").strip():
            raise HTTPException(422, "a resolution note is required to resolve a case")
        patch["status"] = new_status
        if resolution:
            patch["resolution"] = resolution
    new_priority = (body.get("priority") or "").strip().lower()
    if new_priority:
        if new_priority not in _SUPPORT_PRIORITIES:
            raise HTTPException(400, f"invalid priority; use one of {list(_SUPPORT_PRIORITIES)}")
        patch["priority"] = new_priority
    if not patch:
        raise HTTPException(400, "nothing to update")
    _touch_case(cid, patch)
    _case_event(case.get("org_id"), cid, "status",
                body=" · ".join(f"{k}={v}" for k, v in patch.items()),
                author_email=ctx.get("email"), visible_to_user=False)
    return {"ok": True, **patch}


# ── Support config: canned responses + SLA policy (HOUSE-org config, per-setting gated) ─────────
# Read = any support agent; WRITE = _can_edit_setting(caller, 'support_config') (registered in core
# SETTING_AREAS). This is the established per-setting-edit pattern.
def _support_can_edit(authorization: str, x_active_org: str = "") -> bool:
    from app.modules.core.router import _uid_from_token, _resolve_caller, _can_edit_setting
    uid = _uid_from_token(authorization or "")
    if not uid:
        return False
    caller = _resolve_caller(get_supabase(), uid, x_active_org)
    return _can_edit_setting(caller, "support_config")


@router.get("/support/canned-responses")
async def canned_list(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    _require_support(authorization, x_active_org)
    try:
        rows = (db("support_canned_response").select("*").eq("org_id", HOUSE_ORG)
                .order("category").order("title").execute().data) or []
    except Exception:
        rows = []
    return {"canned": rows, "can_edit": _support_can_edit(authorization, x_active_org)}


@router.post("/support/canned-responses")
async def canned_create(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    _require_support(authorization, x_active_org)
    if not _support_can_edit(authorization, x_active_org):
        raise HTTPException(403, "you don't have permission to edit support canned responses")
    title = (body.get("title") or "").strip()
    text = (body.get("body") or "").strip()
    if not title or not text:
        raise HTTPException(422, "title and body are required")
    row = {"org_id": HOUSE_ORG, "title": title, "body": text,
           "category": (body.get("category") or None), "updated_at": _now()}
    if body.get("id"):
        db("support_canned_response").update(row).eq("id", body["id"]).eq("org_id", HOUSE_ORG).execute()
        return {"ok": True, "id": body["id"]}
    r = (db("support_canned_response").insert(row).execute().data or [{}])[0]
    return {"ok": True, "id": r.get("id")}


@router.delete("/support/canned-responses/{rid}")
async def canned_delete(rid: str, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    _require_support(authorization, x_active_org)
    if not _support_can_edit(authorization, x_active_org):
        raise HTTPException(403, "you don't have permission to edit support canned responses")
    db("support_canned_response").delete().eq("id", rid).eq("org_id", HOUSE_ORG).execute()
    return {"deleted": True}


@router.get("/support/sla-policy")
async def sla_get(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    _require_support(authorization, x_active_org)
    return {"policy": _house_sla_policy(), "priorities": list(_SUPPORT_PRIORITIES),
            "can_edit": _support_can_edit(authorization, x_active_org)}


@router.put("/support/sla-policy")
async def sla_put(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Upsert one priority's SLA (body: {priority, response_hours, resolve_hours}). House config."""
    _require_support(authorization, x_active_org)
    if not _support_can_edit(authorization, x_active_org):
        raise HTTPException(403, "you don't have permission to edit the support SLA policy")
    priority = (body.get("priority") or "").strip().lower()
    if priority not in _SUPPORT_PRIORITIES:
        raise HTTPException(400, f"invalid priority; use one of {list(_SUPPORT_PRIORITIES)}")
    def _int(v):
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return None
    row = {"org_id": HOUSE_ORG, "priority": priority,
           "response_hours": _int(body.get("response_hours")),
           "resolve_hours": _int(body.get("resolve_hours")), "updated_at": _now()}
    db("support_sla_policy").upsert(row, on_conflict="org_id,priority").execute()
    return {"ok": True, **row}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# FLEET-WIDE FAILURE TRIAGE + FIX-REQUEST PIPELINE (mig 716) — CROSS-TENANT, house-gated.
#
# House tech-support staff triage EVERY tenant's failure logs in one place, club similar failures into a
# fix request, and (super_admin only) approve those requests into an automation queue the operator/agent
# fleet picks up. Same _require_support gate + super_admin primitive as the console; a tenant user is 403.
# The plain-English grouping + fix-request pure logic lives ONCE in core.router (lazy-imported here).
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def _core_triage():
    """Lazy handle to the shared triage helpers in core.router (avoids any import cycle)."""
    from app.modules.core.router import (
        _fetch_failures, _merge_kind_docs, _build_failure_groups, _house_kind_docs,
        _new_fix_request_row, fix_status_change, FIX_STATUSES)
    return SimpleNamespace(
        fetch_failures=_fetch_failures, merge_kind_docs=_merge_kind_docs,
        build_groups=_build_failure_groups, house_kind_docs=_house_kind_docs,
        new_fix_row=_new_fix_request_row, status_change=fix_status_change, statuses=FIX_STATUSES)


@router.get("/support/failures")
async def support_failures(authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                           org: str = "", module: str = "", kind: str = "", reviewed: str = "false",
                           date_from: str = "", date_to: str = "", limit: int = 1500):
    """CROSS-TENANT failure triage (house-gated): every tenant's core.failure_log, grouped by kind with the
    plain-English doc, filterable by tenant(org) / module / kind / reviewed / date. `reviewed` defaults to
    'false' (the unreviewed queue). Returns groups (with affected_orgs + tenant names) AND the flat rows
    (tenant-named) for the detail table + export."""
    _require_support(authorization, x_active_org)
    t = _core_triage()
    client = get_supabase()
    rows = t.fetch_failures(client, org_id=(org or None), reviewed=reviewed, category=(kind or None),
                            date_from=(date_from or None), date_to=(date_to or None), limit=limit)
    kind_meta = t.merge_kind_docs(t.house_kind_docs(client))
    if module:
        rows = [r for r in rows
                if (kind_meta.get(r.get("category")) or {"module": "admin"}).get("module") == module]
    groups = t.build_groups(rows, kind_meta)
    # decorate affected_orgs + flat rows with tenant names
    all_orgs = [o["org_id"] for g in groups for o in g.get("affected_orgs", [])] + [r.get("org_id") for r in rows]
    names = _tenant_names(all_orgs)
    for g in groups:
        for o in g.get("affected_orgs", []):
            o["org_name"] = names.get(o["org_id"]) or "Tenant"
    flat = [{**r, "tenant_name": names.get(r.get("org_id")) or "Tenant"} for r in rows]
    # STABLE facets (registry + all tenants) so a filter dropdown never self-collapses when it's active.
    row_kinds = {r.get("category") for r in rows if r.get("category")}
    kinds_facet = sorted(({"kind": k, "label": (kind_meta.get(k, {}).get("label") or k)}
                          for k in (set(kind_meta.keys()) | row_kinds)), key=lambda x: x["label"])
    modules_facet = sorted({(m.get("module") or "admin") for m in kind_meta.values()})
    try:
        all_tenants = client.schema("storeops").table("tenants").select("org_id,name").execute().data or []
    except Exception:
        all_tenants = []
    return {"groups": groups, "rows": flat, "total": len(rows),
            "unreviewed_total": sum(g["unreviewed_count"] for g in groups),
            "modules": modules_facet, "kinds": kinds_facet,
            "tenants": [{"org_id": x["org_id"], "name": x.get("name")} for x in all_tenants if x.get("org_id")]}


@router.post("/support/failures/bulk-review")
async def support_failures_bulk_review(body: dict, authorization: str = Header(default=""),
                                       x_active_org: str = Header(default="")):
    """CROSS-TENANT clear: mark the given failure rows reviewed/un-reviewed BY ID (house-gated — the failure
    ids carry their own org_id, so no org filter is applied; this is the sanctioned cross-tenant write)."""
    ctx = _require_support(authorization, x_active_org)
    ids = [str(i) for i in (body.get("ids") or []) if i]
    if not ids:
        raise HTTPException(422, "ids[] required")
    reviewed = bool(body.get("reviewed", True))
    patch = {"reviewed": reviewed,
             "reviewed_by": ((ctx.get("email") or "support") if reviewed else None),
             "reviewed_at": (_now() if reviewed else None)}
    try:
        get_supabase().schema("core").table("failure_log").update(patch).in_("id", ids).execute()
    except Exception as e:
        raise HTTPException(500, f"could not update (run migration 716?): {e}")
    return {"ok": True, "count": len(ids), "reviewed": reviewed}


# ── Fix requests (CROSS-TENANT, house-gated; approve/reject = super_admin ONLY) ──────────────────
def _decorate_fix_request(fr, names):
    orgs = fr.get("affected_orgs") or []
    for o in orgs:
        if isinstance(o, dict) and o.get("org_id"):
            o["org_name"] = names.get(o["org_id"]) or "Tenant"
    return {**fr, "owner_name": names.get(fr.get("org_id")) or "Tenant", "affected_orgs": orgs}


@router.post("/support/fix-requests")
async def support_create_fix_request(body: dict, authorization: str = Header(default=""),
                                     x_active_org: str = Header(default="")):
    """Club a group of similar failures (across one or more tenants) into ONE house-owned fix request.
    House-gated. Enters at 'pending_approval' — a non-super support agent CANNOT create it directly in an
    approval state (the initial status is clamped in `_new_fix_request_row`); only a super_admin may
    create a pre-approved/rejected request (stamped approved_by/approved_at). `affected_orgs` =
    [{org_id, count}] built by the console from the clubbed group; `sample_failure_ids` references the
    clubbed rows. NEVER edits code or data."""
    ctx = _require_support(authorization, x_active_org)
    t = _core_triage()
    ids = [str(i) for i in (body.get("sample_failure_ids") or []) if i]
    affected = [a for a in (body.get("affected_orgs") or []) if isinstance(a, dict) and a.get("org_id")]
    fc = int(body.get("failure_count") or sum(int(a.get("count") or 0) for a in affected) or len(ids))
    row = t.new_fix_row(body, org_id=HOUSE_ORG, created_by=(ctx.get("email") or "support"),
                        sample_ids=ids, affected_orgs=affected, failure_count=fc,
                        status=(body.get("status") or "pending_approval"),
                        is_super_admin=bool(ctx.get("super_admin")))
    try:
        r = db("support_fix_request").insert(row).execute()
        return {"ok": True, "id": (r.data[0]["id"] if r.data else None), "status": row["status"]}
    except Exception as e:
        raise HTTPException(500, f"could not create fix request (run migration 716?): {e}")


@router.get("/support/fix-requests")
async def support_list_fix_requests(authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                                    status: str = "", org: str = "", kind: str = "", limit: int = 500):
    """CROSS-TENANT list of fix requests (house-gated). Filter by status / owner org / kind. Pass
    status='approved' to read the AUTOMATION QUEUE the fleet picks up. `can_approve` = caller is a
    super_admin (the approval gate)."""
    ctx = _require_support(authorization, x_active_org)
    q = db("support_fix_request").select("*")            # NO org filter — cross-tenant by design
    if status:
        q = q.eq("status", status)
    if org:
        q = q.eq("org_id", org)
    if kind:
        q = q.eq("kind", kind)
    try:
        rows = q.order("created_at", desc=True).limit(min(max(limit, 1), 1000)).execute().data or []
    except Exception as e:
        raise HTTPException(500, f"fix requests unavailable (run migration 716?): {e}")
    ids = [r.get("org_id") for r in rows] + [o.get("org_id") for r in rows for o in (r.get("affected_orgs") or []) if isinstance(o, dict)]
    names = _tenant_names(ids)
    out = [_decorate_fix_request(r, names) for r in rows]
    return {"fix_requests": out, "count": len(out), "statuses": list(_core_triage().statuses),
            "can_approve": bool(ctx.get("super_admin"))}


@router.get("/support/fix-requests/{fid}")
async def support_fix_request_detail(fid: str, authorization: str = Header(default=""),
                                     x_active_org: str = Header(default="")):
    """One fix request + its clubbed failure rows (tenant-named). House-gated."""
    ctx = _require_support(authorization, x_active_org)
    rows = db("support_fix_request").select("*").eq("id", fid).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "fix request not found")
    fr = rows[0]
    t = _core_triage()
    ids = [str(i) for i in (fr.get("sample_failure_ids") or []) if i]
    failures = t.fetch_failures(get_supabase(), ids=ids, limit=500) if ids else []
    org_ids = [fr.get("org_id")] + [o.get("org_id") for o in (fr.get("affected_orgs") or []) if isinstance(o, dict)] + [f.get("org_id") for f in failures]
    names = _tenant_names(org_ids)
    failures = [{**f, "tenant_name": names.get(f.get("org_id")) or "Tenant"} for f in failures]
    return {"fix_request": _decorate_fix_request(fr, names), "failures": failures,
            "statuses": list(t.statuses), "can_approve": bool(ctx.get("super_admin"))}


@router.post("/support/fix-requests/{fid}/status")
async def support_fix_request_status(fid: str, body: dict, authorization: str = Header(default=""),
                                     x_active_org: str = Header(default="")):
    """Move a fix request through the pipeline. approve/reject REQUIRE a super_admin (the approval gate);
    other transitions are open to any support agent. Resolving with mark_reviewed=true bulk-marks the
    clubbed failure rows reviewed. House-gated. Approving does NOT run anything — it just enters the queue."""
    ctx = _require_support(authorization, x_active_org)
    rows = db("support_fix_request").select("*").eq("id", fid).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "fix request not found")
    fr = rows[0]
    target = str(body.get("status") or "").strip().lower()
    ok, reason = _core_triage().status_change(fr.get("status"), target, bool(ctx.get("super_admin")))
    if not ok:
        raise HTTPException(403 if "super-admin" in reason else 400, reason)
    patch = {"status": target, "updated_at": _now()}
    if target in ("approved", "rejected"):
        patch["approved_by"] = ctx.get("email")
        patch["approved_at"] = _now()
    if target == "resolved":
        res = (body.get("resolution") or "").strip()
        patch["resolution"] = res or fr.get("resolution")
        patch["resolved_at"] = _now()
        if body.get("mark_reviewed"):
            ids = [str(i) for i in (fr.get("sample_failure_ids") or []) if i]
            if ids:
                try:
                    get_supabase().schema("core").table("failure_log").update({
                        "reviewed": True, "reviewed_by": (ctx.get("email") or "support"),
                        "reviewed_at": _now()}).in_("id", ids).execute()
                except Exception:
                    pass
    db("support_fix_request").update(patch).eq("id", fid).execute()
    return {"ok": True, "status": target, "approved_by": patch.get("approved_by")}


# ── Admin-attention provider (owner 2026-07-26) ───────────────────────────────────────────────────
# Imported for the @register_provider side effect ONLY: a tenant collecting tickets with no alert email
# configured anywhere surfaces in the login attention popup. No routes, no gates, no core edits.
from . import attention as _attention   # noqa: E402,F401
