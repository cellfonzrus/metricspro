"""Internal Chat — Phase 1 (core messaging). Owner directive 2026-08-19. See
docs/APPROVALS_AND_CHAT_PLAN.md.

Channels + 1:1/group DMs + messages + membership + unread/read receipts, in the storeops schema behind
FastAPI (service-role). Identity (sender + membership) is resolved from the caller's token — never
trusted from the body, the same anti-spoof stance as the time clock. Realtime (Supabase Realtime
broadcast) + rich features (reactions, threads, attachments, presence) land in later phases; Phase 1
clients poll GET /messages.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile

from app.core.database import get_supabase
from app.modules.approvals import engine
from app.modules.chat import push, realtime

ORG_ID = "00000000-0000-0000-0000-000000000001"
BUCKET = "chat-attachments"   # Supabase Storage bucket, private (signed-url access), same as helpdesk


def _require_member(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Router-wide gate: every chat endpoint is for a signed-in member of the tenant.

    This import used to name a `_require_member` that had never been defined in storeops.router. A
    router-level dependency runs before EVERY handler, and the import sits inside the function body,
    so nothing failed at boot — each request instead raised ImportError, which is not an
    HTTPException, and came back from main.HardeningMiddleware as a masked 500. Every /api/v1/chat/*
    call answered that way from the day the module shipped, /directory included, which is why user
    search found nobody and nobody could be added.

    The call is unchanged: storeops now DEFINES the shared gate the approvals router imports the same
    way, so both modules resolve membership through one implementation instead of a private copy
    each."""
    from app.modules.storeops.router import _require_member as sm
    sm(authorization, org_id)


router = APIRouter(prefix="/chat", tags=["Chat"], dependencies=[Depends(_require_member)])


def sb():
    return get_supabase().schema("storeops")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _me(authorization: str, org_id: str):
    """(org_id, employee_id, name) for the signed-in caller. 403 if the login isn't linked to an
    employee — you can't chat as a person the system can't name."""
    from app.modules.storeops.router import _caller_identity
    oid, eid = _caller_identity(authorization)
    org_id = oid or org_id
    if not eid:
        raise HTTPException(403, "your login isn't linked to an employee record — ask an admin to set "
                                 "your Employee ID in Roles & Access")
    rows = (sb().table("employees").select("name").eq("org_id", org_id)
            .eq("employee_id", eid).limit(1).execute().data) or []
    return org_id, eid, ((rows[0].get("name") if rows else None) or eid)


def _member_ids(org_id, channel_id):
    return [m.get("employee_id") for m in
            (sb().table("chat_members").select("employee_id").eq("org_id", org_id)
             .eq("channel_id", channel_id).execute().data or [])]


def _is_member(org_id, channel_id, eid) -> bool:
    rows = (sb().table("chat_members").select("id").eq("org_id", org_id)
            .eq("channel_id", channel_id).eq("employee_id", eid).limit(1).execute().data) or []
    return bool(rows)


def _require_channel_member(org_id, channel_id, eid):
    if not _is_member(org_id, channel_id, eid):
        raise HTTPException(403, "you are not a member of this conversation")


def _is_chat_admin(authorization, org_id) -> bool:
    """A moderator who may act on anyone's message (delete) + drive retention (Phase 4). Chat has no
    bespoke roles table — reuse the platform role permissions: an explicit `chat_admin` grant, or a
    company-wide ('all') reporting scope, passes. Best-effort; a resolution failure denies."""
    try:
        from app.modules.core.router import _uid_from_token
        from app.core.tenant_middleware import caller_app_user_http
        from app.modules.storeops.router import _role_permissions
        uid = _uid_from_token(authorization)
        row = (caller_app_user_http(uid, "role") or {}) if uid else {}
        perms = _role_permissions(org_id, (row.get("role") or "").strip())
        return perms.get("chat_admin") is True or (perms.get("scope") or "") == "all"
    except Exception:
        return False


def _person_is_active(person: dict) -> bool:
    """NULL-SAFE "is this person active". `storeops.employees.is_active` is NULLABLE (`DEFAULT true`,
    no NOT NULL), so any roster row that predates the column — or any import that never set it — reads
    NULL, and a PostgREST `.eq("is_active", True)` DROPS it. Only an EXPLICIT false is inactive: the
    same rule storeops._store_is_active / _inactive_ids_from were hard-won on and every frontend
    picker's `is_active !== false` already follows. Python-side, post-fetch, for the reason that
    doctrine spells out. Keeps the chat directory listing the same people HR does.

    Shared with add_member so the API can never refuse someone the picker just offered."""
    return person.get("is_active") is not False


def _dm_key(ids):
    return "|".join(sorted(set(str(i) for i in ids if i)))


def _bump(org_id, channel_id):
    try:
        sb().table("chat_channels").update({"updated_at": _now()}).eq("org_id", org_id).eq("id", channel_id).execute()
    except Exception:
        pass


# ── Channels / DMs ────────────────────────────────────────────────────────────────────────────
@router.get("/channels")
def my_channels(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The caller's conversations — channels + DMs they belong to — newest-active first, each with an
    unread count and a last-message preview. Powers the sidebar."""
    org_id, eid, _name = _me(authorization, org_id)
    mine = (sb().table("chat_members").select("channel_id,last_read_at,muted")
            .eq("org_id", org_id).eq("employee_id", eid).execute().data) or []
    if not mine:
        return {"channels": []}
    by_ch = {m["channel_id"]: m for m in mine}
    ids = list(by_ch.keys())
    chans = (sb().table("chat_channels").select("*").eq("org_id", org_id)
             .in_("id", ids).eq("archived", False).execute().data) or []
    # members (for DM naming) + last message + unread, resolved in Python (robust, no PostgREST embeds)
    members = (sb().table("chat_members").select("channel_id,employee_id")
               .eq("org_id", org_id).in_("channel_id", ids).execute().data) or []
    mem_by_ch: dict = {}
    for m in members:
        mem_by_ch.setdefault(m["channel_id"], []).append(m["employee_id"])
    msgs = (sb().table("chat_messages").select("channel_id,body,kind,sender_name,created_at")
            .eq("org_id", org_id).in_("channel_id", ids).order("created_at", desc=True)
            .limit(1000).execute().data) or []
    last_by_ch: dict = {}
    for m in msgs:
        last_by_ch.setdefault(m["channel_id"], m)   # first seen = newest (desc order)
    out = []
    for c in chans:
        mrow = by_ch.get(c["id"], {})
        lr = mrow.get("last_read_at")
        unread = sum(1 for m in msgs if m["channel_id"] == c["id"]
                     and (lr is None or str(m.get("created_at")) > str(lr)))
        last = last_by_ch.get(c["id"])
        out.append({**c, "members": mem_by_ch.get(c["id"], []), "unread": unread,
                    "muted": mrow.get("muted", False),
                    "last_message": ({"preview": (last.get("body") or "")[:120], "at": last.get("created_at"),
                                      "sender_name": last.get("sender_name")} if last else None)})
    out.sort(key=lambda c: (c.get("last_message") or {}).get("at") or c.get("updated_at") or "", reverse=True)
    return {"channels": out}


@router.post("/channels")
def create_channel(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Create a named channel and add the creator (owner) + any initial members. Body:
    {name, topic?, is_private?, members?:[employee_id,...]}."""
    org_id, eid, _name = _me(authorization, org_id)
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    ch = (sb().table("chat_channels").insert({
        "org_id": org_id, "kind": "channel", "name": name, "topic": body.get("topic"),
        "is_private": bool(body.get("is_private")), "created_by": eid}).execute().data or [{}])[0]
    cid = ch.get("id")
    members = set(body.get("members") or []) | {eid}
    for m in members:
        try:
            sb().table("chat_members").insert({
                "org_id": org_id, "channel_id": cid, "employee_id": m,
                "role": ("owner" if m == eid else "member"),
                "last_read_at": (_now() if m == eid else None)}).execute()
        except Exception:
            pass   # duplicate member → ignore
    return {"channel": ch}


@router.post("/dm")
def open_dm(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Open (or fetch, idempotently) a DM/group with the given people. Body: {employee_ids:[...]}
    or {employee_id}. The caller is always included. Deduped by the member set (dm_key)."""
    org_id, eid, _name = _me(authorization, org_id)
    others = body.get("employee_ids") or ([body.get("employee_id")] if body.get("employee_id") else [])
    ids = [str(i).strip() for i in ([eid] + list(others)) if str(i or "").strip()]
    ids = sorted(set(ids))
    if len(ids) < 2:
        raise HTTPException(400, "a DM needs at least one other person")
    key = _dm_key(ids)
    existing = (sb().table("chat_channels").select("*").eq("org_id", org_id)
                .eq("dm_key", key).limit(1).execute().data) or []
    if existing:
        return {"channel": existing[0], "created": False}
    kind = "dm" if len(ids) == 2 else "group"
    ch = (sb().table("chat_channels").insert({
        "org_id": org_id, "kind": kind, "dm_key": key, "created_by": eid}).execute().data or [{}])[0]
    cid = ch.get("id")
    for m in ids:
        try:
            sb().table("chat_members").insert({
                "org_id": org_id, "channel_id": cid, "employee_id": m,
                "last_read_at": (_now() if m == eid else None)}).execute()
        except Exception:
            pass
    return {"channel": ch, "created": True}


# ── Messages ──────────────────────────────────────────────────────────────────────────────────
@router.get("/channels/{channel_id}/messages")
def list_messages(channel_id: str, limit: int = 50, before: str = "",
                  authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Messages in a conversation, oldest→newest within the page. ?before=ISO paginates backward.
    Membership required."""
    org_id, eid, _name = _me(authorization, org_id)
    _require_channel_member(org_id, channel_id, eid)
    limit = max(1, min(int(limit or 50), 200))
    q = sb().table("chat_messages").select("*").eq("org_id", org_id).eq("channel_id", channel_id)
    if before:
        q = q.lt("created_at", before)
    rows = q.order("created_at", desc=True).limit(limit).execute().data or []
    rows = list(reversed(rows))   # return chronological
    return {"messages": _enrich_messages(org_id, channel_id, rows)}


def _enrich_messages(org_id, channel_id, rows):
    """Attach per-message reactions ({emoji: [employee_id,...]}) and a small reply_to preview for
    threaded messages. A soft-deleted message keeps its row but its body is masked to the tombstone."""
    for r in rows:
        if r.get("deleted_at"):
            r["body"] = None   # never surface the original text of a deleted message
    ids = [r["id"] for r in rows if r.get("id")]
    reactions_by_msg: dict = {}
    if ids:
        rx = (sb().table("chat_reactions").select("message_id,employee_id,emoji")
              .eq("org_id", org_id).in_("message_id", ids).execute().data) or []
        for x in rx:
            reactions_by_msg.setdefault(x["message_id"], {}).setdefault(x["emoji"], []).append(x["employee_id"])
    # Parent previews for replies — fetch the referenced parents in one shot.
    parent_ids = list({r.get("reply_to_id") for r in rows if r.get("reply_to_id")})
    parents: dict = {}
    if parent_ids:
        prows = (sb().table("chat_messages").select("id,sender_name,body,deleted_at")
                 .eq("org_id", org_id).in_("id", parent_ids).execute().data) or []
        for p in prows:
            parents[p["id"]] = {"id": p["id"], "sender_name": p.get("sender_name"),
                                "preview": (None if p.get("deleted_at") else (p.get("body") or "")[:140])}
    # Approval cards — surface the LIVE linked request so the card always reflects the current status.
    appr_ids = list({r.get("approval_request_id") for r in rows if r.get("approval_request_id")})
    appr: dict = {}
    if appr_ids:
        arows = (sb().table("approval_requests")
                 .select("id,title,summary,status,decision,decided_by_name,type,priority,requested_by_name")
                 .eq("org_id", org_id).in_("id", appr_ids).execute().data) or []
        for a in arows:
            appr[a["id"]] = a
    for r in rows:
        r["reactions"] = reactions_by_msg.get(r["id"], {})
        r["reply_to"] = parents.get(r.get("reply_to_id")) if r.get("reply_to_id") else None
        r["approval"] = appr.get(r.get("approval_request_id")) if r.get("approval_request_id") else None
    return rows


@router.post("/channels/{channel_id}/messages")
def send_message(channel_id: str, body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Post a message. Sender is stamped from the token (never the body). Membership required; a
    non-private channel auto-joins the sender. Bumps channel recency + marks the sender caught up."""
    org_id, eid, name = _me(authorization, org_id)
    chans = (sb().table("chat_channels").select("*").eq("org_id", org_id)
             .eq("id", channel_id).limit(1).execute().data) or []
    if not chans:
        raise HTTPException(404, "conversation not found")
    ch = chans[0]
    if not _is_member(org_id, channel_id, eid):
        if ch.get("kind") == "channel" and not ch.get("is_private"):
            sb().table("chat_members").insert({
                "org_id": org_id, "channel_id": channel_id, "employee_id": eid,
                "last_read_at": _now()}).execute()   # open channel → auto-join
        else:
            raise HTTPException(403, "you are not a member of this conversation")
    text = (body.get("body") or "").strip()
    atts = body.get("attachments") or []
    if not isinstance(atts, list):
        atts = []
    if not text and not atts:
        raise HTTPException(400, "a message needs text or an attachment")
    msg = (sb().table("chat_messages").insert({
        "org_id": org_id, "channel_id": channel_id, "sender_employee_id": eid, "sender_name": name,
        "body": text, "kind": "text", "reply_to_id": (body.get("reply_to_id") or None),
        "attachments": atts}).execute().data or [{}])[0]
    _bump(org_id, channel_id)
    # sender is caught up on their own message
    try:
        sb().table("chat_members").update({"last_read_at": _now()}).eq("org_id", org_id).eq(
            "channel_id", channel_id).eq("employee_id", eid).execute()
    except Exception:
        pass
    # Realtime: nudge the channel thread + every member's sidebar to re-fetch (content stays behind REST).
    members = _member_ids(org_id, channel_id)
    realtime.notify_channel(org_id, channel_id, kind="message", message_id=msg.get("id"), member_ids=members)
    # Mobile push to the OTHER members' devices (best-effort; a no-op until the operator sets FCM creds).
    push.notify(org_id, [m for m in members if m != eid], title=name,
                body=(text[:140] if text else "sent an attachment"), data={"channel_id": str(channel_id)})
    return {"message": msg}


@router.post("/channels/{channel_id}/read")
def mark_read(channel_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Mark the conversation read up to now (clears its unread badge for the caller)."""
    org_id, eid, _name = _me(authorization, org_id)
    _require_channel_member(org_id, channel_id, eid)
    sb().table("chat_members").update({"last_read_at": _now()}).eq("org_id", org_id).eq(
        "channel_id", channel_id).eq("employee_id", eid).execute()
    return {"ok": True}


@router.post("/channels/{channel_id}/members")
def add_member(channel_id: str, body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Add someone to a channel/group. Caller must be a member. Body: {employee_id}."""
    org_id, eid, _name = _me(authorization, org_id)
    _require_channel_member(org_id, channel_id, eid)
    who = (body.get("employee_id") or "").strip()
    if not who:
        raise HTTPException(400, "employee_id is required")
    if _is_member(org_id, channel_id, who):
        return {"ok": True, "added": False}
    # The body names WHO to add, so it is checked against the caller's OWN org roster (never trusted
    # as given, same stance as the sender identity) and against the same active rule the directory
    # offers. The insert used to sit under a blanket `except Exception: pass` that answered
    # {"ok": true} to EVERY failure, so a refused add read to the client as a member who was added.
    rows = (sb().table("employees").select("employee_id,is_active").eq("org_id", org_id)
            .eq("employee_id", who).limit(1).execute().data) or []
    if not rows or not _person_is_active(rows[0]):
        raise HTTPException(404, "no active employee with that id in this company")
    sb().table("chat_members").insert({
        "org_id": org_id, "channel_id": channel_id, "employee_id": who}).execute()
    return {"ok": True, "added": True}


# ── Reactions / edit / delete (Phase 2) ────────────────────────────────────────────────────────
def _message_in_channel(org_id, channel_id, message_id):
    rows = (sb().table("chat_messages").select("*").eq("org_id", org_id)
            .eq("id", message_id).eq("channel_id", channel_id).limit(1).execute().data) or []
    return rows[0] if rows else None


def _reactions_for(org_id, message_id):
    rx = (sb().table("chat_reactions").select("employee_id,emoji").eq("org_id", org_id)
          .eq("message_id", message_id).execute().data) or []
    out: dict = {}
    for x in rx:
        out.setdefault(x["emoji"], []).append(x["employee_id"])
    return out


@router.post("/channels/{channel_id}/messages/{message_id}/reactions")
def toggle_reaction(channel_id: str, message_id: str, body: dict,
                    authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Toggle the caller's emoji reaction on a message (add if absent, remove if present). Membership
    required. Body: {emoji}. Returns the message's full reaction map."""
    org_id, eid, _name = _me(authorization, org_id)
    _require_channel_member(org_id, channel_id, eid)
    emoji = (body.get("emoji") or "").strip()
    if not emoji:
        raise HTTPException(400, "emoji is required")
    if not _message_in_channel(org_id, channel_id, message_id):
        raise HTTPException(404, "message not found")
    existing = (sb().table("chat_reactions").select("id").eq("org_id", org_id)
                .eq("message_id", message_id).eq("employee_id", eid).eq("emoji", emoji)
                .limit(1).execute().data) or []
    if existing:
        sb().table("chat_reactions").delete().eq("org_id", org_id).eq("message_id", message_id).eq(
            "employee_id", eid).eq("emoji", emoji).execute()
        added = False
    else:
        try:
            sb().table("chat_reactions").insert({
                "org_id": org_id, "channel_id": channel_id, "message_id": message_id,
                "employee_id": eid, "emoji": emoji}).execute()
        except Exception:
            pass   # raced duplicate → treat as present
        added = True
    realtime.notify_channel(org_id, channel_id, kind="reaction", message_id=message_id,
                            member_ids=_member_ids(org_id, channel_id))
    return {"reactions": _reactions_for(org_id, message_id), "added": added}


@router.patch("/channels/{channel_id}/messages/{message_id}")
def edit_message(channel_id: str, message_id: str, body: dict,
                 authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Edit a message's text. Only the ORIGINAL author may edit; a deleted message can't be edited.
    Stamps edited_at. Body: {body}."""
    org_id, eid, _name = _me(authorization, org_id)
    _require_channel_member(org_id, channel_id, eid)
    m = _message_in_channel(org_id, channel_id, message_id)
    if not m:
        raise HTTPException(404, "message not found")
    if m.get("deleted_at"):
        raise HTTPException(400, "a deleted message can't be edited")
    if m.get("sender_employee_id") != eid:
        raise HTTPException(403, "only the author can edit this message")
    text = (body.get("body") or "").strip()
    if not text:
        raise HTTPException(400, "message body is required")
    upd = {"body": text, "edited_at": _now()}
    sb().table("chat_messages").update(upd).eq("org_id", org_id).eq("id", message_id).execute()
    realtime.notify_channel(org_id, channel_id, kind="edit", message_id=message_id,
                            member_ids=_member_ids(org_id, channel_id))
    return {"message": {**m, **upd}}


@router.delete("/channels/{channel_id}/messages/{message_id}")
def delete_message(channel_id: str, message_id: str,
                   authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Soft-delete a message (tombstone: deleted_at set, body cleared). The author OR a chat admin may
    delete; the row stays so threads/reads don't break."""
    org_id, eid, _name = _me(authorization, org_id)
    _require_channel_member(org_id, channel_id, eid)
    m = _message_in_channel(org_id, channel_id, message_id)
    if not m:
        raise HTTPException(404, "message not found")
    if m.get("sender_employee_id") != eid and not _is_chat_admin(authorization, org_id):
        raise HTTPException(403, "only the author or a chat admin can delete this message")
    upd = {"deleted_at": _now(), "body": None}
    sb().table("chat_messages").update(upd).eq("org_id", org_id).eq("id", message_id).execute()
    realtime.notify_channel(org_id, channel_id, kind="delete", message_id=message_id,
                            member_ids=_member_ids(org_id, channel_id))
    return {"ok": True}


# ── Attachments (Supabase Storage, tenant + channel scoped path) ────────────────────────────────
def _ensure_bucket():
    c = get_supabase()
    try:
        c.storage.get_bucket(BUCKET)
    except Exception:
        try:
            c.storage.create_bucket(BUCKET)   # private by default (signed-url access only)
        except Exception:
            pass
    return c


def _sign(path, expires=3600):
    try:
        res = get_supabase().storage.from_(BUCKET).create_signed_url(path, expires)
        return (res.get("signedURL") or res.get("signed_url") or res.get("signedUrl")) if isinstance(res, dict) else res
    except Exception:
        return None


@router.post("/channels/{channel_id}/attachments")
async def upload_attachment(channel_id: str, file: UploadFile = File(...),
                            authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Upload a file/image to a conversation. Returns an attachment descriptor (with a 1h signed URL)
    the client then attaches to a message via POST /messages {attachments:[...]}. Membership required."""
    org_id, eid, _name = _me(authorization, org_id)
    _require_channel_member(org_id, channel_id, eid)
    data = await file.read()
    safe = (file.filename or "file").replace("/", "_")
    path = f"{org_id}/{channel_id}/{uuid.uuid4().hex}_{safe}"
    c = _ensure_bucket()
    try:
        c.storage.from_(BUCKET).upload(path, data, {"content-type": file.content_type or "application/octet-stream"})
    except Exception as e:
        raise HTTPException(500, f"upload failed: {e}")
    return {"attachment": {"file_name": safe, "storage_path": path,
                           "mime_type": file.content_type, "file_size": len(data),
                           "url": _sign(path)}}


@router.get("/channels/{channel_id}/attachments/sign")
def sign_attachment(channel_id: str, path: str = "",
                    authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Re-sign an attachment for display. The path MUST live under this org+channel prefix (anti-
    traversal), and the caller must be a member — so a signed URL is only ever minted for someone
    entitled to the conversation."""
    org_id, eid, _name = _me(authorization, org_id)
    _require_channel_member(org_id, channel_id, eid)
    prefix = f"{org_id}/{channel_id}/"
    if not path or not path.startswith(prefix) or ".." in path:
        raise HTTPException(403, "not an attachment in this conversation")
    url = _sign(path)
    if not url:
        raise HTTPException(500, "could not sign url")
    return {"url": url}


# ── Approvals-in-chat (Phase 3) ─────────────────────────────────────────────────────────────────
@router.post("/channels/{channel_id}/approvals")
def raise_approval(channel_id: str, body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Raise an approval request INTO a conversation: create it on the unified engine and post a
    kind='approval' card that links to it (approval_request_id). Anyone in the conversation may raise
    one; only an eligible approver can decide it. Body: {title, summary?, type?, priority?, store_code?,
    market?, assignee_email?, assignee_kind?}."""
    org_id, eid, name = _me(authorization, org_id)
    _require_channel_member(org_id, channel_id, eid)
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    # notify=False: the card IS the in-chat notification (the engine's email path stays for module-raised
    # requests). Requester is stamped from the token, never the body.
    req = engine.create_request(
        org_id, type=(body.get("type") or "manual"), title=title,
        summary=body.get("summary"), payload=body.get("payload") or {},
        requested_by=eid, requested_by_name=name,
        store_code=body.get("store_code"), market=body.get("market"),
        assignee_email=body.get("assignee_email"), assignee_kind=body.get("assignee_kind"),
        priority=(body.get("priority") or "normal"), notify=False)
    if not req.get("id"):
        raise HTTPException(400, f"could not create the request: {req.get('error')}")
    msg = (sb().table("chat_messages").insert({
        "org_id": org_id, "channel_id": channel_id, "sender_employee_id": eid, "sender_name": name,
        "body": title, "kind": "approval", "approval_request_id": req["id"]}).execute().data or [{}])[0]
    # Link the request back to its card (the mig-867 chat_message_id column) — best-effort.
    try:
        sb().table("approval_requests").update({"chat_message_id": msg.get("id")}).eq(
            "org_id", org_id).eq("id", req["id"]).execute()
    except Exception:
        pass
    _bump(org_id, channel_id)
    realtime.notify_channel(org_id, channel_id, kind="approval", message_id=msg.get("id"),
                            member_ids=_member_ids(org_id, channel_id))
    return {"message": msg, "approval": req}


@router.post("/channels/{channel_id}/messages/{message_id}/decision")
def decide_from_chat(channel_id: str, message_id: str, body: dict,
                     authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Approve/deny an approval card inline. Reuses the approvals module's OWN RBAC + engine.decide (no
    logic is duplicated), then broadcasts so every viewer's card updates. Body: {decision, note?}."""
    org_id, eid, _name = _me(authorization, org_id)
    _require_channel_member(org_id, channel_id, eid)
    m = _message_in_channel(org_id, channel_id, message_id)
    if not m:
        raise HTTPException(404, "message not found")
    rid = m.get("approval_request_id")
    if m.get("kind") != "approval" or not rid:
        raise HTTPException(400, "not an approval message")
    from app.modules.approvals import router as approvals_router
    mgr = approvals_router._caller(authorization, org_id)
    dorg = mgr.get("org_id") or org_id
    req = engine.get_request(dorg, rid)
    if not req:
        raise HTTPException(404, "unknown request")
    if not approvals_router._may_decide(authorization, dorg, req):
        raise HTTPException(403, "you are not an eligible approver for this request")
    decision = str(body.get("decision") or "").strip().lower()
    try:
        out = engine.decide(dorg, rid, decision=decision, actor=mgr.get("email"),
                            actor_name=mgr.get("email"), note=(body.get("note") or None))
    except ValueError as e:
        emsg = str(e)
        raise HTTPException(409 if "already" in emsg else 400, emsg)
    except Exception as e:
        raise HTTPException(400, f"could not apply the decision: {e}")
    realtime.notify_channel(org_id, channel_id, kind="approval", message_id=message_id,
                            member_ids=_member_ids(org_id, channel_id))
    return {"ok": True, "status": out.get("status")}


@router.get("/unread")
def unread_count(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Total unread across the caller's conversations — for the nav badge."""
    data = my_channels(authorization=authorization, org_id=org_id)
    total = sum(int(c.get("unread") or 0) for c in data["channels"] if not c.get("muted"))
    return {"total": total, "by_channel": {c["id"]: c.get("unread", 0) for c in data["channels"]}}


# ── Search + org management (Phase 4) ────────────────────────────────────────────────────────────
@router.get("/search")
def search_messages(q: str = "", limit: int = 40,
                    authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Full-text-ish search over messages in conversations the caller belongs to. Membership is the
    gate — you never see a hit from a channel you aren't in. Newest first."""
    org_id, eid, _name = _me(authorization, org_id)
    term = (q or "").strip()
    if len(term) < 2:
        return {"results": []}
    my = (sb().table("chat_members").select("channel_id").eq("org_id", org_id)
          .eq("employee_id", eid).execute().data) or []
    ids = [m["channel_id"] for m in my]
    if not ids:
        return {"results": []}
    limit = max(1, min(int(limit or 40), 100))
    rows = (sb().table("chat_messages").select("id,channel_id,sender_name,body,created_at")
            .eq("org_id", org_id).in_("channel_id", ids).is_("deleted_at", "null")
            .ilike("body", f"%{term}%").order("created_at", desc=True).limit(limit).execute().data) or []
    # channel display hints (name for channels; member ids for DMs → client names them)
    chans = (sb().table("chat_channels").select("id,kind,name").eq("org_id", org_id)
             .in_("id", list({r["channel_id"] for r in rows})).execute().data) or [] if rows else []
    cmeta = {c["id"]: c for c in chans}
    out = [{**r, "channel": cmeta.get(r["channel_id"])} for r in rows]
    return {"results": out}


@router.get("/channels/browse")
def browse_channels(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Public channels in the org the caller could join — each flagged with whether they're already a
    member + a member count. Private channels + DMs are never listed (you must be invited)."""
    org_id, eid, _name = _me(authorization, org_id)
    chans = (sb().table("chat_channels").select("id,name,topic,created_by,created_at")
             .eq("org_id", org_id).eq("kind", "channel").eq("is_private", False)
             .eq("archived", False).execute().data) or []
    ids = [c["id"] for c in chans]
    mem = (sb().table("chat_members").select("channel_id,employee_id").eq("org_id", org_id)
           .in_("channel_id", ids).execute().data) or [] if ids else []
    count: dict = {}
    mine = set()
    for m in mem:
        count[m["channel_id"]] = count.get(m["channel_id"], 0) + 1
        if m["employee_id"] == eid:
            mine.add(m["channel_id"])
    out = [{**c, "member_count": count.get(c["id"], 0), "joined": c["id"] in mine} for c in chans]
    out.sort(key=lambda c: (not c["joined"], -(c["member_count"]), (c.get("name") or "").lower()))
    return {"channels": out}


@router.post("/channels/{channel_id}/join")
def join_channel(channel_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Join a PUBLIC channel. A private channel / DM can only be joined by invitation (add_member)."""
    org_id, eid, _name = _me(authorization, org_id)
    chans = (sb().table("chat_channels").select("*").eq("org_id", org_id)
             .eq("id", channel_id).limit(1).execute().data) or []
    if not chans:
        raise HTTPException(404, "conversation not found")
    ch = chans[0]
    if ch.get("kind") != "channel" or ch.get("is_private"):
        raise HTTPException(403, "this conversation is invite-only")
    if not _is_member(org_id, channel_id, eid):
        try:
            sb().table("chat_members").insert({
                "org_id": org_id, "channel_id": channel_id, "employee_id": eid,
                "last_read_at": _now()}).execute()
        except Exception:
            pass
    return {"ok": True}


@router.post("/channels/{channel_id}/leave")
def leave_channel(channel_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Leave a conversation (drops the caller's membership; their unread badge clears with it)."""
    org_id, eid, _name = _me(authorization, org_id)
    sb().table("chat_members").delete().eq("org_id", org_id).eq(
        "channel_id", channel_id).eq("employee_id", eid).execute()
    return {"ok": True}


@router.get("/channels/{channel_id}/members")
def list_members(channel_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The members of a conversation (id + name + role), for the manage-members panel. Membership required."""
    org_id, eid, _name = _me(authorization, org_id)
    _require_channel_member(org_id, channel_id, eid)
    mem = (sb().table("chat_members").select("employee_id,role,joined_at").eq("org_id", org_id)
           .eq("channel_id", channel_id).execute().data) or []
    ids = [m["employee_id"] for m in mem]
    emps = (sb().table("employees").select("employee_id,name").eq("org_id", org_id)
            .in_("employee_id", ids).execute().data) or [] if ids else []
    nm = {e["employee_id"]: e.get("name") for e in emps}
    out = [{**m, "name": nm.get(m["employee_id"]) or m["employee_id"]} for m in mem]
    return {"members": out}


@router.delete("/channels/{channel_id}/members/{who}")
def remove_member(channel_id: str, who: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Remove someone from a channel/group. The caller must be an OWNER of the channel or a chat admin.
    You can always remove yourself (that's `leave`)."""
    org_id, eid, _name = _me(authorization, org_id)
    _require_channel_member(org_id, channel_id, eid)
    if who != eid:
        my = (sb().table("chat_members").select("role").eq("org_id", org_id)
              .eq("channel_id", channel_id).eq("employee_id", eid).limit(1).execute().data) or []
        is_owner = bool(my) and my[0].get("role") == "owner"
        if not is_owner and not _is_chat_admin(authorization, org_id):
            raise HTTPException(403, "only a channel owner or a chat admin can remove members")
    sb().table("chat_members").delete().eq("org_id", org_id).eq(
        "channel_id", channel_id).eq("employee_id", who).execute()
    return {"ok": True}


@router.patch("/channels/{channel_id}")
def update_channel(channel_id: str, body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Rename / re-topic / archive a channel. Owner or chat admin only. Body: {name?, topic?, archived?}."""
    org_id, eid, _name = _me(authorization, org_id)
    _require_channel_member(org_id, channel_id, eid)
    my = (sb().table("chat_members").select("role").eq("org_id", org_id)
          .eq("channel_id", channel_id).eq("employee_id", eid).limit(1).execute().data) or []
    is_owner = bool(my) and my[0].get("role") == "owner"
    if not is_owner and not _is_chat_admin(authorization, org_id):
        raise HTTPException(403, "only a channel owner or a chat admin can change this conversation")
    upd: dict = {}
    if "name" in body and (body.get("name") or "").strip():
        upd["name"] = body["name"].strip()
    if "topic" in body:
        upd["topic"] = body.get("topic")
    if "archived" in body:
        upd["archived"] = bool(body.get("archived"))
    if not upd:
        raise HTTPException(400, "nothing to update")
    row = (sb().table("chat_channels").update(upd).eq("org_id", org_id)
           .eq("id", channel_id).execute().data or [{}])[0]
    return {"channel": row}


@router.post("/admin/retention")
def run_retention(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Chat-admin retention sweep: hard-delete messages older than `days` across the org. Returns how
    many were removed. Reactions cascade via FK. Chat admin only."""
    org_id, eid, _name = _me(authorization, org_id)
    if not _is_chat_admin(authorization, org_id):
        raise HTTPException(403, "chat admin permission required")
    try:
        days = int(body.get("days") or 0)
    except Exception:
        days = 0
    if days < 1:
        raise HTTPException(400, "days must be a positive integer")
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    doomed = (sb().table("chat_messages").select("id").eq("org_id", org_id)
              .lt("created_at", cutoff).execute().data) or []
    n = 0
    for m in doomed:
        sb().table("chat_messages").delete().eq("org_id", org_id).eq("id", m["id"]).execute()
        n += 1
    return {"ok": True, "deleted": n, "cutoff": cutoff}


@router.get("/me")
def whoami(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The caller's identity for the chat client — the employee_id it needs to subscribe to its own
    realtime user topic, plus the topic string itself so the naming lives server-side only."""
    org_id, eid, name = _me(authorization, org_id)
    return {"org_id": org_id, "employee_id": eid, "name": name,
            "user_topic": realtime.user_topic(org_id, eid),
            "is_chat_admin": _is_chat_admin(authorization, org_id)}


# ── Voice/video signaling + mobile push (Phase 5) ────────────────────────────────────────────────
@router.get("/call/config")
def call_config(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """ICE (STUN/TURN) servers for WebRTC. Operator-supplied via CHAT_ICE_SERVERS (a JSON array of
    RTCIceServer objects); defaults to a public STUN server (enough for same-network calls — cross-NAT
    calls need the operator to add a TURN server). Call SIGNALING itself rides the Realtime channel
    client-side, so no relay lives here."""
    _me(authorization, org_id)   # members only
    import json
    import os
    raw = os.environ.get("CHAT_ICE_SERVERS", "")
    ice = None
    if raw:
        try:
            ice = json.loads(raw)
        except Exception:
            ice = None
    if not ice:
        ice = [{"urls": "stun:stun.l.google.com:19302"}]
    def _has_turn(servers):
        for s in servers:
            u = s.get("urls")
            urls = u if isinstance(u, list) else [u]
            if any("turn:" in str(x) for x in urls):
                return True
        return False
    return {"ice_servers": ice, "has_turn": _has_turn(ice), "call_topic_prefix": "chat-call:"}


@router.post("/push/register")
def push_register(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Register this device's push token for the signed-in employee. Body: {token, platform?}."""
    org_id, eid, _name = _me(authorization, org_id)
    token = (body.get("token") or "").strip()
    if not token:
        raise HTTPException(400, "token is required")
    ok = push.register(org_id, eid, token, platform=(body.get("platform") or "web"))
    return {"ok": ok, "delivery_configured": push.configured()}


@router.post("/push/unregister")
def push_unregister(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Drop this device's push token (sign-out / disable notifications). Body: {token}."""
    org_id, eid, _name = _me(authorization, org_id)
    push.unregister(org_id, (body.get("token") or ""))
    return {"ok": True}


@router.get("/directory")
def directory(q: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Active employees the caller can start a DM with (id + name). Optional ?q= name filter."""
    org_id, eid, _name = _me(authorization, org_id)
    rows = (sb().table("employees").select("employee_id,name,is_active").eq("org_id", org_id)
            .order("name").limit(1000).execute().data) or []
    s = (q or "").strip().lower()
    people = [{"employee_id": r.get("employee_id"), "name": r.get("name")}
              for r in rows if r.get("employee_id") and r.get("employee_id") != eid
              and _person_is_active(r) and (not s or s in (r.get("name") or "").lower())]
    return {"people": people}
