"""WhatsApp delivery via the Meta Cloud API (Graph) — plain HTTPS, no SDK.

Business-initiated messages must use an APPROVED template. To send a report file
we (1) upload the file to the media endpoint for a media id, then (2) send a
template message whose HEADER is a document referencing that media id; a BODY text
variable carries the live report link.

Needs env (see app/core/config.py):
  WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_TEMPLATE_NAME,
  WHATSAPP_TEMPLATE_LANG (default en_US), WHATSAPP_GRAPH_VERSION (default v21.0).

The template must exist and be approved in Meta Business Manager with a document
header and a single body variable (the link/description). Until then sends fail
gracefully and are logged.
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


async def send_document(to: str, data: bytes, mime: str, filename: str, body_text: str) -> str:
    """Upload `data` then send the approved template referencing it. Returns message id."""
    if not is_configured():
        raise RuntimeError("WhatsApp not configured (set WHATSAPP_ACCESS_TOKEN / PHONE_NUMBER_ID / TEMPLATE_NAME)")

    async with httpx.AsyncClient(timeout=120) as cx:
        media_id = await upload_media(cx, data, mime, filename)
        msg = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": settings.WHATSAPP_TEMPLATE_NAME,
                "language": {"code": settings.WHATSAPP_TEMPLATE_LANG or "en_US"},
                "components": [
                    {"type": "header", "parameters": [
                        {"type": "document", "document": {"id": media_id, "filename": filename}}]},
                    {"type": "body", "parameters": [
                        {"type": "text", "text": (body_text or "")[:1024]}]},
                ],
            },
        }
        r = await cx.post(f"{_base()}/messages", headers=_headers(), json=msg)
    if r.status_code >= 300:
        raise RuntimeError(f"WhatsApp send {r.status_code}: {r.text[:300]}")
    try:
        return (r.json().get("messages") or [{}])[0].get("id", "")
    except Exception:
        return ""
