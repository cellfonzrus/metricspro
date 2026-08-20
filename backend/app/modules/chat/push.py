"""Chat push notifications — device-token registry + GATED FCM (web/android) and APNs (iOS) send paths.
See docs/APPROVALS_AND_CHAT_PLAN.md (Phase 5) and its Operator TODO.

Structured like the notify module: best-effort, fire-and-forget sends that degrade to a documented
NO-OP when the operator has not supplied credentials — it never pretends a push was delivered. Each
stored token carries a `platform` (web | ios | android); a send is ROUTED by platform to the transport
that platform actually uses:

  • web      →  Web Push protocol + VAPID  (the browser PushSubscription the client stores; gated on
                                            CHAT_VAPID_PUBLIC_KEY / CHAT_VAPID_PRIVATE_KEY /
                                            CHAT_VAPID_SUBJECT; delivery needs the `pywebpush` dep)
  • android  →  FCM HTTP API               (gated on CHAT_FCM_SERVER_KEY)
  • ios      →  APNs HTTP/2 + ES256 JWT    (gated on CHAT_APNS_KEY_ID / CHAT_APNS_TEAM_ID /
                                            CHAT_APNS_AUTH_KEY / CHAT_APNS_BUNDLE_ID)

Each path is independently gated: registration always works (tokens are stored) and a send to a platform
with no credentials (or, for web, no pywebpush) is simply skipped — never a fake success. The APNs auth
token (a short-lived ES256 JWT signed with the operator's .p8 key) is built with PyJWT + cryptography and
cached < 40 min, per Apple's provider-token reuse guidance.
"""
import os
import threading
import time
from datetime import datetime, timezone

import httpx


def _sb():
    from app.core.database import get_supabase
    return get_supabase().schema("storeops")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _setting(name):
    """Read a credential from settings, falling back to the process env (same posture as _fcm_key)."""
    try:
        from app.core.config import settings
        v = getattr(settings, name, "")
    except Exception:
        v = ""
    return (v or os.environ.get(name, "") or "").strip()


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


def _tokens_by_platform(org_id, employee_ids):
    """{'web':[...], 'android':[...], 'ios':[...]} device tokens for the given employees."""
    ids = [e for e in (employee_ids or []) if e]
    out = {"web": [], "android": [], "ios": []}
    if not ids:
        return out
    try:
        rows = (_sb().table("chat_push_tokens").select("token,platform").eq("org_id", org_id)
                .in_("employee_id", ids).execute().data) or []
    except Exception:
        return out
    for r in rows:
        tok = r.get("token")
        if not tok:
            continue
        plat = r.get("platform") if r.get("platform") in out else "web"
        out[plat].append(tok)
    return out


# ── FCM (web + android) ───────────────────────────────────────────────────────────────────────────
def _fcm_key():
    return _setting("CHAT_FCM_SERVER_KEY")


def fcm_configured() -> bool:
    return bool(_fcm_key())


def _send_fcm(tokens, title, body, data):
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


# ── Web Push (browsers) — standard Web Push protocol + VAPID ───────────────────────────────────────
def _vapid():
    """(public_key, private_key, subject) or None when any VAPID credential is missing. The client
    subscribes with NEXT_PUBLIC_VAPID_PUBLIC_KEY (which must equal CHAT_VAPID_PUBLIC_KEY)."""
    pub = _setting("CHAT_VAPID_PUBLIC_KEY")
    priv = _setting("CHAT_VAPID_PRIVATE_KEY")
    sub = _setting("CHAT_VAPID_SUBJECT") or "mailto:admin@example.com"
    if not (pub and priv):
        return None
    return pub, priv, sub


def webpush_configured() -> bool:
    return _vapid() is not None


def _send_webpush(tokens, title, body, data):
    """Send a Web Push to each browser PushSubscription (stored as JSON in the token). REAL when the
    operator has set VAPID keys AND the `pywebpush` dependency is installed; otherwise an honest no-op
    (never a fake send). The service worker (frontend/public/sw.js) renders the notification."""
    conf = _vapid()
    if not conf or not tokens:
        return
    _pub, priv, subject = conf
    try:
        import json
        from pywebpush import webpush   # optional dep — see requirements/Operator TODO
    except Exception:
        return   # pywebpush not installed → documented no-op (the operator must add the dependency)
    payload = json.dumps({"title": title, "body": body, "data": data or {}})
    for tok in tokens[:1000]:
        try:
            sub_info = json.loads(tok) if isinstance(tok, str) else tok
            webpush(subscription_info=sub_info, data=payload, vapid_private_key=priv,
                    vapid_claims={"sub": subject})
        except Exception:
            pass


# ── APNs (iOS) — HTTP/2 + ES256 provider JWT ────────────────────────────────────────────────────────
def _apns_conf():
    """(key_id, team_id, auth_key_pem, bundle_id, host) or None when any credential is missing."""
    kid = _setting("CHAT_APNS_KEY_ID")
    team = _setting("CHAT_APNS_TEAM_ID")
    auth = _setting("CHAT_APNS_AUTH_KEY")   # the .p8 private key CONTENTS (PEM), not a path
    bundle = _setting("CHAT_APNS_BUNDLE_ID")
    if not (kid and team and auth and bundle):
        return None
    sandbox = _setting("CHAT_APNS_USE_SANDBOX") in ("1", "true", "yes")
    host = "api.sandbox.push.apple.com" if sandbox else "api.push.apple.com"
    # An auth key supplied as a one-line env var often has its newlines escaped — restore them so the
    # PEM parses.
    if "\\n" in auth and "-----BEGIN" in auth:
        auth = auth.replace("\\n", "\n")
    return kid, team, auth, bundle, host


def apns_configured() -> bool:
    return _apns_conf() is not None


_APNS_JWT = {"token": None, "iat": 0.0}


def _apns_jwt(kid, team, auth_key_pem):
    """A cached ES256 provider JWT (Apple recommends reuse for up to ~1h; we refresh at 40 min). Returns
    the signed token, or None if signing isn't possible (missing lib / bad key) — a no-op, never a fake."""
    now = time.time()
    if _APNS_JWT["token"] and (now - _APNS_JWT["iat"]) < 2400:
        return _APNS_JWT["token"]
    try:
        import jwt   # PyJWT (ES256 needs `cryptography`, present in the image)
        tok = jwt.encode({"iss": team, "iat": int(now)}, auth_key_pem,
                         algorithm="ES256", headers={"kid": kid})
        if isinstance(tok, bytes):
            tok = tok.decode("utf-8")
        _APNS_JWT["token"], _APNS_JWT["iat"] = tok, now
        return tok
    except Exception:
        return None


def _apns_payload(title, body, data):
    payload = {"aps": {"alert": {"title": title, "body": body}, "sound": "default"}}
    for k, v in (data or {}).items():
        if k != "aps":
            payload[k] = v
    return payload


def _send_apns(tokens, title, body, data):
    conf = _apns_conf()
    if not conf or not tokens:
        return   # no creds / no devices → documented no-op
    kid, team, auth, bundle, host = conf
    jwt_tok = _apns_jwt(kid, team, auth)
    if not jwt_tok:
        return   # couldn't sign (missing lib / bad key) — honest no-op, never a fake send
    payload = _apns_payload(title, body, data)
    try:
        with httpx.Client(http2=True, timeout=8.0) as client:
            for tok in tokens[:1000]:
                try:
                    client.post(f"https://{host}/3/device/{tok}",
                                headers={"authorization": f"bearer {jwt_tok}", "apns-topic": bundle,
                                         "apns-push-type": "alert"},
                                json=payload)
                except Exception:
                    pass
    except Exception:
        pass


# ── public API ──────────────────────────────────────────────────────────────────────────────────────
def configured() -> bool:
    """True when the operator has supplied credentials for AT LEAST ONE platform — the gate for any real
    delivery. Per-platform routing still skips a platform whose own credentials are absent."""
    return webpush_configured() or fcm_configured() or apns_configured()


def _fan(org_id, employee_ids, title, body, data):
    by_plat = _tokens_by_platform(org_id, employee_ids)
    if webpush_configured() and by_plat["web"]:
        _send_webpush(by_plat["web"], title, body, data)
    if fcm_configured() and by_plat["android"]:
        _send_fcm(by_plat["android"], title, body, data)
    if apns_configured() and by_plat["ios"]:
        _send_apns(by_plat["ios"], title, body, data)


def notify(org_id, employee_ids, *, title, body, data=None):
    """Fan a push to the given employees' devices, ROUTED by platform (web/android → FCM, ios → APNs).
    Non-blocking + best-effort; a no-op for any platform whose credentials are absent."""
    if not configured():
        return
    threading.Thread(target=_fan, args=(org_id, employee_ids, title, body, data), daemon=True).start()
