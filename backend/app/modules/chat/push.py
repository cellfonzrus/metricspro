"""Chat push notifications — device-token registry + a GATED FCM send path. See
docs/APPROVALS_AND_CHAT_PLAN.md (Phase 5) and its Operator TODO.

Structured like the notify module: a best-effort, fire-and-forget send that degrades to a documented
NO-OP when the operator has not supplied credentials — it never pretends a push was delivered. Web Push
(VAPID) and Android/iOS registration tokens all funnel through FCM's HTTP API here; an APNs-direct path
is a documented follow-up. The operator must set CHAT_FCM_SERVER_KEY (an FCM server key) for delivery to
actually happen; until then registration still works (tokens are stored) and sends are skipped.
"""
import os
import threading
from datetime import datetime, timezone

import httpx


def _sb():
    from app.core.database import get_supabase
    return get_supabase().schema("storeops")


def _now():
    return datetime.now(timezone.utc).isoformat()


def register(org_id, employee_id, token, platform="web") -> bool:
    """Store (or refresh) a device token for an employee. Idempotent per (org, token)."""
    token = (token or "").strip()
    if not token:
        return False
    plat = platform if platform in ("web", "ios", "android") else "web"
    try:
        existing = (_sb().table("chat_push_tokens").select("id").eq("org_id", org_id)
                    .eq("token", token).limit(1).execute().data) or []
        if existing:
            _sb().table("chat_push_tokens").update({"employee_id": employee_id, "platform": plat,
                                                    "last_seen_at": _now()}).eq("org_id", org_id).eq("token", token).execute()
        else:
            _sb().table("chat_push_tokens").insert({
                "org_id": org_id, "employee_id": employee_id, "token": token,
                "platform": plat, "last_seen_at": _now()}).execute()
        return True
    except Exception:
        return False


def unregister(org_id, token):
    try:
        _sb().table("chat_push_tokens").delete().eq("org_id", org_id).eq("token", (token or "").strip()).execute()
    except Exception:
        pass


def _tokens_for(org_id, employee_ids):
    ids = [e for e in (employee_ids or []) if e]
    if not ids:
        return []
    try:
        rows = (_sb().table("chat_push_tokens").select("token").eq("org_id", org_id)
                .in_("employee_id", ids).execute().data) or []
        return [r["token"] for r in rows if r.get("token")]
    except Exception:
        return []


def _fcm_key():
    from app.core.config import settings
    return getattr(settings, "CHAT_FCM_SERVER_KEY", "") or os.environ.get("CHAT_FCM_SERVER_KEY", "")


def configured() -> bool:
    """True only when the operator has supplied FCM credentials — the gate for any real delivery."""
    return bool(_fcm_key())


def _send(tokens, title, body, data):
    key = _fcm_key()
    if not key or not tokens:
        return   # no creds / no devices → documented no-op, never a fake success
    try:
        httpx.post("https://fcm.googleapis.com/fcm/send",
                   headers={"Authorization": f"key={key}", "Content-Type": "application/json"},
                   json={"registration_ids": tokens[:1000],
                         "notification": {"title": title, "body": body},
                         "data": data or {}},
                   timeout=8.0)
    except Exception:
        pass


def notify(org_id, employee_ids, *, title, body, data=None):
    """Fan a push to the given employees' devices. Non-blocking + best-effort; a no-op without creds."""
    if not configured():
        return
    tokens = _tokens_for(org_id, employee_ids)
    if not tokens:
        return
    threading.Thread(target=_send, args=(tokens, title, body, data), daemon=True).start()
