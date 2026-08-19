"""Chat realtime transport — Supabase Realtime Broadcast, driven from the backend. See
docs/APPROVALS_AND_CHAT_PLAN.md (Phase 1b).

The app never exposes DB tables to the browser (everything is service-role behind FastAPI), so chat
pushes live updates by POSTing to Supabase Realtime's HTTP broadcast API. The browser subscribes to a
per-USER topic (chat-user:<org>:<employee_id>) with its authenticated socket and, for the thread it has
open, the per-CHANNEL topic (chat:<channel_id>). Each broadcast is a lightweight HINT (channel + message
id + kind) — NOT the message body — so a stray subscriber never receives content it isn't entitled to;
the client re-fetches the authoritative row through the membership-gated REST API. Best-effort and
fire-and-forget: a realtime miss never fails the caller's write, and clients also short-poll as a
fallback when the socket is down.
"""
import os
import threading

import httpx

from app.core.config import settings

# One tunable kill switch (harnesses set it so an offline run never touches the network).
_DISABLED = os.environ.get("CHAT_REALTIME_DISABLE") == "1"


def channel_topic(channel_id) -> str:
    """Topic the browser subscribes to for the OPEN thread."""
    return f"chat:{channel_id}"


def user_topic(org_id, employee_id) -> str:
    """Per-recipient topic — every conversation a member belongs to fans out here, so one subscription
    keeps their whole sidebar (unread, recency) live."""
    return f"chat-user:{org_id}:{employee_id}"


def _post(messages):
    url = (settings.SUPABASE_URL or "").rstrip("/")
    key = settings.SUPABASE_SERVICE_KEY or settings.SUPABASE_KEY
    if not url or not key:
        return
    try:
        httpx.post(
            f"{url}/realtime/v1/api/broadcast",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"messages": messages},
            timeout=5.0,
        )
    except Exception:
        pass   # a dropped broadcast is invisible to correctness — the REST poll is the backstop


def publish(topics, event, payload):
    """Broadcast `event`/`payload` to one or more topics. Non-blocking (runs on a daemon thread) and
    best-effort — never raises into the caller's request."""
    if _DISABLED:
        return
    if isinstance(topics, str):
        topics = [topics]
    seen, msgs = set(), []
    for t in topics:
        if not t or t in seen:
            continue
        seen.add(t)
        msgs.append({"topic": t, "event": event, "payload": payload, "private": False})
    if not msgs:
        return
    threading.Thread(target=_post, args=(msgs,), daemon=True).start()


def notify_channel(org_id, channel_id, *, kind, message_id=None, member_ids=None, extra=None):
    """Fan a chat event out to the channel topic + every member's user topic. `kind` is the change class
    ('message' | 'reaction' | 'edit' | 'delete' | 'approval' | 'typing' — informational for the client)."""
    payload = {"v": 1, "kind": kind, "channel_id": str(channel_id)}
    if message_id is not None:
        payload["message_id"] = str(message_id)
    if extra:
        payload.update(extra)
    topics = [channel_topic(channel_id)]
    for m in (member_ids or []):
        topics.append(user_topic(org_id, m))
    publish(topics, "chat", payload)
