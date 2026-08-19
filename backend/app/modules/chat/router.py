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
from app.modules.chat import realtime

ORG_ID = "00000000-0000-0000-0000-000000000001"
BUCKET = "chat-attachments"   # Supabase Storage bucket, private (signed-url access), same as helpdesk


def _require_member(authorization: str = Header(default=""), org_id: str = ORG_ID):
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
    for r in rows:
        r["reactions"] = reactions_by_msg.get(r["id"], {})
        r["reply_to"] = parents.get(r.get("reply_to_id")) if r.get("reply_to_id") else None
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
    realtime.notify_channel(org_id, channel_id, kind="message", message_id=msg.get("id"),
                            member_ids=_member_ids(org_id, channel_id))
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
    try:
        sb().table("chat_members").insert({
            "org_id": org_id, "channel_id": channel_id, "employee_id": who}).execute()
    except Exception:
        pass   # already a member
    return {"ok": True}


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


@router.get("/unread")
def unread_count(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Total unread across the caller's conversations — for the nav badge."""
    data = my_channels(authorization=authorization, org_id=org_id)
    total = sum(int(c.get("unread") or 0) for c in data["channels"] if not c.get("muted"))
    return {"total": total, "by_channel": {c["id"]: c.get("unread", 0) for c in data["channels"]}}


@router.get("/me")
def whoami(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The caller's identity for the chat client — the employee_id it needs to subscribe to its own
    realtime user topic, plus the topic string itself so the naming lives server-side only."""
    org_id, eid, name = _me(authorization, org_id)
    return {"org_id": org_id, "employee_id": eid, "name": name,
            "user_topic": realtime.user_topic(org_id, eid)}


@router.get("/directory")
def directory(q: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Active employees the caller can start a DM with (id + name). Optional ?q= name filter."""
    org_id, eid, _name = _me(authorization, org_id)
    query = sb().table("employees").select("employee_id,name").eq("org_id", org_id).eq("is_active", True)
    rows = query.order("name").limit(1000).execute().data or []
    s = (q or "").strip().lower()
    people = [{"employee_id": r.get("employee_id"), "name": r.get("name")}
              for r in rows if r.get("employee_id") and r.get("employee_id") != eid
              and (not s or s in (r.get("name") or "").lower())]
    return {"people": people}
