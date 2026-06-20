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
