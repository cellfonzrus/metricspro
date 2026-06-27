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
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile, File

from app.core.database import get_supabase

router = APIRouter(prefix="/helpdesk", tags=["Helpdesk"])
ORG_ID = "00000000-0000-0000-0000-000000000001"
BUCKET = "ticket-attachments"


def db(name: str):
    return get_supabase().schema("storeops").table(name)


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── Module entitlement ────────────────────────────────────────────────────────────────────────
def _module_enabled(org_id: str) -> bool:
    rows = (db("tenant_modules").select("is_enabled")
            .eq("org_id", org_id).eq("module_key", "helpdesk").limit(1).execute().data or [])
    return bool(rows and rows[0].get("is_enabled"))


def _require_module(org_id: str):
    if not _module_enabled(org_id):
        raise HTTPException(403, "Helpdesk module not enabled for this tenant")


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
    "ticket_categories": ["name", "description", "sort_order", "is_active"],
    "ticket_priorities": ["key", "label", "color", "sort_order", "is_active"],
    "ticket_statuses": ["key", "label", "stage", "color", "sort_order", "is_active"],
    "ticket_teams": ["name", "is_active"],
    "ticket_custom_fields": ["field_key", "label", "field_type", "options", "is_required", "sort_order", "is_active"],
}


def _clean(table: str, body: dict) -> dict:
    return {k: body[k] for k in _CONFIG_COLS[table] if k in body}


def _cfg_list(table: str, org_id: str):
    return (db(table).select("*").eq("org_id", org_id)
            .order("sort_order").execute().data or [])


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
    return [_decorate(t, st, pr, ca, te) for t in rows]


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
    return {"ticket": ticket, "comments": comments, "events": events, "attachments": atts}


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

    # lifecycle timestamps off the new status's stage
    if "status_id" in upd:
        s = (db("ticket_statuses").select("stage").eq("id", upd["status_id"]).limit(1).execute().data or [{}])[0]
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
            c.storage.create_bucket(BUCKET, options={"public": False})
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
        return {"url": res.get("signedURL") or res.get("signed_url") or res.get("signedUrl")}
    except Exception as e:
        raise HTTPException(500, f"could not sign url: {e}")


# ── Dashboard (this tenant only; uses the fixed lifecycle stage so renamed labels still group) ──
@router.get("/stats/dashboard")
def dashboard(org_id: str = ORG_ID):
    _require_module(org_id)
    st, _, _, _ = _maps(org_id)
    tickets = db("tickets").select("status_id,created_at,resolved_at").eq("org_id", org_id).limit(5000).execute().data or []
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


# ── Notify (best-effort; never blocks ticket creation) ─────────────────────────────────────────
async def _notify_new_ticket(org_id: str, ticket: dict, requester: str):
    try:
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
