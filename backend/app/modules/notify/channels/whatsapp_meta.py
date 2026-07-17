"""WhatsApp delivery via the Meta Cloud API (Graph) — plain HTTPS, no SDK.

Business-initiated messages must use an APPROVED template. Two delivery shapes:
  • If the approved template has a DOCUMENT header (WHATSAPP_TEMPLATE_DOC_HEADER=true)
    we upload the file for a media id and reference it in the header component, so the
    recipient gets the actual file. A BODY text variable still carries the live link.
  • Otherwise (the current metricspro_report template) we send a body-only template —
    the BODY text variable carries the live report link, which the recipient taps to
    view/download. Attaching a file is impossible business-initiated without a doc header
    (Meta returns #132018 "Template does not contain title component, no parameters allowed").

For safety we ALSO self-heal: if a header-equipped send is rejected with that header
error, we retry once body-only so the link still goes out.

Needs env (see app/core/config.py):
  WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_TEMPLATE_NAME,
  WHATSAPP_TEMPLATE_LANG (default en), WHATSAPP_GRAPH_VERSION (default v21.0),
  WHATSAPP_TEMPLATE_DOC_HEADER (default false).
"""
import httpx

from app.core.config import settings


def is_configured() -> bool:
    return bool(
        settings.WHATSAPP_ACCESS_TOKEN
        and settings.WHATSAPP_PHONE_NUMBER_ID
        and settings.WHATSAPP_TEMPLATE_NAME
    )


def _base() -> str:
    ver = settings.WHATSAPP_GRAPH_VERSION or "v21.0"
    return f"https://graph.facebook.com/{ver}/{settings.WHATSAPP_PHONE_NUMBER_ID}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}


async def upload_media(cx: httpx.AsyncClient, data: bytes, mime: str, filename: str) -> str:
    files = {"file": (filename, data, mime)}
    form = {"messaging_product": "whatsapp", "type": mime}
    r = await cx.post(f"{_base()}/media", headers=_headers(), data=form, files=files)
    if r.status_code >= 300:
        raise RuntimeError(f"WhatsApp media upload {r.status_code}: {r.text[:300]}")
    return r.json().get("id", "")


def _template_msg(to: str, filename: str, media_id: str, body_text: str, with_doc_header: bool) -> dict:
    components = []
    if with_doc_header and media_id:
        components.append({"type": "header", "parameters": [
            {"type": "document", "document": {"id": media_id, "filename": filename}}]})
    components.append({"type": "body", "parameters": [
        {"type": "text", "text": (body_text or "")[:1024]}]})
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": settings.WHATSAPP_TEMPLATE_NAME,
            "language": {"code": settings.WHATSAPP_TEMPLATE_LANG or "en"},
            "components": components,
        },
    }


def _is_header_error(text: str) -> bool:
    t = (text or "").lower()
    return "132018" in t or "title component" in t or "does not contain" in t


def approval_configured() -> bool:
    return bool(is_configured() and settings.WHATSAPP_APPROVAL_TEMPLATE)


def _clean_var(s: str, n: int = 280) -> str:
    """Template body variables reject newlines/tabs and long runs of spaces (Meta #132000). Flatten."""
    t = " ".join(str(s or "").split())
    return t[:n] or "—"


async def send_approval(to: str, req_id: str, token: str, issue: str, fix: str, preview: str) -> str:
    """Send the auto-remediation approval template: body {{1}}=issue {{2}}=fix {{3}}=preview and two
    QUICK-REPLY buttons whose per-send payloads carry the decision + request id + token. When the
    recipient taps a button, Meta posts that payload to our webhook, which runs the decision. Returns
    the message id. Requires the 'remediation_approval' template to be APPROVED in WhatsApp Manager."""
    if not approval_configured():
        raise RuntimeError("WhatsApp approval not configured (WHATSAPP_APPROVAL_TEMPLATE + base creds)")
    msg = {
        "messaging_product": "whatsapp", "to": to, "type": "template",
        "template": {
            "name": settings.WHATSAPP_APPROVAL_TEMPLATE,
            "language": {"code": settings.WHATSAPP_APPROVAL_LANG or "en"},
            "components": [
                {"type": "body", "parameters": [
                    {"type": "text", "text": _clean_var(issue)},
                    {"type": "text", "text": _clean_var(fix)},
                    {"type": "text", "text": _clean_var(preview)}]},
                {"type": "button", "sub_type": "quick_reply", "index": "0",
                 "parameters": [{"type": "payload", "payload": f"approve|{req_id}|{token}"}]},
                {"type": "button", "sub_type": "quick_reply", "index": "1",
                 "parameters": [{"type": "payload", "payload": f"reject|{req_id}|{token}"}]},
            ],
        },
    }
    async with httpx.AsyncClient(timeout=30) as cx:
        r = await cx.post(f"{_base()}/messages", headers=_headers(), json=msg)
    if r.status_code >= 300:
        raise RuntimeError(f"WhatsApp approval send {r.status_code}: {r.text[:300]}")
    try:
        return (r.json().get("messages") or [{}])[0].get("id", "")
    except Exception:
        return ""


async def send_text(to: str, body: str) -> str:
    """Plain text message. Only deliverable inside the 24h customer-service window (i.e. right after
    the recipient messaged/tapped us) — used to confirm a decision back in-thread. Best-effort."""
    if not is_configured():
        return ""
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text",
               "text": {"body": (body or "")[:1000]}}
    async with httpx.AsyncClient(timeout=30) as cx:
        r = await cx.post(f"{_base()}/messages", headers=_headers(), json=payload)
    if r.status_code >= 300:
        raise RuntimeError(f"WhatsApp text {r.status_code}: {r.text[:200]}")
    try:
        return (r.json().get("messages") or [{}])[0].get("id", "")
    except Exception:
        return ""


def otp_configured() -> bool:
    """True when a dedicated approved OTP/authentication template is set. Without it, OTP delivery
    falls back to a plain text message (24h-window only) and is best-effort/unconfirmed."""
    return bool(is_configured() and settings.WHATSAPP_OTP_TEMPLATE)


async def send_otp(to: str, code: str, purpose: str = "verification") -> str:
    """Deliver a one-time code to `to`. Prefers an approved AUTHENTICATION template (single body var =
    the code) when WHATSAPP_OTP_TEMPLATE is configured; otherwise falls back to a plain text message
    (Meta delivers text only inside the 24h service window → cold sends may silently not arrive). Marks
    UNCONFIRMED in the handoff. Returns the message id; raises RuntimeError on a hard send failure."""
    if not is_configured():
        raise RuntimeError("WhatsApp not configured (set WHATSAPP_ACCESS_TOKEN / PHONE_NUMBER_ID / TEMPLATE_NAME)")
    if settings.WHATSAPP_OTP_TEMPLATE:
        msg = {
            "messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {
                "name": settings.WHATSAPP_OTP_TEMPLATE,
                "language": {"code": settings.WHATSAPP_OTP_LANG or "en"},
                "components": [
                    {"type": "body", "parameters": [{"type": "text", "text": _clean_var(code, 32)}]},
                    # AUTHENTICATION templates require the code echoed as the button parameter too.
                    {"type": "button", "sub_type": "url", "index": "0",
                     "parameters": [{"type": "text", "text": _clean_var(code, 32)}]},
                ],
            },
        }
        async with httpx.AsyncClient(timeout=30) as cx:
            r = await cx.post(f"{_base()}/messages", headers=_headers(), json=msg)
            # If the template has no URL button, retry body-only (mirrors send_document's self-heal).
            if r.status_code >= 300:
                msg["template"]["components"] = [msg["template"]["components"][0]]
                r = await cx.post(f"{_base()}/messages", headers=_headers(), json=msg)
        if r.status_code >= 300:
            raise RuntimeError(f"WhatsApp OTP send {r.status_code}: {r.text[:300]}")
        try:
            return (r.json().get("messages") or [{}])[0].get("id", "")
        except Exception:
            return ""
    # Fallback: plain text (24h-window only). Best-effort; returns "" outside the window.
    return await send_text(to, f"Your MetricsPro {purpose} code is {code}. It expires shortly. "
                               f"Do not share it with anyone.")


async def send_document(to: str, data: bytes, mime: str, filename: str, body_text: str) -> str:
    """Send the approved template to `to`. Attaches the file as a document header when the
    template supports one (WHATSAPP_TEMPLATE_DOC_HEADER), else sends body-only with the link.
    Self-heals to body-only if Meta rejects the header component. Returns the message id."""
    if not is_configured():
        raise RuntimeError("WhatsApp not configured (set WHATSAPP_ACCESS_TOKEN / PHONE_NUMBER_ID / TEMPLATE_NAME)")

    use_doc_header = bool(settings.WHATSAPP_TEMPLATE_DOC_HEADER)
    async with httpx.AsyncClient(timeout=120) as cx:
        media_id = await upload_media(cx, data, mime, filename) if use_doc_header else ""
        r = await cx.post(f"{_base()}/messages", headers=_headers(),
                          json=_template_msg(to, filename, media_id, body_text, use_doc_header))
        # The approved template may not actually have a document header → retry body-only.
        if r.status_code >= 300 and use_doc_header and _is_header_error(r.text):
            r = await cx.post(f"{_base()}/messages", headers=_headers(),
                              json=_template_msg(to, filename, "", body_text, False))
    if r.status_code >= 300:
        raise RuntimeError(f"WhatsApp send {r.status_code}: {r.text[:300]}")
    try:
        return (r.json().get("messages") or [{}])[0].get("id", "")
    except Exception:
        return ""
